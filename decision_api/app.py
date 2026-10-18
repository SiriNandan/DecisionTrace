"""FastAPI application for DecisionTrace decision recording, listing, inspection, and verification."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Literal
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from backend.contract import ReconstructionReport
from backend.auth import Principal, ensure_decision_visible, log_access, require_scope, tenant_connection
from backend.ingest import DecisionEnvelopeV1, record_decision
from decision_api.service import (
    create_decision,
    get_decision,
    get_decision_detail,
    list_decisions,
    list_decisions_page,
    record_override,
    verify_decision,
)
from backend.anchoring import append_anchor
from backend.web import configure_app
from backend.migrate import apply_migrations, auto_migrate_enabled
from backend.signals import signals_for_decision
from decision_api.access_api import router as access_router
from decision_api.anchors_api import router as anchors_router
from decision_api.consent_api import router as consent_router
from decision_api.team_api import router as team_router
from decision_api.mappings_api import router as mappings_router
from decision_api.signals_api import router as signals_router
from decision_api.quality_api import router as quality_router
from recon_agent.router import evict_decision, reset_state as reset_reconstruct_cache, router as reconstruct_router

logger = logging.getLogger(__name__)


class DecideRequest(BaseModel):
    """Request payload for POST /decide."""

    applicant_id: str = Field(..., min_length=1, description="Applicant identifier (e.g. APP-0001)")
    requested_amount: float | None = Field(
        default=None, gt=0, description="Requested loan amount override"
    )
    document_refs: list[str] | None = Field(
        default=None, description="Optional list of document references to inspect"
    )


class OverrideRequest(BaseModel):
    """Request payload for POST /overrides."""

    decision_id: str = Field(..., min_length=1, description="UUID of the decision to override")
    new_outcome: Literal["APPROVE", "DECLINE", "REFER"] = Field(
        ..., description="Overridden outcome (APPROVE, DECLINE, or REFER)"
    )
    reason: str = Field(
        ..., min_length=20, description="Justification for the override (minimum 20 characters)"
    )
    actor_role: str | None = Field(
        default=None,
        description="Ignored. The recorded role always comes from the authenticated credential.",
    )


def _parse_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid decision_id UUID.") from exc


def _anchor(created: dict[str, Any]) -> None:
    try:
        append_anchor(created["decision_id"], created["chain_length"], created["chain_head"], created["decided_at"])
    except OSError:
        logger.exception("Could not write chain-head anchor for %s", created["decision_id"])


@asynccontextmanager
async def _lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    """In local development, bring an older database up to date on start so new screens don't hit missing tables."""
    if auto_migrate_enabled():
        try:
            applied = apply_migrations()
            logger.info("Database is up to date (%d migration files checked).", len(applied))
        except Exception:
            logger.exception("Could not update the database on start-up; /health will say what is missing.")
    yield
    from backend.auth import close_pools

    close_pools()


def create_app() -> FastAPI:
    """Create and configure the DecisionTrace FastAPI application."""
    application = FastAPI(
        lifespan=_lifespan,
        title="DecisionTrace Decision API",
        description=(
            "Records loan decisions with hash-chained, decision-time evidence that "
            "supports audit and explanation and is tamper-evident against a retained reference."
        ),
        version="0.1.0",
    )

    configure_app(application)
    application.include_router(reconstruct_router)
    application.include_router(mappings_router)
    application.include_router(quality_router)
    application.include_router(access_router)
    application.include_router(signals_router)
    application.include_router(anchors_router)
    application.include_router(consent_router)
    application.include_router(team_router)

    @application.post("/decide")
    def post_decide(
        payload: DecideRequest,
        request: Request,
        principal: Principal = Depends(require_scope("decide:write")),
        x_decision_date: str | None = Header(default=None, alias="X-Decision-Date"),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: str | None = Header(default=None, alias="X-Idempotency-Key"),
    ) -> dict[str, Any]:
        env_mode = os.environ.get("ENV", "dev").lower()
        if x_decision_date is not None and env_mode != "dev":
            raise HTTPException(
                status_code=403,
                detail="X-Decision-Date header is only accepted in ENV=dev.",
            )

        effective_idem_key = idempotency_key or x_idempotency_key
        data_dir_override = getattr(request.app.state, "data_dir", None)

        try:
            with tenant_connection(principal.tenant_id) as conn:
                created = create_decision(
                    conn,
                    applicant_id=payload.applicant_id,
                    requested_amount=payload.requested_amount,
                    document_refs=payload.document_refs,
                    decided_at=x_decision_date,
                    allow_backdating=(x_decision_date is not None and env_mode == "dev"),
                    idempotency_key=effective_idem_key,
                    data_dir=data_dir_override,
                )
                log_access(conn, principal, "create", "decision", created["decision_id"])
            _anchor(created)
            return created
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/v1/decisions")
    def post_v1_decision(
        envelope: DecisionEnvelopeV1,
        response: Response,
        principal: Principal = Depends(require_scope("ingest:write")),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> dict[str, Any]:
        """Record a decision the lender's own engine made, with the evidence it used (envelope schema v1)."""
        key = (idempotency_key or "").strip()
        if not key or len(key) > 200:
            raise HTTPException(status_code=422, detail="An Idempotency-Key header (1-200 characters) is required.")
        try:
            with tenant_connection(principal.tenant_id) as conn:
                result, replayed = record_decision(
                    conn, tenant_id=principal.tenant_id, envelope=envelope, idempotency_key=key
                )
                log_access(conn, principal, "ingest_replay" if replayed else "ingest", "decision", result["decision_id"])
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not replayed:
            _anchor(result)
            response.status_code = 201
        return {**result, "replayed": replayed, "schema_version": envelope.schema_version}

    @application.post("/overrides")
    def post_override(
        payload: OverrideRequest,
        principal: Principal = Depends(require_scope(None)),
    ) -> dict[str, Any]:
        try:
            dec_uuid = uuid.UUID(payload.decision_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid decision_id UUID.") from exc
        if principal.is_customer:
            # A customer can ask for their own decision to be looked at again. Nothing more.
            if "review:request" not in principal.scopes or payload.decision_id != principal.decision_id:
                raise HTTPException(status_code=404, detail="We couldn't find that decision.")
            if payload.new_outcome != "REFER":
                raise HTTPException(status_code=403, detail="You can ask for a review, but only a person at the lender can change the result.")
            kind = "review_request"
        elif "overrides:write" not in principal.scopes:
            raise HTTPException(status_code=403, detail="This sign-in isn't allowed to do that.")
        else:
            kind = "override"
        # Who did this comes from the credential, never from the request body.
        actor_role = "customer" if kind == "review_request" else principal.role

        try:
            with tenant_connection(principal.tenant_id) as conn:
                ensure_decision_visible(conn, principal, dec_uuid)
                if kind == "review_request" and conn.execute(
                    "SELECT 1 FROM overrides WHERE decision_id = %s AND kind = 'review_request'", (dec_uuid,)
                ).fetchone():
                    raise HTTPException(status_code=409, detail="You've already asked for a review. A person at your lender will look at it.")
                result = record_override(
                    conn,
                    decision_id=dec_uuid,
                    new_outcome=payload.new_outcome,
                    reason=payload.reason,
                    actor_role=actor_role,
                    kind=kind,
                )
                log_access(conn, principal, "override", "decision", str(dec_uuid))
            evict_decision(str(dec_uuid))
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/decisions")
    def get_decisions_list(
        response: Response,
        applicant_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        cursor: str | None = Query(default=None, description="The X-Next-Cursor value from the previous page"),
        principal: Principal = Depends(require_scope("decisions:read")),
    ) -> list[dict[str, Any]]:
        """Newest first. When there is more, the response carries an `X-Next-Cursor` header: send it back as `cursor`."""
        with tenant_connection(principal.tenant_id) as conn:
            try:
                rows, next_cursor = list_decisions_page(
                    conn, applicant_id=applicant_id, limit=limit, tenant_id=principal.tenant_id,
                    only_decision_id=principal.decision_id, cursor=cursor,
                    portfolios=list(principal.portfolios) if principal.portfolios is not None else None,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            if next_cursor:
                response.headers["X-Next-Cursor"] = next_cursor
            log_access(conn, principal, "list", "decision", f"n={len(rows)}")
            return rows

    @application.get("/decisions/{decision_id}")
    def get_decision_by_id(
        decision_id: str,
        principal: Principal = Depends(require_scope("decisions:read")),
    ) -> dict[str, Any]:
        dec_uuid = _parse_uuid(decision_id)
        try:
            with tenant_connection(principal.tenant_id) as conn:
                ensure_decision_visible(conn, principal, dec_uuid)
                record = get_decision(conn, dec_uuid)
                if principal.is_customer:  # internal notes are for the lender's staff only
                    record["snapshots"] = [s for s in record["snapshots"] if s["evidence_type"] != "analyst_note"]
                elif "signals:read" in principal.scopes:
                    record["signals"] = signals_for_decision(conn, principal.tenant_id, str(dec_uuid))
                log_access(conn, principal, "read", "decision", str(dec_uuid))
                return record
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="We couldn't find that decision.") from exc

    @application.get("/decisions/{decision_id}/verify")
    def get_decision_verification(
        decision_id: str,
        principal: Principal = Depends(require_scope("decisions:read")),
    ) -> dict[str, Any]:
        dec_uuid = _parse_uuid(decision_id)
        try:
            with tenant_connection(principal.tenant_id) as conn:
                ensure_decision_visible(conn, principal, dec_uuid)
                result = verify_decision(conn, dec_uuid)
                log_access(conn, principal, "verify", "decision", str(dec_uuid))
                return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="We couldn't find that decision.") from exc

    @application.get("/v1/access-log")
    def get_access_log(
        limit: int = Query(default=100, ge=1, le=1000),
        principal: Principal = Depends(require_scope("audit:read")),
    ) -> list[dict[str, Any]]:
        """Who looked at or changed what, for this tenant only (newest first)."""
        with tenant_connection(principal.tenant_id) as conn:
            rows = conn.execute(
                "SELECT log_id, credential_id, role, action, object_type, object_id, outcome, logged_at "
                "FROM access_log WHERE tenant_id = %s ORDER BY log_id DESC LIMIT %s",
                (principal.tenant_id, limit),
            ).fetchall()
        return [
            {
                "log_id": r[0], "credential_id": r[1], "role": r[2], "action": r[3],
                "object_type": r[4], "object_id": r[5], "outcome": r[6], "logged_at": r[7].isoformat(),
            }
            for r in rows
        ]

    return application


app = create_app()


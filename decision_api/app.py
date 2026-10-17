"""FastAPI application for DecisionTrace decision recording, listing, inspection, and verification."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any, Literal
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
import psycopg

from backend.contract import ReconstructionReport
from data.load_policies import get_db_url
from decision_api.service import (
    create_decision,
    get_decision,
    get_decision_detail,
    list_decisions,
    record_override,
    verify_decision,
)
from backend.anchoring import append_anchor
from backend.web import configure_app
from recon_agent.router import reset_state as reset_reconstruct_cache, router as reconstruct_router

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
    actor_role: str = Field(..., min_length=1, description="Role of the reviewer recording the override")


def get_connection() -> psycopg.Connection[Any]:
    """Open a database connection using DB_URL from environment configuration."""
    db_url = get_db_url(os.environ.get("DB_URL"))
    return psycopg.connect(db_url, connect_timeout=5)


def require_write_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    """Protect write endpoints when DT_API_KEY is configured; open (local demo) when it is unset."""
    expected = os.environ.get("DT_API_KEY", "").strip()
    if not expected:
        return
    if x_api_key is None or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key.")


def create_app() -> FastAPI:
    """Create and configure the DecisionTrace FastAPI application."""
    application = FastAPI(
        title="DecisionTrace Decision API",
        description=(
            "Records loan decisions with hash-chained, decision-time evidence that "
            "supports audit and explanation and is tamper-evident against a retained reference."
        ),
        version="0.1.0",
    )

    configure_app(application)
    application.include_router(reconstruct_router)

    @application.post("/decide", dependencies=[Depends(require_write_key)])
    def post_decide(
        payload: DecideRequest,
        request: Request,
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
            with get_connection() as conn:
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
            try:
                append_anchor(
                    created["decision_id"], created["chain_length"], created["chain_head"], created["decided_at"]
                )
            except OSError:
                logger.exception("Could not write chain-head anchor for %s", created["decision_id"])
            return created
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.post("/overrides", dependencies=[Depends(require_write_key)])
    def post_override(payload: OverrideRequest) -> dict[str, Any]:
        try:
            dec_uuid = uuid.UUID(payload.decision_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid decision_id UUID.") from exc

        try:
            with get_connection() as conn:
                result = record_override(
                    conn,
                    decision_id=dec_uuid,
                    new_outcome=payload.new_outcome,
                    reason=payload.reason,
                    actor_role=payload.actor_role,
                )
            reset_reconstruct_cache()
            return result
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @application.get("/decisions")
    def get_decisions_list(
        applicant_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        with get_connection() as conn:
            return list_decisions(conn, applicant_id=applicant_id, limit=limit)

    @application.get("/decisions/{decision_id}")
    def get_decision_by_id(decision_id: str) -> dict[str, Any]:
        try:
            dec_uuid = uuid.UUID(decision_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid decision_id UUID.") from exc

        try:
            with get_connection() as conn:
                return get_decision(conn, dec_uuid)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.get("/decisions/{decision_id}/verify")
    def get_decision_verification(decision_id: str) -> dict[str, Any]:
        try:
            dec_uuid = uuid.UUID(decision_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid decision_id UUID.") from exc

        try:
            with get_connection() as conn:
                return verify_decision(conn, dec_uuid)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return application


app = create_app()


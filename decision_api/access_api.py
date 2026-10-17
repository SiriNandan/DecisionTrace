"""Who am I, is the service healthy, and customer links (one customer, one decision)."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.auth import (
    Principal,
    plain_connection,
    ensure_decision_visible,
    hash_api_key,
    log_access,
    require_scope,
    tenant_connection,
)
from backend.migrate import schema_status

router = APIRouter()


class ClaimRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    label: str = Field(default="", max_length=100)


@router.get("/health")
def health() -> dict[str, Any]:
    """Open to anyone: is the service up, and is its database up to date? No data is returned."""
    try:
        with plain_connection() as conn:
            status = schema_status(conn)
    except psycopg.Error:
        return {"status": "database_down", "database": "unreachable"}
    return {"status": "ok" if status["ok"] else "needs_upgrade", "database": "ready" if status["ok"] else "behind"}


@router.get("/v1/me")
def who_am_i(principal: Principal = Depends(require_scope(None))) -> dict[str, Any]:
    """What this sign-in is: lets the screen show who is signed in and which parts to offer."""
    name = principal.tenant_id
    with tenant_connection(principal.tenant_id) as conn:
        row = conn.execute("SELECT display_name FROM tenants WHERE tenant_id = %s", (principal.tenant_id,)).fetchone()
        if row:
            name = str(row[0])
    return {
        "role": principal.role,
        "kind": "customer" if principal.is_customer else "staff",
        "tenant_id": principal.tenant_id,
        "tenant_name": name,
        "label": principal.label,
        "scopes": sorted(principal.scopes),
        "decision_id": principal.decision_id,
        "expires_at": principal.expires_at.isoformat() if principal.expires_at else None,
    }


def _claim_row(r: tuple[Any, ...]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = "withdrawn" if r[5] else "expired" if r[3] <= now else "active"
    return {"claim_id": int(r[0]), "decision_id": str(r[1]), "label": r[2], "expires_at": r[3].isoformat(),
            "created_at": r[4].isoformat(), "state": state}


@router.post("/v1/decisions/{decision_id}/claim-links")
def create_claim_link(
    decision_id: uuid.UUID, req: ClaimRequest, principal: Principal = Depends(require_scope("claims:issue"))
) -> dict[str, Any]:
    """Make a link a customer can use to open this one decision. The secret is shown once and never stored."""
    token = "dtc_" + secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=req.days)
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        row = conn.execute(
            "INSERT INTO claim_links (tenant_id, decision_id, token_sha256, label, expires_at, created_by_credential, created_by_role) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING claim_id",
            (principal.tenant_id, decision_id, hash_api_key(token), req.label, expires, principal.credential_id, principal.role),
        ).fetchone()
        log_access(conn, principal, "claim_link_create", "decision", str(decision_id))
    return {"claim_id": int(row[0]), "decision_id": str(decision_id), "token": token, "expires_at": expires.isoformat(),
            "path": f"?d={decision_id}&t={token}"}


@router.get("/v1/decisions/{decision_id}/claim-links")
def list_claim_links(decision_id: uuid.UUID, principal: Principal = Depends(require_scope("claims:issue"))) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        rows = conn.execute(
            "SELECT c.claim_id, c.decision_id, c.label, c.expires_at, c.created_at, "
            "EXISTS (SELECT 1 FROM claim_revocations r WHERE r.claim_id = c.claim_id) "
            "FROM claim_links c WHERE c.decision_id = %s AND c.tenant_id = %s ORDER BY c.claim_id DESC",
            (decision_id, principal.tenant_id),
        ).fetchall()
    return [_claim_row(r) for r in rows]


@router.post("/v1/claim-links/{claim_id}/revoke")
def revoke_claim_link(claim_id: int, principal: Principal = Depends(require_scope("claims:issue"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        row = conn.execute("SELECT decision_id FROM claim_links WHERE claim_id = %s AND tenant_id = %s",
                           (claim_id, principal.tenant_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="We couldn't find that link.")
        conn.execute("INSERT INTO claim_revocations (claim_id, tenant_id, revoked_by_role) VALUES (%s, %s, %s)",
                     (claim_id, principal.tenant_id, principal.role))
        log_access(conn, principal, "claim_link_revoke", "decision", str(row[0]))
    return {"claim_id": claim_id, "state": "withdrawn"}

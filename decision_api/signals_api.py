"""Risk-signal routes: run the checks, list signals, and record that a person looked at one."""

from __future__ import annotations

import uuid
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.auth import Principal, ensure_decision_visible, log_access, require_scope, tenant_connection
from backend.signals import SIGNAL_COLS, scan_signals, signal_row, signals_for_decision

router = APIRouter()


class RunRequest(BaseModel):
    decision_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    recent: int = Field(default=5000, ge=1, le=10000)


class ReviewRequest(BaseModel):
    action: Literal["acknowledge", "clear"]
    note: str = Field(..., min_length=10, max_length=500)


@router.post("/v1/signals/run")
def run_signals(req: RunRequest, principal: Principal = Depends(require_scope("signals:write"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        result = scan_signals(conn, principal.tenant_id, recent=req.recent,
                              decision_ids=[str(d) for d in req.decision_ids] if req.decision_ids else None)
        open_total = conn.execute(
            "SELECT count(*) FROM risk_signals s WHERE s.tenant_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM signal_reviews r WHERE r.signal_id = s.signal_id)", (principal.tenant_id,),
        ).fetchone()[0]
        log_access(conn, principal, "signals_run", "decision", f"checked={result['decisions_checked']}")
    return {**result, "open_total": int(open_total)}


@router.get("/v1/signals")
def list_signals(
    status: Literal["open", "acknowledged", "cleared", "all"] = "open",
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_scope("signals:read")),
) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        rows = conn.execute(
            f"SELECT * FROM (SELECT {SIGNAL_COLS}, COALESCE((SELECT action FROM signal_reviews r WHERE r.signal_id = s.signal_id "
            "ORDER BY review_id DESC LIMIT 1), 'open') AS status FROM risk_signals s WHERE s.tenant_id = %s) q "
            "WHERE (%s = 'all' OR q.status = %s) "
            "ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, signal_id DESC LIMIT %s",
            (principal.tenant_id, status, status, limit),
        ).fetchall()
    return [signal_row(r[:8], str(r[8])) for r in rows]


@router.post("/v1/signals/{signal_id}/review")
def review_signal(signal_id: int, req: ReviewRequest, principal: Principal = Depends(require_scope("signals:resolve"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        row = conn.execute("SELECT signal_id FROM risk_signals WHERE signal_id = %s AND tenant_id = %s",
                           (signal_id, principal.tenant_id)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="We couldn't find that item.")
        if conn.execute("SELECT 1 FROM signal_reviews WHERE signal_id = %s", (signal_id,)).fetchone():
            raise HTTPException(status_code=409, detail="This item was already looked at.")
        action = "acknowledged" if req.action == "acknowledge" else "cleared"
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO signal_reviews (signal_id, tenant_id, action, note, reviewer_credential, reviewer_role) VALUES (%s, %s, %s, %s, %s, %s)",
                    (signal_id, principal.tenant_id, action, req.note, principal.credential_id, principal.role),
                )
        except psycopg.errors.UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="This item was already looked at.") from exc
        log_access(conn, principal, f"signal_{action}", "signal", str(signal_id))
    return {"signal_id": signal_id, "status": action}


@router.get("/v1/decisions/{decision_id}/signals")
def decision_signals_route(decision_id: uuid.UUID, principal: Principal = Depends(require_scope("signals:read"))) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        return signals_for_decision(conn, principal.tenant_id, str(decision_id))

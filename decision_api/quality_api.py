"""Data-quality routes: run the checks, list findings, and resolve them (correct or dismiss)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.auth import Principal, log_access, require_scope, tenant_connection
from backend.mapping_ai import default_llm
from backend.crypto import Reveal
from backend.quality import CorrectionConflict, append_correction, finding_status, load_corrections, open_finding, scan

router = APIRouter()


class RunRequest(BaseModel):
    decision_ids: list[uuid.UUID] | None = Field(default=None, max_length=500)
    recent: int = Field(default=200, ge=1, le=2000)
    use_ai: bool = True


class ResolveRequest(BaseModel):
    action: Literal["correct", "dismiss"]
    note: str = Field(..., min_length=10, max_length=500)
    new_value: float | str | None = None


def _row(r: tuple[Any, ...], status: str, reveal: Reveal | None = None) -> dict[str, Any]:
    out = {
        "finding_id": int(r[0]), "decision_id": str(r[1]), "rule_id": r[2], "kind": r[3], "severity": r[4],
        "evidence_ref": r[5], "field": r[6], "observed": r[7].get("value") if isinstance(r[7], dict) else r[7],
        "message": r[8], "suggested_fix": r[9], "ai_note": r[10], "found_by": r[11], "created_at": r[12].isoformat(),
        "status": status,
    }
    return open_finding(reveal, out, r[13]) if reveal is not None else out


COLS = ("finding_id, decision_id, rule_id, kind, severity, evidence_ref, field, observed, message, suggested_fix, "
        "ai_note, found_by, created_at, enc_detail")


@router.post("/v1/quality/run")
def run_checks(req: RunRequest, principal: Principal = Depends(require_scope("quality:write"))) -> dict[str, Any]:
    """Check this lender's decisions. Safe to repeat: findings already on file aren't added again."""
    llm = default_llm() if req.use_ai else None
    with tenant_connection(principal.tenant_id) as conn:
        result = scan(conn, principal.tenant_id, decision_ids=[str(d) for d in req.decision_ids] if req.decision_ids else None,
                      recent=req.recent, llm=llm)
        open_total = conn.execute(
            "SELECT count(*) FROM quality_findings f WHERE f.tenant_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM finding_reviews r WHERE r.finding_id = f.finding_id)", (principal.tenant_id,)
        ).fetchone()[0]
        log_access(conn, principal, "quality_run", "decision", f"checked={result['decisions_checked']}")
    return {**result, "open_total": int(open_total), "ai_used": llm is not None}


@router.get("/v1/quality/findings")
def list_findings(
    status: Literal["open", "corrected", "dismissed", "all"] = "open",
    limit: int = Query(default=100, ge=1, le=500),
    principal: Principal = Depends(require_scope("quality:read")),
) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        rows = conn.execute(
            f"SELECT {COLS}, COALESCE((SELECT action FROM finding_reviews r WHERE r.finding_id = f.finding_id "
            "ORDER BY review_id DESC LIMIT 1), 'open') AS status FROM quality_findings f WHERE f.tenant_id = %s "
            "ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, finding_id DESC LIMIT %s",
            (principal.tenant_id, 2000),
        ).fetchall()
        reveal = Reveal(conn)
        out = [_row(r[:14], str(r[14]), reveal) for r in rows]
    return [f for f in out if status == "all" or f["status"] == status][:limit]


@router.post("/v1/quality/findings/{finding_id}/resolve")
def resolve_finding(
    finding_id: int, req: ResolveRequest, principal: Principal = Depends(require_scope("quality:resolve"))
) -> dict[str, Any]:
    """A person accepts a correction or says the data is right as it is. The original record is never changed."""
    with tenant_connection(principal.tenant_id) as conn:
        row = conn.execute(f"SELECT {COLS} FROM quality_findings WHERE finding_id = %s", (finding_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="We couldn't find that item.")
        finding = _row(row, "open", Reveal(conn))
        if finding_status(conn, finding_id) != "open":
            raise HTTPException(status_code=409, detail="This item was already dealt with.")
        correction = None
        if req.action == "correct":
            fix = finding["suggested_fix"] or {}
            new_value = req.new_value if req.new_value is not None else fix.get("new_value")
            ref, key = fix.get("evidence_ref") or finding["evidence_ref"], fix.get("field") or finding["field"]
            if new_value is None or not ref or not key:
                raise HTTPException(status_code=422, detail="Enter the corrected value to apply.")
            try:
                correction = append_correction(
                    conn, tenant_id=principal.tenant_id, decision_id=finding["decision_id"], evidence_ref=ref,
                    field_name=key, new_value=new_value, reason=req.note,
                    source="suggested" if req.new_value is None or req.new_value == fix.get("new_value") else "person",
                    role=principal.role, credential_id=principal.credential_id, finding_id=finding_id,
                )
            except LookupError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except CorrectionConflict as exc:
                raise HTTPException(status_code=409, detail="Someone else just changed this record. Please try again.") from exc
        conn.execute(
            "INSERT INTO finding_reviews (finding_id, tenant_id, action, note, reviewer_credential, reviewer_role) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (finding_id, principal.tenant_id, "corrected" if correction else "dismissed", req.note, principal.credential_id,
             principal.role),
        )
        log_access(conn, principal, "quality_correct" if correction else "quality_dismiss", "decision", finding["decision_id"])
    return {"finding_id": finding_id, "status": "corrected" if correction else "dismissed", "correction": correction}


@router.get("/v1/decisions/{decision_id}/corrections")
def decision_corrections(decision_id: uuid.UUID, principal: Principal = Depends(require_scope("decisions:read"))) -> dict[str, Any]:
    from backend.auth import ensure_decision_visible

    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        corrections, ok, why = load_corrections(conn, str(decision_id))
        log_access(conn, principal, "read_corrections", "decision", str(decision_id))
    return {"decision_id": str(decision_id), "corrections": corrections, "chain_valid": ok, "problem": why}

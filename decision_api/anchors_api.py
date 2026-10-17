"""Sealing routes: seal new records, list and check seals, and give a proof for one decision."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import Principal, ensure_decision_visible, log_access, require_scope, tenant_connection
from backend.merkle import FilePublisher, load_signer
from backend.seals import SealConflict, list_batches, pending_count, proof_for, publish_missing, seal_batch, verify_seals

router = APIRouter()


@router.get("/v1/seals/public-key")
def public_key() -> dict[str, Any]:
    """The key that checks seals. Open, because it can only be used to check, never to seal."""
    s = load_signer()
    return {"algorithm": "Ed25519", "key_id": s.key_id, "public_key": s.public_b64}


@router.post("/v1/seals/run")
def run_seal(principal: Principal = Depends(require_scope("seals:write"))) -> dict[str, Any]:
    """Seal every record of this lender that isn't sealed yet into one signed batch."""
    with tenant_connection(principal.tenant_id) as conn:
        try:
            head = seal_batch(conn, principal.tenant_id, load_signer())
        except SealConflict as exc:
            raise HTTPException(status_code=409, detail="Another seal was just made. Please try again.") from exc
        log_access(conn, principal, "seal_run", "seal", str(head["seq"]) if head else "nothing")
    publisher = FilePublisher.from_env()
    if publisher is not None:  # after the seal is safely recorded; a failure here is repaired by the next run
        with tenant_connection(principal.tenant_id) as conn:
            publish_missing(conn, principal.tenant_id, publisher)
    with tenant_connection(principal.tenant_id) as conn:
        waiting = pending_count(conn, principal.tenant_id)
    message = "Everything was already sealed." if head is None else "Sealed."
    if waiting:
        message += f" {waiting} more records are waiting; run it again."
    return {"sealed": head is not None, "batch": head, "message": message, "not_yet_sealed": waiting}


@router.get("/v1/seals")
def seals(limit: int = Query(default=50, ge=1, le=500), principal: Principal = Depends(require_scope("seals:read"))) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        return list_batches(conn, principal.tenant_id, limit)


@router.get("/v1/seals/verify")
def check_seals(principal: Principal = Depends(require_scope("seals:read"))) -> dict[str, Any]:
    """Recompute every seal and compare with the live records and with the published copy (if one is configured)."""
    with tenant_connection(principal.tenant_id) as conn:
        result = verify_seals(conn, principal.tenant_id, load_signer(), FilePublisher.from_env())
        log_access(conn, principal, "seal_verify", "seal", f"ok={result['ok']}")
    return result


@router.get("/v1/decisions/{decision_id}/seal")
def decision_seal(decision_id: uuid.UUID, principal: Principal = Depends(require_scope("decisions:read"))) -> dict[str, Any]:
    """Proof that this decision's record was sealed (and, if it has corrections, that they were too)."""
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        signer = load_signer()
        out = proof_for(conn, principal.tenant_id, str(decision_id), signer)
        out["corrections"] = proof_for(conn, principal.tenant_id, str(decision_id), signer, "corrections")
        log_access(conn, principal, "seal_proof", "decision", str(decision_id))
    return out

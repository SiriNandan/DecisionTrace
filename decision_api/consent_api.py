"""Customer choices: read the current state, and record a change (a new row; nothing is edited)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend import consent
from backend.auth import Principal, ensure_decision_visible, log_access, require_scope, tenant_connection
from recon_agent.router import evict_decision

router = APIRouter()


class ChoiceRequest(BaseModel):
    granted: bool


@router.get("/v1/decisions/{decision_id}/consents")
def get_consents(decision_id: uuid.UUID, principal: Principal = Depends(require_scope("decisions:read"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        return consent.state(conn, str(decision_id))


@router.post("/v1/decisions/{decision_id}/consents/{purpose}")
def record_choice(
    decision_id: uuid.UUID, purpose: str, req: ChoiceRequest, principal: Principal = Depends(require_scope("consent:write"))
) -> dict[str, Any]:
    """Allow or stop allowing something for this decision. The customer's own choice, or a staff member recording it for them."""
    if purpose not in consent.PURPOSES:
        raise HTTPException(status_code=404, detail="We don't have that choice.")
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, decision_id)
        current = consent.state(conn, str(decision_id))
        if current["policy"] == "never":
            raise HTTPException(status_code=409, detail="Your lender doesn't use AI-written summaries, so there is nothing to choose.")
        conn.execute(
            "INSERT INTO consents (tenant_id, decision_id, purpose, granted, wording_version, given_by, claim_id, recorded_by_role) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (principal.tenant_id, decision_id, purpose, req.granted, consent.WORDING_VERSION,
             "customer" if principal.is_customer else "staff_for_customer", principal.claim_id, principal.role),
        )
        log_access(conn, principal, "consent_given" if req.granted else "consent_withdrawn", "decision", str(decision_id))
        new_state = consent.state(conn, str(decision_id))
    evict_decision(str(decision_id))  # a cached explanation may have been written under the old choice
    return new_state

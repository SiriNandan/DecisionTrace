"""A lender's own administrator manages the team: who has a key, what it lets them do, and which portfolios it covers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.mailer import send_invite
from backend.auth import (
    SCOPABLE_ROLES, TEAM_ROLES, Principal, generate_api_key, hash_api_key, log_access, require_scope, tenant_connection,
)

router = APIRouter()

MAX_ACTIVE_KEYS = 200

ROLE_INFO: dict[str, tuple[str, str]] = {
    "lender_admin": ("Team administrator", "Adds and removes people, sees who looked at what, and can read decisions and insights. Cannot change anything."),
    "lender_ingest": ("Data officer", "The only role that loads decision data. Cannot read decisions."),
    "lender_analyst": ("Analyst", "Reads decisions, records reviews and overrides, signs off data matching, gives customers their link."),
    "lender_support": ("Support", "Opens a decision to help a customer and gives them their link. Cannot change anything."),
    "auditor": ("Auditor", "Reads everything, including checks and the access log. Cannot change anything."),
}


class NewKey(BaseModel):
    label: str = Field(..., min_length=2, max_length=80, description="The person's name, e.g. 'Priya Shah'")
    email: str = Field(..., max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="Where the invitation and key are sent")
    role: str | None = Field(default=None, description="One role (use `roles` for several)")
    roles: list[str] | None = Field(default=None, min_length=1, max_length=len(TEAM_ROLES))
    expires_days: int | None = Field(default=None, ge=1, le=365)
    portfolios: list[str] | None = Field(default=None, max_length=50)


def _member(r: tuple[Any, ...]) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = "withdrawn" if r[5] else "expired" if r[4] is not None and r[4] <= now else "active"
    return {"credential_id": int(r[0]), "label": r[1], "role": r[2], "portfolios": list(r[3]) if r[3] is not None else None,
            "expires_at": r[4].isoformat() if r[4] else None, "withdrawn_at": r[5].isoformat() if r[5] else None,
            "created_at": r[6].isoformat(), "email": r[7], "roles": [r[2], *(r[8] or [])], "state": state}


@router.get("/v1/team")
def list_team(principal: Principal = Depends(require_scope("team:manage"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        rows = conn.execute(
            "SELECT credential_id, label, role, portfolios, expires_at, revoked_at, created_at, email, extra_roles FROM api_credentials "
            "WHERE tenant_id = %s ORDER BY credential_id DESC LIMIT 500", (principal.tenant_id,),
        ).fetchall()
        portfolios = conn.execute(
            "SELECT portfolio, count(*) FROM decisions WHERE tenant_id = %s AND portfolio IS NOT NULL GROUP BY portfolio ORDER BY portfolio",
            (principal.tenant_id,),
        ).fetchall()
    return {"members": [_member(r) for r in rows], "you": principal.credential_id,
            "portfolios": [{"name": p[0], "decisions": int(p[1])} for p in portfolios],
            "roles": [{"role": r, "name": ROLE_INFO[r][0], "description": ROLE_INFO[r][1], "can_limit_to_portfolios": r in SCOPABLE_ROLES}
                      for r in TEAM_ROLES]}


@router.post("/v1/team/keys", status_code=201)
def add_member(req: NewKey, principal: Principal = Depends(require_scope("team:manage"))) -> dict[str, Any]:
    """Create a key for a new team member. The key is shown once and only its fingerprint is kept."""
    roles = list(dict.fromkeys(req.roles or ([req.role] if req.role else [])))
    if not roles or any(r not in TEAM_ROLES for r in roles):
        raise HTTPException(status_code=422, detail="Please choose at least one of the roles listed.")
    portfolios = sorted({p.strip() for p in req.portfolios or [] if p.strip()}) or None
    whole = [ROLE_INFO[r][0] for r in roles if r not in SCOPABLE_ROLES]
    if portfolios is not None and whole:
        raise HTTPException(status_code=422, detail=f"{' and '.join(whole)} covers the whole lender and can't be limited to portfolios.")
    expires = datetime.now(timezone.utc) + timedelta(days=req.expires_days) if req.expires_days else None
    key = generate_api_key()
    email = req.email.strip().lower()
    with tenant_connection(principal.tenant_id) as conn:
        active = conn.execute(
            "SELECT count(*) FROM api_credentials WHERE tenant_id = %s AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())",
            (principal.tenant_id,),
        ).fetchone()[0]
        if active >= MAX_ACTIVE_KEYS:
            raise HTTPException(status_code=409, detail="This team has reached its limit of active keys. Withdraw some you no longer need.")
        cred_id = conn.execute(
            "SELECT team_issue_key(%s, %s, %s, %s, %s, %s, %s, %s)",
            (hash_api_key(key), roles[0], req.label.strip(), expires, portfolios, principal.credential_id, email, roles[1:] or None),
        ).fetchone()[0]
        log_access(conn, principal, "team_key_issued", "credential", f"{cred_id} roles={','.join(roles)}")
        tenant_name = (conn.execute("SELECT display_name FROM tenants WHERE tenant_id = %s", (principal.tenant_id,)).fetchone() or [principal.tenant_id])[0]
    expires_iso = expires.isoformat() if expires else None
    mail = send_invite(email, req.label.strip(), str(tenant_name), " and ".join(ROLE_INFO[r][0] for r in roles), key, expires_iso)
    return {"credential_id": int(cred_id), "key": key, "role": roles[0], "roles": roles, "label": req.label.strip(), "email": email,
            "portfolios": portfolios, "expires_at": expires_iso, "email_status": mail}


@router.post("/v1/team/keys/{credential_id}/revoke")
def withdraw_member(credential_id: int, principal: Principal = Depends(require_scope("team:manage"))) -> dict[str, Any]:
    if credential_id == principal.credential_id:
        raise HTTPException(status_code=409, detail="You can't withdraw the key you're signed in with. Ask another administrator.")
    with tenant_connection(principal.tenant_id) as conn:
        if not conn.execute("SELECT team_revoke_key(%s, %s)", (credential_id, principal.credential_id)).fetchone()[0]:
            raise HTTPException(status_code=404, detail="We couldn't find an active key with that number.")
        log_access(conn, principal, "team_key_withdrawn", "credential", str(credential_id))
    return {"credential_id": credential_id, "state": "withdrawn"}

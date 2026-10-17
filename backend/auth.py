"""Per-tenant credentials, roles, object-level checks and the access log shared by both APIs.

Every request is authenticated to a Principal (tenant + role). Data access then goes through
`tenant_connection`, which pins the session to that tenant so Postgres row-level security applies
(when connected as app_role), and `ensure_decision_visible`, which re-checks ownership in the query itself.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Callable

import psycopg
from fastapi import Header, HTTPException

from data.load_policies import get_db_url

logger = logging.getLogger(__name__)

LEGACY_TENANT = "demo"

# What each role may do. Unknown roles get nothing.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "lender_ingest": frozenset({"ingest:write", "decide:write", "mappings:read", "mappings:write"}),
    "lender_analyst": frozenset(
        {"decisions:read", "reconstruct", "overrides:write", "mappings:read", "mappings:write", "mappings:approve"}
    ),
    "auditor": frozenset({"decisions:read", "reconstruct", "audit:read", "mappings:read"}),
    "admin": frozenset(
        {
            "ingest:write", "decide:write", "decisions:read", "reconstruct", "overrides:write", "audit:read",
            "mappings:read", "mappings:write", "mappings:approve",
        }
    ),
}


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str
    credential_id: int | None
    label: str = ""

    @property
    def scopes(self) -> frozenset[str]:
        return ROLE_SCOPES.get(self.role, frozenset())

    @property
    def rate_key(self) -> str:
        return f"{self.tenant_id}:{self.credential_id if self.credential_id is not None else 'legacy'}"


def generate_api_key() -> str:
    """A new random key. Shown once; only its hash is stored."""
    return "dtk_" + secrets.token_urlsafe(32)


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def api_db_url() -> str:
    """The API connects as app_role (APP_DB_URL) so row-level security applies; falls back to DB_URL."""
    app_url = os.environ.get("APP_DB_URL", "").strip()
    if app_url:
        return app_url
    if os.environ.get("ENV", "dev").lower() != "dev":
        logger.warning("APP_DB_URL is not set: connecting as the owner role bypasses row-level security.")
    return get_db_url(os.environ.get("DB_URL"))


def tenant_connection(tenant_id: str) -> psycopg.Connection[Any]:
    """Open a connection pinned to `tenant_id` (session setting read by the row-level security policies)."""
    conn = psycopg.connect(api_db_url(), connect_timeout=5)
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant_id,))
    conn.commit()
    return conn


def _lookup_credential(key: str) -> Principal | None:
    # tenants/api_credentials carry no row-level security (the tenant is not known yet); app_role can only read them.
    with psycopg.connect(api_db_url(), connect_timeout=5) as conn:
        row = conn.execute(
            """
            SELECT c.credential_id, c.tenant_id, c.role, c.label
            FROM api_credentials c JOIN tenants t ON t.tenant_id = c.tenant_id
            WHERE c.key_sha256 = %s AND c.revoked_at IS NULL AND t.status = 'active'
            """,
            (hash_api_key(key),),
        ).fetchone()
    if row is None:
        return None
    return Principal(tenant_id=str(row[1]), role=str(row[2]), credential_id=int(row[0]), label=str(row[3]))


def authenticate(key: str | None) -> Principal:
    """Resolve an X-API-Key to a Principal or raise 401. DT_API_KEY, if set, is an admin key for the demo tenant."""
    if not key:
        raise HTTPException(status_code=401, detail="Sign-in is needed to see this.")
    legacy = os.environ.get("DT_API_KEY", "").strip()
    if legacy and hmac.compare_digest(key, legacy):
        return Principal(tenant_id=LEGACY_TENANT, role="admin", credential_id=None, label="DT_API_KEY")
    principal = _lookup_credential(key)
    if principal is None:
        raise HTTPException(status_code=401, detail="Sign-in is needed to see this.")
    return principal


def require_scope(scope: str) -> Callable[..., Principal]:
    """FastAPI dependency: authenticate, then require `scope` (403 if the role lacks it)."""

    def dependency(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> Principal:
        principal = authenticate(x_api_key)
        if scope not in principal.scopes:
            raise HTTPException(status_code=403, detail="This sign-in isn't allowed to do that.")
        return principal

    return dependency


def ensure_decision_visible(conn: psycopg.Connection[Any], principal: Principal, decision_id: uuid.UUID) -> None:
    """404 unless the decision belongs to the principal's tenant (same answer as 'does not exist')."""
    row = conn.execute(
        "SELECT 1 FROM decisions WHERE decision_id = %s AND tenant_id = %s",
        (decision_id, principal.tenant_id),
    ).fetchone()
    if row is None:
        log_access(conn, principal, "read", "decision", str(decision_id), "not_found")
        conn.commit()
        raise HTTPException(status_code=404, detail="We couldn't find that decision.")


def log_access(
    conn: psycopg.Connection[Any],
    principal: Principal,
    action: str,
    object_type: str,
    object_id: str,
    outcome: str = "allowed",
) -> None:
    """Append one row to the access log (insert-only for app_role)."""
    conn.execute(
        """
        INSERT INTO access_log (tenant_id, credential_id, role, action, object_type, object_id, outcome)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (principal.tenant_id, principal.credential_id, principal.role, action, object_type, object_id, outcome),
    )

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
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

import psycopg
from fastapi import Header, HTTPException, Request

from data.load_policies import get_db_url

logger = logging.getLogger(__name__)

LEGACY_TENANT = "demo"

# What each role may do. Unknown roles get nothing.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "lender_ingest": frozenset(
        {"ingest:write", "decide:write", "mappings:read", "mappings:write", "quality:read"}
    ),
    "lender_analyst": frozenset(
        {
            "decisions:read", "reconstruct", "overrides:write", "mappings:read", "mappings:write", "mappings:approve",
            "quality:read", "quality:write", "quality:resolve", "claims:issue", "signals:read", "signals:write",
            "signals:resolve",
        }
    ),
    "auditor": frozenset({"decisions:read", "reconstruct", "audit:read", "mappings:read", "quality:read", "signals:read"}),
    "admin": frozenset(
        {
            "ingest:write", "decide:write", "decisions:read", "reconstruct", "overrides:write", "audit:read",
            "mappings:read", "mappings:write", "mappings:approve", "quality:read", "quality:write", "quality:resolve",
            "claims:issue", "signals:read", "signals:write", "signals:resolve",
        }
    ),
    # A customer opening their own decision from a link: one decision, nothing else.
    "consumer": frozenset({"decisions:read", "reconstruct", "review:request"}),
}

CUSTOMER_ROLE = "consumer"
SIGN_IN_MESSAGE = "Sign-in is needed to see this."


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    role: str
    credential_id: int | None
    label: str = ""
    decision_id: str | None = None  # set for customer links: the one decision this sign-in may open
    claim_id: int | None = None
    expires_at: datetime | None = None

    @property
    def is_customer(self) -> bool:
        return self.role == CUSTOMER_ROLE

    @property
    def scopes(self) -> frozenset[str]:
        return ROLE_SCOPES.get(self.role, frozenset())

    @property
    def rate_key(self) -> str:
        if self.claim_id is not None:
            return f"{self.tenant_id}:claim{self.claim_id}"
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


class FailureTracker:
    """Counts bad sign-in attempts per client in a short window and refuses further tries (one process only)."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit, self.window = limit, window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _trim(self, client: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(client, deque())
        while hits and now - hits[0] > self.window:
            hits.popleft()
        return hits

    def check(self, client: str) -> None:
        if self.limit <= 0:
            return
        with self._lock:
            hits = self._trim(client, time.monotonic())
            if len(hits) >= self.limit:
                retry = max(1, int(self.window - (time.monotonic() - hits[0])))
                raise HTTPException(
                    status_code=429, detail="Too many sign-in attempts. Please wait a minute and try again.",
                    headers={"Retry-After": str(retry)},
                )

    def record(self, client: str) -> None:
        with self._lock:
            self._trim(client, time.monotonic()).append(time.monotonic())

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


failures = FailureTracker(int(os.environ.get("DT_AUTH_FAIL_LIMIT", "20")))


def client_id(request: Request | None) -> str:
    """Who is asking, for rate limits: the connection address, or the proxy-supplied one if DT_TRUST_PROXY=1."""
    if request is None:
        return "unknown"
    if os.environ.get("DT_TRUST_PROXY", "").strip() == "1":
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd[:64]
    return request.client.host if request.client else "unknown"


def _security_event(kind: str, client: str, detail: str = "") -> None:
    """Best-effort record of a failed sign-in; never blocks the response."""
    try:
        with psycopg.connect(api_db_url(), connect_timeout=3) as conn:
            conn.execute("INSERT INTO security_events (kind, client, detail) VALUES (%s, %s, %s)", (kind, client[:64], detail[:200]))
    except Exception:
        logger.warning("Could not record security event %s", kind)


def _lookup_claim(token: str) -> Principal | None:
    with psycopg.connect(api_db_url(), connect_timeout=5) as conn:
        row = conn.execute("SELECT claim_id, tenant_id, decision_id, expires_at, revoked FROM lookup_claim(%s)", (hash_api_key(token),)).fetchone()
    if row is None:
        return None
    claim_id, tenant_id, decision_id, expires_at, revoked = row
    if revoked or expires_at <= datetime.now(timezone.utc):
        return Principal(tenant_id=str(tenant_id), role="expired", credential_id=None, claim_id=int(claim_id))
    return Principal(tenant_id=str(tenant_id), role=CUSTOMER_ROLE, credential_id=None, label="customer link",
                     decision_id=str(decision_id), claim_id=int(claim_id), expires_at=expires_at)


def authenticate(key: str | None, claim_token: str | None = None, client: str = "unknown") -> Principal:
    """Resolve an X-API-Key (staff) or X-Claim-Token (a customer's link) to a Principal, or raise 401/429.

    DT_API_KEY, if set, is an admin key for the demo tenant. Bad attempts are counted per client.
    """
    failures.check(client)
    if key:
        legacy = os.environ.get("DT_API_KEY", "").strip()
        if legacy and hmac.compare_digest(key.encode("utf-8", "replace"), legacy.encode("utf-8", "replace")):
            return Principal(tenant_id=LEGACY_TENANT, role="admin", credential_id=None, label="DT_API_KEY")
        principal = _lookup_credential(key)
        if principal is not None:
            return principal
        failures.record(client)
        _security_event("bad_key", client)
    elif claim_token:
        principal = _lookup_claim(claim_token)
        if principal is not None and principal.is_customer:
            return principal
        failures.record(client)
        if principal is not None:
            _security_event("expired_claim", client, f"claim {principal.claim_id}")
            raise HTTPException(status_code=401, detail="This link has expired or was withdrawn. Please ask your lender for a new one.")
        _security_event("bad_claim", client)
    raise HTTPException(status_code=401, detail=SIGN_IN_MESSAGE)


def require_scope(scope: str | None) -> Callable[..., Principal]:
    """FastAPI dependency: authenticate (staff key or customer link), then require `scope` (403 if the role lacks it).

    `scope=None` accepts any signed-in principal (used for "who am I").
    """

    def dependency(
        request: Request,
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_claim_token: str | None = Header(default=None, alias="X-Claim-Token"),
    ) -> Principal:
        principal = authenticate(x_api_key, x_claim_token, client_id(request))
        if scope is not None and scope not in principal.scopes:
            raise HTTPException(status_code=403, detail="This sign-in isn't allowed to do that.")
        return principal

    return dependency


def ensure_decision_visible(conn: psycopg.Connection[Any], principal: Principal, decision_id: uuid.UUID) -> None:
    """404 unless the decision belongs to the principal's tenant (same answer as 'does not exist')."""
    row = None
    if principal.decision_id is None or principal.decision_id == str(decision_id):  # a customer link opens one decision
        row = conn.execute(
            "SELECT 1 FROM decisions WHERE decision_id = %s AND tenant_id = %s",
            (decision_id, principal.tenant_id),
        ).fetchone()
    if row is None:
        log_access(conn, principal, "read", "decision", str(decision_id), "not_found")
        try:
            from backend.signals import flag_enumeration  # deferred: signals pulls in the quality and mapping modules

            flag_enumeration(conn, principal.tenant_id, principal.credential_id, principal.claim_id, principal.role)
        except Exception:
            logger.exception("Could not check for guessing")
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
        INSERT INTO access_log (tenant_id, credential_id, claim_id, role, action, object_type, object_id, outcome)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (principal.tenant_id, principal.credential_id, principal.claim_id, principal.role, action, object_type,
         object_id, outcome),
    )

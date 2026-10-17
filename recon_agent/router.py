"""Single definition of POST /reconstruct/{decision_id}, mounted by both the Decision API and the agent app."""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Depends, HTTPException

from backend.contract import ReconstructionReport
from backend.auth import Principal, ensure_decision_visible, log_access, require_scope, tenant_connection
from backend.web import SlidingWindowLimiter, TTLCache
from data.load_policies import get_db_url
from recon_agent.service import reconstruct_decision

router = APIRouter()

_limiter = SlidingWindowLimiter(int(os.environ.get("RECON_RATE_LIMIT_PER_MINUTE", "20")))
_cache = TTLCache(float(os.environ.get("RECON_CACHE_TTL_SECONDS", "60")))


def evict_decision(decision_id: str) -> None:
    """Forget cached reports for one decision (after an override). Rate limits are untouched."""
    _cache.evict_suffix(f"|{decision_id}")


def reset_state() -> None:
    """Clear the cache and rate-limit counters (used by tests)."""
    _cache.clear()
    _limiter._hits.clear()


@router.post("/reconstruct/{decision_id}", response_model=ReconstructionReport)
def post_reconstruct(
    decision_id: str,
    principal: Principal = Depends(require_scope("reconstruct")),
) -> ReconstructionReport:
    """Reconstruct a cited explanation for `decision_id` from preserved decision-time evidence.

    Requires a credential whose tenant owns the decision (404 otherwise, same as a missing one), is rate limited
    per credential, and briefly caches successful reports so repeated clicks do not repeat the model calls.
    """
    try:
        dec_uuid = uuid.UUID(str(decision_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid decision_id UUID: {decision_id}") from exc

    _limiter.check(principal.rate_key)
    with tenant_connection(principal.tenant_id) as conn:
        ensure_decision_visible(conn, principal, dec_uuid)
        log_access(conn, principal, "reconstruct", "decision", str(dec_uuid))

    db_url = get_db_url(os.environ.get("DB_URL"))
    cache_key = f"{db_url}|{os.environ.get('LLM_BACKEND', '')}|{principal.tenant_id}|{decision_id}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        report = reconstruct_decision(decision_id, db_url=db_url)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if report.status == "ok":
        _cache.put(cache_key, report)
    return report

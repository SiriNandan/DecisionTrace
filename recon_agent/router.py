"""Single definition of POST /reconstruct/{decision_id}, mounted by both the Decision API and the agent app."""

from __future__ import annotations

import hmac
import os
import uuid

from fastapi import APIRouter, Header, HTTPException, Request

from backend.contract import ReconstructionReport
from backend.web import SlidingWindowLimiter, TTLCache
from data.load_policies import get_db_url
from recon_agent.service import reconstruct_decision

router = APIRouter()

_limiter = SlidingWindowLimiter(int(os.environ.get("RECON_RATE_LIMIT_PER_MINUTE", "20")))
_cache = TTLCache(float(os.environ.get("RECON_CACHE_TTL_SECONDS", "60")))


def reset_state() -> None:
    """Clear the cache and rate-limit counters (used by tests)."""
    _cache.clear()
    _limiter._hits.clear()


@router.post("/reconstruct/{decision_id}", response_model=ReconstructionReport)
def post_reconstruct(
    decision_id: str,
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> ReconstructionReport:
    """Reconstruct a cited explanation for `decision_id` from preserved decision-time evidence.

    Requires X-API-Key when DT_API_KEY is set (each call can spend LLM quota), is rate limited per client,
    and briefly caches successful reports so repeated clicks do not repeat the model calls.
    """
    expected = os.environ.get("DT_API_KEY", "").strip()
    if expected and (x_api_key is None or not hmac.compare_digest(x_api_key, expected)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key.")

    try:
        uuid.UUID(str(decision_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid decision_id UUID: {decision_id}") from exc

    db_url = get_db_url(os.environ.get("DB_URL"))
    cache_key = f"{db_url}|{os.environ.get('LLM_BACKEND', '')}|{decision_id}"
    cached = _cache.get(cache_key)
    if cached is not None:
        return cached

    _limiter.check(request.client.host if request.client else "unknown")
    try:
        report = reconstruct_decision(decision_id, db_url=db_url)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if report.status == "ok":
        _cache.put(cache_key, report)
    return report

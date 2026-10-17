"""Tests for shared CORS/security headers, reconstruct auth, rate limiting and caching."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH

from backend.web import SlidingWindowLimiter, TTLCache, cors_origins
from decision_api.app import create_app
from recon_agent import router as recon_router
from recon_agent.app import app as recon_app


@pytest.fixture(autouse=True)
def _reset_router_state():
    recon_router.reset_state()
    yield
    recon_router.reset_state()


def test_security_headers_and_restricted_cors() -> None:
    client = TestClient(create_app(), headers=AUTH)
    resp = client.get("/decisions", params={"limit": 1}, headers={"Origin": "http://localhost:5173"})
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    pre = client.options(
        "/decide",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "DELETE"},
    )
    assert "DELETE" not in pre.headers.get("access-control-allow-methods", "")


def test_cors_origins_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://clearly.example, https://b.example")
    assert cors_origins() == ["https://clearly.example", "https://b.example"]


def test_reconstruct_requires_key_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_API_KEY", "secret")
    client = TestClient(recon_app)
    url = f"/reconstruct/{uuid.uuid4()}"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.post(url, headers={"X-API-Key": "secret"}).status_code == 404


def test_reconstruct_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recon_router, "_limiter", SlidingWindowLimiter(2))
    client = TestClient(recon_app, headers=AUTH)
    url = f"/reconstruct/{uuid.uuid4()}"
    assert client.post(url).status_code == 404
    assert client.post(url).status_code == 404
    limited = client.post(url)
    assert limited.status_code == 429
    assert "retry-after" in limited.headers


def test_reconstruct_caches_ok_reports_and_override_invalidates(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(create_app(), headers=AUTH)
    decision_id = client.get("/decisions", params={"limit": 1}).json()[0]["decision_id"]
    calls: list[str] = []
    real = recon_router.reconstruct_decision

    def counting(decision_id: str, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(decision_id)
        return real(decision_id, **kwargs)

    monkeypatch.setattr(recon_router, "reconstruct_decision", counting)
    monkeypatch.setenv("LLM_BACKEND", "fake")
    first = client.post(f"/reconstruct/{decision_id}")
    second = client.post(f"/reconstruct/{decision_id}")
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "ok"
    assert len(calls) == 1

    override = client.post(
        "/overrides",
        json={
            "decision_id": decision_id,
            "new_outcome": "REFER",
            "reason": "Manual review requested by a senior underwriter.",
            "actor_role": "senior_underwriter",
        },
    )
    assert override.status_code == 200
    client.post(f"/reconstruct/{decision_id}")
    assert len(calls) == 2


def test_ttl_cache_expires() -> None:
    now = [0.0]
    cache = TTLCache(10, clock=lambda: now[0])
    cache.put("k", 1)
    assert cache.get("k") == 1
    now[0] = 11
    assert cache.get("k") is None

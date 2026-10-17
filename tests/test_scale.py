"""Paging through long lists, and reusing database connections without ever mixing up two lenders."""

from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.auth import close_pools, plain_connection, tenant_connection
from backend.sandbox import provision_tenants, send_decisions
from decision_api.app import create_app
from recon_agent import router as recon_router


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def lender() -> dict[str, Any]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 1, prefix=f"scl-{uuid.uuid4().hex[:5]}")[0]


# ---------- paging ----------

def pages(client: TestClient, key: str, size: int, extra: str = "") -> list[list[dict[str, Any]]]:
    out, cursor = [], None
    while True:
        r = client.get(f"/decisions?limit={size}{extra}" + (f"&cursor={cursor}" if cursor else ""), headers=h(key))
        assert r.status_code == 200, r.text
        out.append(r.json())
        cursor = r.headers.get("X-Next-Cursor")
        if not cursor:
            return out
        assert len(out) < 50, "paging never ended"


def test_every_decision_appears_exactly_once_across_pages_in_order(client: TestClient) -> None:
    t = lender()
    assert send_decisions(client, t, 23, run="pg")["created"] == 23
    key = t["keys"]["lender_analyst"]
    got = pages(client, key, 5)
    assert [len(p) for p in got] == [5, 5, 5, 5, 3]
    flat = [d for p in got for d in p]
    assert len({d["decision_id"] for d in flat}) == 23
    assert [d["decided_at"] for d in flat] == sorted((d["decided_at"] for d in flat), reverse=True)
    assert [d["decision_id"] for d in flat] == [d["decision_id"] for d in client.get("/decisions?limit=1000", headers=h(key)).json()]
    exact = pages(client, key, 23)
    assert len(exact) == 1 and len(exact[0]) == 23  # a full last page doesn't promise another one
    assert client.get("/decisions?limit=100", headers=h(key)).headers.get("X-Next-Cursor") is None


def test_paging_survives_new_decisions_arriving_while_reading(client: TestClient) -> None:
    t = lender()
    send_decisions(client, t, 12, run="a")
    key = t["keys"]["lender_analyst"]
    first = client.get("/decisions?limit=5", headers=h(key))
    seen = {d["decision_id"] for d in first.json()}
    send_decisions(client, t, 4, run="b")  # same sandbox sequence continues; these are newer or equal to older ones
    rest = []
    cursor = first.headers["X-Next-Cursor"]
    while cursor:
        r = client.get(f"/decisions?limit=5&cursor={cursor}", headers=h(key))
        rest += [d["decision_id"] for d in r.json()]
        cursor = r.headers.get("X-Next-Cursor")
    assert not seen & set(rest) and len(rest) == len(set(rest))  # nothing repeated


def test_the_applicant_filter_now_applies_before_the_page_is_cut(client: TestClient) -> None:
    demo = h(os.environ["DT_API_KEY"])
    everyone = client.get("/decisions?limit=1000", headers=demo).json()
    target = everyone[-1]["applicant_id"]  # the oldest one: used to be lost when the page was cut first
    small = client.get(f"/decisions?limit=1&applicant_id={target}", headers=demo).json()
    assert [d["applicant_id"] for d in small] == [target]


def test_a_bad_page_marker_is_a_plain_error_not_a_crash(client: TestClient) -> None:
    key = lender()["keys"]["lender_analyst"]
    for bad in ("not-a-cursor", "e30", "eyJ0IjoieCIsImkiOiJ5In0"):
        r = client.get(f"/decisions?cursor={bad}", headers=h(key))
        assert r.status_code == 422 and "page marker" in r.json()["detail"]


def test_a_customer_pages_through_exactly_their_own_decision(client: TestClient) -> None:
    t = lender()
    send_decisions(client, t, 3, run="c")
    dec = client.get("/decisions", headers=h(t["keys"]["lender_analyst"])).json()[0]["decision_id"]
    tok = client.post(f"/v1/decisions/{dec}/claim-links", json={}, headers=h(t["keys"]["lender_analyst"])).json()["token"]
    r = client.get("/decisions?limit=1", headers={"X-Claim-Token": tok})
    assert [d["decision_id"] for d in r.json()] == [dec] and "X-Next-Cursor" not in r.headers


def test_the_browser_may_read_the_page_marker() -> None:
    r = TestClient(create_app()).get("/decisions?limit=1", headers={**h(os.environ["DT_API_KEY"]), "Origin": "http://localhost:5173"})
    assert "x-next-cursor" in r.headers["access-control-expose-headers"].lower()


# ---------- reusing connections ----------

@pytest.fixture()
def pooled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DT_DB_POOL_SIZE", "3")
    close_pools()
    yield
    close_pools()


def test_reused_connections_never_carry_one_lenders_identity_into_the_next_request(pooled: None) -> None:
    a, b = lender(), lender()
    seen: list[Any] = []
    for _ in range(12):  # far more checkouts than the pool has connections
        for t in (a, b):
            with tenant_connection(t["tenant_id"]) as conn:
                seen.append(conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0] == t["tenant_id"])
        with plain_connection() as conn:  # a lookup made before any lender is known must see no lender at all
            assert conn.execute("SELECT NULLIF(current_setting('app.tenant_id', true), '')").fetchone()[0] is None
    assert all(seen)
    assert len(auth._pools) == 1 and next(iter(auth._pools.values())).max_size == 3


def test_a_connection_that_failed_halfway_is_clean_when_it_comes_back(pooled: None) -> None:
    t = lender()
    with pytest.raises(RuntimeError):
        with tenant_connection(t["tenant_id"]) as conn:
            conn.execute("CREATE TEMP TABLE leftover (x int)")
            raise RuntimeError("something went wrong in the middle of a request")
    for _ in range(6):
        with plain_connection() as conn:
            assert conn.execute("SELECT NULLIF(current_setting('app.tenant_id', true), '')").fetchone()[0] is None
            assert conn.execute("SELECT to_regclass('pg_temp.leftover')").fetchone()[0] is None
    with pytest.raises(psycopg.errors.DivisionByZero):  # and a failed statement leaves nothing aborted behind
        with tenant_connection(t["tenant_id"]) as conn:
            conn.execute("SELECT 1/0")
    with tenant_connection(t["tenant_id"]) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1


def test_many_requests_at_once_from_two_lenders_never_see_each_others_decisions(pooled: None, client: TestClient) -> None:
    a, b = lender(), lender()
    send_decisions(client, a, 6, run="p")
    send_decisions(client, b, 6, run="p")
    ids = {}
    for name, t in (("a", a), ("b", b)):
        ids[name] = {d["decision_id"] for d in client.get("/decisions?limit=100", headers=h(t["keys"]["lender_analyst"])).json()}
    assert len(ids["a"]) == len(ids["b"]) == 6 and ids["a"].isdisjoint(ids["b"])

    def ask(i: int) -> bool:
        who, other = ("a", "b") if i % 2 == 0 else ("b", "a")
        t = a if who == "a" else b
        mine = TestClient(create_app()).get("/decisions?limit=100", headers=h(t["keys"]["lender_analyst"])).json()
        got = {d["decision_id"] for d in mine}
        foreign = TestClient(create_app()).get(f"/decisions/{next(iter(ids[other]))}", headers=h(t["keys"]["lender_analyst"]))
        return got == ids[who] and foreign.status_code == 404

    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(ask, range(48)))
    assert all(results)


def test_the_default_is_still_a_fresh_connection_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DT_DB_POOL_SIZE", raising=False)
    close_pools()
    t = lender()
    with tenant_connection(t["tenant_id"]) as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    assert auth._pools == {}


# ---------- things the independent review found ----------

def test_paging_never_skips_decisions_that_share_a_time_and_ignores_a_cursor_from_elsewhere(client: TestClient) -> None:
    t = lender()
    key = t["keys"]["lender_analyst"]
    when = "2026-09-01T10:00:00+00:00"
    ids = set()
    for i in range(12):
        env = {"schema_version": 1, "external_id": f"TIE-{i}", "subject_ref": f"s-{i}", "decided_at": when, "outcome": "DECLINE", "score": 0.4,
               "model_version": "m", "policy_version": "p", "reasons": [],
               "evidence": [{"evidence_type": "application", "evidence_ref": f"application:TIE-{i}", "source_id": "x", "source_timestamp": when,
                             "payload": {"income_annual": 30000 + i, "requested_amount": 1000}}]}
        r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": f"TIE-{i}"})
        ids.add(r.json()["decision_id"])
    got = [d["decision_id"] for p in pages(client, key, 5) for d in p]
    assert len(got) == 12 and set(got) == ids                      # every one, once, though they all have the same time
    assert got == sorted(got)                                       # ties are broken by id, the same way every time

    other = lender()
    foreign = client.get("/decisions?limit=1", headers=h(other["keys"]["lender_analyst"]))
    assert foreign.json() == []                                     # a cursor only ever marks a position in the caller's own list
    send_decisions(client, other, 2, run="x")
    cursor = client.get("/decisions?limit=1", headers=h(other["keys"]["lender_analyst"])).headers["X-Next-Cursor"]
    leaked = client.get(f"/decisions?cursor={cursor}", headers=h(key)).json()
    assert {d["decision_id"] for d in leaked} <= ids


def test_pool_exhaustion_is_reported_as_busy_not_as_a_missing_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from psycopg_pool import PoolTimeout

    import backend.auth as auth_module

    def exhausted(*a: Any, **k: Any):
        raise PoolTimeout("couldn't get a connection after 10 sec")

    monkeypatch.setattr(auth_module, "_open", exhausted)
    r = TestClient(create_app(), raise_server_exceptions=False).get("/decisions", headers=h(os.environ["DT_API_KEY"]))
    assert r.status_code == 503 and r.json()["code"] == "busy" and r.headers["retry-after"] == "2"
    assert "can't reach its database" not in r.text

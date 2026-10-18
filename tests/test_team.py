"""A lender's administrator manages its own team: who has a key, what it can do, and which portfolios it can see."""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.sandbox import provision_tenants
from decision_api.app import create_app
from recon_agent import router as recon_router


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


@pytest.fixture()
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 2, prefix=f"team-{uuid.uuid4().hex[:5]}")


def envelope(ext: str, portfolio: str | None = None) -> dict[str, Any]:
    when = "2026-09-01T10:00:00+00:00"
    env = {
        "schema_version": 1, "external_id": ext, "subject_ref": f"s-{ext}", "decided_at": when, "outcome": "DECLINE", "score": 0.4,
        "model_version": "m", "policy_version": "p", "reasons": [{"code": "R", "text": "Debt is high compared with income."}],
        "evidence": [{"evidence_type": "application", "evidence_ref": f"application:{ext}", "source_id": "los", "source_timestamp": when,
                      "payload": {"income_annual": 30000, "requested_amount": 5000}}],
    }
    if portfolio:
        env["portfolio"] = portfolio
    return env


def ingest(client: TestClient, t: dict[str, Any], ext: str, portfolio: str | None = None) -> str:
    r = client.post("/v1/decisions", json=envelope(ext, portfolio), headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": ext})
    assert r.status_code in (200, 201), r.text
    return r.json()["decision_id"]


def add(client: TestClient, t: dict[str, Any], role: str, label: str = "Someone new", **extra: Any) -> dict[str, Any]:
    r = client.post("/v1/team/keys", json={"label": label, "email": "person@example.com", "role": role, **extra}, headers=h(t["keys"]["lender_admin"]))
    assert r.status_code == 201, r.text
    return r.json()


def test_an_administrator_adds_a_person_who_can_then_sign_in_and_withdraws_them_again(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    made = add(client, t, "lender_analyst", "Priya, Leeds branch")
    me = client.get("/v1/me", headers=h(made["key"])).json()
    assert me["role"] == "lender_analyst" and me["tenant_id"] == t["tenant_id"]

    team = client.get("/v1/team", headers=h(t["keys"]["lender_admin"])).json()
    mine = next(m for m in team["members"] if m["credential_id"] == made["credential_id"])
    assert mine["label"] == "Priya, Leeds branch" and mine["state"] == "active" and mine["email"] == "person@example.com"
    assert made["email_status"] == "not_configured"  # no SMTP_HOST in the test environment: nothing is sent, the key is shown instead
    assert "key" not in mine and made["key"] not in str(team)  # shown once, never listed again
    assert {r["role"] for r in team["roles"]} == {"lender_admin", "lender_ingest", "lender_analyst", "lender_support", "auditor"}

    withdrawn = client.post(f"/v1/team/keys/{made['credential_id']}/revoke", headers=h(t["keys"]["lender_admin"]))
    assert withdrawn.json()["state"] == "withdrawn"
    assert client.get("/v1/me", headers=h(made["key"])).status_code == 401
    after = client.get("/v1/team", headers=h(t["keys"]["lender_admin"])).json()
    assert next(m for m in after["members"] if m["credential_id"] == made["credential_id"])["state"] == "withdrawn"
    assert client.post(f"/v1/team/keys/{made['credential_id']}/revoke", headers=h(t["keys"]["lender_admin"])).status_code == 404


def test_the_administrator_cannot_withdraw_their_own_key_or_touch_another_lender(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    a, b = lenders
    me = client.get("/v1/me", headers=h(a["keys"]["lender_admin"])).json()
    assert me["role"] == "lender_admin"
    own = next(m for m in client.get("/v1/team", headers=h(a["keys"]["lender_admin"])).json()["members"] if m["role"] == "lender_admin")
    assert client.post(f"/v1/team/keys/{own['credential_id']}/revoke", headers=h(a["keys"]["lender_admin"])).status_code == 409

    theirs = next(m for m in client.get("/v1/team", headers=h(b["keys"]["lender_admin"])).json()["members"] if m["role"] == "auditor")
    assert client.post(f"/v1/team/keys/{theirs['credential_id']}/revoke", headers=h(a["keys"]["lender_admin"])).status_code == 404
    assert client.get("/v1/me", headers=h(b["keys"]["auditor"])).status_code == 200  # untouched
    assert theirs["credential_id"] not in {m["credential_id"] for m in client.get("/v1/team", headers=h(a["keys"]["lender_admin"])).json()["members"]}


def test_only_the_administrator_manages_the_team_and_every_change_is_logged(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    for role in ("lender_ingest", "lender_analyst", "lender_support", "auditor"):
        assert client.get("/v1/team", headers=h(t["keys"]["lender_analyst"] if role == "lender_analyst" else t["keys"][role])).status_code == 403
        assert client.post("/v1/team/keys", json={"label": "Sneaky one", "email": "s@example.com", "role": "lender_admin"}, headers=h(t["keys"][role])).status_code == 403
    assert client.get("/v1/team").status_code == 401
    bad = client.post("/v1/team/keys", json={"label": "Someone", "email": "s@example.com", "role": "admin"}, headers=h(t["keys"]["lender_admin"]))
    assert bad.status_code == 422  # the demo superuser role can't be handed out
    assert client.post("/v1/team/keys", json={"label": "a", "email": "s@example.com", "role": "auditor"}, headers=h(t["keys"]["lender_admin"])).status_code == 422

    made = add(client, t, "auditor")
    client.post(f"/v1/team/keys/{made['credential_id']}/revoke", headers=h(t["keys"]["lender_admin"]))
    log = client.get("/v1/access-log?limit=20", headers=h(t["keys"]["lender_admin"])).json()
    assert {"team_key_issued", "team_key_withdrawn"} <= {r["action"] for r in log}


def test_one_role_loads_data_and_the_others_read_or_review(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    dec = ingest(client, t, "ROLE-1")
    for role in ("lender_admin", "lender_analyst", "lender_support", "auditor"):
        r = client.post("/v1/decisions", json=envelope(f"NO-{role}"), headers={**h(t["keys"][role]), "Idempotency-Key": f"NO-{role}"})
        assert r.status_code == 403, role
        imp = client.post("/v1/imports", json={"mapping_id": 1, "format": "json", "rows": [], "dry_run": False}, headers=h(t["keys"][role]))
        assert imp.status_code in (403, 422), role  # never accepted
    assert client.get("/decisions", headers=h(t["keys"]["lender_ingest"])).status_code == 403  # the data officer can't read decisions back

    assert client.get("/v1/me", headers=h(t["keys"]["lender_support"])).json()["scopes"] == ["claims:issue", "consent:write", "decisions:read", "reconstruct"]
    assert client.get(f"/decisions/{dec}", headers=h(t["keys"]["lender_support"])).status_code == 200
    link = client.post(f"/v1/decisions/{dec}/claim-links", json={"days": 7, "label": "call"}, headers=h(t["keys"]["lender_support"]))
    assert link.status_code == 200
    override = {"decision_id": dec, "new_outcome": "APPROVE", "reason": "Verified supplemental savings statement.", "actor_role": "support"}
    assert client.post("/overrides", json=override, headers=h(t["keys"]["lender_support"])).status_code == 403
    assert client.get("/v1/signals", headers=h(t["keys"]["lender_support"])).status_code == 403
    assert client.get("/v1/access-log", headers=h(t["keys"]["lender_support"])).status_code == 403

    assert client.get("/decisions", headers=h(t["keys"]["lender_admin"])).status_code == 403  # the administrator manages people, not customers' data
    assert client.get("/v1/access-log", headers=h(t["keys"]["lender_admin"])).status_code == 200


def test_a_key_can_expire(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    made = add(client, t, "auditor", expires_days=7)
    assert made["expires_at"] and client.get("/v1/me", headers=h(made["key"])).status_code == 200
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        conn.execute("UPDATE api_credentials SET expires_at = now() - interval '1 minute' WHERE credential_id = %s", (made["credential_id"],))
    assert client.get("/v1/me", headers=h(made["key"])).status_code == 401
    state = next(m for m in client.get("/v1/team", headers=h(t["keys"]["lender_admin"])).json()["members"] if m["credential_id"] == made["credential_id"])
    assert state["state"] == "expired"


def test_a_key_limited_to_a_portfolio_sees_only_that_portfolio(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    leeds, york = ingest(client, t, "PF-1", "Leeds"), ingest(client, t, "PF-2", "York")
    plain = ingest(client, t, "PF-3")
    scoped = add(client, t, "lender_analyst", "Priya, Leeds only", portfolios=["Leeds"])["key"]

    seen = {d["decision_id"] for d in client.get("/decisions", headers=h(scoped)).json()}
    assert seen == {leeds}
    everyone = {d["decision_id"] for d in client.get("/decisions", headers=h(t["keys"]["lender_analyst"])).json()}
    assert {leeds, york, plain} <= everyone
    assert client.get(f"/decisions/{leeds}", headers=h(scoped)).status_code == 200
    assert client.post(f"/reconstruct/{leeds}", headers=h(scoped)).status_code == 200  # so the 404s below mean "hidden", not "wrong address"
    for other in (york, plain):  # another portfolio, or none, looks exactly like a decision that doesn't exist
        assert client.get(f"/decisions/{other}", headers=h(scoped)).status_code == 404
        assert client.get(f"/decisions/{other}/verify", headers=h(scoped)).status_code == 404
        assert client.post(f"/reconstruct/{other}", headers=h(scoped)).status_code == 404
        assert client.post(f"/v1/decisions/{other}/claim-links", json={"days": 7, "label": "x"}, headers=h(scoped)).status_code == 404
    for page in ("/v1/signals", "/v1/access-log", "/v1/quality/findings", "/v1/seals"):  # whole-lender pages are not for a limited key
        assert client.get(page, headers=h(scoped)).status_code == 403, page
    assert "signals:read" not in client.get("/v1/me", headers=h(scoped)).json()["scopes"]

    teams = client.get("/v1/team", headers=h(t["keys"]["lender_admin"])).json()
    assert {p["name"] for p in teams["portfolios"]} == {"Leeds", "York"}
    assert next(m for m in teams["members"] if m["label"] == "Priya, Leeds only")["portfolios"] == ["Leeds"]


def test_loading_data_and_running_the_team_cannot_be_limited_to_a_portfolio(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    for role in ("lender_ingest", "lender_admin"):
        r = client.post("/v1/team/keys", json={"label": "Limited one", "email": "s@example.com", "role": role, "portfolios": ["Leeds"]}, headers=h(t["keys"]["lender_admin"]))
        assert r.status_code == 422, role


def test_a_person_can_hold_several_roles_and_gets_what_each_allows(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    r = client.post("/v1/team/keys", json={"label": "Dual role", "email": "d@example.com", "roles": ["lender_analyst", "lender_ingest"]},
                    headers=h(t["keys"]["lender_admin"]))
    assert r.status_code == 201, r.text
    made = r.json()
    assert made["roles"] == ["lender_analyst", "lender_ingest"]
    me = client.get("/v1/me", headers=h(made["key"])).json()
    assert me["roles"] == ["lender_analyst", "lender_ingest"]
    assert {"decisions:read", "ingest:write"} <= set(me["scopes"])  # reads like an analyst and loads like a data officer
    member = next(m for m in client.get("/v1/team", headers=h(t["keys"]["lender_admin"])).json()["members"] if m["credential_id"] == made["credential_id"])
    assert member["roles"] == ["lender_analyst", "lender_ingest"]
    # A role that covers the whole lender can't be combined with a portfolio limit.
    bad = client.post("/v1/team/keys", json={"label": "Limited", "email": "l@example.com", "roles": ["lender_analyst", "lender_ingest"], "portfolios": ["Leeds"]},
                      headers=h(t["keys"]["lender_admin"]))
    assert bad.status_code == 422
    assert client.post("/v1/team/keys", json={"label": "Nobody", "email": "n@example.com", "roles": []}, headers=h(t["keys"]["lender_admin"])).status_code == 422

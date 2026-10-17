"""Customer choices: an append-only ledger, a lender policy, and AI summaries that only happen when allowed."""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.consent import effective
from backend.sandbox import load_fixtures, provision_tenants
from decision_api.app import create_app
from recon_agent import router as recon_router


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def c(token: str) -> dict[str, str]:
    return {"X-Claim-Token": token}


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def lender(policy: str = "ask") -> dict[str, Any]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        t = provision_tenants(conn, 1, prefix=f"con-{uuid.uuid4().hex[:5]}")[0]
        conn.execute("UPDATE tenants SET ai_summary_policy = %s WHERE tenant_id = %s", (policy, t["tenant_id"]))
        conn.commit()
    return t


def decision(client: TestClient, t: dict[str, Any], name: str = "decline") -> tuple[str, str]:
    env = load_fixtures()[name]
    r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": f"{name}-{uuid.uuid4().hex[:6]}"})
    dec = r.json()["decision_id"]
    link = client.post(f"/v1/decisions/{dec}/claim-links", json={}, headers=h(t["keys"]["lender_analyst"])).json()
    return dec, link["token"]


@pytest.mark.parametrize("policy,choice,expected", [
    ("never", None, False), ("never", {"granted": True}, False), ("never", {"granted": False}, False),
    ("ask", None, False), ("ask", {"granted": True}, True), ("ask", {"granted": False}, False),
    ("allow", None, True), ("allow", {"granted": True}, True), ("allow", {"granted": False}, False),
])
def test_who_decides_whether_an_ai_summary_is_written(policy: str, choice: dict | None, expected: bool) -> None:
    assert effective(policy, choice) is expected


def test_a_customer_can_allow_and_withdraw_and_every_change_is_kept(client: TestClient) -> None:
    t = lender("ask")
    dec, tok = decision(client, t)
    start = client.get(f"/v1/decisions/{dec}/consents", headers=c(tok)).json()
    assert start["policy"] == "ask" and start["choice"] is None and start["ai_summary_on"] is False and start["history"] == []

    on = client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": True}, headers=c(tok)).json()
    assert on["ai_summary_on"] is True and on["choice"]["granted"] is True and on["choice"]["given_by"] == "customer"
    off = client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": False}, headers=c(tok)).json()
    assert off["ai_summary_on"] is False and [x["granted"] for x in off["history"]] == [False, True]  # newest first, nothing lost

    with psycopg.connect(os.environ["DB_URL"]) as conn:
        rows = conn.execute("SELECT granted, given_by, claim_id IS NOT NULL, wording_version FROM consents WHERE decision_id = %s ORDER BY consent_id", (dec,)).fetchall()
    assert rows == [(True, "customer", True, "ai-summary-2026-10"), (False, "customer", True, "ai-summary-2026-10")]
    log = client.get("/v1/access-log?limit=50", headers=h(t["keys"]["auditor"])).json()
    assert {"consent_given", "consent_withdrawn"} <= {r["action"] for r in log if r["object_id"] == dec}


def test_the_lenders_policy_sets_the_starting_point_and_never_means_never(client: TestClient) -> None:
    allow = lender("allow")
    dec, tok = decision(client, allow)
    assert client.get(f"/v1/decisions/{dec}/consents", headers=c(tok)).json()["ai_summary_on"] is True  # on until the customer says no
    assert client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": False}, headers=c(tok)).json()["ai_summary_on"] is False

    never = lender("never")
    dec2, tok2 = decision(client, never)
    r = client.post(f"/v1/decisions/{dec2}/consents/ai_summary", json={"granted": True}, headers=c(tok2))
    assert r.status_code == 409 and "doesn't use AI-written summaries" in r.json()["detail"]
    assert client.get(f"/v1/decisions/{dec2}/consents", headers=c(tok2)).json()["ai_summary_on"] is False


def test_who_can_record_a_choice_and_for_whom(client: TestClient) -> None:
    t = lender("ask")
    dec, tok = decision(client, t)
    other, _ = decision(client, t)
    body = {"granted": True}
    assert client.post(f"/v1/decisions/{dec}/consents/ai_summary", json=body, headers=h(t["keys"]["auditor"])).status_code == 403
    assert client.post(f"/v1/decisions/{dec}/consents/ai_summary", json=body, headers=h(t["keys"]["lender_ingest"])).status_code == 403
    assert client.post(f"/v1/decisions/{dec}/consents/ai_summary", json=body).status_code == 401
    assert client.post(f"/v1/decisions/{other}/consents/ai_summary", json=body, headers=c(tok)).status_code == 404  # only their own
    assert client.post(f"/v1/decisions/{dec}/consents/marketing", json=body, headers=c(tok)).status_code == 404   # only choices that exist
    staff = client.post(f"/v1/decisions/{dec}/consents/ai_summary", json=body, headers=h(t["keys"]["lender_analyst"])).json()
    assert staff["choice"]["given_by"] == "staff_for_customer"  # recorded as such, never as the customer's own act
    stranger = lender("ask")
    assert client.post(f"/v1/decisions/{dec}/consents/ai_summary", json=body, headers=h(stranger["keys"]["lender_analyst"])).status_code == 404
    assert client.get(f"/v1/decisions/{dec}/consents", headers=h(stranger["keys"]["auditor"])).status_code == 404


class _Writer:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate_summary(self, report: dict, session: Any) -> str:
        return self.text


def test_a_model_is_only_asked_when_the_choice_allows_it_and_a_withdrawal_takes_effect_at_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import recon_agent.service as svc

    t = lender("ask")
    dec, tok = decision(client, t)
    env = load_fixtures()["decline"]
    ref = f"lender_decision:{env['external_id']}"
    asked: list[str] = []

    class Writer(_Writer):
        def generate_summary(self, report: dict, session: Any) -> str:
            asked.append("asked")
            return f"Your lender declined the application [{ref}]."

    monkeypatch.setattr(svc, "get_llm_backend", lambda *a, **k: Writer(""))
    summary = lambda: client.post(f"/reconstruct/{dec}", headers=c(tok)).json()["plain_language_summary"]  # noqa: E731

    assert summary() is None and asked == []                       # no choice yet: the model is never called
    client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": True}, headers=c(tok))
    assert summary() and asked == ["asked"]                         # allowed: written, even though a report was cached a moment ago
    client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": False}, headers=c(tok))
    assert summary() is None and asked == ["asked"]                 # withdrawn: gone at once, and the model wasn't asked again


def test_the_choices_table_is_append_only_for_the_api_role() -> None:
    for stmt in ("UPDATE consents SET granted = true", "DELETE FROM consents"):
        with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)


# ---------- things the independent review found ----------

class _Counting:
    """Stands in for a real model backend: records what it was asked, and answers like the offline one."""

    def __new__(cls):  # build a subclass of the offline backend that calls itself 'gemini_api' and counts its use
        from recon_agent.backends import FakeLLMBackend

        class Counting(FakeLLMBackend):
            backend_name = "gemini_api"
            calls: list[str] = []

            def generate_report(self, decision_id, session, **kw):  # noqa: ANN001
                Counting.calls.append("report")
                return super().generate_report(decision_id, session, **kw)

            def generate_summary(self, report, session):  # noqa: ANN001
                Counting.calls.append("summary")
                return None

        Counting.calls = []
        return Counting()


def test_a_model_never_rebuilds_the_explanation_of_a_scored_decision_unless_allowed(client: TestClient) -> None:
    from recon_agent.service import reconstruct_decision

    demo = h(os.environ["DT_API_KEY"])
    dec = next(d["decision_id"] for d in client.get("/decisions?limit=50", headers=demo).json()
               if client.get(f"/decisions/{d['decision_id']}/verify", headers=demo).json()["chain_valid"])
    tok = client.post(f"/v1/decisions/{dec}/claim-links", json={}, headers=demo).json()["token"]

    def set_policy(policy: str) -> None:
        with psycopg.connect(os.environ["DB_URL"]) as conn:
            conn.execute("UPDATE tenants SET ai_summary_policy = %s WHERE tenant_id = 'demo'", (policy,))
            conn.commit()

    try:
        for policy in ("never", "ask"):
            set_policy(policy)
            backend = _Counting()
            rep = reconstruct_decision(dec, backend=backend)
            assert rep.status == "ok" and type(backend).calls == [], policy          # a complete explanation, with no model asked

        set_policy("allow")
        backend = _Counting()
        reconstruct_decision(dec, backend=backend)
        assert "report" in type(backend).calls                                      # the lender allows it

        set_policy("ask")
        client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": True}, headers={"X-Claim-Token": tok})
        backend = _Counting()
        reconstruct_decision(dec, backend=backend)
        assert "report" in type(backend).calls                                      # the customer said yes
        client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": False}, headers={"X-Claim-Token": tok})
        backend = _Counting()
        reconstruct_decision(dec, backend=backend)
        assert type(backend).calls == []                                            # and then no again
    finally:
        set_policy("ask")
        with psycopg.connect(os.environ["DB_URL"]) as conn:  # leave the shared demo decision as it was found
            conn.execute("DELETE FROM consents WHERE decision_id = %s", (dec,))
            conn.commit()


def test_a_withdrawal_made_while_the_model_is_writing_is_respected_in_what_is_returned_and_remembered(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import recon_agent.service as svc

    t = lender("ask")
    dec, tok = decision(client, t)
    ref = f"lender_decision:{load_fixtures()['decline']['external_id']}"
    client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": True}, headers=c(tok))

    class Withdrawing:
        def generate_summary(self, report: dict, session: Any) -> str:
            client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": False}, headers=c(tok))  # changes their mind meanwhile
            return f"Your lender declined the application [{ref}]."

    monkeypatch.setattr(svc, "get_llm_backend", lambda *a, **k: Withdrawing())
    first = client.post(f"/reconstruct/{dec}", headers=c(tok)).json()
    assert first["plain_language_summary"] is None                                  # not handed over
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).json()["plain_language_summary"] is None  # and not kept for later


def test_a_summary_cached_by_another_worker_is_not_served_after_a_withdrawal(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import recon_agent.service as svc

    t = lender("ask")
    dec, tok = decision(client, t)
    ref = f"lender_decision:{load_fixtures()['decline']['external_id']}"
    monkeypatch.setattr(svc, "get_llm_backend", lambda *a, **k: _Writer(f"Your lender declined the application [{ref}]."))
    client.post(f"/v1/decisions/{dec}/consents/ai_summary", json={"granted": True}, headers=c(tok))
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).json()["plain_language_summary"]   # cached with the summary
    with psycopg.connect(os.environ["DB_URL"]) as conn:  # the withdrawal is written by another process: this one's cache isn't told
        conn.execute("INSERT INTO consents (tenant_id, decision_id, purpose, granted, wording_version, given_by, recorded_by_role) "
                     "SELECT tenant_id, decision_id, 'ai_summary', false, 'x', 'customer', 'consumer' FROM decisions WHERE decision_id = %s", (dec,))
        conn.commit()
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).json()["plain_language_summary"] is None


def test_data_checks_do_not_send_records_to_a_model_the_lender_has_ruled_out_or_protected_records(client: TestClient) -> None:
    from backend.auth import tenant_connection
    from backend.quality import scan

    asked: list[str] = []

    def model(prompt: str) -> str:
        asked.append(prompt)
        return "[]"

    def bad_decision(t: dict[str, Any], ext: str) -> str:
        env = load_fixtures()["decline"]
        env = {**env, "external_id": ext, "evidence": [{**env["evidence"][0], "evidence_ref": f"application:{ext}", "payload": {"income_annual": -5, "requested_amount": 100}}]}
        return client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": ext}).json()["decision_id"]

    ask = lender("ask")
    d1 = bad_decision(ask, "AI-1")
    with tenant_connection(ask["tenant_id"]) as conn:
        scan(conn, ask["tenant_id"], decision_ids=[d1], llm=model)
    assert len(asked) == 1                                                          # allowed: a model may help

    never = lender("never")
    d2 = bad_decision(never, "AI-2")
    with tenant_connection(never["tenant_id"]) as conn:
        scan(conn, never["tenant_id"], decision_ids=[d2], llm=model)
    assert len(asked) == 1                                                          # ruled out by the lender: nothing sent

    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        protected = provision_tenants(conn, 1, prefix=f"con-{uuid.uuid4().hex[:5]}", encrypt=True)[0]
    d3 = bad_decision(protected, "AI-3")
    with tenant_connection(protected["tenant_id"]) as conn:
        scan(conn, protected["tenant_id"], decision_ids=[d3], llm=model)
    assert len(asked) == 1                                                          # protected content stays inside


def test_the_mapping_assistant_stays_with_the_built_in_rules_for_a_lender_that_ruled_out_ai(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    mapping_module = importlib.import_module("decision_api.mappings_api")
    seen: list[bool] = []
    real = mapping_module.propose
    monkeypatch.setattr(mapping_module, "propose", lambda *a, **k: (seen.append(k.get("use_ai", True)), real(*a, **k))[1])
    csv_text = "ref,customer_id,when,result,pd,model,policy,income\nA1,c1,2026-09-01T10:00:00Z,Approved,0.1,m,p,100\n"
    for policy, expected in (("never", False), ("ask", True)):
        t = lender(policy)
        r = client.post("/v1/mappings/propose", json={"name": "x", "format": "csv", "content": csv_text}, headers=h(t["keys"]["lender_ingest"]))
        assert r.status_code == 200 and seen[-1] is expected and r.json()["proposed_by"] == "rules"

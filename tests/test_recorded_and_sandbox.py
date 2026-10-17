"""Explanations for lender-made decisions, the golden fixtures, and the bulk sandbox (all synthetic)."""

from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.ingest import DecisionEnvelopeV1
from backend.sandbox import load_fixtures, provision_tenants, send_decisions
from decision_api.app import create_app
from recon_agent import router as recon_router


def h(key: str, **extra: str) -> dict[str, str]:
    return {"X-API-Key": key, **extra}


@pytest.fixture(scope="module")
def sandbox() -> list[dict]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 2, prefix=f"sbx-{uuid.uuid4().hex[:5]}")


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def post_fixture(client: TestClient, tenant: dict, name: str) -> str:
    env = load_fixtures()[name]
    r = client.post(
        "/v1/decisions", json=env, headers=h(tenant["keys"]["lender_ingest"], **{"Idempotency-Key": f"fx-{name}"})
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["decision_id"]


def report_for(client: TestClient, tenant: dict, dec: str) -> dict:
    r = client.post(f"/reconstruct/{dec}", headers=h(tenant["keys"]["lender_analyst"]))
    assert r.status_code == 200, r.text
    return r.json()


def test_every_fixture_is_a_valid_envelope_and_covers_the_cases() -> None:
    fixtures = load_fixtures()
    assert {"approve", "decline", "refer", "missing_document", "decline_without_reasons"} <= set(fixtures)
    for env in fixtures.values():
        DecisionEnvelopeV1.model_validate(env)
    assert {f["outcome"] for f in fixtures.values()} == {"APPROVE", "DECLINE", "REFER"}


def test_recorded_decision_is_explained_from_the_lenders_own_reasons(client: TestClient, sandbox: list[dict]) -> None:
    dec = post_fixture(client, sandbox[0], "decline")
    rep = report_for(client, sandbox[0], dec)
    env = load_fixtures()["decline"]

    assert rep["recording_mode"] == "recorded" and rep["status"] == "ok"
    assert rep["outcome"] == "DECLINE" and rep["chain_verified"] is True
    assert rep["replay_verified"] is False and rep["rule_checks"] == []  # nothing of ours to re-score
    assert rep["principal_reasons"] == [r["text"] for r in env["reasons"]]
    assert "did not make or re-check" in rep["policy_note"]

    claims = [c for g in rep["reasons"] for c in g["claims"]]
    record = client.get(f"/decisions/{dec}", headers=h(sandbox[0]["keys"]["lender_analyst"])).json()
    assert record["recording_mode"] == "recorded"
    snap = next(s for s in record["snapshots"] if s["evidence_type"] == "lender_decision")
    assert {c["citation"] for c in claims} == {snap["evidence_ref"]}
    codes = {r["code"] for r in snap["payload"]["reasons"]}
    assert {c["value"] for c in claims if c["field"] == "reason_code"} == codes
    assert {c["value"] for c in claims if c["field"] == "outcome"} == {snap["payload"]["outcome"]}


def test_decision_without_reasons_says_so_instead_of_inventing_any(client: TestClient, sandbox: list[dict]) -> None:
    rep = report_for(client, sandbox[0], post_fixture(client, sandbox[0], "decline_without_reasons"))
    assert rep["principal_reasons"] == ["The lender's system recorded this outcome without giving reasons."]
    assert all(c["field"] != "reason_code" for g in rep["reasons"] for c in g["claims"])


def test_recorded_reports_do_not_call_a_model(client: TestClient, sandbox: list[dict], monkeypatch: pytest.MonkeyPatch) -> None:
    import recon_agent.service as svc

    monkeypatch.setattr(svc, "get_llm_backend", lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM used")))
    rep = report_for(client, sandbox[0], post_fixture(client, sandbox[0], "approve"))
    assert rep["plain_language_summary"] is None and rep["outcome"] == "APPROVE"


def test_override_and_tampering_show_in_the_recorded_report(client: TestClient, sandbox: list[dict]) -> None:
    t = sandbox[1]
    dec = post_fixture(client, t, "refer")
    ov = {"decision_id": dec, "new_outcome": "APPROVE", "reason": "Reviewed by a person; income confirmed.", "actor_role": "underwriter"}
    assert client.post("/overrides", json=ov, headers=h(t["keys"]["lender_analyst"])).status_code == 200
    rep = report_for(client, t, dec)
    assert rep["outcome"] == "APPROVE" and rep["human_override"]["actor_role"] == "underwriter"

    with psycopg.connect(os.environ["DB_URL"]) as conn:  # owner-level edit, as an insider or DBA could do
        conn.execute(
            "UPDATE decision_snapshots SET payload = jsonb_set(payload, '{score}', '0.01') "
            "WHERE decision_id = %s AND evidence_type = 'lender_decision'",
            (dec,),
        )
        conn.commit()
    recon_router.reset_state()
    assert report_for(client, t, dec)["chain_verified"] is False
    assert client.get(f"/decisions/{dec}/verify", headers=h(t["keys"]["lender_analyst"])).json()["chain_valid"] is False


def test_removed_lender_evidence_is_reported_as_unavailable_and_not_cited(client: TestClient, sandbox: list[dict]) -> None:
    t = sandbox[0]
    dec = post_fixture(client, t, "missing_document")
    with psycopg.connect(os.environ["DB_URL"]) as conn:
        conn.execute(
            "INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at, tenant_id) "
            "SELECT decision_id, evidence_ref, 'erased_by_retention', now(), tenant_id "
            "FROM decision_snapshots WHERE decision_id = %s AND evidence_type = 'lender_decision'",
            (dec,),
        )
        conn.commit()
    recon_router.reset_state()
    rep = report_for(client, t, dec)
    assert rep["status"] == "evidence_unavailable" and rep["completeness"] < 1.0
    assert rep["reasons"] == [] and rep["missing_evidence"]


def test_bulk_sandbox_goes_through_the_real_api_stays_separate_and_replays(client: TestClient, sandbox: list[dict]) -> None:
    first = send_decisions(client, sandbox[0], 25, run="bulk")
    assert first == {"created": 25, "replayed": 0, "failed": 0}
    assert send_decisions(client, sandbox[0], 25, run="bulk") == {"created": 0, "replayed": 25, "failed": 0}
    send_decisions(client, sandbox[1], 10, run="bulk")

    ids = lambda t: {  # noqa: E731
        d["decision_id"] for d in client.get("/decisions?limit=1000", headers=h(t["keys"]["lender_analyst"])).json()
    }
    a, b = ids(sandbox[0]), ids(sandbox[1])
    assert len(a) >= 25 and len(b) >= 10 and a.isdisjoint(b)
    sample = sorted(a)[:8]
    for dec in sample:
        assert client.get(f"/decisions/{dec}/verify", headers=h(sandbox[0]["keys"]["auditor"])).json()["chain_valid"]
        assert client.get(f"/decisions/{dec}", headers=h(sandbox[1]["keys"]["auditor"])).status_code == 404

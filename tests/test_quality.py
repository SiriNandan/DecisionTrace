"""Data-quality checks, AI help with grounding, and corrections that never touch the original record."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.quality import DecisionView, Item, ai_triage, check_decision, find_outliers, grounded
from backend.sandbox import provision_tenants
from decision_api.app import create_app
from recon_agent import router as recon_router

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def view(outcome: str = "DECLINE", score: float | None = 0.4, items: list[Item] | None = None, decided: datetime = NOW) -> DecisionView:
    return DecisionView("d1", outcome, score, decided, items or [])


def item(ref: str, etype: str, payload: dict[str, Any], ts: datetime | None = NOW) -> Item:
    return Item(ref, etype, payload, ts, "h" * 64)


def rules(v: DecisionView) -> dict[str, dict[str, Any]]:
    return {f["rule_id"]: f for f in check_decision(v, now=NOW)}


# ---------- the rules ----------

def test_a_clean_decision_raises_nothing() -> None:
    v = view("APPROVE", 0.1, [
        item("application:1", "application", {"income_annual": 40000, "requested_amount": 8000, "dti_ratio": 0.3}),
        item("bureau:1", "bureau", {"credit_score": 700, "delinquencies_24m": 0}),
        item("document:1", "document", {"status": "received", "monthly_income": 3333}),
    ])
    assert rules(v) == {}


def test_implausible_values_are_named_in_plain_words() -> None:
    v = view(items=[
        item("application:1", "application", {"income_annual": -5, "requested_amount": 0, "dti_ratio": 250}),
        item("bureau:1", "bureau", {"credit_score": 4000, "delinquencies_24m": -2}),
    ])
    found = rules(v)
    assert {"income_not_positive", "amount_not_positive", "dti_range", "credit_score_range", "negative_count"} <= set(found)
    assert "isn't a positive amount" in found["income_not_positive"]["message"]
    assert all(f["suggested_fix"] is None for f in found.values())  # nothing to guess from


def test_a_percentage_in_a_fraction_field_comes_with_a_rule_based_fix() -> None:
    f = rules(view(items=[item("application:1", "application", {"income_annual": 30000, "requested_amount": 5000, "dti_ratio": 45})]))["dti_looks_like_percentage"]
    assert f["suggested_fix"]["new_value"] == 0.45 and f["suggested_fix"]["field"] == "dti_ratio"
    assert f["suggested_fix"]["evidence_ref"] == "application:1"


def test_monthly_income_entered_as_yearly_is_spotted_and_the_fix_is_traced() -> None:
    v = view("APPROVE", 0.1, [
        item("application:1", "application", {"income_annual": 2500, "requested_amount": 4000}),
        item("document:1", "document", {"monthly_income": 2500, "status": "received"}),
    ])
    f = rules(v)["income_unit_mismatch"]
    assert f["kind"] == "conflict" and f["suggested_fix"]["new_value"] == 30000
    assert "document:1" in f["suggested_fix"]["basis"]
    assert "income_sources_differ" not in rules(v)  # one finding for one problem


def test_other_sources_disagreeing_missing_and_inconsistent_evidence() -> None:
    v = view("APPROVE", 0.9, [
        item("application:1", "application", {"income_annual": 30000}),
        item("bureau:1", "bureau", {"fico": 700}),
        item("bureau:2", "bureau", {"credit_score": 600}),
        item("document:1", "document", {"payslip_status": "Missing"}),
        item("extra:1", "other", {"note": "x"}, ts=NOW + timedelta(days=2)),
    ])
    found = rules(v)
    assert "sources_differ" in found and "700" in found["sources_differ"]["message"]
    assert found["missing_expected"]["kind"] == "missing"
    assert "approved_document_missing" in found and "score_outcome_unusual" in found
    assert "evidence_after_decision" in found
    assert rules(view("DECLINE", 0.4, [item("application:1", "application", {"income_annual": 1})], decided=NOW + timedelta(days=9)))["decision_in_future"]


def test_policy_limits_and_model_numbers_are_not_compared_with_what_the_applicant_said() -> None:
    v = view("DECLINE", 0.4, [
        item("application:1", "application", {"income_annual": 30000, "requested_amount": 5000, "dti_ratio": 0.5}),
        item("policy:LP-1", "policy", {"dti_limit": 0.4, "min_credit_score": 620}),
        item("model:m1", "model", {"features": 1, "income_annual": 99999}),
    ])
    assert rules(v) == {}


def test_a_decline_with_no_reason_is_flagged_only_for_lender_recorded_decisions() -> None:
    lender = item("lender_decision:1", "lender_decision", {"reasons": [], "outcome": "DECLINE"})
    assert "decline_without_reason" in rules(view("DECLINE", 0.4, [lender]))
    assert "decline_without_reason" not in rules(view("DECLINE", 0.4, [item("application:1", "application", {"income_annual": 1, "requested_amount": 1})]))


def test_outliers_use_the_usual_range_so_one_huge_value_cannot_hide() -> None:
    views = [DecisionView(f"d{i}", "APPROVE", 0.1, NOW, [item(f"application:{i}", "application", {"income_annual": 30000 + i * 100})]) for i in range(40)]
    views[7].items[0].payload["income_annual"] = 9_000_000
    out = find_outliers(views)
    assert [f["decision_id"] for f in out] == ["d7"] and out[0]["kind"] == "outlier"
    assert find_outliers(views[:10]) == []  # too few to say what is usual


# ---------- AI help is checked against the record ----------

def test_only_values_traceable_to_the_record_are_kept() -> None:
    v = view("APPROVE", 0.1, [item("application:1", "application", {"income_annual": 2500, "requested_amount": 4000}),
                              item("document:1", "document", {"monthly_income": 2500})])
    assert grounded(30000, [2500, 4000]) and grounded(25, [2500]) and grounded(0.45, [45]) and not grounded(31000, [2500, 4000])
    findings = [
        {"decision_id": "d1", "rule_id": "amount_vs_income", "kind": "implausible", "evidence_ref": "application:1",
         "field": "requested_amount", "observed": {"value": 4000}, "message": "m", "suggested_fix": None, "found_by": "rules"},
        {"decision_id": "d1", "rule_id": "x", "kind": "implausible", "evidence_ref": "application:1",
         "field": "income_annual", "observed": {"value": 2500}, "message": "m", "suggested_fix": None, "found_by": "rules"},
    ]
    answer = [{"index": 0, "likely_cause": "Call me on 07700 900123 about it", "suggested_value": 4_000_000},
              {"index": 1, "likely_cause": "Looks like a monthly figure.", "suggested_value": 30000}]
    notes = ai_triage(findings, {"d1": v}, lambda _p: "here: " + json.dumps(answer))
    assert findings[0]["suggested_fix"] is None and any("wasn't backed by the record" in n for n in notes)
    assert "07700" not in findings[0]["ai_note"]  # phone numbers are masked
    assert findings[1]["suggested_fix"]["new_value"] == 30000.0 and findings[1]["found_by"] == "rules+ai"


def test_a_failing_model_changes_nothing() -> None:
    f = [{"decision_id": "d1", "rule_id": "x", "kind": "implausible", "evidence_ref": "r", "field": "f",
          "observed": {"value": 1}, "message": "m", "suggested_fix": None, "found_by": "rules"}]
    notes = ai_triage(f, {"d1": view()}, lambda _p: (_ for _ in ()).throw(TimeoutError("down")))
    assert f[0]["suggested_fix"] is None and "ai_note" not in f[0] and "checks were not affected" in notes[0]


# ---------- through the API ----------

def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 2, prefix=f"qa-{uuid.uuid4().hex[:5]}")


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def envelope(ext: str, *, outcome: str = "APPROVE", app: dict[str, Any] | None = None, extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    ts = "2026-09-01T10:00:00+00:00"
    ev = [{"evidence_type": "application", "evidence_ref": f"application:{ext}", "source_id": "los", "source_timestamp": ts,
           "payload": app or {"income_annual": 2500, "requested_amount": 4000, "dti_ratio": 0.3}}]
    ev += extra or [{"evidence_type": "document", "evidence_ref": f"document:{ext}", "source_id": "docs", "source_timestamp": ts,
                     "payload": {"monthly_income": 2500, "status": "missing"}}]
    return {"schema_version": 1, "external_id": ext, "subject_ref": "s-1", "decided_at": ts, "outcome": outcome, "score": 0.2,
            "model_version": "m1", "policy_version": "p1", "reasons": [{"code": "R", "text": "A reason."}], "evidence": ev}


def post(client: TestClient, t: dict[str, Any], env: dict[str, Any]) -> str:
    r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": env["external_id"]})
    assert r.status_code in (200, 201), r.text
    return r.json()["decision_id"]


def findings(client: TestClient, t: dict[str, Any], status: str = "open") -> list[dict[str, Any]]:
    return client.get(f"/v1/quality/findings?status={status}", headers=h(t["keys"]["auditor"])).json()


def test_checks_find_problems_once_and_roles_are_respected(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    dec = post(client, t, envelope("Q-1"))
    analyst = h(t["keys"]["lender_analyst"])
    assert client.post("/v1/quality/run", json={}, headers=h(t["keys"]["auditor"])).status_code == 403
    assert client.post("/v1/quality/run", json={}).status_code == 401

    run = client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst).json()
    assert run["decisions_checked"] == 1 and run["new_findings"] >= 2 and run["ai_used"] is False
    again = client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst).json()
    assert again["new_findings"] == 0  # nothing is added twice
    rule_ids = {f["rule_id"] for f in findings(client, t) if f["decision_id"] == dec}
    assert {"income_unit_mismatch", "approved_document_missing"} <= rule_ids
    assert client.post("/v1/quality/findings/1/resolve", json={"action": "dismiss", "note": "long enough note"},
                       headers=h(t["keys"]["auditor"])).status_code == 403


def test_a_correction_is_appended_and_the_original_record_stays_intact(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    analyst = h(t["keys"]["lender_analyst"])
    dec = post(client, t, envelope("Q-2"))
    client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst)
    f = next(x for x in findings(client, t) if x["decision_id"] == dec and x["rule_id"] == "income_unit_mismatch")
    assert f["suggested_fix"]["new_value"] == 30000 and f["status"] == "open"

    before = client.get(f"/decisions/{dec}", headers=analyst).json()
    assert client.post(f"/v1/quality/findings/{f['finding_id']}/resolve", json={"action": "correct", "note": "short"},
                       headers=analyst).status_code == 422
    r = client.post(f"/v1/quality/findings/{f['finding_id']}/resolve",
                    json={"action": "correct", "note": "Payslip shows 2,500 a month, so 30,000 a year."}, headers=analyst)
    assert r.status_code == 200 and r.json()["status"] == "corrected"
    assert r.json()["correction"]["old_value"] == 2500 and r.json()["correction"]["new_value"] == 30000.0
    assert client.post(f"/v1/quality/findings/{f['finding_id']}/resolve",
                       json={"action": "dismiss", "note": "changed my mind"}, headers=analyst).status_code == 409

    after = client.get(f"/decisions/{dec}", headers=analyst).json()
    assert after["chain_head"] == before["chain_head"] and after["snapshots"] == before["snapshots"]  # untouched
    assert after["chain_valid"] is True and after["corrections_valid"] is True
    assert [(c["field"], c["old_value"], c["new_value"], c["source"]) for c in after["corrections"]] == [("income_annual", 2500, 30000.0, "suggested")]
    v = client.get(f"/decisions/{dec}/verify", headers=analyst).json()
    assert v["chain_valid"] is True and v["corrections_count"] == 1 and v["corrections_valid"] is True
    assert client.get(f"/v1/decisions/{dec}/corrections", headers=h(t["keys"]["auditor"])).json()["chain_valid"] is True
    assert f["finding_id"] not in {x["finding_id"] for x in findings(client, t)}
    assert f["finding_id"] in {x["finding_id"] for x in findings(client, t, "corrected")}

    # a second correction chains onto the first, and a person may enter their own value
    f2 = next(x for x in findings(client, t) if x["decision_id"] == dec and x["rule_id"] == "approved_document_missing")
    client.post(f"/v1/quality/findings/{f2['finding_id']}/resolve",
                json={"action": "correct", "note": "Document was received on 3 Sept.", "new_value": "received"}, headers=analyst)
    corr = client.get(f"/decisions/{dec}", headers=analyst).json()["corrections"]
    assert [c["seq"] for c in corr] == [1, 2] and corr[1]["source"] == "person" and corr[1]["new_value"] == "received"


def test_editing_or_removing_a_correction_is_detected(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    analyst = h(t["keys"]["lender_analyst"])
    dec = post(client, t, envelope("Q-3", app={"income_annual": 30000, "requested_amount": 5000, "dti_ratio": 45}, extra=[
        {"evidence_type": "bureau", "evidence_ref": "bureau:Q-3", "source_id": "b", "source_timestamp": "2026-09-01T09:00:00+00:00",
         "payload": {"credit_score": 650}}]))
    client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst)
    f = next(x for x in findings(client, t) if x["decision_id"] == dec and x["rule_id"] == "dti_looks_like_percentage")
    client.post(f"/v1/quality/findings/{f['finding_id']}/resolve", json={"action": "correct", "note": "Entered as a percentage."}, headers=analyst)
    assert client.get(f"/decisions/{dec}", headers=analyst).json()["corrections_valid"] is True

    with psycopg.connect(os.environ["DB_URL"]) as conn:  # an insider with owner rights rewrites the correction
        conn.execute("UPDATE corrections SET payload = jsonb_set(payload, '{new_value}', '0.99') WHERE decision_id = %s", (dec,))
        conn.commit()
    rec = client.get(f"/decisions/{dec}", headers=analyst).json()
    assert rec["corrections_valid"] is False and rec["chain_valid"] is True  # the decision itself is still intact
    assert client.get(f"/decisions/{dec}/verify", headers=analyst).json()["corrections_valid"] is False


def test_dismissing_keeps_the_record_and_the_reason(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    analyst = h(t["keys"]["lender_analyst"])
    dec = post(client, t, envelope("Q-4"))
    client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst)
    f = next(x for x in findings(client, t) if x["decision_id"] == dec)
    r = client.post(f"/v1/quality/findings/{f['finding_id']}/resolve", json={"action": "dismiss", "note": "Confirmed with the customer."}, headers=analyst)
    assert r.json() == {"finding_id": f["finding_id"], "status": "dismissed", "correction": None}
    assert client.get(f"/decisions/{dec}", headers=analyst).json()["corrections"] == []
    assert f["finding_id"] in {x["finding_id"] for x in findings(client, t, "dismissed")}


def test_findings_and_corrections_stay_inside_each_lender(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    a, b = lenders
    dec = post(client, a, envelope("Q-5"))
    client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=h(a["keys"]["lender_analyst"]))
    fid = next(x for x in findings(client, a) if x["decision_id"] == dec)["finding_id"]
    assert fid not in {x["finding_id"] for x in findings(client, b, "all")}
    assert client.post(f"/v1/quality/findings/{fid}/resolve", json={"action": "dismiss", "note": "not yours at all"},
                       headers=h(b["keys"]["lender_analyst"])).status_code == 404
    assert client.get(f"/v1/decisions/{dec}/corrections", headers=h(b["keys"]["lender_analyst"])).status_code == 404
    other = client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=h(b["keys"]["lender_analyst"])).json()
    assert other["decisions_checked"] == 0  # a lender can't scan another lender's decision


def test_files_are_checked_as_they_are_imported(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    csv_text = ("ref,customer_id,when,result,pd,model,policy,income,amount\n"
                "IMP-1,c1,2026-09-01T10:00:00Z,Approved,0.1,m1,p1,-100,5000\n"
                "IMP-2,c2,2026-09-01T10:00:00Z,Declined,0.4,m1,p1,40000,5000\n")
    m = client.post("/v1/mappings/propose", json={"name": "q-imp", "format": "csv", "content": csv_text}, headers=h(t["keys"]["lender_ingest"])).json()
    assert m["gaps"] == [], m
    client.post(f"/v1/mappings/{m['mapping_id']}/approve", json={}, headers=h(t["keys"]["lender_analyst"]))
    out = client.post("/v1/imports", json={"mapping_id": m["mapping_id"], "format": "csv", "content": csv_text}, headers=h(t["keys"]["lender_ingest"])).json()
    assert out["created"] == 2 and out["quality"]["decisions_checked"] == 2 and out["quality"]["new_findings"] >= 1
    assert any(f["rule_id"] == "income_not_positive" for f in findings(client, t))


def test_new_tables_are_append_only_for_the_api_role() -> None:
    for sql in ("UPDATE quality_findings SET message = 'x'", "DELETE FROM quality_findings", "UPDATE corrections SET field = 'x'",
                "DELETE FROM corrections", "UPDATE finding_reviews SET note = 'x'", "DELETE FROM finding_reviews"):
        with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)

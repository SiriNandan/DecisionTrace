"""Propose → review → import over HTTP: roles, isolation, idempotency and the audit trail."""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.sandbox import provision_tenants
from decision_api.app import create_app
from recon_agent import router as recon_router

SRC = Path(__file__).resolve().parent.parent / "fixtures" / "sources"
UK = (SRC / "uk_bank.csv").read_text(encoding="utf-8")
US = (SRC / "us_lender.csv").read_text(encoding="utf-8")
NESTED = json.loads((SRC / "nested.json").read_text(encoding="utf-8"))


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture(scope="module")
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 2, prefix=f"map-{uuid.uuid4().hex[:5]}")


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def keys(t: dict[str, Any]) -> tuple[str, str, str]:
    return t["keys"]["lender_ingest"], t["keys"]["lender_analyst"], t["keys"]["auditor"]


def propose(client: TestClient, t: dict[str, Any], name: str, **src: Any):
    return client.post("/v1/mappings/propose", json={"name": name, **src}, headers=h(keys(t)[0]))


def approve(client: TestClient, t: dict[str, Any], mid: int):
    return client.post(f"/v1/mappings/{mid}/approve", json={"note": "checked"}, headers=h(keys(t)[1]))


def do_import(client: TestClient, t: dict[str, Any], mid: int, **src: Any):
    return client.post("/v1/imports", json={"mapping_id": mid, **src}, headers=h(keys(t)[0]))


def decision_count(t: dict[str, Any]) -> int:
    return len(TestClient(create_app()).get("/decisions?limit=1000", headers=h(keys(t)[1])).json())


def test_the_whole_path_from_an_unfamiliar_file_to_a_verified_explained_decision(
    client: TestClient, lenders: list[dict[str, Any]]
) -> None:
    t = lenders[0]
    ingest_key, analyst_key, auditor_key = keys(t)

    r = propose(client, t, "uk-bank", format="csv", content=UK)
    assert r.status_code == 200, r.text
    body = r.json()
    mid = body["mapping_id"]
    assert body["status"] == "proposed" and body["version"] == 1 and body["proposed_by"] == "rules"
    assert body["gaps"] == [] and body["preview"]["valid"] == 40 and "Decision" in body["columns"]
    assert sorted(body["outcome_values"]) == ["Approved", "Declined", "Referred"]

    # nothing can be imported until a person approves
    assert do_import(client, t, mid, format="csv", content=UK).status_code == 409
    assert client.post(f"/v1/mappings/{mid}/approve", json={}, headers=h(ingest_key)).status_code == 403
    assert approve(client, t, mid).json()["status"] == "approved"
    assert approve(client, t, mid).status_code == 409  # already reviewed

    # an analyst can look but not import; a dry run stores nothing
    before = decision_count(t)
    assert client.post("/v1/imports", json={"mapping_id": mid, "format": "csv", "content": UK},
                       headers=h(analyst_key)).status_code == 403
    dry = do_import(client, t, mid, format="csv", content=UK, dry_run=True).json()
    assert dry["dry_run"] is True and dry["rejected"] == 0 and decision_count(t) == before

    done = do_import(client, t, mid, format="csv", content=UK).json()
    assert (done["created"], done["replayed"], done["rejected"]) == (40, 0, 0)
    again = do_import(client, t, mid, format="csv", content=UK).json()
    assert (again["created"], again["replayed"]) == (0, 40)
    assert decision_count(t) == before + 40

    dec = done["results"][0]["decision_id"]
    record = client.get(f"/decisions/{dec}", headers=h(analyst_key)).json()
    snap = next(s for s in record["snapshots"] if s["evidence_type"] == "lender_decision")
    stamp = snap["payload"]["source_mapping"]
    assert stamp["mapping_id"] == mid and stamp["version"] == 1 and len(stamp["spec_sha256"]) == 64
    assert client.get(f"/decisions/{dec}/verify", headers=h(auditor_key)).json()["chain_valid"] is True
    for s in record["snapshots"]:  # personal columns never reached storage
        assert "Alex" not in json.dumps(s["payload"]) and "example.com" not in json.dumps(s["payload"])
    rep = client.post(f"/reconstruct/{dec}", headers=h(analyst_key)).json()
    assert rep["recording_mode"] == "recorded" and rep["status"] == "ok" and rep["principal_reasons"]

    log = client.get("/v1/access-log?limit=200", headers=h(auditor_key)).json()
    actions = {r["action"] for r in log}
    assert {"mapping_propose", "mapping_approved", "import"} <= actions


def test_an_incomplete_mapping_cannot_be_approved_until_a_person_completes_it(
    client: TestClient, lenders: list[dict[str, Any]]
) -> None:
    t = lenders[0]
    body = propose(client, t, "us-lender", format="csv", content=US).json()
    assert len(body["gaps"]) == 2
    r = approve(client, t, body["mapping_id"])
    assert r.status_code == 422 and "model version" in r.json()["detail"]

    spec = body["spec"]
    spec["outcome"]["value_map"]["R"] = "REFER"
    spec["model_version"] = {"const": "us_model_4", "transform": "text", "confidence": 1}
    spec["policy_version"] = {"const": "us_policy_9", "transform": "text", "confidence": 1}
    saved = client.post("/v1/mappings", json={"name": "us-lender", "spec": spec}, headers=h(keys(t)[0])).json()
    assert saved["version"] == 2 and saved["status"] == "proposed" and saved["proposed_by"] == "person"
    assert saved["gaps"] == []
    assert approve(client, t, saved["mapping_id"]).status_code == 200
    out = do_import(client, t, saved["mapping_id"], format="csv", content=US).json()
    assert out["created"] == 40 and out["rejected"] == 0
    # the earlier draft stays unapproved and unusable
    assert do_import(client, t, body["mapping_id"], format="csv", content=US).status_code == 409


def test_rows_that_cannot_be_read_are_rejected_one_by_one_and_explained(
    client: TestClient, lenders: list[dict[str, Any]]
) -> None:
    t = lenders[0]
    mid = propose(client, t, "uk-partial", format="csv", content=UK).json()["mapping_id"]
    approve(client, t, mid)
    lines = UK.splitlines()
    lines[3] = lines[3].replace("Declined", "Maybe").replace("Approved", "Maybe").replace("Referred", "Maybe")
    out = do_import(client, t, mid, format="csv", content="\n".join(lines)).json()
    bad = [r for r in out["results"] if r["status"] == "rejected"]
    assert len(bad) == 1 and bad[0]["row"] == 3 and "isn't in the mapping" in bad[0]["problems"][0]

    # row 3 now reads fine (new), the rest replay, and a changed row with an old reference is refused, not overwritten
    rows = list(csv.reader(io.StringIO(UK)))
    rows[6][10] = "99999"  # Loan Amount of data row 6
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    again = do_import(client, t, mid, format="csv", content=buf.getvalue()).json()
    assert (again["created"], again["replayed"], again["rejected"]) == (1, 38, 1)
    refused = next(r for r in again["results"] if r["status"] == "rejected")
    assert refused["row"] == 6 and "different content" in refused["problems"][0]


def test_json_files_work_and_limits_hold(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    body = propose(client, t, "nested", format="json", rows=NESTED).json()
    assert body["spec"]["decided_at"]["column"] == "decision.when" and len(body["gaps"]) == 2  # only the versions
    spec = body["spec"]
    spec["model_version"] = {"const": "nx_model", "transform": "text", "confidence": 1}
    spec["policy_version"] = {"const": "nx_policy", "transform": "text", "confidence": 1}
    spec["outcome"]["value_map"] = {"Accepted": "APPROVE", "Declined": "DECLINE", "Manual review": "REFER"}
    saved = client.post("/v1/mappings", json={"name": "nested", "spec": spec}, headers=h(keys(t)[0])).json()
    assert saved["gaps"] == [] and approve(client, t, saved["mapping_id"]).status_code == 200
    out = do_import(client, t, saved["mapping_id"], format="json", rows=NESTED).json()
    assert out["created"] == 40 and out["rejected"] == 0

    assert do_import(client, t, saved["mapping_id"], format="json", rows=[NESTED[0]] * 501).status_code == 422
    assert propose(client, t, "empty", format="csv", content="a,b\n").status_code == 422
    assert propose(client, t, "nohdr", format="csv", content="").status_code == 422
    assert propose(client, t, "x", format="csv").status_code == 422


def test_each_lender_only_sees_and_uses_its_own_mappings(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    a, b = lenders
    mid = propose(client, a, "private-map", format="csv", content=UK).json()["mapping_id"]
    approve(client, a, mid)
    assert client.get(f"/v1/mappings/{mid}", headers=h(keys(b)[1])).status_code == 404
    assert do_import(client, b, mid, format="csv", content=UK).status_code == 404
    assert client.post(f"/v1/mappings/{mid}/approve", json={}, headers=h(keys(b)[1])).status_code == 404
    names_b = {m["name"] for m in client.get("/v1/mappings", headers=h(keys(b)[2])).json()}
    names_a = {m["name"] for m in client.get("/v1/mappings", headers=h(keys(a)[2])).json()}
    assert "private-map" in names_a and "private-map" not in names_b


def test_auditors_can_read_but_not_change_mappings_and_everything_needs_a_key(
    client: TestClient, lenders: list[dict[str, Any]]
) -> None:
    t = lenders[0]
    assert client.get("/v1/mappings", headers=h(keys(t)[2])).status_code == 200
    assert client.post("/v1/mappings/propose", json={"name": "n", "format": "csv", "content": UK},
                       headers=h(keys(t)[2])).status_code == 403
    for method, url in (("get", "/v1/mappings"), ("post", "/v1/mappings/1/approve"), ("post", "/v1/imports"),
                        ("post", "/v1/mappings/propose")):
        assert getattr(client, method)(url).status_code == 401


def test_mappings_are_append_only_for_the_api_role() -> None:
    for sql in ("UPDATE mappings SET name = 'x'", "DELETE FROM mappings",
                "UPDATE mapping_reviews SET action = 'approved'", "DELETE FROM mapping_reviews"):
        with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)

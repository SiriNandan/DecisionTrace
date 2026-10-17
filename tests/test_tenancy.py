"""Tenant isolation, per-tenant credentials, record-mode ingest and the access log."""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.auth import hash_api_key
from backend.ingest import keyed_decision_id
from backend.tenants import create_tenant, issue_key, revoke_key
from decision_api.app import create_app
from recon_agent import router as recon_router


def _owner_conn() -> psycopg.Connection:
    return psycopg.connect(os.environ["DB_URL"], connect_timeout=5)


@pytest.fixture(scope="module")
def tenants() -> dict[str, Any]:
    """Two lenders with ingest/analyst/auditor keys each, created fresh in the throwaway test database."""
    suffix = uuid.uuid4().hex[:6]
    out: dict[str, Any] = {}
    with _owner_conn() as conn:
        for name in ("a", "b"):
            tid = f"bank-{name}-{suffix}"
            create_tenant(conn, tid, f"Bank {name.upper()}")
            out[name] = {"id": tid}
            for role in ("lender_ingest", "lender_analyst", "auditor"):
                cred, key = issue_key(conn, tid, role, f"test {role}")
                out[name][role] = key
                out[name][f"{role}_id"] = cred
    return out


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def h(key: str, **extra: str) -> dict[str, str]:
    return {"X-API-Key": key, **extra}


def envelope(ext: str = "APP-X-1", **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema_version": 1,
        "external_id": ext,
        "subject_ref": "subj-7f3a",
        "decided_at": "2026-03-01T10:00:00Z",
        "outcome": "DECLINE",
        "score": 0.42,
        "model_version": "lender_model_7",
        "policy_version": "credit_policy_2026_03",
        "reasons": [{"code": "DTI_HIGH", "text": "Debt is high compared with income."}],
        "evidence": [
            {
                "evidence_type": "application",
                "evidence_ref": f"application:{ext}",
                "source_id": "los_v2",
                "source_timestamp": "2026-03-01T09:58:00Z",
                "payload": {"income_annual": 31000, "requested_amount": 9000},
            },
            {
                "evidence_type": "bureau",
                "evidence_ref": f"bureau:{ext}",
                "source_id": "bureau_api",
                "source_timestamp": "2026-03-01T09:59:00Z",
                "payload": {"credit_score": 610},
            },
        ],
    }
    body.update(over)
    return body


def ingest(client: TestClient, key: str, idem: str, body: dict[str, Any] | None = None):
    return client.post("/v1/decisions", json=body or envelope(), headers=h(key, **{"Idempotency-Key": idem}))


# ---------- authentication and roles ----------

def test_every_read_and_write_requires_a_credential(client: TestClient) -> None:
    some = str(uuid.uuid4())
    assert client.get("/decisions").status_code == 401
    assert client.get(f"/decisions/{some}").status_code == 401
    assert client.get(f"/decisions/{some}/verify").status_code == 401
    assert client.post(f"/reconstruct/{some}").status_code == 401
    assert client.post("/v1/decisions", json=envelope()).status_code == 401
    assert client.get("/v1/access-log").status_code == 401
    assert client.get("/decisions", headers=h("dtk_not-a-real-key")).status_code == 401


def test_roles_limit_what_a_credential_can_do(client: TestClient, tenants: dict[str, Any]) -> None:
    a = tenants["a"]
    assert ingest(client, a["lender_analyst"], "role-1").status_code == 403
    assert ingest(client, a["auditor"], "role-2").status_code == 403
    assert client.get("/decisions", headers=h(a["lender_ingest"])).status_code == 403
    assert client.get("/v1/access-log", headers=h(a["lender_analyst"])).status_code == 403
    ov = {"decision_id": str(uuid.uuid4()), "new_outcome": "APPROVE", "reason": "x" * 25, "actor_role": "analyst"}
    assert client.post("/overrides", json=ov, headers=h(a["auditor"])).status_code == 403


def test_revoked_key_and_suspended_tenant_are_refused(client: TestClient, tenants: dict[str, Any]) -> None:
    suffix = uuid.uuid4().hex[:6]
    with _owner_conn() as conn:
        create_tenant(conn, f"temp-{suffix}", "Temp Bank")
        cred, key = issue_key(conn, f"temp-{suffix}", "lender_analyst")
        assert client.get("/decisions", headers=h(key)).status_code == 200
        revoke_key(conn, cred)
        assert client.get("/decisions", headers=h(key)).status_code == 401
        _, key2 = issue_key(conn, f"temp-{suffix}", "lender_analyst")
        assert client.get("/decisions", headers=h(key2)).status_code == 200
        conn.execute("UPDATE tenants SET status = 'suspended' WHERE tenant_id = %s", (f"temp-{suffix}",))
        conn.commit()
        assert client.get("/decisions", headers=h(key2)).status_code == 401


def test_keys_are_stored_hashed_only(tenants: dict[str, Any]) -> None:
    key = tenants["a"]["lender_ingest"]
    with _owner_conn() as conn:
        rows = conn.execute("SELECT key_sha256 FROM api_credentials WHERE key_sha256 = %s", (hash_api_key(key),)).fetchall()
        assert len(rows) == 1
        assert conn.execute("SELECT count(*) FROM api_credentials WHERE key_sha256 = %s", (key,)).fetchone()[0] == 0


# ---------- ingest ----------

def test_ingest_records_verifies_and_replays(client: TestClient, tenants: dict[str, Any]) -> None:
    a = tenants["a"]
    first = ingest(client, a["lender_ingest"], "ing-1")
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["replayed"] is False and body["chain_length"] == 3

    again = ingest(client, a["lender_ingest"], "ing-1")
    assert again.status_code == 200
    assert again.json()["decision_id"] == body["decision_id"] and again.json()["replayed"] is True

    changed = envelope(outcome="APPROVE")
    changed["evidence"][0]["payload"]["income_annual"] = 99999
    assert ingest(client, a["lender_ingest"], "ing-1", changed).status_code == 409

    verify = client.get(f"/decisions/{body['decision_id']}/verify", headers=h(a["lender_analyst"]))
    assert verify.status_code == 200 and verify.json()["chain_valid"] is True and verify.json()["snapshot_count"] == 3
    record = client.get(f"/decisions/{body['decision_id']}", headers=h(a["lender_analyst"])).json()
    assert record["outcome"] == "DECLINE" and record["model_version"] == "lender_model_7"


def test_ingest_validates_the_envelope(client: TestClient, tenants: dict[str, Any]) -> None:
    key = tenants["a"]["lender_ingest"]
    assert client.post("/v1/decisions", json=envelope(), headers=h(key)).status_code == 422  # no Idempotency-Key
    assert ingest(client, key, "v-1", envelope(outcome="MAYBE")).status_code == 422
    assert ingest(client, key, "v-2", envelope(schema_version=2)).status_code == 422
    assert ingest(client, key, "v-3", envelope(score=1.5)).status_code == 422
    assert ingest(client, key, "v-4", envelope(unexpected="field")).status_code == 422
    assert ingest(client, key, "v-5", envelope(evidence=[])).status_code == 422
    big = envelope()
    big["evidence"][0]["payload"] = {"blob": "x" * 70_000}
    assert ingest(client, key, "v-6", big).status_code == 422


def test_decision_ids_are_keyed_per_tenant_and_not_the_old_deterministic_value(tenants: dict[str, Any]) -> None:
    id_a = keyed_decision_id(tenants["a"]["id"], "same-key")
    id_b = keyed_decision_id(tenants["b"]["id"], "same-key")
    assert id_a != id_b and id_a.version == 4
    assert id_a == keyed_decision_id(tenants["a"]["id"], "same-key")
    assert id_a != uuid.uuid5(uuid.NAMESPACE_OID, "decisiontrace:decide:idemp_key:same-key")


def test_ingest_requires_id_secret_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.delenv("DT_ID_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        keyed_decision_id("t", "k")


# ---------- isolation ----------

def test_a_tenant_cannot_reach_another_tenants_decision(client: TestClient, tenants: dict[str, Any]) -> None:
    a, b = tenants["a"], tenants["b"]
    dec = ingest(client, a["lender_ingest"], "iso-1").json()["decision_id"]
    ingest(client, b["lender_ingest"], "iso-b-1", envelope("APP-B-1"))

    assert client.get(f"/decisions/{dec}", headers=h(a["lender_analyst"])).status_code == 200
    for key in (b["lender_analyst"], b["auditor"]):
        assert client.get(f"/decisions/{dec}", headers=h(key)).status_code == 404
        assert client.get(f"/decisions/{dec}/verify", headers=h(key)).status_code == 404
        assert client.post(f"/reconstruct/{dec}", headers=h(key)).status_code == 404
    ov = {"decision_id": dec, "new_outcome": "APPROVE", "reason": "A reasonable justification text.", "actor_role": "analyst"}
    assert client.post("/overrides", json=ov, headers=h(b["lender_analyst"])).status_code == 404

    ids_b = {d["decision_id"] for d in client.get("/decisions", headers=h(b["lender_analyst"])).json()}
    ids_a = {d["decision_id"] for d in client.get("/decisions", headers=h(a["lender_analyst"])).json()}
    assert dec in ids_a and dec not in ids_b
    assert ids_a.isdisjoint(ids_b)
    demo_ids = {d["decision_id"] for d in client.get("/decisions", headers={"X-API-Key": os.environ["DT_API_KEY"]}).json()}
    assert dec not in demo_ids and len(demo_ids) >= 10  # the demo tenant still sees its seeded scenarios


def test_a_decision_cannot_be_overwritten_by_another_tenant_using_the_same_key(
    client: TestClient, tenants: dict[str, Any]
) -> None:
    a, b = tenants["a"], tenants["b"]
    ra = ingest(client, a["lender_ingest"], "shared-key").json()
    rb = ingest(client, b["lender_ingest"], "shared-key", envelope("APP-B-9", outcome="APPROVE")).json()
    assert ra["decision_id"] != rb["decision_id"]


def test_row_level_security_blocks_the_database_role_itself(tenants: dict[str, Any], client: TestClient) -> None:
    a, b = tenants["a"], tenants["b"]
    dec = ingest(client, a["lender_ingest"], "rls-1").json()["decision_id"]
    app_url = os.environ["APP_DB_URL"]

    def visible(tenant: str | None) -> tuple[int, int]:
        with psycopg.connect(app_url) as conn:
            if tenant is not None:
                conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant,))
            d = conn.execute("SELECT count(*) FROM decisions WHERE decision_id = %s", (dec,)).fetchone()[0]
            s = conn.execute("SELECT count(*) FROM decision_snapshots WHERE decision_id = %s", (dec,)).fetchone()[0]
            return d, s

    assert visible(a["id"]) == (1, 3)
    assert visible(b["id"]) == (0, 0)
    assert visible(None) == (0, 0)

    with psycopg.connect(app_url) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (b["id"],))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO decisions (decision_id, application_id, decided_at, outcome, model_version, policy_version, "
                "score, chain_length, chain_head, tenant_id) VALUES (%s, 'x', now(), 'APPROVE', 'm', 'p', 0.1, 1, 'h', %s)",
                (uuid.uuid4(), a["id"]),
            )


# ---------- access log ----------

def test_access_log_records_reads_denials_and_is_tenant_scoped(client: TestClient, tenants: dict[str, Any]) -> None:
    a, b = tenants["a"], tenants["b"]
    dec = ingest(client, a["lender_ingest"], "log-1").json()["decision_id"]
    client.get(f"/decisions/{dec}", headers=h(a["lender_analyst"]))
    client.get(f"/decisions/{dec}", headers=h(b["lender_analyst"]))  # denied: not theirs

    log_a = client.get("/v1/access-log?limit=50", headers=h(a["auditor"])).json()
    actions_a = {(r["action"], r["outcome"]) for r in log_a if r["object_id"] == dec}
    assert ("ingest", "allowed") in actions_a and ("read", "allowed") in actions_a
    assert all(r["outcome"] == "allowed" for r in log_a if r["object_id"] == dec)  # A never saw a denial for its own object

    log_b = client.get("/v1/access-log?limit=50", headers=h(b["auditor"])).json()
    assert any(r["object_id"] == dec and r["outcome"] == "not_found" and r["action"] == "read" for r in log_b)
    assert not any(r["action"] == "ingest" and r["object_id"] == dec for r in log_b)


def test_access_log_cannot_be_edited_by_the_api_role() -> None:
    with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE access_log SET action = 'x'")
    with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM access_log")

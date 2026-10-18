"""Tests for DecisionTrace Decision API (POST /decide, GET /decisions, GET /decisions/{id}, GET /decisions/{id}/verify) and source readers."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import uuid

from fastapi.testclient import TestClient

from tests.conftest import AUTH
import psycopg
import pytest

from backend.sources import (
    read_account,
    read_application,
    read_bureau,
    read_consent,
    read_documents,
    read_notes,
)
from data.load_policies import MIGRATION_FILE, get_db_url, load_policies
from data.train_model import train_and_register_model
from decision_api.app import create_app
import decision_api.service as decision_service


REQUIRED_EVIDENCE_TYPES = {
    "application",
    "bureau",
    "account_history",
    "document",
    "consent",
    "policy",
    "model",
    "rule_check",
    "analyst_note",
}

REQUIRED_RULE_CHECK_KEYS = {
    "rule_id",
    "observed_value",
    "operator",
    "threshold",
    "result",
    "policy_version",
    "section",
}


@pytest.fixture(scope="module")
def prepared_db() -> str:
    """Ensure schema, policies, and model_registry are initialized before API tests."""
    db_url = get_db_url()
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            if MIGRATION_FILE.is_file():
                cur.execute(MIGRATION_FILE.read_text(encoding="utf-8"))
    load_policies(db_url=db_url)
    train_and_register_model(db_url=db_url, sync_db=True)
    return db_url


@pytest.fixture()
def api_client(prepared_db: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return a FastAPI TestClient configured with ENV=dev."""
    monkeypatch.setenv("ENV", "dev")
    monkeypatch.setenv("DB_URL", prepared_db)
    app = create_app()
    return TestClient(app, headers=AUTH)


def test_source_readers_return_payload_source_id_and_timestamp() -> None:
    """Each source reader in backend/sources/*.py returns payload, source_id, and source_timestamp."""
    applicant_id = "APP-0001"
    readers = [
        read_application(applicant_id, requested_amount=9500.0),
        read_bureau(applicant_id),
        read_account(applicant_id),
        read_documents(applicant_id, document_refs=[f"payslip:{applicant_id}"]),
        read_consent(applicant_id),
        read_notes(applicant_id),
    ]
    for result in readers:
        assert isinstance(result, dict)
        assert "payload" in result and isinstance(result["payload"], dict)
        assert "source_id" in result and isinstance(result["source_id"], str) and result["source_id"]
        assert (
            "source_timestamp" in result
            and isinstance(result["source_timestamp"], str)
            and result["source_timestamp"]
        )


def test_ten_decisions_created_and_verified(api_client: TestClient, prepared_db: str) -> None:
    """Create 10 decisions via POST /decide and verify all 10 via GET /decisions/{id}/verify."""
    applicant_ids = [f"APP-{i:04d}" for i in range(101, 111)]
    created_ids: list[str] = []

    for idx, applicant_id in enumerate(applicant_ids):
        resp = api_client.post(
            "/decide",
            json={
                "applicant_id": applicant_id,
                "requested_amount": 8000.0 + idx * 500.0,
                "document_refs": [f"payslip:{applicant_id}"],
            },
            headers={"X-Decision-Date": f"2026-02-{10 + idx:02d}T12:00:00Z"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        decision_id = body["decision_id"]
        created_ids.append(decision_id)

        assert body["applicant_id"] == applicant_id
        assert body["outcome"] in ("APPROVE", "DECLINE", "REFER")
        assert body["policy_version"] == "LP-2026-v1"
        assert body["model_version"] == "loan_lr_v1"
        assert isinstance(body["score"], float)
        assert isinstance(body["contributions"], dict) and len(body["contributions"]) == 7
        assert body["chain_length"] >= 9
        assert len(body["chain_head"]) == 64

        # Verify via GET /decisions/{id}/verify
        verify_resp = api_client.get(f"/decisions/{decision_id}/verify")
        assert verify_resp.status_code == 200, verify_resp.text
        v_body = verify_resp.json()
        assert v_body["ok"] is True
        assert v_body["valid"] is True
        assert v_body["chain_valid"] is True
        assert v_body["status"] == "verified"
        assert v_body["first_broken_seq"] is None
        assert v_body["reason"] is None

        # Inspect full detail via GET /decisions/{id}
        detail_resp = api_client.get(f"/decisions/{decision_id}")
        assert detail_resp.status_code == 200, detail_resp.text
        detail = detail_resp.json()
        snapshots = detail["snapshots"]
        assert len(snapshots) == body["chain_length"]

        observed_types = {snap["evidence_type"] for snap in snapshots}
        assert REQUIRED_EVIDENCE_TYPES.issubset(observed_types)

        rule_snapshots = [s for s in snapshots if s["evidence_type"] == "rule_check"]
        assert len(rule_snapshots) >= 3
        for rs in rule_snapshots:
            assert REQUIRED_RULE_CHECK_KEYS.issubset(rs["payload"].keys())

    # Verify GET /decisions lists created decisions
    list_resp = api_client.get("/decisions?limit=200")
    assert list_resp.status_code == 200
    listed_ids = {item["decision_id"] for item in list_resp.json()}
    for cid in created_ids:
        assert cid in listed_ids


def test_forced_exception_mid_transaction_writes_nothing(
    prepared_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A forced exception mid-transaction (after INSERT INTO decisions and snapshot 1-2) rolls back completely."""
    monkeypatch.setenv("ENV", "dev")
    applicant_id = "APP-0155"
    decision_date = "2026-03-01T15:30:00Z"
    real_insert_snapshot = decision_service._insert_snapshot_row

    def failing_insert_snapshot(cur: psycopg.Cursor[Any], snap: dict[str, Any]) -> None:
        if snap["seq"] == 3:
            raise RuntimeError("Simulated mid-transaction failure at snapshot seq=3")
        real_insert_snapshot(cur, snap)

    monkeypatch.setattr(decision_service, "_insert_snapshot_row", failing_insert_snapshot)

    with psycopg.connect(prepared_db, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM decisions;")
            decisions_before = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM decision_snapshots;")
            snapshots_before = int(cur.fetchone()[0])

        with pytest.raises(RuntimeError, match="Simulated mid-transaction failure at snapshot seq=3"):
            decision_service.create_decision(
                conn,
                applicant_id=applicant_id,
                requested_amount=11250.0,
                document_refs=[f"payslip:{applicant_id}"],
                decided_at=decision_date,
                allow_backdating=True,
                idempotency_key="tx-rollback-test-key-0155",
            )

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM decisions;")
            decisions_after = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM decision_snapshots;")
            snapshots_after = int(cur.fetchone()[0])

    assert decisions_after == decisions_before
    assert snapshots_after == snapshots_before


def test_replayed_identical_request_does_not_create_duplicates(
    api_client: TestClient,
    prepared_db: str,
) -> None:
    """Replaying an identical POST /decide request (by payload or Idempotency-Key) returns the same decision without creating duplicate rows."""
    payload = {
        "applicant_id": "APP-0160",
        "requested_amount": 9200.0,
        "document_refs": ["payslip:APP-0160"],
    }
    headers = {
        "X-Decision-Date": "2026-02-25T09:15:00Z",
        "Idempotency-Key": "idem-replay-test-app-0160",
    }

    resp1 = api_client.post("/decide", json=payload, headers=headers)
    assert resp1.status_code == 200, resp1.text
    first = resp1.json()
    decision_id = first["decision_id"]

    with psycopg.connect(prepared_db, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision_id = %s;",
                (decision_id,),
            )
            dec_count_1 = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM decision_snapshots WHERE decision_id = %s;",
                (decision_id,),
            )
            snap_count_1 = int(cur.fetchone()[0])

    # Replay identical request with same Idempotency-Key
    resp2 = api_client.post("/decide", json=payload, headers=headers)
    assert resp2.status_code == 200, resp2.text
    second = resp2.json()
    assert second["decision_id"] == decision_id
    assert second["chain_head"] == first["chain_head"]
    assert second["chain_length"] == first["chain_length"]

    # Also test natural payload idempotency without header
    no_key_headers = {"X-Decision-Date": "2026-02-26T09:15:00Z"}
    resp3 = api_client.post("/decide", json=payload, headers=no_key_headers)
    assert resp3.status_code == 200
    resp4 = api_client.post("/decide", json=payload, headers=no_key_headers)
    assert resp4.status_code == 200
    assert resp3.json()["decision_id"] == resp4.json()["decision_id"]

    with psycopg.connect(prepared_db, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM decisions WHERE decision_id = %s;",
                (decision_id,),
            )
            dec_count_2 = int(cur.fetchone()[0])
            cur.execute(
                "SELECT COUNT(*) FROM decision_snapshots WHERE decision_id = %s;",
                (decision_id,),
            )
            snap_count_2 = int(cur.fetchone()[0])

    assert dec_count_1 == 1
    assert dec_count_2 == 1
    assert snap_count_2 == snap_count_1


def test_x_decision_date_restricted_to_env_dev(
    api_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X-Decision-Date header is accepted in ENV=dev and rejected with 403 when ENV != dev."""
    monkeypatch.setenv("ENV", "dev")
    dev_resp = api_client.post(
        "/decide",
        json={"applicant_id": "APP-0170", "requested_amount": 7500.0},
        headers={"X-Decision-Date": "2025-04-10T10:00:00Z"},
    )
    assert dev_resp.status_code == 200, dev_resp.text
    assert dev_resp.json()["policy_version"] == "LP-2025-v1"

    monkeypatch.setenv("ENV", "prod")
    prod_resp = api_client.post(
        "/decide",
        json={"applicant_id": "APP-0170", "requested_amount": 7500.0},
        headers={"X-Decision-Date": "2025-04-10T10:00:00Z"},
    )
    assert prod_resp.status_code == 403


def _fetch_decision_and_snapshots_bytes(
    conn: psycopg.Connection[Any],
    decision_id: str,
) -> tuple[bytes, list[bytes]]:
    """Return canonical UTF-8 byte representations of the decisions row and all decision_snapshots rows."""
    from backend.integrity import canonical_json

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT decision_id::text, application_id, decided_at::text, outcome,
                   model_version, policy_version, score::text, chain_length, chain_head,
                   row_to_json(d)::text
            FROM decisions d
            WHERE decision_id = %s;
            """,
            (decision_id,),
        )
        dec_row = cur.fetchone()
        assert dec_row is not None

        cur.execute(
            """
            SELECT decision_id::text, seq, evidence_type, evidence_ref,
                   source_id, source_timestamp::text, captured_at::text,
                   payload::text, payload_sha256, prev_hash, entry_hash,
                   row_to_json(ds)::text
            FROM decision_snapshots ds
            WHERE decision_id = %s
            ORDER BY seq ASC;
            """,
            (decision_id,),
        )
        snap_rows = cur.fetchall()

    decision_bytes = canonical_json(
        {
            "decision_id": dec_row[0],
            "application_id": dec_row[1],
            "decided_at": dec_row[2],
            "outcome": dec_row[3],
            "model_version": dec_row[4],
            "policy_version": dec_row[5],
            "score": dec_row[6],
            "chain_length": dec_row[7],
            "chain_head": dec_row[8],
            "pg_row_json": dec_row[9],
        }
    )
    snapshots_bytes = [
        canonical_json(
            {
                "decision_id": r[0],
                "seq": r[1],
                "evidence_type": r[2],
                "evidence_ref": r[3],
                "source_id": r[4],
                "source_timestamp": r[5],
                "captured_at": r[6],
                "payload_text": r[7],
                "payload_sha256": r[8],
                "prev_hash": r[9],
                "entry_hash": r[10],
                "pg_row_json": r[11],
            }
        )
        for r in snap_rows
    ]
    return decision_bytes, snapshots_bytes


def test_post_overrides_preserves_byte_identical_decision_and_snapshots_and_returns_both(
    api_client: TestClient,
    prepared_db: str,
) -> None:
    """POST /overrides inserts into overrides only, keeps decision and snapshots byte-identical, and get_decision returns both original outcome and override."""
    from recon_agent.tools import get_decision as agent_get_decision
    import uuid

    # 1. Create a fresh decision via POST /decide
    decide_resp = api_client.post(
        "/decide",
        json={
            "applicant_id": "APP-0011",
            "requested_amount": 18000.0,
            "document_refs": ["payslip:APP-0011"],
        },
        headers={
            "X-Decision-Date": "2026-03-10T11:00:00Z",
            "Idempotency-Key": f"override-test-decision-{uuid.uuid4()}",
        },
    )
    assert decide_resp.status_code == 200, decide_resp.text
    decision_data = decide_resp.json()
    decision_id = decision_data["decision_id"]
    original_outcome = decision_data["outcome"]
    new_outcome = "APPROVE" if original_outcome != "APPROVE" else "DECLINE"

    # Capture byte-level state of decisions and decision_snapshots before override
    with psycopg.connect(prepared_db, connect_timeout=5) as conn:
        dec_bytes_before, snap_bytes_before = _fetch_decision_and_snapshots_bytes(conn, decision_id)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM overrides WHERE decision_id = %s;", (decision_id,))
            overrides_before = int(cur.fetchone()[0])
    assert overrides_before == 0

    # 2. Verify reason < 20 chars is rejected with 422 and writes nothing
    short_resp = api_client.post(
        "/overrides",
        json={
            "decision_id": decision_id,
            "new_outcome": new_outcome,
            "reason": "Too short reason",
            "actor_role": "senior_credit_underwriter",
        },
    )
    assert short_resp.status_code == 422

    padded_short_resp = api_client.post(
        "/overrides",
        json={
            "decision_id": decision_id,
            "new_outcome": new_outcome,
            "reason": "   short_padded     ",
            "actor_role": "senior_credit_underwriter",
        },
    )
    assert padded_short_resp.status_code == 422

    # 3. Record valid override (>= 20 chars) via POST /overrides
    override_reason = (
        "Verified supplemental savings contract and co-signer guarantee clearing manual review."
    )
    assert len(override_reason) >= 20
    actor_role = "admin"  # recorded from the credential (the open-demo admin), whatever the body claims

    ov_resp = api_client.post(
        "/overrides",
        json={
            "decision_id": decision_id,
            "new_outcome": new_outcome,
            "reason": override_reason,
        },
    )
    assert ov_resp.status_code == 200, ov_resp.text
    ov_body = ov_resp.json()
    assert ov_body["decision_id"] == decision_id
    assert ov_body["new_outcome"] == new_outcome
    assert ov_body["reason"] == override_reason
    assert ov_body["actor_role"] == actor_role
    assert isinstance(ov_body["override_id"], int)
    assert isinstance(ov_body["created_at"], str) and ov_body["created_at"]

    # 4. Verify decisions row and all decision_snapshots rows are byte-identical after override
    with psycopg.connect(prepared_db, connect_timeout=5) as conn:
        dec_bytes_after, snap_bytes_after = _fetch_decision_and_snapshots_bytes(conn, decision_id)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM overrides WHERE decision_id = %s;", (decision_id,))
            overrides_after = int(cur.fetchone()[0])

        assert overrides_after == overrides_before + 1
        assert dec_bytes_after == dec_bytes_before
        assert len(snap_bytes_after) == len(snap_bytes_before)
        for before_b, after_b in zip(snap_bytes_before, snap_bytes_after, strict=True):
            assert after_b == before_b

        # 5. Verify service.get_decision returns both the original outcome and the override
        svc_decision = decision_service.get_decision(conn, decision_id)
        assert svc_decision["outcome"] == original_outcome
        assert svc_decision["original_outcome"] == original_outcome
        assert svc_decision["initial_outcome"] == original_outcome
        assert svc_decision["effective_outcome"] == new_outcome
        assert svc_decision["override"] is not None
        assert svc_decision["override"]["new_outcome"] == new_outcome
        assert svc_decision["override"]["reason"] == override_reason
        assert svc_decision["override"]["actor_role"] == actor_role
        assert svc_decision["human_override"] is not None
        assert svc_decision["human_override"]["new_outcome"] == new_outcome

    # 6. Verify HTTP GET /decisions/{decision_id} returns both original outcome and override
    get_resp = api_client.get(f"/decisions/{decision_id}")
    assert get_resp.status_code == 200, get_resp.text
    http_decision = get_resp.json()
    assert http_decision["outcome"] == original_outcome
    assert http_decision["original_outcome"] == original_outcome
    assert http_decision["initial_outcome"] == original_outcome
    assert http_decision["effective_outcome"] == new_outcome
    assert http_decision["override"]["new_outcome"] == new_outcome
    assert http_decision["override"]["reason"] == override_reason
    assert http_decision["override"]["actor_role"] == actor_role

    # 7. Verify recon_agent.tools.get_decision returns both original outcome and override
    tool_decision = agent_get_decision(decision_id, db_url=prepared_db)
    assert tool_decision["outcome"] == original_outcome
    assert tool_decision["original_outcome"] == original_outcome
    assert tool_decision["initial_outcome"] == original_outcome
    assert tool_decision["effective_outcome"] == new_outcome
    assert tool_decision["override"]["new_outcome"] == new_outcome
    assert tool_decision["override"]["reason"] == override_reason
    assert tool_decision["override"]["actor_role"] == actor_role
    assert tool_decision["human_override"]["new_outcome"] == new_outcome
    assert tool_decision["human_override"]["reason"] == override_reason
    assert tool_decision["human_override"]["actor_role"] == actor_role




def test_write_endpoints_require_api_key_when_configured(
    api_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With DT_API_KEY set, POST /decide and /overrides reject missing/invalid X-API-Key (401); reads need a key too."""
    monkeypatch.setenv("DT_API_KEY", "secret-test-key")
    body = {"applicant_id": "APP-0001"}
    assert api_client.post("/decide", json=body).status_code == 401
    assert api_client.post("/decide", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
    ov = {
        "decision_id": str(uuid.uuid4()),
        "new_outcome": "APPROVE",
        "reason": "Twenty or more characters of reason.",
        "actor_role": "credit_supervisor",
    }
    assert api_client.post("/overrides", json=ov).status_code == 401
    assert api_client.get("/decisions?limit=1").status_code == 401
    assert api_client.get("/decisions?limit=1", headers={"X-API-Key": "secret-test-key"}).status_code == 200
    # Correct key passes the auth gate (404 = decision not found, not 401)
    ok = api_client.post("/overrides", json=ov, headers={"X-API-Key": "secret-test-key"})
    assert ok.status_code == 404

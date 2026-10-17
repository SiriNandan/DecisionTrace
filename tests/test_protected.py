"""Protected evidence: encrypted at rest, readable by the right people, and erasable without breaking the record."""

from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend import crypto
from backend.auth import tenant_connection
from backend.crypto import (
    ERASED_PAYLOAD, LocalKeyProvider, Reveal, decrypt_payload, encrypt_payload, erase_decision, is_encrypted, rotate_tenant,
)
from backend.merkle import verify_proof
from backend.quality import append_correction
from backend.sandbox import provision_tenants
from backend.tenants import set_encryption
from decision_api.app import create_app
from recon_agent import router as recon_router
from recon_agent.tools import ToolSession

SECRET_REASON = "Debt is high compared with income."
INCOME = 31337


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def lender(encrypt: bool = True) -> dict[str, Any]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 1, prefix=f"enc-{uuid.uuid4().hex[:5]}", encrypt=encrypt)[0]


def envelope(ext: str, income: float = INCOME, subject: str | None = None) -> dict[str, Any]:
    ts = "2026-09-01T10:00:00+00:00"
    return {"schema_version": 1, "external_id": ext, "subject_ref": subject or f"s-{ext}", "decided_at": ts, "outcome": "DECLINE", "score": 0.4,
            "model_version": "m", "policy_version": "p", "reasons": [{"code": "R", "text": SECRET_REASON}],
            "evidence": [{"evidence_type": "application", "evidence_ref": f"application:{ext}", "source_id": "los", "source_timestamp": ts,
                          "payload": {"income_annual": income, "requested_amount": 5000}},
                         {"evidence_type": "bureau", "evidence_ref": f"bureau:{ext}", "source_id": "b", "source_timestamp": ts,
                          "payload": {"credit_score": 640}}]}


def post(client: TestClient, t: dict[str, Any], ext: str, **kw: Any) -> str:
    env = envelope(ext, **kw)
    r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": ext})
    assert r.status_code in (200, 201), r.text
    return r.json()["decision_id"]


def owner() -> psycopg.Connection:
    return psycopg.connect(os.environ["DB_URL"])


def detail(client: TestClient, t: dict[str, Any], dec: str) -> dict[str, Any]:
    r = client.get(f"/decisions/{dec}", headers=h(t["keys"]["lender_analyst"]))
    assert r.status_code == 200, r.text
    return r.json()


# ---------- the building blocks ----------

def test_encryption_round_trips_and_is_tied_to_what_it_protects() -> None:
    dek = os.urandom(32)
    env = encrypt_payload(dek, "dec|app:1", {"income": INCOME})
    assert is_encrypted(env) and "income" not in json.dumps(env) and str(INCOME) not in json.dumps(env)
    assert decrypt_payload(dek, "dec|app:1", env) == {"income": INCOME}
    assert encrypt_payload(dek, "dec|app:1", {"income": INCOME}) != env  # a fresh nonce each time
    with pytest.raises(Exception):
        decrypt_payload(dek, "dec|app:2", env)  # moved to another item
    with pytest.raises(Exception):
        decrypt_payload(os.urandom(32), "dec|app:1", env)


def test_fingerprints_are_keyed_salted_and_tied_to_the_item() -> None:
    f = crypto.commit_for
    base = f("lender-a", "d1", "app:1", "salt1", {"x": 1})
    assert base == f("lender-a", "d1", "app:1", "salt1", {"x": 1})            # same inputs, same fingerprint
    assert len({base, f("lender-b", "d1", "app:1", "salt1", {"x": 1}), f("lender-a", "d2", "app:1", "salt1", {"x": 1}),
                f("lender-a", "d1", "app:2", "salt1", {"x": 1}), f("lender-a", "d1", "app:1", "salt2", {"x": 1}),
                f("lender-a", "d1", "app:1", "salt1", {"x": 2})}) == 6          # lender, decision, item, salt and content all matter
    assert len(base) == 64 and base != __import__("backend.integrity", fromlist=["x"]).payload_sha256({"x": 1})  # not a plain hash
    dek = os.urandom(32)
    env1, c1 = crypto.seal_payload("lender-a", "d1", "app:1", dek, {"x": 1})
    env2, c2 = crypto.seal_payload("lender-a", "d1", "app:1", dek, {"x": 1})
    assert c1 != c2 and env1 != env2  # the same content sealed twice looks different, so a fingerprint can't be matched by guessing


def test_a_wrapped_key_only_opens_for_its_own_lender_version_and_decision() -> None:
    p = LocalKeyProvider()
    dek = os.urandom(32)
    wrapped = p.wrap("t1", 1, "d1", dek)
    assert p.unwrap("t1", 1, "d1", wrapped) == dek
    for args in (("t2", 1, "d1"), ("t1", 2, "d1"), ("t1", 1, "d2")):
        with pytest.raises(Exception):
            p.unwrap(*args, wrapped)


def test_the_master_key_is_required_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())
    assert len(crypto.master_key()) == 32
    monkeypatch.setenv("DT_MASTER_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        crypto.master_key()
    monkeypatch.delenv("DT_MASTER_KEY")
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError):
        crypto.master_key()


# ---------- a protected lender, end to end ----------

def test_nothing_readable_is_stored_but_everything_works_for_the_lender(client: TestClient) -> None:
    t = lender()
    dec = post(client, t, "P-1")
    with owner() as conn:
        raw = conn.execute("SELECT payload::text FROM decision_snapshots WHERE decision_id = %s", (dec,)).fetchall()
        flag = conn.execute("SELECT encrypted FROM decisions WHERE decision_id = %s", (dec,)).fetchone()[0]
        wraps = conn.execute("SELECT count(*) FROM data_key_wraps WHERE decision_id = %s", (dec,)).fetchone()[0]
    stored = " ".join(r[0] for r in raw)
    for needle in (str(INCOME), "income_annual", SECRET_REASON, "credit_score", "s-P-1"):
        assert needle not in stored, needle
    assert flag is True and wraps == 1 and stored.count("_enc") == 3

    rec = detail(client, t, dec)
    assert rec["chain_valid"] is True and rec["status"] == "verified"
    shown = {s["evidence_type"]: s["payload"] for s in rec["snapshots"]}
    assert shown["application"]["income_annual"] == INCOME and shown["lender_decision"]["reasons"][0]["text"] == SECRET_REASON
    assert client.get(f"/decisions/{dec}/verify", headers=h(t["keys"]["auditor"])).json()["chain_valid"] is True
    rep = client.post(f"/reconstruct/{dec}", headers=h(t["keys"]["lender_analyst"])).json()
    assert rep["status"] == "ok" and rep["chain_verified"] is True and rep["principal_reasons"] == [SECRET_REASON]


def test_replays_and_conflicts_still_work_on_protected_records(client: TestClient) -> None:
    t = lender()
    post(client, t, "P-2")
    again = client.post("/v1/decisions", json=envelope("P-2"), headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": "P-2"})
    assert again.status_code == 200 and again.json()["replayed"] is True
    changed = client.post("/v1/decisions", json=envelope("P-2", income=1), headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": "P-2"})
    assert changed.status_code == 409


def test_the_checks_signals_corrections_and_seals_all_read_through_the_protection(client: TestClient) -> None:
    t = lender()
    analyst = h(t["keys"]["lender_analyst"])
    bad = post(client, t, "P-3", income=-50)
    ids = [post(client, t, f"P-R{i}", subject="s-repeat") for i in range(3)]
    run = client.post("/v1/quality/run", json={}, headers=analyst).json()
    assert run["new_findings"] >= 1
    assert any(f["decision_id"] == bad and f["rule_id"] == "income_not_positive" for f in client.get("/v1/quality/findings", headers=analyst).json())
    assert client.post("/v1/signals/run", json={}, headers=analyst).json()["new_signals"] >= 1
    assert any(s["signal_type"] == "applicant_velocity" for s in client.get("/v1/signals", headers=analyst).json())

    with tenant_connection(t["tenant_id"]) as conn:
        append_correction(conn, tenant_id=t["tenant_id"], decision_id=bad, evidence_ref="application:P-3", field_name="income_annual",
                          new_value=41000.0, reason="Payslip shows more.", source="person", role="lender_analyst", credential_id=None, finding_id=None)
    with owner() as conn:
        stored = conn.execute("SELECT payload::text FROM corrections WHERE decision_id = %s", (bad,)).fetchone()[0]
    assert "41000" not in stored and "Payslip" not in stored and "new_value" not in stored  # corrections are protected too
    rec = detail(client, t, bad)
    assert [(c["field"], c["old_value"], c["new_value"], c["reason"]) for c in rec["corrections"]] == [("income_annual", -50, 41000.0, "Payslip shows more.")]
    assert rec["corrections_valid"] is True

    assert client.post("/v1/seals/run", headers=analyst).json()["sealed"] is True
    proof = client.get(f"/v1/decisions/{ids[0]}/seal", headers=analyst).json()
    assert verify_proof(proof)[0] and client.get("/v1/seals/verify", headers=analyst).json()["ok"] is True


def test_swapping_or_damaging_protected_content_is_caught(client: TestClient) -> None:
    t = lender()
    a, b = post(client, t, "P-4"), post(client, t, "P-5")
    assert detail(client, t, a)["chain_valid"] is True
    with owner() as conn:  # an insider puts another decision's (perfectly valid) ciphertext in its place
        donor = conn.execute("SELECT payload FROM decision_snapshots WHERE decision_id = %s AND evidence_type = 'application'", (b,)).fetchone()[0]
        conn.execute("UPDATE decision_snapshots SET payload = %s::jsonb WHERE decision_id = %s AND evidence_type = 'application'", (json.dumps(donor), a))
        conn.commit()
    rec = detail(client, t, a)
    assert rec["chain_valid"] is False and "fingerprint mismatch" in (rec["broken_reason"] or "")

    c = post(client, t, "P-6")
    with owner() as conn:
        conn.execute("UPDATE decision_snapshots SET payload = jsonb_set(payload, '{_enc,ct}', to_jsonb('AAAA'::text)) WHERE decision_id = %s AND evidence_type = 'bureau'", (c,))
        conn.commit()
    assert detail(client, t, c)["chain_valid"] is False
    assert client.get(f"/decisions/{c}/verify", headers=h(t["keys"]["auditor"])).json()["chain_valid"] is False


# ---------- erasing ----------

def test_erasing_destroys_the_content_but_not_the_record(client: TestClient) -> None:
    t = lender()
    analyst = h(t["keys"]["lender_analyst"])
    gone, kept = post(client, t, "E-1"), post(client, t, "E-2")
    with tenant_connection(t["tenant_id"]) as conn:
        append_correction(conn, tenant_id=t["tenant_id"], decision_id=gone, evidence_ref="application:E-1", field_name="income_annual",
                          new_value=99999.0, reason="Payslip.", source="person", role="lender_analyst", credential_id=None, finding_id=None)
    client.post("/v1/seals/run", headers=analyst)
    before = detail(client, t, gone)

    with owner() as conn:
        out = erase_decision(conn, gone, "Customer asked for erasure (ticket 123)", "operator-1")
        rows_left = conn.execute("SELECT count(*) FROM decision_snapshots WHERE decision_id = %s", (gone,)).fetchone()[0]
        wraps = conn.execute("SELECT count(*) FROM data_key_wraps WHERE decision_id = %s", (gone,)).fetchone()[0]
        why = conn.execute("SELECT erased_by, reason FROM erasures WHERE decision_id = %s", (gone,)).fetchone()
    assert out["keys_destroyed"] == 1 and rows_left == 3 and wraps == 0 and why == ("operator-1", "Customer asked for erasure (ticket 123)")

    after = detail(client, t, gone)
    assert after["chain_valid"] is True and after["chain_head"] == before["chain_head"]   # the record itself is untouched
    assert after["status"] == "evidence_unavailable"
    assert all(s["payload"] == ERASED_PAYLOAD for s in after["snapshots"])
    assert str(INCOME) not in json.dumps(after) and SECRET_REASON not in json.dumps(after) and "99999" not in json.dumps(after)
    assert client.get(f"/decisions/{gone}/verify", headers=analyst).json()["chain_valid"] is True

    rep = client.post(f"/reconstruct/{gone}", headers=analyst).json()
    assert rep["status"] == "evidence_unavailable" and rep["completeness"] < 1.0 and rep["missing_evidence"] and rep["reasons"] == []
    assert rep["chain_verified"] is True

    # the seals made earlier still hold, and the lender's other decisions are untouched
    assert client.get("/v1/seals/verify", headers=analyst).json()["ok"] is True
    assert detail(client, t, kept)["snapshots"][0]["payload"] != ERASED_PAYLOAD
    assert client.post("/v1/quality/run", json={"decision_ids": [gone]}, headers=analyst).json()["new_findings"] == 0  # no false "missing" flags

    # not even someone holding the master key can read it: the wrapped keys no longer exist
    with owner() as conn:
        ct = conn.execute("SELECT payload FROM decision_snapshots WHERE decision_id = %s AND evidence_type = 'application'", (gone,)).fetchone()[0]
        assert Reveal(conn).payload(gone, "application:E-1", ct) is None
    session = ToolSession(db_url=os.environ["DB_URL"])
    snaps = session.get_snapshot(gone, "all")["snapshots"]
    assert snaps and all(s.get("payload") is None or s["payload"].get("status") == "erased_by_retention" for s in snaps)


def test_erasing_wins_even_when_the_clocks_disagree(client: TestClient) -> None:
    t = lender()
    dec = post(client, t, "E-5")
    with owner() as conn:  # the machine that recorded the evidence is hours ahead of the database
        conn.execute("UPDATE evidence_status SET changed_at = now() + interval '5 hours' WHERE decision_id = %s", (dec,))
        conn.commit()
        erase_decision(conn, dec, "clock skew test", "ci")
    after = detail(client, t, dec)
    assert after["status"] == "evidence_unavailable" and all(s["payload"] == ERASED_PAYLOAD for s in after["snapshots"])


def test_the_erase_command_refuses_what_it_cannot_do() -> None:
    t = lender(encrypt=False)
    with owner() as conn:
        with pytest.raises(LookupError):
            erase_decision(conn, str(uuid.uuid4()), "x", "me")
    # a plain (unprotected) decision has no key to destroy
    client = TestClient(create_app())
    dec = post(client, t, "E-3")
    with owner() as conn:
        with pytest.raises(ValueError):
            erase_decision(conn, dec, "x", "me")


def test_the_command_line_wrapper(client: TestClient) -> None:
    t = lender()
    dec = post(client, t, "E-4")
    assert crypto.main(["erase", dec, "--reason", "test", "--by", "ci"]) == 0
    assert detail(client, t, dec)["status"] == "evidence_unavailable"


# ---------- rotation, switching on and off, and who can touch the keys ----------

def test_keys_can_be_rotated_without_losing_access(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = lender()
    dec = post(client, t, "K-1")
    monkeypatch.setenv("DT_KEK_VERSION", "2")
    later = post(client, t, "K-2")
    with owner() as conn:
        versions = {r[0]: r[1] for r in conn.execute("SELECT decision_id::text, kek_version FROM data_key_wraps WHERE tenant_id = %s", (t["tenant_id"],)).fetchall()}
        assert versions == {dec: 1, later: 2}
        out = rotate_tenant(conn, t["tenant_id"], 2, drop_old=True)
        left = conn.execute("SELECT kek_version, count(*) FROM data_key_wraps WHERE tenant_id = %s GROUP BY 1", (t["tenant_id"],)).fetchall()
    assert out["rewrapped"] == 1 and out["old_wraps_dropped"] == 1 and left == [(2, 2)]
    assert {x["evidence_type"]: x["payload"] for x in detail(client, t, dec)["snapshots"]}["application"]["income_annual"] == INCOME
    with owner() as conn:
        assert erase_decision(conn, dec, "after rotation", "ci")["keys_destroyed"] == 1


def test_switching_protection_on_and_off_only_affects_new_decisions(client: TestClient) -> None:
    t = lender(encrypt=False)
    plain = post(client, t, "S-1")
    with owner() as conn:
        set_encryption(conn, t["tenant_id"], True)
    protected = post(client, t, "S-2")
    with owner() as conn:
        set_encryption(conn, t["tenant_id"], False)
    plain_again = post(client, t, "S-3")
    with owner() as conn:
        flags = {str(r[0]): r[1] for r in conn.execute("SELECT decision_id, encrypted FROM decisions WHERE tenant_id = %s", (t["tenant_id"],)).fetchall()}
    assert flags == {plain: False, protected: True, plain_again: False}
    for dec in (plain, protected, plain_again):
        assert detail(client, t, dec)["snapshots"][0]["payload"] and detail(client, t, dec)["chain_valid"] is True


def test_the_api_role_cannot_destroy_or_read_other_lenders_keys(client: TestClient) -> None:
    a, b = lender(), lender()
    dec = post(client, a, "X-1")
    with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
        for stmt in ("DELETE FROM data_key_wraps", "UPDATE data_key_wraps SET kek_version = 9", "INSERT INTO erasures (tenant_id, decision_id, erased_by, reason) "
                     f"VALUES ('{a['tenant_id']}', '{dec}', 'x', 'x')", "DELETE FROM erasures"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)
            conn.rollback()
    with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (b["tenant_id"],))
        assert conn.execute("SELECT count(*) FROM data_key_wraps WHERE decision_id = %s", (dec,)).fetchone()[0] == 0
    assert client.get(f"/decisions/{dec}", headers=h(b["keys"]["lender_analyst"])).status_code == 404


# ---------- things the independent review found ----------

def test_an_unprotected_decision_cannot_be_hidden_by_swapping_in_something_that_looks_encrypted(client: TestClient) -> None:
    t = lender(encrypt=False)
    dec = post(client, t, "R-1")
    assert detail(client, t, dec)["chain_valid"] is True
    stub = {"_enc": {"v": 1, "alg": "A256GCM", "nonce": "AAAA", "ct": "AAAA"}}
    for fake in (stub, {"_enc": 1}):
        with owner() as conn:
            conn.execute("UPDATE decision_snapshots SET payload = %s::jsonb WHERE decision_id = %s AND evidence_type = 'bureau'", (json.dumps(fake), dec))
            conn.commit()
        rec = detail(client, t, dec)
        assert rec["chain_valid"] is False and rec["status"] == "tamper_detected", fake
        assert client.get(f"/decisions/{dec}/verify", headers=h(t["keys"]["auditor"])).json()["chain_valid"] is False


def test_evidence_sent_in_cannot_use_the_reserved_name(client: TestClient) -> None:
    t = lender(encrypt=False)
    env = envelope("R-2")
    env["evidence"][0]["payload"] = {"_enc": {"v": 1}, "income_annual": 1}
    r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": "R-2"})
    assert r.status_code == 422 and "reserved" in r.text


def test_a_protected_decision_does_not_accept_plaintext_in_place_of_its_ciphertext(client: TestClient) -> None:
    t = lender()
    dec = post(client, t, "R-3")
    with owner() as conn:
        conn.execute("UPDATE decision_snapshots SET payload = %s::jsonb WHERE decision_id = %s AND evidence_type = 'bureau'", (json.dumps({"credit_score": 999}), dec))
        conn.commit()
    assert detail(client, t, dec)["chain_valid"] is False


def test_keys_that_vanish_or_cannot_be_opened_are_a_failure_and_only_a_recorded_erasure_is_a_removal(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = lender()
    a, b = post(client, t, "R-4"), post(client, t, "R-5")
    with owner() as conn:  # someone deletes the wrapped keys without the erase command: nothing on record says why
        conn.execute("DELETE FROM data_key_wraps WHERE decision_id = %s", (a,))
        conn.commit()
    rec = detail(client, t, a)
    assert rec["chain_valid"] is False and rec["status"] == "tamper_detected"
    assert {s["payload"]["status"] for s in rec["snapshots"]} == {"key_unavailable"}  # not claimed to be a deliberate removal
    assert client.post(f"/reconstruct/{a}", headers=h(t["keys"]["lender_analyst"])).json()["chain_verified"] is False

    monkeypatch.setenv("DT_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())  # wrong master secret: nothing can be opened
    wrong = detail(client, t, b)
    assert wrong["chain_valid"] is False and wrong["status"] == "tamper_detected"
    monkeypatch.undo()
    assert detail(client, t, b)["chain_valid"] is True  # and it was only the configuration

    with owner() as conn:
        erase_decision(conn, b, "on record", "ci")
    ok = detail(client, t, b)
    assert ok["chain_valid"] is True and {s["payload"]["status"] for s in ok["snapshots"]} == {"erased_by_retention"}


def test_what_is_derived_from_protected_content_is_protected_too_and_goes_when_the_content_does(client: TestClient) -> None:
    t = lender()
    analyst = h(t["keys"]["lender_analyst"])
    dec = post(client, t, "R-6", income=-4242)
    client.post("/v1/quality/run", json={"decision_ids": [dec]}, headers=analyst)
    found = [f for f in client.get("/v1/quality/findings", headers=analyst).json() if f["decision_id"] == dec]
    assert found and any("-4,242" in f["message"] or "4,242" in f["message"] for f in found)  # people can read it through the API

    with owner() as conn:
        rows = conn.execute("SELECT message, observed::text, suggested_fix::text, ai_note, enc_detail::text FROM quality_findings WHERE decision_id = %s", (dec,)).fetchall()
    stored = json.dumps(rows)
    assert "4,242" not in stored and "4242" not in stored and "isn't a positive amount" not in stored  # nothing readable at rest
    assert all(r[0] == "The details of this item are protected." and r[4] for r in rows)

    fid = next(f["finding_id"] for f in found if f["rule_id"] == "income_not_positive")
    assert client.post(f"/v1/quality/findings/{fid}/resolve", json={"action": "dismiss", "note": "It is the customer's own figure, 4242."}, headers=analyst).status_code == 200
    client.post("/overrides", json={"decision_id": dec, "new_outcome": "APPROVE", "reason": "Customer phoned from 07700 900123 to explain.", "actor_role": "underwriter"}, headers=analyst)
    with owner() as conn:
        erase_decision(conn, dec, "erasure request", "ci")
        left = conn.execute(
            "SELECT (SELECT string_agg(message || observed::text || coalesce(enc_detail::text, ''), ' ') FROM quality_findings WHERE decision_id = %s), "
            "(SELECT string_agg(note, ' ') FROM finding_reviews WHERE finding_id IN (SELECT finding_id FROM quality_findings WHERE decision_id = %s)), "
            "(SELECT string_agg(reason, ' ') FROM overrides WHERE decision_id = %s)", (dec, dec, dec)).fetchone()
    for text in left:
        assert text and "4242" not in text and "07700" not in text and "removed under an erasure request" in text.replace("[", "").replace("]", "") or "removed" in text
    after = [f for f in client.get("/v1/quality/findings?status=all", headers=analyst).json() if f["decision_id"] == dec]
    assert after and all(f["message"] == "The details of this item were removed." or "removed" in f["message"] for f in after)


def test_reports_for_protected_decisions_are_never_kept_and_an_erasure_shows_at_once(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from recon_agent import router

    t = lender()
    analyst = h(t["keys"]["lender_analyst"])
    dec, plain = post(client, t, "R-7"), None
    calls: list[str] = []
    real = router.reconstruct_decision
    monkeypatch.setattr(router, "reconstruct_decision", lambda d, **k: (calls.append(d), real(d, **k))[1])
    first = client.post(f"/reconstruct/{dec}", headers=analyst).json()
    client.post(f"/reconstruct/{dec}", headers=analyst)
    assert len(calls) == 2 and first["principal_reasons"] == [SECRET_REASON]  # asked twice: nothing was cached
    with owner() as conn:
        erase_decision(conn, dec, "now", "ci")
    after = client.post(f"/reconstruct/{dec}", headers=analyst).json()      # same client, no cache reset
    assert after["status"] == "evidence_unavailable" and SECRET_REASON not in json.dumps(after)

    other = lender(encrypt=False)  # unprotected decisions are still cached, as before
    pd = post(client, other, "R-8")
    client.post(f"/reconstruct/{pd}", headers=h(other["keys"]["lender_analyst"]))
    client.post(f"/reconstruct/{pd}", headers=h(other["keys"]["lender_analyst"]))
    assert calls.count(pd) == 1


def test_a_destroyed_key_cannot_come_back_through_a_rotation_and_keys_cannot_be_lost_by_one(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = lender()
    gone, kept = post(client, t, "R-9"), post(client, t, "R-10")
    with owner() as conn:
        erase_decision(conn, gone, "x", "ci")
        conn.commit()
        out = rotate_tenant(conn, t["tenant_id"], 2)
        conn.commit()
        wraps = {str(r[0]): r[1] for r in conn.execute("SELECT decision_id, kek_version FROM data_key_wraps WHERE tenant_id = %s ORDER BY kek_version", (t["tenant_id"],)).fetchall()}
        assert out["rewrapped"] == 1 and gone not in wraps  # the erased decision is not given a new key
        with pytest.raises(ValueError, match="DT_KEK_VERSION"):
            rotate_tenant(conn, t["tenant_id"], 2, drop_old=True)  # new decisions would still get version 1 keys: refuse
        conn.rollback()
        monkeypatch.setenv("DT_KEK_VERSION", "2")
        late = post(client, t, "R-11")
        done = rotate_tenant(conn, t["tenant_id"], 2, drop_old=True)
        left = conn.execute("SELECT decision_id::text, kek_version FROM data_key_wraps WHERE tenant_id = %s", (t["tenant_id"],)).fetchall()
    assert done["old_wraps_dropped"] == 1 and sorted(d for d, _ in left) == sorted([kept, late]) and {v for _, v in left} == {2}
    assert detail(client, t, kept)["chain_valid"] is True and detail(client, t, late)["chain_valid"] is True


def test_replaying_a_request_for_a_decision_whose_content_was_removed_is_refused_not_matched(client: TestClient) -> None:
    t = lender()
    dec = post(client, t, "R-12")
    with owner() as conn:
        erase_decision(conn, dec, "x", "ci")
    r = client.post("/v1/decisions", json=envelope("R-12"), headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": "R-12"})
    assert r.status_code == 409

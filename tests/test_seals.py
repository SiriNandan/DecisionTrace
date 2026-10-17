"""Sealing: Merkle proofs, signed batches, and noticing changes made after the fact."""

from __future__ import annotations

import base64
import copy
import json
import os
import uuid
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.auth import tenant_connection
from backend.integrity import canonical_json
from backend.merkle import (
    FilePublisher, GENESIS_ROOT, Signer, head_message, inclusion_path, leaf_data_bytes, leaf_hash, load_signer, node_hash,
    root_from_path, sign_head, tree_root, verify_head, verify_proof,
)
from backend.quality import append_correction
from backend.sandbox import provision_tenants
from backend.seals import proof_for, seal_batch, verify_seals
from decision_api.app import create_app
from recon_agent import router as recon_router


def hashes(n: int) -> list[bytes]:
    return [leaf_hash(f"item-{i}".encode()) for i in range(n)]


# ---------- the tree ----------

def test_the_tree_matches_a_hand_computed_shape() -> None:
    a, b, c, d, e = hashes(5)
    assert tree_root([a]) == a
    assert tree_root([a, b]) == node_hash(a, b)
    assert tree_root([a, b, c]) == node_hash(node_hash(a, b), c)            # split at the largest power of two below 3
    assert tree_root([a, b, c, d, e]) == node_hash(node_hash(node_hash(a, b), node_hash(c, d)), e)
    assert leaf_hash(b"x") != node_hash(b"", b"x")  # a leaf can never be passed off as an inner node


@pytest.mark.parametrize("n", list(range(1, 34)))
def test_every_leaf_of_every_size_has_a_proof_that_reaches_the_root_and_nothing_else_does(n: int) -> None:
    leaves = hashes(n)
    root = tree_root(leaves)
    for i in range(n):
        path = inclusion_path(leaves, i)
        assert root_from_path(leaves[i], path) == root
        assert len(path) <= n.bit_length()
        other = leaf_hash(b"not in the tree")
        assert root_from_path(other, path) != root
        if path:
            side, sib = path[0]
            assert root_from_path(leaves[i], [(side, leaf_hash(b"forged").hex())] + path[1:]) != root
    with pytest.raises(IndexError):
        inclusion_path(leaves, n)


# ---------- signing ----------

def test_a_signed_head_checks_out_and_any_change_breaks_it() -> None:
    signer = load_signer()
    head = {"tenant_id": "t", "seq": 1, "root": "ab" * 32, "leaf_count": 3, "prev_root": GENESIS_ROOT,
            "signed_at": "2026-10-01T00:00:00+00:00", "key_id": signer.key_id}
    head["signature"] = sign_head(signer, head)
    assert verify_head(head, signer.public_b64)
    for field, value in (("root", "cd" * 32), ("seq", 2), ("leaf_count", 4), ("tenant_id", "u"), ("prev_root", "x"), ("signed_at", "2027-01-01")):
        assert not verify_head({**head, field: value}, signer.public_b64), field
    other = Signer(__import__("cryptography.hazmat.primitives.asymmetric.ed25519", fromlist=["x"]).Ed25519PrivateKey.generate(), "other")
    assert not verify_head(head, other.public_b64)
    assert not verify_head({**head, "signature": "!!not base64!!"}, signer.public_b64)


def test_the_signing_key_comes_from_the_environment_and_is_required_outside_development(monkeypatch: pytest.MonkeyPatch) -> None:
    seed = base64.b64encode(bytes(range(32))).decode()
    monkeypatch.setenv("DT_SEAL_KEY", seed)
    assert load_signer().public_b64 == load_signer().public_b64
    monkeypatch.setenv("DT_SEAL_KEY", base64.b64encode(b"short").decode())
    with pytest.raises(ValueError):
        load_signer()
    monkeypatch.delenv("DT_SEAL_KEY")
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(RuntimeError):
        load_signer()


# ---------- through the database and the API ----------

def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def new_lender() -> dict[str, Any]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 1, prefix=f"seal-{uuid.uuid4().hex[:5]}")[0]


def envelope(ext: str) -> dict[str, Any]:
    ts = "2026-09-01T10:00:00+00:00"
    return {"schema_version": 1, "external_id": ext, "subject_ref": f"s-{ext}", "decided_at": ts, "outcome": "DECLINE", "score": 0.4,
            "model_version": "m", "policy_version": "p", "reasons": [{"code": "R", "text": "Debt is high compared with income."}],
            "evidence": [{"evidence_type": "application", "evidence_ref": f"application:{ext}", "source_id": "los", "source_timestamp": ts,
                          "payload": {"income_annual": 30000 + len(ext), "requested_amount": 5000}}]}


def add(client: TestClient, t: dict[str, Any], n: int, prefix: str = "D") -> list[str]:
    out = []
    for i in range(n):
        env = envelope(f"{prefix}-{i}")
        r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": env["external_id"]})
        assert r.status_code in (200, 201), r.text
        out.append(r.json()["decision_id"])
    return out


def seal(client: TestClient, t: dict[str, Any]) -> dict[str, Any]:
    r = client.post("/v1/seals/run", headers=h(t["keys"]["lender_analyst"]))
    assert r.status_code == 200, r.text
    return r.json()


def verify(client: TestClient, t: dict[str, Any]) -> dict[str, Any]:
    return client.get("/v1/seals/verify", headers=h(t["keys"]["auditor"])).json()


def test_sealing_makes_batches_that_chain_and_proofs_that_verify_offline(client: TestClient) -> None:
    t = new_lender()
    analyst, auditor = h(t["keys"]["lender_analyst"]), h(t["keys"]["auditor"])
    ids = add(client, t, 5)
    assert client.get(f"/v1/decisions/{ids[0]}/seal", headers=analyst).json()["sealed"] is False

    first = seal(client, t)
    assert first["sealed"] and first["batch"]["seq"] == 1 and first["batch"]["leaf_count"] == 5 and first["batch"]["prev_root"] == GENESIS_ROOT
    assert seal(client, t) == {"sealed": False, "batch": None, "message": "Everything was already sealed.", "not_yet_sealed": 0}

    more = add(client, t, 3, "E")
    second = seal(client, t)["batch"]
    assert second["seq"] == 2 and second["leaf_count"] == 3 and second["prev_root"] == first["batch"]["root"]
    assert [b["seq"] for b in client.get("/v1/seals", headers=auditor).json()] == [2, 1]

    pub = client.get("/v1/seals/public-key").json()  # open: it can only check, never seal
    for dec in ids + more:
        proof = client.get(f"/v1/decisions/{dec}/seal", headers=analyst).json()
        assert proof["sealed"] and proof["leaf"]["decision_id"] == dec
        ok, why = verify_proof(proof, pub["public_key"])
        assert ok, why
        # a proof is useful on its own: nothing in it needs the database, and any change to it is caught
        broken = copy.deepcopy(proof)
        broken["leaf"]["chain_head"] = "0" * 64
        assert verify_proof(broken, pub["public_key"])[0] is False
        wrong_root = copy.deepcopy(proof)
        wrong_root["head"]["root"] = "11" * 32
        assert verify_proof(wrong_root, pub["public_key"])[0] is False
    assert verify(client, t) == {"ok": True, "batches": 2, "records": 8, "checked_published_copy": False, "not_yet_sealed": 0, "problems": []}


def test_the_command_line_checker_says_whether_the_signer_was_checked(client: TestClient, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from backend.merkle import main

    t = new_lender()
    dec = add(client, t, 2)[0]
    seal(client, t)
    proof = client.get(f"/v1/decisions/{dec}/seal", headers=h(t["keys"]["lender_analyst"])).json()
    key = client.get("/v1/seals/public-key").json()["public_key"]
    f = tmp_path / "proof.json"
    f.write_text(json.dumps(proof))
    assert main(["verify-proof", str(f), key]) == 0                    # checked against a key the person got themselves
    assert main(["verify-proof", str(f)]) == 3                         # consistent, but whose seal is it? not claimed
    assert "signer was not checked" in capsys.readouterr().out.split("The proof is valid")[-1]
    fake = copy.deepcopy(proof)
    other_key = base64.b64encode(os.urandom(32)).decode()
    assert main(["verify-proof", str(f), other_key]) == 1               # someone else's key does not vouch for it
    proof["leaf"]["chain_length"] += 1
    f.write_text(json.dumps(proof))
    assert main(["verify-proof", str(f), key]) == 1
    assert verify_proof(fake)[1].startswith("consistent")                # and the library says the same


def test_who_may_seal_and_see_seals(client: TestClient) -> None:
    t = new_lender()
    add(client, t, 1)
    keys = t["keys"]
    assert client.post("/v1/seals/run", headers=h(keys["auditor"])).status_code == 403
    assert client.post("/v1/seals/run", headers=h(keys["lender_ingest"])).status_code == 403
    assert client.get("/v1/seals", headers=h(keys["lender_ingest"])).status_code == 403
    assert client.get("/v1/seals/verify", headers=h(keys["auditor"])).status_code == 200
    assert client.post("/v1/seals/run").status_code == 401
    assert client.post("/v1/seals/run", headers=h(keys["lender_analyst"])).status_code == 200


def test_a_customer_can_get_the_proof_for_their_own_decision_only(client: TestClient) -> None:
    t = new_lender()
    a, b = add(client, t, 2)
    seal(client, t)
    tok = client.post(f"/v1/decisions/{a}/claim-links", json={}, headers=h(t["keys"]["lender_analyst"])).json()["token"]
    mine = client.get(f"/v1/decisions/{a}/seal", headers={"X-Claim-Token": tok}).json()
    assert mine["sealed"] and verify_proof(mine)[0]
    assert client.get(f"/v1/decisions/{b}/seal", headers={"X-Claim-Token": tok}).status_code == 404
    assert client.post("/v1/seals/run", headers={"X-Claim-Token": tok}).status_code == 403
    other = new_lender()
    assert client.get(f"/v1/decisions/{a}/seal", headers=h(other["keys"]["lender_analyst"])).status_code == 404
    assert client.get("/v1/seals", headers=h(other["keys"]["auditor"])).json() == []


def test_corrections_are_sealed_too_and_a_later_correction_is_sealed_again(client: TestClient) -> None:
    t = new_lender()
    dec = add(client, t, 1)[0]
    seal(client, t)
    with tenant_connection(t["tenant_id"]) as conn:
        append_correction(conn, tenant_id=t["tenant_id"], decision_id=dec, evidence_ref="application:D-0", field_name="income_annual",
                          new_value=41000.0, reason="Payslip shows more.", source="person", role="lender_analyst", credential_id=None, finding_id=None)
    sealed = seal(client, t)["batch"]
    assert sealed["seq"] == 2 and sealed["leaf_count"] == 1
    proof = client.get(f"/v1/decisions/{dec}/seal", headers=h(t["keys"]["lender_analyst"])).json()
    assert verify_proof(proof["corrections"])[0] and proof["corrections"]["leaf"]["kind"] == "corrections" and proof["corrections"]["leaf"]["count"] == 1
    assert verify(client, t)["ok"] is True


# ---------- noticing changes made later ----------

def owner() -> psycopg.Connection:
    return psycopg.connect(os.environ["DB_URL"])


def problems(client: TestClient, t: dict[str, Any]) -> list[str]:
    return [p["problem"] for p in verify(client, t)["problems"]]


def sealed_lender(client: TestClient, n: int = 4) -> tuple[dict[str, Any], list[str]]:
    t = new_lender()
    ids = add(client, t, n)
    seal(client, t)
    assert verify(client, t)["ok"]
    return t, ids


def test_rewriting_a_decision_and_its_chain_after_sealing_is_caught(client: TestClient) -> None:
    t, ids = sealed_lender(client)
    with owner() as conn:  # an insider rewrites a decision's chain head so that its own check still passes
        conn.execute("UPDATE decisions SET chain_head = %s WHERE decision_id = %s", ("f" * 64, ids[1]))
        conn.commit()
    assert any(f"Decision {ids[1]} changed after it was sealed" in p for p in problems(client, t))


def test_removing_a_decision_after_sealing_is_caught(client: TestClient) -> None:
    t, ids = sealed_lender(client)
    with owner() as conn:
        conn.execute("DELETE FROM anchor_leaves WHERE decision_id = %s", (ids[2],))  # the owner can remove the leaf...
        conn.commit()
    found = problems(client, t)
    assert any("records but" in p or "no longer add up" in p for p in found)  # ...but then the batch no longer adds up


def test_editing_the_stored_fingerprints_or_root_is_caught_by_the_signature_and_the_arithmetic(client: TestClient) -> None:
    t, ids = sealed_lender(client)
    with owner() as conn:
        conn.execute("UPDATE anchor_leaves SET leaf_data = jsonb_set(leaf_data, '{chain_head}', to_jsonb(%s::text)) WHERE decision_id = %s", ("a" * 64, ids[0]))
        conn.commit()
    found = problems(client, t)
    assert any("fingerprint was altered" in p for p in found) and any(f"Decision {ids[0]} changed" in p for p in found)

    t2, _ = sealed_lender(client)
    with owner() as conn:
        conn.execute("UPDATE anchor_batches SET root = %s WHERE tenant_id = %s", ("e" * 64, t2["tenant_id"]))
        conn.commit()
    found = problems(client, t2)
    assert any("signature on this batch isn't valid" in p for p in found) and any("no longer add up" in p for p in found)


def test_a_rewrite_of_the_whole_batch_still_fails_without_the_signing_key(client: TestClient) -> None:
    t, ids = sealed_lender(client, 3)
    with owner() as conn:
        # change a decision, then recompute and replace the leaf, the root and (without the key) the signature
        conn.execute("UPDATE decisions SET chain_head = %s WHERE decision_id = %s", ("9" * 64, ids[0]))
        new_leaf = {"kind": "decision", "decision_id": ids[0], "chain_length": 1, "chain_head": "9" * 64}
        row = conn.execute("SELECT chain_length FROM decisions WHERE decision_id = %s", (ids[0],)).fetchone()
        new_leaf["chain_length"] = row[0]
        conn.execute("UPDATE anchor_leaves SET leaf_data = %s::jsonb, leaf_hash = %s WHERE decision_id = %s",
                     (json.dumps(new_leaf), leaf_hash(leaf_data_bytes(new_leaf)).hex(), ids[0]))
        hs = [bytes.fromhex(r[0]) for r in conn.execute(
            "SELECT leaf_hash FROM anchor_leaves l JOIN anchor_batches b USING (batch_id) WHERE b.tenant_id = %s ORDER BY leaf_index", (t["tenant_id"],)).fetchall()]
        conn.execute("UPDATE anchor_batches SET root = %s WHERE tenant_id = %s", (tree_root(hs).hex(), t["tenant_id"]))
        conn.commit()
    found = problems(client, t)
    assert found == ["The signature on this batch isn't valid."]  # everything else lines up; only the key can't be faked


def test_what_was_published_outside_the_database_exposes_a_full_rewrite_and_a_deleted_batch(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_SEAL_PUBLISH_DIR", str(tmp_path))
    t = new_lender()
    add(client, t, 3)
    seal(client, t)
    add(client, t, 2, "F")
    seal(client, t)
    result = verify(client, t)
    assert result["ok"] and result["checked_published_copy"] is True
    published, unreadable = FilePublisher(tmp_path).read(t["tenant_id"])
    assert unreadable == 0 and [p["seq"] for p in published] == [1, 2] and all(verify_head(p, load_signer().public_b64) for p in published)

    with owner() as conn:  # the administrator deletes the newest batch and its leaves, leaving no trace in the database
        bid = conn.execute("SELECT batch_id FROM anchor_batches WHERE tenant_id = %s AND seq = 2", (t["tenant_id"],)).fetchone()[0]
        conn.execute("DELETE FROM anchor_leaves WHERE batch_id = %s", (bid,))
        conn.execute("DELETE FROM anchor_batches WHERE batch_id = %s", (bid,))
        conn.commit()
    found = problems(client, t)
    assert found == ["A batch that was published is missing from the database."]

    (tmp_path / f"{t['tenant_id']}.seals.jsonl").write_text("")  # and deleting the published copy is noticed too
    assert any("never published" in p for p in problems(client, t))


def test_a_failed_copy_never_leaves_a_published_head_without_a_record_and_the_next_run_makes_it_good(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    t = new_lender()
    add(client, t, 2)
    real = FilePublisher.publish
    monkeypatch.setattr(FilePublisher, "publish", lambda self, head: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setenv("DT_SEAL_PUBLISH_DIR", str(tmp_path))
    failing = TestClient(create_app(), raise_server_exceptions=False)
    assert failing.post("/v1/seals/run", headers=h(t["keys"]["lender_analyst"])).status_code == 500
    assert [b["seq"] for b in client.get("/v1/seals", headers=h(t["keys"]["auditor"])).json()] == [1]  # the seal itself is safe
    assert FilePublisher(tmp_path).read(t["tenant_id"]) == ([], 0)                                      # nothing half-published
    assert any("never published" in p for p in problems(client, t))                                   # and the gap is visible

    monkeypatch.setattr(FilePublisher, "publish", real)
    again = seal(client, t)                                                                            # nothing new to seal...
    assert again["sealed"] is False
    heads, bad = FilePublisher(tmp_path).read(t["tenant_id"])                                          # ...but the copy was made good
    assert [h_["seq"] for h_ in heads] == [1] and bad == 0
    assert verify(client, t)["ok"] is True


def test_a_damaged_or_duplicated_published_file_is_reported_not_trusted(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_SEAL_PUBLISH_DIR", str(tmp_path))
    t = new_lender()
    add(client, t, 2)
    seal(client, t)
    path = FilePublisher(tmp_path).path(t["tenant_id"])
    good = path.read_text()
    path.write_text(good + "this is not json\n" + good.replace('"root":"', '"root":"00'))
    found = problems(client, t)
    assert any("can't be read" in p for p in found) and any("two different versions" in p for p in found)
    with pytest.raises(ValueError):
        FilePublisher(tmp_path).path("../../etc/passwd")


def test_overlapping_seal_runs_seal_each_decision_exactly_once(client: TestClient) -> None:
    from concurrent.futures import ThreadPoolExecutor

    t = new_lender()
    add(client, t, 12)

    def run(_: int) -> int:
        return TestClient(create_app()).post("/v1/seals/run", headers=h(t["keys"]["lender_analyst"])).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        codes = list(pool.map(run, range(4)))
    assert set(codes) <= {200, 409}
    with owner() as conn:
        per_decision = conn.execute("SELECT decision_id, count(*) FROM anchor_leaves WHERE tenant_id = %s AND kind = 'decision' GROUP BY 1", (t["tenant_id"],)).fetchall()
        seqs = [r[0] for r in conn.execute("SELECT seq FROM anchor_batches WHERE tenant_id = %s ORDER BY seq", (t["tenant_id"],)).fetchall()]
    assert len(per_decision) == 12 and all(n == 1 for _, n in per_decision)
    assert seqs == list(range(1, len(seqs) + 1))
    assert verify(client, t)["ok"] is True
    with owner() as conn:  # and the database itself refuses a second seal of the same decision
        dec = conn.execute("SELECT decision_id, batch_id FROM anchor_leaves WHERE tenant_id = %s AND kind = 'decision' LIMIT 1", (t["tenant_id"],)).fetchone()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO anchor_leaves (batch_id, tenant_id, leaf_index, kind, decision_id, leaf_data, leaf_hash) VALUES (%s, %s, 999, 'decision', %s, '{}'::jsonb, 'x')",
                         (dec[1], t["tenant_id"], dec[0]))


def test_a_big_backlog_does_not_hold_back_corrections_and_says_more_is_waiting(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.seals as seals_module

    t = new_lender()
    ids = add(client, t, 4)
    seal(client, t)
    with tenant_connection(t["tenant_id"]) as conn:
        append_correction(conn, tenant_id=t["tenant_id"], decision_id=ids[0], evidence_ref="application:D-0", field_name="income_annual",
                          new_value=1.0, reason="r", source="person", role="lender_analyst", credential_id=None, finding_id=None)
    add(client, t, 9, "Z")
    monkeypatch.setattr(seals_module, "MAX_BATCH", 6)
    monkeypatch.setattr(seals_module, "CORRECTION_RESERVE", 2)
    out = seal(client, t)
    assert out["batch"]["leaf_count"] == 6 and out["not_yet_sealed"] == 4 and "4 more records are waiting" in out["message"]
    assert client.get(f"/v1/decisions/{ids[0]}/seal", headers=h(t["keys"]["lender_analyst"])).json()["corrections"]["sealed"] is True  # first batch, not last
    assert verify(client, t)["not_yet_sealed"] == 4
    monkeypatch.undo()
    assert seal(client, t)["not_yet_sealed"] == 0


def test_checking_does_not_depend_on_the_time_zone_the_database_session_uses(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    t = new_lender()
    ids = add(client, t, 3)
    seal(client, t)
    monkeypatch.setenv("PGTZ", "Asia/Kolkata")  # every new connection now renders times as +05:30
    assert verify(client, t)["ok"] is True
    proof = client.get(f"/v1/decisions/{ids[0]}/seal", headers=h(t["keys"]["lender_analyst"])).json()
    assert verify_proof(proof, client.get("/v1/seals/public-key").json()["public_key"]) == (True, "ok")
    assert proof["head"]["signed_at"].endswith("+00:00")


def test_proofs_for_unsealed_work_are_reported_as_such_and_new_records_show_as_waiting(client: TestClient) -> None:
    t = new_lender()
    add(client, t, 2)
    seal(client, t)
    add(client, t, 3, "N")
    check = verify(client, t)
    assert check["ok"] is True and check["not_yet_sealed"] == 3  # clean, but not complete: the newest records have no seal


def test_seal_tables_are_append_only_for_the_api_role() -> None:
    for stmt in ("UPDATE anchor_batches SET root = 'x'", "DELETE FROM anchor_batches", "UPDATE anchor_leaves SET leaf_hash = 'x'", "DELETE FROM anchor_leaves"):
        with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)

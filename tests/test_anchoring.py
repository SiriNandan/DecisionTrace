"""Tests for the external chain-head anchor log."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.anchoring import append_anchor, compare_with_database, verify_anchor_log
from decision_api.app import create_app


def test_log_is_chained_and_detects_edit_and_truncation(tmp_path: Path) -> None:
    log = tmp_path / "anchors.jsonl"
    for i in range(3):
        append_anchor(f"d{i}", i + 1, f"head{i}", "2026-01-01T00:00:00+00:00", path=log, key="k")
    assert verify_anchor_log(log, "k")["ok"]

    lines = log.read_text().splitlines()
    edited = json.loads(lines[1])
    edited["chain_head"] = "forged"
    tampered = tmp_path / "edited.jsonl"
    tampered.write_text("\n".join([lines[0], json.dumps(edited, sort_keys=True, separators=(",", ":")), lines[2]]) + "\n")
    assert not verify_anchor_log(tampered, "k")["ok"]

    dropped = tmp_path / "dropped.jsonl"
    dropped.write_text("\n".join([lines[0], lines[2]]) + "\n")
    assert not verify_anchor_log(dropped, "k")["ok"]

    wrong_key = verify_anchor_log(log, "other")
    assert not wrong_key["ok"]


def test_replay_is_not_anchored_twice(tmp_path: Path) -> None:
    log = tmp_path / "a.jsonl"
    assert append_anchor("d", 2, "h", "t", path=log) is not None
    assert append_anchor("d", 2, "h", "t", path=log) is None
    assert len(log.read_text().splitlines()) == 1


def test_noop_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DT_ANCHOR_LOG", raising=False)
    assert append_anchor("d", 1, "h", "t") is None


def test_decide_anchors_and_database_rewrite_is_caught(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    log = tmp_path / "anchors.jsonl"
    monkeypatch.setenv("DT_ANCHOR_LOG", str(log))
    client = TestClient(create_app())
    resp = client.post("/decide", json={"applicant_id": "APP-0011"})
    assert resp.status_code == 200, resp.text
    decision = resp.json()
    db_url = os.environ["DB_URL"]

    assert compare_with_database(db_url, log)["ok"]

    # A privileged actor rewrites the stored head; the external log no longer matches.
    with psycopg.connect(db_url) as conn:
        conn.execute("ALTER TABLE decisions DISABLE TRIGGER USER")
        conn.execute(
            "UPDATE decisions SET chain_head = %s WHERE decision_id = %s", ("0" * 64, decision["decision_id"])
        )
        conn.commit()
    report = compare_with_database(db_url, log)
    assert not report["ok"]
    assert decision["decision_id"] in report["mismatched_heads"]

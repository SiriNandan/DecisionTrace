"""Ingest robustness: concurrent retries, ambiguous evidence, and one bad import row not losing the rest."""

from __future__ import annotations

import copy
import os
import threading
import uuid
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend import ingest
from backend.sandbox import provision_tenants
from decision_api import mappings_api
from decision_api.app import create_app
from tests.test_access_and_signals import envelope, h
from tests.test_mapping_api import UK, approve, do_import, keys, propose


@pytest.fixture(scope="module")
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 1, prefix=f"rob-{uuid.uuid4().hex[:5]}")


def test_repeated_evidence_ref_is_rejected(lenders: list[dict[str, Any]]) -> None:
    client = TestClient(create_app())
    env = envelope("ROB-DUP-" + uuid.uuid4().hex[:6])
    env["evidence"].append(copy.deepcopy(env["evidence"][0]))
    r = client.post("/v1/decisions", json=env, headers={**h(lenders[0]["keys"]["lender_ingest"]), "Idempotency-Key": env["external_id"]})
    assert r.status_code == 422 and "evidence_ref" in r.text


def test_concurrent_retries_with_one_key_all_succeed_and_store_one_record(lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    client = TestClient(create_app())
    for attempt in range(3):  # the race is timing-dependent, so try a few fresh keys
        env = envelope(f"ROB-RACE-{attempt}-" + uuid.uuid4().hex[:6])
        out: list[tuple[int, dict[str, Any]]] = []

        def go() -> None:
            r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": env["external_id"]})
            out.append((r.status_code, r.json()))

        threads = [threading.Thread(target=go) for _ in range(8)]
        [x.start() for x in threads]
        [x.join() for x in threads]
        assert sorted(c for c, _ in out)[0] in (200, 201) and all(c in (200, 201) for c, _ in out), out
        assert [c for c, _ in out].count(201) == 1  # exactly one created it, the rest replayed
        assert len({b["decision_id"] for _, b in out}) == 1


def test_import_survives_an_unexpected_row_failure_and_keeps_the_rest(
    lenders: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    t = lenders[0]
    client = TestClient(create_app())
    mid = propose(client, t, "uk-robust", format="csv", content=UK).json()["mapping_id"]
    approve(client, t, mid)

    real = ingest.record_decision
    calls = {"n": 0}

    def flaky(conn: Any, **kw: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 3:
            raise psycopg.OperationalError("simulated database hiccup")
        return real(conn, **kw)

    monkeypatch.setattr(mappings_api, "record_decision", flaky)
    out = do_import(client, t, mid, format="csv", content=UK)
    assert out.status_code == 200, out.text
    body = out.json()
    bad = [r for r in body["results"] if r["status"] == "rejected"]
    assert [r["row"] for r in bad] == [3] and "server problem" in bad[0]["problems"][0]
    assert body["created"] + body["replayed"] == len(body["results"]) - 1
    assert body["created"] > 3  # rows after the failure were still recorded

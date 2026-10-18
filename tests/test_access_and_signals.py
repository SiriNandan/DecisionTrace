"""Customer links, who-am-I, the health check and safe errors, and risk signals."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.conninfo import make_conninfo

from backend.auth import Principal, failures, log_access, tenant_connection
from backend.migrate import apply_migrations, schema_status
from backend.quality import DecisionView, Item
from backend.sandbox import provision_tenants
from backend.signals import access_signals, decision_signals, drift_signals
from decision_api.app import create_app
from recon_agent import router as recon_router

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def h(key: str) -> dict[str, str]:
    return {"X-API-Key": key}


def c(token: str) -> dict[str, str]:
    return {"X-Claim-Token": token}


@pytest.fixture(scope="module")
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 2, prefix=f"acc-{uuid.uuid4().hex[:5]}")


@pytest.fixture()
def client() -> TestClient:
    recon_router.reset_state()
    return TestClient(create_app())


def envelope(ext: str, subject: str = "s-1", outcome: str = "DECLINE", app: dict[str, Any] | None = None,
             when: str = "2026-09-01T10:00:00+00:00") -> dict[str, Any]:
    return {
        "schema_version": 1, "external_id": ext, "subject_ref": subject, "decided_at": when, "outcome": outcome, "score": 0.4,
        "model_version": "m", "policy_version": "p", "reasons": [{"code": "R", "text": "Debt is high compared with income."}],
        "evidence": [{"evidence_type": "application", "evidence_ref": f"application:{ext}", "source_id": "los", "source_timestamp": when,
                      "payload": app or {"income_annual": 30000, "requested_amount": 5000}}],
    }


def post(client: TestClient, t: dict[str, Any], env: dict[str, Any]) -> str:
    r = client.post("/v1/decisions", json=env, headers={**h(t["keys"]["lender_ingest"]), "Idempotency-Key": env["external_id"]})
    assert r.status_code in (200, 201), r.text
    return r.json()["decision_id"]


def issue(client: TestClient, t: dict[str, Any], dec: str, days: int = 7) -> dict[str, Any]:
    r = client.post(f"/v1/decisions/{dec}/claim-links", json={"days": days, "label": "test"}, headers=h(t["keys"]["lender_analyst"]))
    assert r.status_code == 200, r.text
    return r.json()


# ---------- who am I, health, safe errors ----------

def test_who_am_i_and_health(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    me = client.get("/v1/me", headers=h(lenders[0]["keys"]["lender_analyst"])).json()
    assert me["kind"] == "staff" and me["role"] == "lender_analyst" and me["tenant_id"] == lenders[0]["tenant_id"]
    assert "claims:issue" in me["scopes"] and me["decision_id"] is None
    assert client.get("/v1/me").status_code == 401
    assert client.get("/health").json() == {"status": "ok", "database": "ready"}  # open, and it returns no data


def test_an_older_database_gets_a_clear_answer_with_cors_headers_then_updates_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    base = os.environ["DB_URL"]
    name = f"dt_old_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(make_conninfo(base, dbname="postgres"), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        old_url = make_conninfo(base, dbname=name)
        first = (__import__("pathlib").Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql").read_text(encoding="utf-8")
        with psycopg.connect(old_url) as conn:
            conn.execute(first)
            assert schema_status(conn)["ok"] is False and "tenants" in schema_status(conn)["missing"]
        monkeypatch.setenv("DB_URL", old_url)
        monkeypatch.setenv("APP_DB_URL", make_conninfo(os.environ["APP_DB_URL"], dbname=name))
        monkeypatch.setenv("DT_AUTO_MIGRATE", "0")
        origin = {"Origin": "http://localhost:5173"}
        plain = TestClient(create_app(), raise_server_exceptions=False)
        r = plain.get("/decisions", headers={**h(os.environ["DT_API_KEY"]), **origin})
        assert r.status_code == 503 and r.json()["code"] == "needs_upgrade"
        assert r.headers["access-control-allow-origin"] == "http://localhost:5173"  # so the browser can show the message
        assert plain.get("/health").json()["status"] == "needs_upgrade"

        monkeypatch.setenv("DT_AUTO_MIGRATE", "1")
        with TestClient(create_app()) as migrating:  # start-up updates the database
            assert migrating.get("/health").json()["status"] == "ok"
            assert migrating.get("/decisions", headers=h(os.environ["DT_API_KEY"])).status_code == 200
        assert apply_migrations(old_url)  # safe to repeat
    finally:
        with psycopg.connect(make_conninfo(base, dbname="postgres"), autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


def test_unexpected_errors_are_plain_and_keep_cors_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    app_module = importlib.import_module("decision_api.app")  # the package also exports an object named `app`

    monkeypatch.setattr(app_module, "list_decisions_page", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom with secrets")))
    r = TestClient(create_app(), raise_server_exceptions=False).get(
        "/decisions", headers={**h(os.environ["DT_API_KEY"]), "Origin": "http://localhost:5173"})
    assert r.status_code == 500 and "secrets" not in r.text and r.json()["code"] == "server_error"
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


# ---------- customer links ----------

def test_a_customer_link_opens_one_decision_and_nothing_else(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    dec = post(client, t, envelope("C-1"))
    other = post(client, t, envelope("C-2"))
    link = issue(client, t, dec)
    tok = link["token"]
    assert link["path"] == f"?d={dec}&t={tok}" and len(tok) > 30

    me = client.get("/v1/me", headers=c(tok)).json()
    assert me["kind"] == "customer" and me["decision_id"] == dec and me["scopes"] == ["consent:write", "decisions:read", "reconstruct", "review:request"]
    assert [d["decision_id"] for d in client.get("/decisions", headers=c(tok)).json()] == [dec]
    rec = client.get(f"/decisions/{dec}", headers=c(tok)).json()
    assert rec["chain_valid"] is True and "signals" not in rec
    assert client.get(f"/decisions/{dec}/verify", headers=c(tok)).json()["chain_valid"] is True
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).status_code == 200

    assert client.get(f"/decisions/{other}", headers=c(tok)).status_code == 404  # same lender, another customer
    assert client.post(f"/reconstruct/{other}", headers=c(tok)).status_code == 404
    for method, url in (("get", "/v1/mappings"), ("get", "/v1/quality/findings"), ("get", "/v1/signals"), ("get", "/v1/access-log"),
                        ("post", f"/v1/decisions/{dec}/claim-links"), ("get", f"/v1/decisions/{dec}/claim-links"),
                        ("post", "/v1/quality/run"), ("post", "/v1/imports"), ("post", "/v1/decisions")):
        assert getattr(client, method)(url, headers=c(tok)).status_code == 403, url


def test_a_customer_can_ask_for_a_review_but_never_changes_the_result(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    analyst = h(t["keys"]["lender_analyst"])
    dec = post(client, t, envelope("C-3"))
    other = post(client, t, envelope("C-4"))
    tok = issue(client, t, dec)["token"]
    body = {"decision_id": dec, "new_outcome": "REFER", "reason": "I think my income was recorded wrongly.", "actor_role": "underwriter"}
    assert client.post("/overrides", json={**body, "new_outcome": "APPROVE"}, headers=c(tok)).status_code == 403
    assert client.post("/overrides", json={**body, "decision_id": other}, headers=c(tok)).status_code == 404
    ok = client.post("/overrides", json=body, headers=c(tok))
    assert ok.status_code == 200 and ok.json()["actor_role"] == "customer"  # whatever role was sent, it is recorded as a customer

    rec = client.get(f"/decisions/{dec}", headers=analyst).json()
    assert rec["effective_outcome"] == rec["outcome"] == "DECLINE"  # a request is not a change of result
    assert rec["override"] is None and rec["human_override"] is None and rec["overrides"] == []
    assert [r["reason"] for r in rec["review_requests"]] == ["I think my income was recorded wrongly."]
    assert client.get("/decisions", headers=analyst).json()[0]["effective_outcome"] in ("DECLINE", "APPROVE", "REFER")
    listed = {d["decision_id"]: d for d in client.get("/decisions?limit=1000", headers=analyst).json()}
    assert listed[dec]["effective_outcome"] == "DECLINE"
    mine = client.get(f"/decisions/{dec}", headers=c(tok)).json()
    assert [r["reason"] for r in mine["review_requests"]] == ["I think my income was recorded wrongly."]
    rep = client.post(f"/reconstruct/{dec}", headers=c(tok)).json()
    assert rep["outcome"] == "DECLINE" and rep["human_override"] is None

    # one request per decision, so a link can't be used to flood the record
    again = client.post("/overrides", json=body, headers=c(tok))
    assert again.status_code == 409 and "already asked" in again.json()["detail"]
    # a lender's own override still works, and still wins
    ov = {"decision_id": dec, "new_outcome": "APPROVE", "reason": "Reviewed by a person; income confirmed.", "actor_role": "underwriter"}
    done = client.post("/overrides", json=ov, headers=analyst)
    assert done.status_code == 200
    assert done.json()["actor_role"] == "lender_analyst"  # taken from the key, not from the body's "underwriter"
    assert client.get(f"/decisions/{dec}", headers=analyst).json()["effective_outcome"] == "APPROVE"


def test_a_customer_cannot_reset_other_peoples_limits_or_cache(client: TestClient, lenders: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.web import SlidingWindowLimiter

    t = lenders[0]
    dec = post(client, t, envelope("C-7"))
    tok = issue(client, t, dec)["token"]
    monkeypatch.setattr(recon_router, "_limiter", SlidingWindowLimiter(2))
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).status_code == 200
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).status_code == 200
    body = {"decision_id": dec, "new_outcome": "REFER", "reason": "Please look at this again, thank you.", "actor_role": "customer"}
    assert client.post("/overrides", json=body, headers=c(tok)).status_code == 200  # this used to zero everyone's counters
    assert client.post(f"/reconstruct/{dec}", headers=c(tok)).status_code == 429


def test_internal_notes_never_reach_a_customer_or_a_report(client: TestClient) -> None:
    from recon_agent.tools import ToolSession

    demo = h(os.environ["DT_API_KEY"])
    staff_view = None
    for d in client.get("/decisions?limit=50", headers=demo).json():
        rec = client.get(f"/decisions/{d['decision_id']}", headers=demo).json()
        note = next((s for s in rec["snapshots"] if s["evidence_type"] == "analyst_note"), None)
        if note and note["payload"].get("note_text") and rec["chain_valid"]:
            staff_view = (d["decision_id"], note["payload"]["note_text"])
            break
    assert staff_view, "seed data should include an analyst note"
    dec, text = staff_view
    tok = client.post(f"/v1/decisions/{dec}/claim-links", json={}, headers=demo).json()["token"]
    mine = client.get(f"/decisions/{dec}", headers=c(tok)).json()
    assert all(s["evidence_type"] != "analyst_note" for s in mine["snapshots"]) and text not in json.dumps(mine)
    assert mine["chain_valid"] is True  # hiding a note from view doesn't change the check on the record
    assert text not in client.post(f"/reconstruct/{dec}", headers=c(tok)).text
    snap = ToolSession(db_url=os.environ["DB_URL"]).get_snapshot(dec, "all")
    assert all(s["evidence_type"] != "analyst_note" for s in snap["snapshots"]) and text not in json.dumps(snap)


def test_links_expire_and_can_be_withdrawn_and_bad_ones_are_counted(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    dec = post(client, t, envelope("C-5"))
    analyst = h(t["keys"]["lender_analyst"])
    link = issue(client, t, dec)
    listing = client.get(f"/v1/decisions/{dec}/claim-links", headers=analyst).json()
    assert listing[0]["state"] == "active" and "token" not in listing[0]

    assert client.post(f"/v1/claim-links/{link['claim_id']}/revoke", headers=analyst).json()["state"] == "withdrawn"
    r = client.get(f"/decisions/{dec}", headers=c(link["token"]))
    assert r.status_code == 401 and "expired or was withdrawn" in r.json()["detail"]

    old = issue(client, t, dec)
    with psycopg.connect(os.environ["DB_URL"]) as conn:
        conn.execute("UPDATE claim_links SET expires_at = now() - interval '1 minute' WHERE claim_id = %s", (old["claim_id"],))
        conn.commit()
    assert client.get(f"/decisions/{dec}", headers=c(old["token"])).status_code == 401

    with psycopg.connect(os.environ["DB_URL"]) as conn:
        before = conn.execute("SELECT count(*) FROM security_events").fetchone()[0]
    assert client.get(f"/decisions/{dec}", headers=c("dtc_not-a-real-link")).status_code == 401
    with psycopg.connect(os.environ["DB_URL"]) as conn:
        kinds = {r[0] for r in conn.execute("SELECT kind FROM security_events ORDER BY event_id DESC LIMIT 3").fetchall()}
        assert conn.execute("SELECT count(*) FROM security_events").fetchone()[0] > before and "bad_claim" in kinds


def test_guessing_is_slowed_down_but_a_missing_key_is_not_counted(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    failures.reset()
    for _ in range(30):
        client.get("/decisions")  # no credentials at all: not an attempt
    assert client.get("/decisions", headers=h(lenders[0]["keys"]["lender_analyst"])).status_code == 200
    codes = [client.get("/decisions", headers=h(f"dtk_guess{i}")).status_code for i in range(25)]
    assert codes[:20] == [401] * 20 and set(codes[20:]) == {429}
    assert client.get("/decisions", headers=h(lenders[0]["keys"]["lender_analyst"])).status_code == 429  # even the right key waits
    failures.reset()
    assert client.get("/decisions", headers=h(lenders[0]["keys"]["lender_analyst"])).status_code == 200


def test_links_belong_to_one_lender_and_the_database_keeps_them_apart(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    a, b = lenders
    dec = post(client, a, envelope("C-6"))
    assert client.post(f"/v1/decisions/{dec}/claim-links", json={}, headers=h(b["keys"]["lender_analyst"])).status_code == 404
    link = issue(client, a, dec)
    assert client.post(f"/v1/claim-links/{link['claim_id']}/revoke", headers=h(b["keys"]["lender_analyst"])).status_code == 404
    with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM claim_links").fetchone()[0] == 0  # no lender selected: nothing visible
        conn.execute("SELECT set_config('app.tenant_id', %s, false)", (b["tenant_id"],))
        assert conn.execute("SELECT count(*) FROM claim_links WHERE decision_id = %s", (dec,)).fetchone()[0] == 0
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE claim_links SET label = 'x'")
    # customer reads are in the log, tied to the link
    client.get(f"/decisions/{dec}", headers=c(link["token"]))
    log = client.get("/v1/access-log?limit=50", headers=h(a["keys"]["auditor"])).json()
    assert any(r["role"] == "consumer" and r["action"] == "read" and r["object_id"] == dec for r in log)


# ---------- risk signals ----------

def view(i: int, subject: str, outcome: str = "DECLINE", when: datetime = NOW, score: float = 0.4,
         payload: dict[str, Any] | None = None) -> DecisionView:
    return DecisionView(f"d{i}", outcome, score, when, [
        Item(f"lender_decision:{i}", "lender_decision", {"subject_ref": subject, "reasons": []}, when, "h"),
        Item(f"application:{i}", "application", payload or {"income_annual": 20000 + i, "requested_amount": 5000 + i}, when, "h"),
    ])


def test_patterns_across_decisions_are_flagged_with_cautious_wording() -> None:
    quiet = [view(i, f"s{i}", when=NOW + timedelta(days=i * 3)) for i in range(6)]
    assert decision_signals(quiet) == []

    fast = [view(i, "s-same", when=NOW + timedelta(days=i)) for i in range(3)]
    sig = decision_signals(fast)
    assert [(s["signal_type"], s["decision_id"]) for s in sig] == [("applicant_velocity", "d2")]
    five = decision_signals([view(i, "s-same", when=NOW + timedelta(hours=i)) for i in range(5)])
    assert {s["severity"] for s in five if s["decision_id"] == "d4"} == {"high"}

    flip = decision_signals([view(1, "s9", "DECLINE", NOW), view(2, "s9", "APPROVE", NOW + timedelta(days=10))])
    assert [s["signal_type"] for s in flip] == ["outcome_flip"] and flip[0]["decision_id"] == "d2"
    assert decision_signals([view(1, "s9", "DECLINE", NOW), view(2, "s9", "APPROVE", NOW + timedelta(days=60))]) == []

    same = {"income_annual": 41000, "requested_amount": 7500, "credit_score": 612}
    shared = decision_signals([view(i, f"s{i}", payload=dict(same)) for i in range(3)])
    assert {s["decision_id"] for s in shared} == {"d0", "d1", "d2"} and shared[0]["signal_type"] == "shared_details"
    assert decision_signals([view(i, f"s{i}", payload=dict(same)) for i in range(2)]) == []  # two is a coincidence
    assert all(w not in s["message"].lower() for s in shared + sig + flip for w in ("fraud", "criminal", "guilty", "cheat"))


def test_a_sharp_shift_in_outcomes_is_flagged_only_with_enough_data() -> None:
    base = [view(i, f"b{i}", "DECLINE" if i % 5 == 0 else "APPROVE", NOW - timedelta(days=20 + i % 60), 0.2) for i in range(80)]
    recent_steady = [view(100 + i, f"r{i}", "DECLINE" if i % 5 == 0 else "APPROVE", NOW - timedelta(days=i % 6), 0.2) for i in range(40)]
    assert drift_signals(base + recent_steady) == []
    recent_bad = [view(200 + i, f"r{i}", "DECLINE" if i % 5 else "APPROVE", NOW - timedelta(days=i % 6), 0.7) for i in range(40)]
    kinds = {s["signal_type"] for s in drift_signals(base + recent_bad)}
    assert kinds == {"decline_rate_shift", "score_shift"}
    assert drift_signals(base[:10] + recent_bad[:10]) == []


def test_enumeration_and_bulk_reading_are_noticed_per_actor(lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    now = datetime.now(timezone.utc)
    guesser = Principal(tenant_id=t["tenant_id"], role="lender_analyst", credential_id=987001)
    reader = Principal(tenant_id=t["tenant_id"], role="auditor", credential_id=987002)
    careful = Principal(tenant_id=t["tenant_id"], role="auditor", credential_id=987003)
    with tenant_connection(t["tenant_id"]) as conn:
        for i in range(12):
            log_access(conn, guesser, "read", "decision", str(uuid.uuid4()), "not_found")
        for i in range(210):
            log_access(conn, reader, "read", "decision", str(uuid.uuid4()))
        for i in range(3):
            log_access(conn, careful, "read", "decision", str(uuid.uuid4()), "not_found")
        found = {(s["signal_type"], s["details"]["actor"]) for s in access_signals(conn, t["tenant_id"], now)}
    assert ("id_enumeration", "credential 987001") in found and ("bulk_reading", "credential 987002") in found
    assert not any(a == "credential 987003" for _, a in found)


def test_signals_through_the_api_roles_review_and_privacy(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    analyst, auditor, ingest = h(t["keys"]["lender_analyst"]), h(t["keys"]["auditor"]), h(t["keys"]["lender_ingest"])
    ids = [post(client, t, envelope(f"S-{i}", subject="s-repeat", when=f"2026-08-{10 + i:02d}T10:00:00+00:00")) for i in range(3)]
    for _ in range(11):  # someone guesses reference numbers
        client.get(f"/decisions/{uuid.uuid4()}", headers=analyst)

    assert client.post("/v1/signals/run", json={}, headers=auditor).status_code == 403
    assert client.get("/v1/signals", headers=ingest).status_code == 403
    run = client.post("/v1/signals/run", json={}, headers=analyst).json()
    assert run["new_signals"] >= 2
    again = client.post("/v1/signals/run", json={}, headers=analyst).json()
    assert again["new_signals"] == 0  # nothing is added twice

    found = client.get("/v1/signals", headers=auditor).json()
    kinds = {s["signal_type"] for s in found}
    assert {"applicant_velocity", "id_enumeration"} <= kinds
    velocity = next(s for s in found if s["signal_type"] == "applicant_velocity" and s["decision_id"] == ids[2])
    assert velocity["decision_id"] == ids[2]

    rec = client.get(f"/decisions/{ids[2]}", headers=analyst).json()
    assert [s["signal_type"] for s in rec["signals"]] == ["applicant_velocity"]
    tok = issue(client, t, ids[2])["token"]
    assert "signals" not in client.get(f"/decisions/{ids[2]}", headers=c(tok)).json()  # the customer never sees these
    assert client.get(f"/v1/decisions/{ids[2]}/signals", headers=c(tok)).status_code == 403

    sid = velocity["signal_id"]
    assert client.post(f"/v1/signals/{sid}/review", json={"action": "clear", "note": "short"}, headers=analyst).status_code == 422
    assert client.post(f"/v1/signals/{sid}/review", json={"action": "clear", "note": "Known repeat applicant, checked."}, headers=auditor).status_code == 403
    ok = client.post(f"/v1/signals/{sid}/review", json={"action": "clear", "note": "Known repeat applicant, checked."}, headers=analyst)
    assert ok.json() == {"signal_id": sid, "status": "cleared"}
    assert client.post(f"/v1/signals/{sid}/review", json={"action": "acknowledge", "note": "second look at it"}, headers=analyst).status_code == 409
    assert sid not in {s["signal_id"] for s in client.get("/v1/signals", headers=auditor).json()}
    assert sid in {s["signal_id"] for s in client.get("/v1/signals?status=cleared", headers=auditor).json()}

    other = lenders[1]
    assert sid not in {s["signal_id"] for s in client.get("/v1/signals?status=all", headers=h(other["keys"]["auditor"])).json()}
    assert client.post(f"/v1/signals/{sid}/review", json={"action": "clear", "note": "not my lender at all"},
                       headers=h(other["keys"]["lender_analyst"])).status_code == 404


def test_imports_look_for_signals_as_they_are_saved(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    csv_text = "ref,customer_id,when,result,pd,model,policy,income,amount\n" + "\n".join(
        f"IMS-{i},same-person,2026-07-0{i + 1}T10:00:00Z,Declined,0.4,m1,p1,30000,5000" for i in range(3))
    m = client.post("/v1/mappings/propose", json={"name": "sig-imp", "format": "csv", "content": csv_text}, headers=h(t["keys"]["lender_ingest"])).json()
    assert m["gaps"] == [], m
    client.post(f"/v1/mappings/{m['mapping_id']}/approve", json={}, headers=h(t["keys"]["lender_analyst"]))
    out = client.post("/v1/imports", json={"mapping_id": m["mapping_id"], "format": "csv", "content": csv_text},
                      headers=h(t["keys"]["lender_ingest"])).json()
    assert out["created"] == 3 and out["signals"]["new_signals"] >= 1


def test_signal_tables_are_append_only_for_the_api_role() -> None:
    for stmt in ("UPDATE risk_signals SET message = 'x'", "DELETE FROM risk_signals", "UPDATE signal_reviews SET note = 'x'",
                 "DELETE FROM signal_reviews", "UPDATE claim_links SET label = 'x'", "DELETE FROM claim_links",
                 "UPDATE claim_revocations SET revoked_by_role = 'x'", "SELECT * FROM security_events",
                 "UPDATE security_events SET detail = 'x'"):
        with psycopg.connect(os.environ["APP_DB_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(stmt)


def test_a_key_with_unusual_characters_is_just_a_wrong_key(client: TestClient) -> None:
    failures.reset()
    r = client.get("/decisions", headers={"X-API-Key": "caf\u00e9-\u00e9\u00e8".encode("latin-1")})  # what a browser can send
    assert r.status_code == 401 and r.json()["detail"] == "Sign-in is needed to see this."
    with psycopg.connect(os.environ["DB_URL"]) as conn:
        assert conn.execute("SELECT count(*) FROM security_events WHERE kind = 'bad_key' AND at > now() - interval '1 minute'").fetchone()[0] >= 1


# ---------- the checks that were missing ----------

_ROLES = ("lender_ingest", "lender_analyst", "auditor")
# (method, url, body, expected status per role: ingest, analyst, auditor). 422/404 mean "allowed in, but this request is not valid / not found".
_MATRIX = [
    ("get", "/decisions", None, (403, 200, 200)),
    ("get", "/v1/access-log", None, (403, 403, 200)),
    ("get", "/v1/mappings", None, (200, 200, 200)),
    ("get", "/v1/quality/findings", None, (200, 200, 200)),
    ("post", "/v1/quality/run", {}, (403, 200, 403)),
    ("get", "/v1/signals", None, (403, 200, 200)),
    ("post", "/v1/signals/run", {}, (403, 200, 403)),
    ("post", "/v1/signals/999999/review", {"action": "clear", "note": "a long enough note"}, (403, 404, 403)),
    ("post", "/v1/decisions", None, (422, 403, 403)),
    ("post", "/v1/mappings/propose", {"name": "x", "format": "csv"}, (422, 422, 403)),
    ("post", "/v1/mappings/999999/approve", {}, (403, 404, 403)),
    ("post", "/v1/claim-links/999999/revoke", None, (403, 404, 403)),
    ("post", "/v1/decisions/00000000-0000-4000-8000-000000000000/claim-links", {}, (403, 404, 403)),
]


@pytest.mark.parametrize("method,url,body,expected", _MATRIX)
def test_each_role_gets_exactly_the_access_it_should(client: TestClient, lenders: list[dict[str, Any]], method: str, url: str, body: Any, expected: tuple[int, ...]) -> None:
    t = lenders[0]
    for role, want in zip(_ROLES, expected):
        kwargs: dict[str, Any] = {"headers": h(t["keys"][role])}
        if body is not None:
            kwargs["json"] = body
        got = getattr(client, method)(url, **kwargs).status_code
        assert got == want, f"{role} {method.upper()} {url}: expected {want}, got {got}"


def test_the_demo_admin_key_can_do_everything_and_only_in_the_demo_lender(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    demo = h(os.environ["DT_API_KEY"])
    assert client.get("/v1/signals", headers=demo).status_code == 200
    assert client.get("/v1/access-log", headers=demo).status_code == 200
    me = client.get("/v1/me", headers=demo).json()
    assert me["tenant_id"] == "demo" and "claims:issue" in me["scopes"] and "signals:resolve" in me["scopes"]
    other_decision = post(client, lenders[0], envelope("C-8"))
    assert client.get(f"/decisions/{other_decision}", headers=demo).status_code == 404  # the demo key is not a master key


def test_thresholds_windows_and_customer_links_in_access_signals(lenders: list[dict[str, Any]]) -> None:
    from backend.signals import BULK_MIN, ENUMERATION_MIN, ENUMERATION_WINDOW_MIN

    t = lenders[1]
    now = datetime.now(timezone.utc)
    cases = {
        "below": (Principal(t["tenant_id"], "auditor", 880001), ENUMERATION_MIN - 1, BULK_MIN - 1),
        "exact": (Principal(t["tenant_id"], "auditor", 880002), ENUMERATION_MIN, BULK_MIN),
        "link": (Principal(t["tenant_id"], "consumer", None, claim_id=880003, decision_id=str(uuid.uuid4())), ENUMERATION_MIN, 0),
    }
    with tenant_connection(t["tenant_id"]) as conn:
        for _, (who, missed, reads) in cases.items():
            for _ in range(missed):
                log_access(conn, who, "read", "decision", str(uuid.uuid4()), "not_found")
            for _ in range(reads):
                log_access(conn, who, "read", "decision", str(uuid.uuid4()))
        found = {(s["signal_type"], s["details"]["actor"]) for s in access_signals(conn, t["tenant_id"], now)}
        later = {(s["signal_type"], s["details"]["actor"]) for s in access_signals(conn, t["tenant_id"], now + timedelta(minutes=ENUMERATION_WINDOW_MIN + 1))}
    assert not {a for _, a in found if a == "credential 880001"}
    assert ("id_enumeration", "credential 880002") in found and ("bulk_reading", "credential 880002") in found
    assert ("id_enumeration", "customer link 880003") in found  # a customer guessing other decisions is the main abuse case
    assert not any(a in ("credential 880002", "customer link 880003") for _, a in later)  # outside the window, nothing


def test_guessing_is_flagged_as_it_happens_without_anyone_running_the_checks(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    analyst = h(t["keys"]["lender_analyst"])
    before = {s["signal_id"] for s in client.get("/v1/signals?status=all", headers=analyst).json()}
    for _ in range(11):
        client.get(f"/decisions/{uuid.uuid4()}", headers=analyst)
    fresh = [s for s in client.get("/v1/signals?status=all", headers=analyst).json() if s["signal_id"] not in before]
    assert [s["signal_type"] for s in fresh] == ["id_enumeration"] and fresh[0]["severity"] == "high"


def test_signals_include_what_was_just_saved_even_when_older_than_the_recent_window(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    from backend.signals import scan_signals

    t = lenders[0]
    ids = [post(client, t, envelope(f"OLD-{i}", subject="s-old-repeat", when=f"2019-03-0{i + 1}T10:00:00+00:00")) for i in range(3)]
    with tenant_connection(t["tenant_id"]) as conn:
        result = scan_signals(conn, t["tenant_id"], decision_ids=ids, recent=1, include_access=False)  # window far too small to hold them
        kinds = {r[0] for r in conn.execute("SELECT signal_type FROM risk_signals WHERE decision_id = ANY(%s::uuid[])", (ids,)).fetchall()}
    assert result["new_signals"] >= 1 and "applicant_velocity" in kinds


def test_a_mistyped_future_date_cannot_blind_the_drift_check() -> None:
    base = [view(i, f"b{i}", "DECLINE" if i % 5 == 0 else "APPROVE", NOW - timedelta(days=20 + i % 60), 0.2) for i in range(80)]
    recent_bad = [view(200 + i, f"r{i}", "DECLINE" if i % 5 else "APPROVE", NOW - timedelta(days=i % 6), 0.7) for i in range(40)]
    typo = view(999, "typo", "APPROVE", NOW + timedelta(days=400), 0.2)  # year typed wrongly
    found = drift_signals(base + recent_bad + [typo], now=NOW)
    assert {s["signal_type"] for s in found} == {"decline_rate_shift", "score_shift"}
    assert all("In the week to" in s["message"] for s in found)


def test_two_people_reviewing_the_same_signal_cannot_both_win(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[1]
    analyst = h(t["keys"]["lender_analyst"])
    for _ in range(11):
        client.get(f"/decisions/{uuid.uuid4()}", headers=analyst)
    sid = client.get("/v1/signals", headers=analyst).json()[0]["signal_id"]
    with psycopg.connect(os.environ["DB_URL"]) as conn:
        conn.execute("INSERT INTO signal_reviews (signal_id, tenant_id, action, note, reviewer_role) VALUES (%s, %s, 'cleared', 'first', 'x')", (sid, t["tenant_id"]))
        conn.commit()
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute("INSERT INTO signal_reviews (signal_id, tenant_id, action, note, reviewer_role) VALUES (%s, %s, 'cleared', 'second', 'x')", (sid, t["tenant_id"]))


# ---------- plain-language "why this fired" ----------

class _WhyModel:
    """Stands in for a real model: records every ask and answers with a fixed sentence."""

    def __init__(self, text: str) -> None:
        self.text, self.asked = text, []

    def generate_signal_why(self, kind: str, facts: dict[str, Any]) -> str:
        self.asked.append((kind, facts))
        return self.text


GOOD_WHY = "This customer reference has 3 decisions within 7 days, so it is worth a look."


def _repeat_applicant(client: TestClient, t: dict[str, Any], tag: str) -> list[str]:
    return [post(client, t, envelope(f"{tag}-{i}", subject=f"s-{tag}", when=f"2026-08-{10 + i:02d}T10:00:00+00:00")) for i in range(3)]


def _fresh_lender(policy: str, encrypt: bool = False) -> dict[str, Any]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        t = provision_tenants(conn, 1, prefix=f"why-{uuid.uuid4().hex[:5]}", encrypt=encrypt)[0]
        conn.execute("UPDATE tenants SET ai_summary_policy = %s WHERE tenant_id = %s", (policy, t["tenant_id"]))
    return t


def _velocity(client: TestClient, t: dict[str, Any]) -> dict[str, Any]:
    found = client.get("/v1/signals", headers=h(t["keys"]["auditor"])).json()
    return next(s for s in found if s["signal_type"] == "applicant_velocity")


def test_every_signal_gets_a_template_sentence_built_only_from_its_own_facts() -> None:
    from backend.signals import signal_facts, why_template

    assert why_template("applicant_velocity", {"count": 4}) == (
        "This rule fired because the same customer reference has 4 decisions within 7 days, and it looks for 3 or more.")
    assert "30 days" in why_template("outcome_flip", {})
    assert "5 different customer references" in why_template("shared_details", {"distinct_references": 5})
    assert "50%" in why_template("decline_rate_shift", {"recent_rate": 0.5, "baseline_rate": 0.2, "recent_n": 40, "baseline_n": 90})
    assert "0.61" in why_template("score_shift", {"recent_mean": 0.61, "baseline_mean": 0.4})
    assert "12 lookups" in why_template("id_enumeration", {"not_found": 12})
    assert "300 different decisions" in why_template("bulk_reading", {"distinct": 300})
    assert why_template("applicant_velocity", {}) is None and why_template("something_new", {"count": 1}) is None  # missing facts: no sentence, no guess
    assert signal_facts("applicant_velocity", {"count": 4})["rule_minimum"] == 3


def test_a_model_sentence_is_kept_only_if_one_short_sentence_with_numbers_from_the_facts() -> None:
    from backend.signals import grounded_why, signal_facts

    facts = signal_facts("applicant_velocity", {"count": 3})
    assert grounded_why(GOOD_WHY, facts) == GOOD_WHY
    assert grounded_why("This customer reference has 9 decisions within 7 days.", facts) is None  # invented number
    assert grounded_why("It happened 3 times. It is certainly fraud.", facts) is None  # not one sentence
    assert grounded_why("x" * 400 + ".", facts) is None and grounded_why("", facts) is None and grounded_why(None, facts) is None


def test_the_offline_backend_gives_the_template_and_labels_it_as_a_rule(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_BACKEND", "fake")
    t = _fresh_lender("allow")
    _repeat_applicant(client, t, "fk")
    client.post("/v1/signals/run", json={}, headers=h(t["keys"]["lender_analyst"]))
    sig = _velocity(client, t)
    assert sig["why"] == {"text": "This rule fired because the same customer reference has 3 decisions within 7 days, and it looks for 3 or more.",
                          "source": "rule"}
    assert "why_ai" not in sig["details"]


def test_a_grounded_model_sentence_is_used_once_and_stops_the_moment_consent_is_withdrawn(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.signals as signals

    model = _WhyModel(GOOD_WHY)
    monkeypatch.setattr(signals, "_llm_backend", lambda: model)
    t = _fresh_lender("allow")
    _repeat_applicant(client, t, "ai")
    run = lambda: client.post("/v1/signals/run", json={}, headers=h(t["keys"]["lender_analyst"]))  # noqa: E731
    run()
    assert _velocity(client, t)["why"] == {"text": GOOD_WHY, "source": "ai"}
    assert model.asked and model.asked[0][1] == {"decisions_in_window": 3, "window_days": 7, "rule_minimum": 3}
    asked = len(model.asked)
    run()
    assert len(model.asked) == asked  # signals already on file are not sent again

    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        conn.execute("UPDATE tenants SET ai_summary_policy = 'never' WHERE tenant_id = %s", (t["tenant_id"],))
    after = _velocity(client, t)
    assert after["why"]["source"] == "rule" and GOOD_WHY not in json.dumps(after)


def test_a_model_sentence_with_an_invented_number_is_dropped_for_the_template(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.signals as signals

    model = _WhyModel("This customer reference has 9 decisions within 7 days.")
    monkeypatch.setattr(signals, "_llm_backend", lambda: model)
    t = _fresh_lender("allow")
    _repeat_applicant(client, t, "bad")
    client.post("/v1/signals/run", json={}, headers=h(t["keys"]["lender_analyst"]))
    assert model.asked  # it was asked, and its answer was refused
    sig = _velocity(client, t)
    assert sig["why"]["source"] == "rule" and "9" not in sig["why"]["text"]


@pytest.mark.parametrize("policy,encrypt", [("never", False), ("ask", False), ("allow", True)])
def test_no_model_is_asked_without_consent_or_for_protected_decisions(client: TestClient, monkeypatch: pytest.MonkeyPatch, policy: str, encrypt: bool) -> None:
    import backend.signals as signals

    model = _WhyModel(GOOD_WHY)
    monkeypatch.setattr(signals, "_llm_backend", lambda: model)
    t = _fresh_lender(policy, encrypt)
    _repeat_applicant(client, t, "no")
    client.post("/v1/signals/run", json={}, headers=h(t["keys"]["lender_analyst"]))
    assert model.asked == []
    assert _velocity(client, t)["why"]["source"] == "rule"


def test_override_actor_role_cannot_be_spoofed(client: TestClient, lenders: list[dict[str, Any]]) -> None:
    t = lenders[0]
    analyst = {"X-API-Key": t["keys"]["lender_analyst"]}
    dec = post(client, t, envelope("SPOOF-1"))
    body = {"decision_id": dec, "new_outcome": "APPROVE", "reason": "Verified income documents by phone call."}
    for claimed in ("admin", "chief_risk_officer", "customer"):
        r = client.post("/overrides", json={**body, "actor_role": claimed}, headers=analyst)
        assert r.status_code == 200 and r.json()["actor_role"] == "lender_analyst", claimed
    # omitting the field works too
    assert client.post("/overrides", json=body, headers=analyst).json()["actor_role"] == "lender_analyst"

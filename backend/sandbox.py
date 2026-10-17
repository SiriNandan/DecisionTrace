"""Sandbox lenders and synthetic decisions, sent through the real ingest API (SYNTHETIC DATA - FOR DEMONSTRATION).

    python -m backend.sandbox --api-url http://localhost:8000 --tenants 5 --decisions 200

Creates `sandbox-bank-NN` tenants (needs the owner DB_URL), issues each an ingest, analyst and auditor key,
and posts envelope-v1 decisions to `--api-url`. Keys are printed once; they only open synthetic sandbox data.
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

import psycopg

from backend.tenants import create_tenant, issue_key
from data.load_policies import get_db_url

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "envelopes"
ROLES = ("lender_ingest", "lender_analyst", "auditor")

REASONS = {
    "DTI_HIGH": "Debt is high compared with income.",
    "SCORE_LOW": "The credit score is below what this lender requires.",
    "DOC_MISSING": "A required document was not received.",
    "RECENT_ARREARS": "There were recent missed payments, so a person needs to review the application.",
}


class HttpLike(Protocol):
    def post(self, url: str, **kwargs: Any) -> Any: ...


def make_envelope(rng: random.Random, index: int, tenant_tag: str, now: datetime) -> dict[str, Any]:
    """One coherent synthetic decision: the outcome and reasons follow from the inputs."""
    income = round(rng.uniform(14_000, 90_000), 2)
    amount = round(rng.uniform(1_000, 25_000), 2)
    dti = round(rng.uniform(0.05, 0.75), 3)
    credit = rng.randint(380, 850)
    arrears = rng.choice([0, 0, 0, 0, 1, 2])
    doc_ok = rng.random() > 0.08

    reasons: list[str] = []
    if dti > 0.45:
        reasons.append("DTI_HIGH")
    if credit < 560:
        reasons.append("SCORE_LOW")
    if not doc_ok:
        reasons.append("DOC_MISSING")
    if reasons:
        outcome = "DECLINE"
    elif arrears >= 2:
        outcome, reasons = "REFER", ["RECENT_ARREARS"]
    else:
        outcome = "APPROVE"

    score = round(min(0.99, max(0.01, 0.15 + dti * 0.5 + (700 - credit) / 1000 + 0.08 * arrears)), 4)
    decided = now - timedelta(minutes=index * 7)
    ext = f"{tenant_tag}-{index:05d}"
    stamp = (decided - timedelta(minutes=2)).isoformat()
    return {
        "schema_version": 1,
        "external_id": ext,
        "subject_ref": f"subj-{rng.getrandbits(40):010x}",
        "decided_at": decided.isoformat(),
        "outcome": outcome,
        "score": score,
        "model_version": f"{tenant_tag}_model_v{1 + index % 2}",
        "policy_version": f"{tenant_tag}_policy_2026",
        "reasons": [{"code": c, "text": REASONS[c]} for c in reasons],
        "evidence": [
            {
                "evidence_type": "application",
                "evidence_ref": f"application:{ext}",
                "source_id": "los",
                "source_timestamp": stamp,
                "payload": {"income_annual": income, "requested_amount": amount, "dti_ratio": dti},
            },
            {
                "evidence_type": "bureau",
                "evidence_ref": f"bureau:{ext}",
                "source_id": "bureau_api",
                "source_timestamp": stamp,
                "payload": {"credit_score": credit, "delinquencies_24m": arrears},
            },
            {
                "evidence_type": "document",
                "evidence_ref": f"document:payslip:{ext}",
                "source_id": "doc_upload",
                "source_timestamp": stamp,
                "payload": {"status": "received" if doc_ok else "missing"},
            },
        ],
    }


def load_fixtures() -> dict[str, dict[str, Any]]:
    """The golden envelopes lenders can test their integration against."""
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURE_DIR.glob("*.json"))}


def provision_tenants(conn: psycopg.Connection, count: int, prefix: str = "sandbox-bank") -> list[dict[str, Any]]:
    out = []
    for n in range(1, count + 1):
        tid = f"{prefix}-{n:02d}"
        create_tenant(conn, tid, f"Sandbox Bank {n:02d} (synthetic data)")
        keys = {role: issue_key(conn, tid, role, "sandbox")[1] for role in ROLES}
        out.append({"tenant_id": tid, "keys": keys})
    return out


def send_decisions(
    client: HttpLike, tenant: dict[str, Any], count: int, *, seed: int = 42, run: str = "run1"
) -> dict[str, int]:
    """Post `count` decisions for one tenant. Re-running with the same seed and run id replays, not duplicates."""
    rng = random.Random(f"{seed}:{tenant['tenant_id']}")
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    tally = {"created": 0, "replayed": 0, "failed": 0}
    for i in range(count):
        env = make_envelope(rng, i, tenant["tenant_id"], now)
        resp = client.post(
            "/v1/decisions",
            json=env,
            headers={"X-API-Key": tenant["keys"]["lender_ingest"], "Idempotency-Key": f"{run}-{env['external_id']}"},
        )
        if resp.status_code == 201:
            tally["created"] += 1
        elif resp.status_code == 200:
            tally["replayed"] += 1
        else:
            tally["failed"] += 1
    return tally


def main() -> None:
    parser = argparse.ArgumentParser(description="Create sandbox lenders and send synthetic decisions to the API.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--tenants", type=int, default=3)
    parser.add_argument("--decisions", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    import httpx

    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        tenants = provision_tenants(conn, args.tenants)
    with httpx.Client(base_url=args.api_url, timeout=30) as client:
        for t in tenants:
            tally = send_decisions(client, t, args.decisions, seed=args.seed)
            print(f"{t['tenant_id']}: {tally}")
            print(f"  analyst key: {t['keys']['lender_analyst']}\n  auditor key: {t['keys']['auditor']}")


if __name__ == "__main__":
    main()

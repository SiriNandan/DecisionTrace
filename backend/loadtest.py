"""A small, honest load test for the API: each step reports throughput and latency percentiles.

It creates its own throwaway lender (needs the owner DB_URL), sends decisions through the real HTTP API, then reads,
verifies, pages, seals and explains them at a chosen concurrency. Numbers depend entirely on the machine, the database
and how the API was started, so print those alongside (see `--label`) and never compare runs from different setups.

    python -m backend.loadtest --api-url http://localhost:8000 --ingest 600 --concurrency 1,8,32 --label "1 worker, no pool"
"""

from __future__ import annotations

import argparse
import itertools
import json
import random
import statistics
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import psycopg

from backend.sandbox import make_envelope, provision_tenants
from data.load_policies import get_db_url


def percentile(sorted_values: list[float], p: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(len(sorted_values) * p))]


def run_step(name: str, work: list[Any], fn: Callable[[httpx.Client, Any], bool], api_url: str, concurrency: int) -> dict[str, Any]:
    latencies: list[float] = []
    errors = 0
    lock = threading.Lock()
    local = threading.local()

    def one(item: Any) -> None:
        nonlocal errors
        if not hasattr(local, "client"):
            local.client = httpx.Client(base_url=api_url, timeout=60)
        start = time.perf_counter()
        try:
            ok = fn(local.client, item)
        except Exception:
            ok = False
        took = time.perf_counter() - start
        with lock:
            latencies.append(took)
            errors += 0 if ok else 1

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(concurrency) as pool:
        list(pool.map(one, work))
    wall = time.perf_counter() - wall_start
    latencies.sort()
    return {
        "step": name, "concurrency": concurrency, "requests": len(work), "errors": errors,
        "per_second": round(len(work) / wall, 1),
        "p50_ms": round(percentile(latencies, 0.50) * 1000, 1),
        "p95_ms": round(percentile(latencies, 0.95) * 1000, 1),
        "p99_ms": round(percentile(latencies, 0.99) * 1000, 1),
        "max_ms": round((latencies[-1] if latencies else 0) * 1000, 1),
    }


def run(api_url: str, ingest: int, levels: list[int], label: str, encrypt: bool = False) -> dict[str, Any]:
    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        tenant = provision_tenants(conn, 1, prefix=f"load-{uuid.uuid4().hex[:6]}", encrypt=encrypt)[0]
    ingest_key, analyst_key = tenant["keys"]["lender_ingest"], tenant["keys"]["lender_analyst"]
    rng = random.Random(7)
    now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    ids: list[str] = []
    ids_lock = threading.Lock()
    sequence = itertools.count()
    results: list[dict[str, Any]] = []

    def ingest_one(c: httpx.Client, i: int) -> bool:
        env = make_envelope(random.Random(i), i, tenant["tenant_id"], now)
        r = c.post("/v1/decisions", json=env, headers={"X-API-Key": ingest_key, "Idempotency-Key": f"{label}-{i}"})
        if r.status_code in (200, 201):
            with ids_lock:
                ids.append(r.json()["decision_id"])
            return True
        return False

    for level in levels:
        results.append(run_step("ingest one decision", [next(sequence) for _ in range(ingest)], ingest_one, api_url, level))

    analyst = {"X-API-Key": analyst_key}
    sample = [rng.choice(ids) for _ in range(min(len(ids) * 2, 600))] if ids else []
    for level in levels:
        results.append(run_step("read a decision", sample, lambda c, d: c.get(f"/decisions/{d}", headers=analyst).status_code == 200, api_url, level))
        results.append(run_step("check a record's chain", sample, lambda c, d: c.get(f"/decisions/{d}/verify", headers=analyst).status_code == 200, api_url, level))
        results.append(run_step("list 100 newest", list(range(200)), lambda c, _: c.get("/decisions?limit=100", headers=analyst).status_code == 200, api_url, level))

    with httpx.Client(base_url=api_url, timeout=300) as c:
        start = time.perf_counter()
        sealed = c.post("/v1/seals/run", headers=analyst).json()
        seal_seconds = round(time.perf_counter() - start, 2)
    for level in levels:
        results.append(run_step("get a seal proof", sample, lambda c, d: c.get(f"/v1/decisions/{d}/seal", headers=analyst).status_code == 200, api_url, level))
        results.append(run_step("explain a decision (no model)", sample[:200], lambda c, d: c.post(f"/reconstruct/{d}", headers=analyst).status_code == 200, api_url, level))
    with httpx.Client(base_url=api_url, timeout=600) as c:
        start = time.perf_counter()
        check = c.get("/v1/seals/verify", headers=analyst).json()
        verify_seconds = round(time.perf_counter() - start, 2)
    return {
        "label": label, "decisions_sent": len(ids), "seal": {"records": (sealed.get("batch") or {}).get("leaf_count"), "seconds": seal_seconds},
        "seal_check": {"ok": check.get("ok"), "records": check.get("records"), "seconds": verify_seconds}, "steps": results,
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [f"### {report['label']}", "", *( [report["note"], ""] if report.get("note") else [] ), f"{report['decisions_sent']} decisions sent. Sealing {report['seal']['records']} records took {report['seal']['seconds']} s; "
             f"checking all seals ({report['seal_check']['records']} records) took {report['seal_check']['seconds']} s (ok: {report['seal_check']['ok']}).", "",
             "| Step | Concurrency | Requests | Errors | Per second | p50 ms | p95 ms | p99 ms |", "|---|---|---|---|---|---|---|---|"]
    for s in report["steps"]:
        lines.append(f"| {s['step']} | {s['concurrency']} | {s['requests']} | {s['errors']} | {s['per_second']} | {s['p50_ms']} | {s['p95_ms']} | {s['p99_ms']} |")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load-test a running DecisionTrace API (creates a throwaway lender).")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--ingest", type=int, default=300, help="decisions to send at each concurrency level")
    parser.add_argument("--concurrency", default="1,8", help="comma-separated levels")
    parser.add_argument("--label", default="run")
    parser.add_argument("--note", default="", help="free text printed under the title (machine, database, settings)")
    parser.add_argument("--encrypt", action="store_true", help="use a lender whose evidence is protected (encrypted)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = run(args.api_url, args.ingest, [int(x) for x in args.concurrency.split(",")], args.label, args.encrypt)
    report["note"] = args.note
    print(json.dumps(report, indent=1) if args.json else markdown(report))


if __name__ == "__main__":
    main()

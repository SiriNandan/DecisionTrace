"""Optional external anchoring of decision chain heads.

Each new decision appends `(decision_id, chain_length, chain_head, decided_at)` to an append-only
JSONL log. Log lines are themselves hash-chained, and HMAC-signed when `DT_ANCHOR_KEY` is set.
Point `DT_ANCHOR_LOG` at storage outside the database's trust domain (a WORM bucket mount, a
separately permissioned volume, a git-tracked file pushed elsewhere) to close the anchoring gap
described in docs/limitations.md. A log on the same host and under the same admin as the database
adds little: the file is only as independent as where it is kept.

CLI:  python -m backend.anchoring verify   # log integrity + comparison with the database
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

GENESIS = "ANCHOR_GENESIS"


def anchor_log_path() -> Path | None:
    raw = os.environ.get("DT_ANCHOR_LOG", "").strip()
    return Path(raw) if raw else None


def _line_hash(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def _sign(body: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def _read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def append_anchor(
    decision_id: str,
    chain_length: int,
    chain_head: str,
    decided_at: str | datetime | None,
    *,
    path: Path | None = None,
    key: str | None = None,
) -> dict[str, Any] | None:
    """Append one anchor record; returns it, or None when anchoring is not configured."""
    target = path or anchor_log_path()
    if target is None:
        return None
    signing_key = key if key is not None else os.environ.get("DT_ANCHOR_KEY", "")
    lines = _read_lines(target)
    marker = f'"chain_head":"{chain_head}","chain_length":{int(chain_length)},"decided_at"'
    if any(f'"decision_id":"{decision_id}"' in ln and marker in ln for ln in lines):
        return None  # idempotent replay of POST /decide: already anchored
    prev = _line_hash(lines[-1]) if lines else GENESIS
    record: dict[str, Any] = {
        "decision_id": str(decision_id),
        "chain_length": int(chain_length),
        "chain_head": str(chain_head),
        "decided_at": decided_at.isoformat() if isinstance(decided_at, datetime) else decided_at,
        "prev_line_sha256": prev,
    }
    body = json.dumps(record, sort_keys=True, separators=(",", ":"))
    if signing_key:
        record["hmac_sha256"] = _sign(body, signing_key)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    return record


def verify_anchor_log(path: Path, key: str | None = None) -> dict[str, Any]:
    """Check the log's own hash chain (and signatures when a key is given)."""
    signing_key = key if key is not None else os.environ.get("DT_ANCHOR_KEY", "")
    prev = GENESIS
    records: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for n, line in enumerate(_read_lines(path), start=1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {n}: not valid JSON")
            break
        if rec.get("prev_line_sha256") != prev:
            problems.append(f"line {n}: previous-line hash does not match (log edited, reordered or truncated)")
        if signing_key:
            sig = rec.get("hmac_sha256")
            body = json.dumps({k: v for k, v in rec.items() if k != "hmac_sha256"}, sort_keys=True, separators=(",", ":"))
            if not sig or not hmac.compare_digest(sig, _sign(body, signing_key)):
                problems.append(f"line {n}: signature missing or invalid")
        records[str(rec.get("decision_id"))] = rec
        prev = _line_hash(line)
    return {"ok": not problems, "entries": len(records), "problems": problems, "records": records}


def compare_with_database(db_url: str, path: Path, key: str | None = None) -> dict[str, Any]:
    """Compare stored chain heads against the anchor log; reports mismatches and unanchored decisions."""
    import psycopg

    log = verify_anchor_log(path, key)
    mismatched: list[str] = []
    missing_in_db: list[str] = []
    seen: set[str] = set()
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        rows = conn.execute("SELECT decision_id::text, chain_length, chain_head FROM decisions").fetchall()
    db = {r[0]: (int(r[1]), str(r[2])) for r in rows}
    for decision_id, rec in log["records"].items():
        seen.add(decision_id)
        if decision_id not in db:
            missing_in_db.append(decision_id)
        elif db[decision_id] != (rec["chain_length"], rec["chain_head"]):
            mismatched.append(decision_id)
    unanchored = sorted(set(db) - seen)
    return {
        "log_ok": log["ok"],
        "log_problems": log["problems"],
        "anchored": len(seen),
        "mismatched_heads": mismatched,
        "anchored_but_missing_in_db": missing_in_db,
        "not_anchored": len(unanchored),
        "ok": log["ok"] and not mismatched and not missing_in_db,
    }


def main() -> int:
    import argparse

    from data.load_policies import get_db_url

    parser = argparse.ArgumentParser(description="Verify the anchor log against the database.")
    parser.add_argument("command", choices=["verify"])
    parser.add_argument("--log", default=None, help="Defaults to DT_ANCHOR_LOG")
    parser.add_argument("--db-url", default=None)
    args = parser.parse_args()
    path = Path(args.log) if args.log else anchor_log_path()
    if path is None:
        print("No anchor log configured (set DT_ANCHOR_LOG or pass --log).")
        return 2
    result = compare_with_database(get_db_url(args.db_url), path)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

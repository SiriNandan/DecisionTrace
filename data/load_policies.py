# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Parse policy markdown files with YAML front matter and load them into PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import os
from pathlib import Path
from typing import Any

import psycopg
import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_POLICIES_DIR = ROOT_DIR / "policies"
MIGRATION_FILE = ROOT_DIR / "migrations" / "001_init.sql"
SYNTHETIC_LINE = "SYNTHETIC DATA - FOR DEMONSTRATION."


@dataclass(frozen=True)
class PolicyRecord:
    policy_id: str
    policy_family: str
    effective_from: date
    effective_to: date | None
    text_sha256: str
    body: str


def _parse_env_file(path: Path) -> dict[str, str]:
    env_map: dict[str, str] = {}
    if not path.is_file():
        return env_map
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env_map[key.strip()] = val.strip()
    return env_map


def get_db_url(explicit_url: str | None = None) -> str:
    if explicit_url:
        return explicit_url
    if "DB_URL" in os.environ:
        return os.environ["DB_URL"]
    env_map = _parse_env_file(ROOT_DIR / ".env")
    if "DB_URL" in env_map:
        return env_map["DB_URL"]
    example_map = _parse_env_file(ROOT_DIR / ".env.example")
    return example_map["DB_URL"]


def _coerce_date(val: Any) -> date | None:
    if val is None or val == "":
        return None
    if isinstance(val, date):
        return val
    return date.fromisoformat(str(val))


def parse_policy_file(file_path: Path) -> PolicyRecord:
    """Parse a single policy markdown file with YAML front matter."""
    raw_text = file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    lines = raw_text.splitlines()
    if not lines:
        raise ValueError(f"Empty policy file: {file_path}")

    first_clean = lines[0].lstrip("# ").strip()
    if first_clean != SYNTHETIC_LINE and SYNTHETIC_LINE not in raw_text:
        raise ValueError(f"Policy file {file_path} missing mandatory synthetic data header")

    # Locate YAML front matter delimited by '---'
    start_idx: int | None = None
    end_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip() == "---":
            if start_idx is None:
                start_idx = idx
            else:
                end_idx = idx
                break

    if start_idx is None or end_idx is None or end_idx <= start_idx:
        raise ValueError(f"Policy file {file_path} missing valid YAML front matter")

    front_matter_raw = "\n".join(lines[start_idx + 1 : end_idx])
    meta = yaml.safe_load(front_matter_raw)
    if not isinstance(meta, dict):
        raise ValueError(f"Invalid YAML front matter in {file_path}")

    body = "\n".join(lines[end_idx + 1 :]).strip() + "\n"
    if SYNTHETIC_LINE not in body:
        raise ValueError(f"Policy markdown body in {file_path} must include {SYNTHETIC_LINE}")

    policy_id = str(meta["policy_id"])
    policy_family = str(meta["policy_family"])
    effective_from = _coerce_date(meta["effective_from"])
    if effective_from is None:
        raise ValueError(f"Policy {policy_id} requires effective_from")
    effective_to = _coerce_date(meta.get("effective_to"))

    if effective_to is not None and effective_to < effective_from:
        raise ValueError(
            f"Policy {policy_id} has effective_to ({effective_to}) before effective_from ({effective_from})"
        )

    text_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return PolicyRecord(
        policy_id=policy_id,
        policy_family=policy_family,
        effective_from=effective_from,
        effective_to=effective_to,
        text_sha256=text_sha256,
        body=body,
    )


def validate_non_overlapping_ranges(policies: list[PolicyRecord]) -> None:
    """Assert that within each policy_family, effective ranges do not overlap."""
    by_family: dict[str, list[PolicyRecord]] = {}
    for pol in policies:
        by_family.setdefault(pol.policy_family, []).append(pol)

    for family, fam_policies in by_family.items():
        ordered = sorted(fam_policies, key=lambda p: (p.effective_from, p.policy_id))
        for i in range(len(ordered) - 1):
            curr = ordered[i]
            nxt = ordered[i + 1]
            if curr.effective_to is None:
                raise ValueError(
                    f"Open-ended policy {curr.policy_id} in family '{family}' overlaps with subsequent policy {nxt.policy_id} starting {nxt.effective_from}"
                )
            if curr.effective_to >= nxt.effective_from:
                raise ValueError(
                    f"Overlapping effective date ranges in family '{family}': {curr.policy_id} ({curr.effective_from}..{curr.effective_to}) overlaps {nxt.policy_id} ({nxt.effective_from}..{nxt.effective_to})"
                )


def load_policy_records(policies_dir: Path = DEFAULT_POLICIES_DIR) -> list[PolicyRecord]:
    """Read and validate all policy markdown files from policies_dir."""
    md_files = sorted(policies_dir.glob("*.md"))
    records = [parse_policy_file(p) for p in md_files]
    validate_non_overlapping_ranges(records)
    return sorted(records, key=lambda r: (r.effective_from, r.policy_id))


def load_policies(
    db_url: str | None = None,
    policies_dir: Path = DEFAULT_POLICIES_DIR,
) -> list[PolicyRecord]:
    """Insert or update policies (with SHA-256 of text) into the PostgreSQL policies table."""
    records = load_policy_records(policies_dir)
    target_url = get_db_url(db_url)

    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            if MIGRATION_FILE.is_file():
                cur.execute(MIGRATION_FILE.read_text(encoding="utf-8"))
            for rec in records:
                cur.execute(
                    """
                    INSERT INTO policies (
                        policy_id, policy_family, effective_from, effective_to, text_sha256, body
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (policy_id) DO UPDATE SET
                        policy_family = EXCLUDED.policy_family,
                        effective_from = EXCLUDED.effective_from,
                        effective_to = EXCLUDED.effective_to,
                        text_sha256 = EXCLUDED.text_sha256,
                        body = EXCLUDED.body;
                    """,
                    (
                        rec.policy_id,
                        rec.policy_family,
                        rec.effective_from,
                        rec.effective_to,
                        rec.text_sha256,
                        rec.body,
                    ),
                )
        conn.commit()
    return records


def main() -> None:
    records = load_policies()
    for r in records:
        eff_to = r.effective_to.isoformat() if r.effective_to else "open-ended"
        print(
            f"Loaded {r.policy_id} ({r.policy_family}): {r.effective_from.isoformat()} -> {eff_to} [sha256={r.text_sha256[:12]}...]"
        )


if __name__ == "__main__":
    main()


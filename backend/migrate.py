"""Bring a database up to date by applying every migrations/*.sql in order (each file is idempotent).

Runs as the owner role (DB_URL), never the API role. The API calls this on start-up in local development so that
an older database picks up new tables instead of failing.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import psycopg

from data.load_policies import get_db_url

logger = logging.getLogger(__name__)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# One table from each migration: if any is missing, the database is behind.
EXPECTED_TABLES = ("decisions", "tenants", "mappings", "quality_findings", "claim_links", "risk_signals")


def apply_migrations(db_url: str | None = None) -> list[str]:
    applied = []
    with psycopg.connect(get_db_url(db_url), connect_timeout=5) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
        conn.commit()
    return applied


def auto_migrate_enabled() -> bool:
    flag = os.environ.get("DT_AUTO_MIGRATE", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return os.environ.get("ENV", "dev").lower() == "dev"


def schema_status(conn: psycopg.Connection[Any]) -> dict[str, Any]:
    missing = [
        t for t in EXPECTED_TABLES
        if conn.execute("SELECT to_regclass(%s)", (f"public.{t}",)).fetchone()[0] is None
    ]
    return {"ok": not missing, "missing": missing}

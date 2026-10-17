# SYNTHETIC DATA - FOR DEMONSTRATION.
"""One-command local setup: create the database, apply migrations, load policies, register the model, seed scenarios.

Usage:
    python -m data.seed            # idempotent; adds the 10 scenarios + baseline decisions
    python -m data.seed --reset    # drop and recreate the target database first (destroys its data)
"""

from __future__ import annotations

import argparse
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from data.load_policies import get_db_url, load_policies
from data.seed_scenarios import DATA_DIR, seed_scenarios
from data.train_model import train_and_register_model

ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT_DIR / "migrations"


def _db_name(db_url: str) -> str:
    name = conninfo_to_dict(db_url).get("dbname")
    if not name:
        raise ValueError("DB_URL must name a database.")
    return str(name)


def ensure_database(db_url: str, *, reset: bool = False) -> None:
    """Create the database named in `db_url` if missing; with `reset`, drop it first."""
    name = _db_name(db_url)
    admin_url = make_conninfo(db_url, dbname="postgres")
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if exists and reset:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))
            exists = None
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))


def apply_migrations(db_url: str) -> None:
    """Apply every migrations/*.sql file in order (all are idempotent)."""
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))
        conn.commit()


def seed_database(
    db_url: str | None = None,
    *,
    reset: bool = False,
    data_dir: Path = DATA_DIR,
) -> dict[str, dict]:
    """Bring `db_url` to a ready-to-use state and return the seeded scenarios."""
    target = get_db_url(db_url)
    ensure_database(target, reset=reset)
    apply_migrations(target)
    load_policies(db_url=target)
    train_and_register_model(db_url=target, sync_db=True)
    return seed_scenarios(db_url=target, data_dir=data_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and seed the DecisionTrace database.")
    parser.add_argument("--db-url", default=None, help="Defaults to DB_URL from the environment or .env")
    parser.add_argument("--reset", action="store_true", help="Drop and recreate the database first")
    args = parser.parse_args()

    target = get_db_url(args.db_url)
    results = seed_database(target, reset=args.reset)
    print(f"Seeded {len(results)} scenarios into database '{_db_name(target)}'.")


if __name__ == "__main__":
    main()

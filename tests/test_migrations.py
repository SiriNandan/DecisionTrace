"""Tests for migrations/001_init.sql idempotency, schema constraints, and app_role permissions."""

from datetime import datetime, timezone
from pathlib import Path
import os
import uuid

import psycopg
from psycopg.types.json import Jsonb
import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent
MIGRATION_FILE = ROOT_DIR / "migrations" / "001_init.sql"


def _parse_env_file(path: Path) -> dict[str, str]:
    env_map: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env_map[key.strip()] = val.strip()
    return env_map


def _get_admin_db_url() -> str:
    env_map = _parse_env_file(ROOT_DIR / ".env.example")
    return os.environ.get("DB_URL", env_map["DB_URL"])


def _get_app_db_url() -> str:
    env_map = _parse_env_file(ROOT_DIR / ".env.example")
    return os.environ.get("APP_DB_URL", env_map["APP_DB_URL"])


def test_migration_is_idempotent() -> None:
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    admin_url = _get_admin_db_url()

    # Execute migration twice to verify idempotency
    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(sql)


def test_app_role_permissions_and_append_only_enforcement() -> None:
    sql = MIGRATION_FILE.read_text(encoding="utf-8")
    admin_url = _get_admin_db_url()
    app_url = _get_app_db_url()

    with psycopg.connect(admin_url, autocommit=True, connect_timeout=5) as admin_conn:
        with admin_conn.cursor() as cur:
            cur.execute(sql)

    decision_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    with psycopg.connect(app_url, connect_timeout=5) as app_conn:
        with app_conn.cursor() as cur:
            cur.execute("SELECT current_user;")
            assert cur.fetchone() == ("app_role",)
            cur.execute("SELECT set_config('app.tenant_id', 'demo', false);")

            # SELECT allowed on policies and model_registry
            cur.execute("SELECT count(*) FROM policies;")
            assert cur.fetchone() is not None
            cur.execute("SELECT count(*) FROM model_registry;")
            assert cur.fetchone() is not None

            # INSERT allowed on decisions, decision_snapshots, overrides, evidence_status
            cur.execute(
                """
                INSERT INTO decisions (
                    decision_id, application_id, decided_at, outcome,
                    model_version, policy_version, score, chain_length, chain_head
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    decision_id,
                    "APP-TEST-001",
                    now,
                    "APPROVE",
                    "loan_lr_v1",
                    "POL-2026-01",
                    0.812345,
                    1,
                    "abc123head",
                ),
            )

            cur.execute(
                """
                INSERT INTO decision_snapshots (
                    decision_id, seq, evidence_type, evidence_ref, source_id,
                    source_timestamp, payload, payload_sha256, prev_hash, entry_hash, captured_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    decision_id,
                    1,
                    "application",
                    "ev-ref-1",
                    "src-1",
                    now,
                    Jsonb({"income": 55000}),
                    "sha256_payload",
                    "GENESIS",
                    "abc123head",
                    now,
                ),
            )

            cur.execute(
                """
                INSERT INTO overrides (
                    decision_id, new_outcome, reason, actor_role, created_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    decision_id,
                    "REFER",
                    "Manual underwriter review requested for verification.",
                    "underwriter",
                    now,
                ),
            )

            cur.execute(
                """
                INSERT INTO evidence_status (
                    decision_id, evidence_ref, status, changed_at
                ) VALUES (%s, %s, %s, %s)
                """,
                (decision_id, "ev-ref-1", "available", now),
            )
        app_conn.commit()

    # Verify UPDATE on decisions fails as app_role
    with psycopg.connect(app_url, connect_timeout=5) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app_conn.cursor() as cur:
                cur.execute(
                    "UPDATE decisions SET outcome = 'DECLINE' WHERE decision_id = %s;",
                    (decision_id,),
                )

    # Verify DELETE on decisions fails as app_role
    with psycopg.connect(app_url, connect_timeout=5) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM decisions WHERE decision_id = %s;",
                    (decision_id,),
                )

    # Verify UPDATE on decision_snapshots fails as app_role
    with psycopg.connect(app_url, connect_timeout=5) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app_conn.cursor() as cur:
                cur.execute(
                    "UPDATE decision_snapshots SET entry_hash = 'tampered' WHERE decision_id = %s;",
                    (decision_id,),
                )

    # Verify DELETE on decision_snapshots fails as app_role
    with psycopg.connect(app_url, connect_timeout=5) as app_conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with app_conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM decision_snapshots WHERE decision_id = %s;",
                    (decision_id,),
                )


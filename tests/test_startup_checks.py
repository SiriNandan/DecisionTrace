"""Start-up checks: unsafe configuration is refused outside ENV=dev."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.crypto import DEV_MASTER
from backend.merkle import DEV_SEED
from backend.startup_checks import UnsafeConfigError, check_startup_config, config_problems

NAMES = ["ENV", "APP_DB_URL", "DT_SEAL_KEY", "DT_MASTER_KEY", "DT_ID_SECRET", "DT_ANCHOR_LOG", "DT_SEAL_PUBLISH_DIR", "CORS_ORIGINS"]


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


@pytest.fixture
def good_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    (tmp_path / "seals").mkdir()
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "prod")
    monkeypatch.setenv("DT_SEAL_KEY", _b64(os.urandom(32)))
    monkeypatch.setenv("DT_MASTER_KEY", _b64(os.urandom(32)))
    monkeypatch.setenv("APP_DB_URL", "postgresql://app_role:Zk3fPq9@postgres:5432/decisiontrace")
    monkeypatch.setenv("DT_ID_SECRET", "a-long-random-secret-value")
    monkeypatch.setenv("DT_ANCHOR_LOG", str(tmp_path / "anchors.jsonl"))
    monkeypatch.setenv("DT_SEAL_PUBLISH_DIR", str(tmp_path / "seals"))
    monkeypatch.setenv("CORS_ORIGINS", "https://demo.example.com")
    return monkeypatch


def test_dev_is_never_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "dev")
    check_startup_config()


def test_good_config_passes(good_env: pytest.MonkeyPatch) -> None:
    assert config_problems() == []
    check_startup_config()


def test_everything_missing_is_reported_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(UnsafeConfigError) as exc:
        check_startup_config()
    text = str(exc.value)
    for name in NAMES[1:]:
        assert name in text, name


@pytest.mark.parametrize(
    ("name", "value", "fragment"),
    [
        ("DT_SEAL_KEY", _b64(hashlib.sha256(DEV_SEED).digest()), "development default"),
        ("DT_MASTER_KEY", _b64(DEV_MASTER), "development default"),
        ("DT_SEAL_KEY", _b64(b"short"), "32 bytes"),
        ("DT_MASTER_KEY", "not base64 !!", "base64"),
        ("APP_DB_URL", "postgresql://app_role:app_role_local_dev@postgres:5432/x", "development password"),
        ("DT_ID_SECRET", "changeme", "DT_ID_SECRET"),
        ("DT_ID_SECRET", "tooshort", "16 characters"),
        ("DT_ANCHOR_LOG", "/no/such/dir/anchors.jsonl", "does not exist"),
        ("DT_SEAL_PUBLISH_DIR", "/no/such/dir", "does not exist"),
        ("CORS_ORIGINS", "*", "'*'"),
        ("CORS_ORIGINS", "http://localhost:5173", "local address"),
        ("CORS_ORIGINS", "http://demo.example.com", "not https"),
    ],
)
def test_unsafe_values_are_refused(good_env: pytest.MonkeyPatch, name: str, value: str, fragment: str) -> None:
    good_env.setenv(name, value)
    problems = config_problems()
    assert any(name in p and fragment in p for p in problems), problems


def test_apps_refuse_to_start_when_unsafe(monkeypatch: pytest.MonkeyPatch) -> None:
    from decision_api.app import create_app
    from recon_agent.app import app as recon_app

    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENV", "prod")
    for application in (create_app(), recon_app):
        with pytest.raises(UnsafeConfigError):
            with TestClient(application):
                pass


def test_app_role_password_is_replaced_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    """APP_DB_PASSWORD swaps the development password; without it nothing changes."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from backend.migrate import set_app_role_password

    owner = os.environ["DB_URL"]
    new_pw = "Rot4ted" + os.urandom(6).hex()
    with psycopg.connect(owner, connect_timeout=5) as conn:
        monkeypatch.delenv("APP_DB_PASSWORD", raising=False)
        assert set_app_role_password(conn) is False
        monkeypatch.setenv("APP_DB_PASSWORD", new_pw)
        assert set_app_role_password(conn) is True
        conn.commit()
    d = conninfo_to_dict(owner)
    try:
        with psycopg.connect(make_conninfo(owner, user="app_role", password=new_pw), connect_timeout=5) as c:
            assert c.execute("SELECT current_user").fetchone()[0] == "app_role"
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(make_conninfo(owner, user="app_role", password="app_role_local_dev"), connect_timeout=5)
    finally:  # put the development password back for the rest of the suite
        with psycopg.connect(owner, connect_timeout=5) as conn:
            conn.execute("ALTER ROLE app_role WITH LOGIN PASSWORD 'app_role_local_dev'")
            conn.commit()
    assert d.get("dbname")

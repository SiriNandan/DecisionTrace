"""Refuse to start with unsafe configuration when ENV is not dev.

Each key, path and origin that a development default would quietly cover for must be set explicitly in a
deployed environment. All problems are reported together so they can be fixed in one pass.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from backend.crypto import DEV_MASTER
from backend.merkle import DEV_SEED

_PLACEHOLDERS = {"", "changeme", "change-me", "secret", "password", "test", "dev", "default", "your_secret_here"}
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


class UnsafeConfigError(RuntimeError):
    """Raised at start-up when the configuration is not safe outside development."""


def _is_dev() -> bool:
    return os.environ.get("ENV", "dev").strip().lower() == "dev"


def _check_key(name: str, dev_default: bytes, problems: list[str]) -> None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        problems.append(f"{name} is not set.")
        return
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        problems.append(f"{name} is not valid base64.")
        return
    if len(key) != 32:
        problems.append(f"{name} must be 32 bytes, base64 encoded.")
    elif key == dev_default:
        problems.append(f"{name} is the public development default.")


def _check_writable_dir(name: str, problems: list[str], *, is_file: bool) -> None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        problems.append(f"{name} is not set.")
        return
    path = Path(raw)
    directory = path.parent if is_file else path
    if not directory.is_dir():
        problems.append(f"{name}: directory {directory} does not exist.")
    elif not os.access(directory, os.W_OK):
        problems.append(f"{name}: directory {directory} is not writable.")


def config_problems() -> list[str]:
    """Everything wrong with the current environment for a non-dev run (empty in dev)."""
    if _is_dev():
        return []
    problems: list[str] = []

    _check_key("DT_SEAL_KEY", hashlib.sha256(DEV_SEED).digest(), problems)
    _check_key("DT_MASTER_KEY", DEV_MASTER, problems)

    id_secret = os.environ.get("DT_ID_SECRET", "").strip()
    if id_secret.lower() in _PLACEHOLDERS:
        problems.append("DT_ID_SECRET is not set or is a placeholder.")
    elif len(id_secret) < 16:
        problems.append("DT_ID_SECRET must be at least 16 characters.")

    app_db = os.environ.get("APP_DB_URL", "").strip()
    if not app_db:
        problems.append("APP_DB_URL is not set (the API would connect as the owner role and bypass row-level security).")
    elif "app_role_local_dev" in app_db:
        problems.append("APP_DB_URL still uses the development password for app_role (set APP_DB_PASSWORD).")

    _check_writable_dir("DT_ANCHOR_LOG", problems, is_file=True)
    _check_writable_dir("DT_SEAL_PUBLISH_DIR", problems, is_file=False)

    origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if not origins:
        problems.append("CORS_ORIGINS is not set (the local-development default is not allowed).")
    for origin in origins:
        if origin == "*":
            problems.append("CORS_ORIGINS must not contain '*'.")
        elif any(f"//{h}" in origin for h in _LOCAL_HOSTS):
            problems.append(f"CORS_ORIGINS contains a local address: {origin}.")
        elif not origin.startswith("https://"):
            problems.append(f"CORS_ORIGINS entry is not https: {origin}.")
    return problems


def check_startup_config() -> None:
    """Raise UnsafeConfigError listing every problem; does nothing when ENV=dev."""
    problems = config_problems()
    if problems:
        raise UnsafeConfigError(
            "Refusing to start with unsafe configuration (ENV is not dev):\n  - " + "\n  - ".join(problems)
        )

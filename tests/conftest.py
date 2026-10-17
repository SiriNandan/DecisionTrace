"""Pytest session fixtures ensuring PostgreSQL is available for database tests."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import socket
import subprocess
from urllib.parse import urlparse

import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent


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


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _find_pg_bin() -> Path | None:
    pg_ctl = shutil.which("pg_ctl")
    if pg_ctl:
        return Path(pg_ctl).parent
    win_pg = Path(r"C:\Program Files\PostgreSQL")
    if win_pg.is_dir():
        for version_dir in sorted(win_pg.iterdir(), reverse=True):
            bin_dir = version_dir / "bin"
            if (bin_dir / "pg_ctl.exe").is_file():
                return bin_dir
    return None


@pytest.fixture(scope="session", autouse=True)
def ensure_postgres_running(tmp_path_factory: pytest.TempPathFactory):
    """Ensure PostgreSQL is listening on DB_URL port for tests."""
    env_map = _parse_env_file(ROOT_DIR / ".env.example")
    db_url = os.environ.get("DB_URL", env_map.get("DB_URL", ""))
    parsed = urlparse(db_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 5433
    user = parsed.username or "decisiontrace"
    dbname = (parsed.path or "/decisiontrace").lstrip("/") or "decisiontrace"

    if _is_port_open(host, port):
        yield
        return

    pg_bin = _find_pg_bin()
    if pg_bin is None:
        yield
        return

    pgdata = tmp_path_factory.mktemp("pgdata")
    initdb_exe = pg_bin / ("initdb.exe" if os.name == "nt" else "initdb")
    pg_ctl_exe = pg_bin / ("pg_ctl.exe" if os.name == "nt" else "pg_ctl")
    createdb_exe = pg_bin / ("createdb.exe" if os.name == "nt" else "createdb")

    subprocess.run(
        [
            str(initdb_exe),
            "-D",
            str(pgdata),
            "-U",
            user,
            "-A",
            "trust",
            "-E",
            "UTF8",
            "--no-locale",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            str(pg_ctl_exe),
            "-D",
            str(pgdata),
            "-l",
            str(pgdata / "postgres.log"),
            "-o",
            f"-p {port}",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        subprocess.run(
            [
                str(createdb_exe),
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                dbname,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        migration_sql = (ROOT_DIR / "migrations" / "001_init.sql").read_text(encoding="utf-8")
        import psycopg
        with psycopg.connect(db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(migration_sql)
            conn.commit()
        yield
    finally:
        subprocess.run(
            [str(pg_ctl_exe), "-D", str(pgdata), "-m", "fast", "stop"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


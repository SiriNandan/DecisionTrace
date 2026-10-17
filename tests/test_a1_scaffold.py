"""Tests for Task A1: Project layout, configuration templates, and PostgreSQL 15 connectivity."""

from pathlib import Path
import os
import psycopg
import pytest


ROOT_DIR = Path(__file__).resolve().parent.parent

REQUIRED_DIRS = [
    "decision_api",
    "recon_agent",
    "backend",
    "data",
    "data/scenarios",
    "policies",
    "migrations",
    "eval",
    "frontend",
    "docs",
    "tests",
]

REQUIRED_FILES = [
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
    "README.md",
    "TODO.md",
]

REQUIRED_ENV_KEYS = {
    "DB_URL",
    "LLM_BACKEND",
    "SEARCH_BACKEND",
    "STORAGE_BACKEND",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "MODEL_VERSION",
    "ENV",
    "VITE_API_URL",
    "VITE_USE_MOCKS",
}


def _parse_env_file(path: Path) -> dict[str, str]:
    env_map: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env_map[key.strip()] = val.strip()
    return env_map


def test_required_directories_exist() -> None:
    for rel_dir in REQUIRED_DIRS:
        dir_path = ROOT_DIR / rel_dir
        assert dir_path.is_dir(), f"Missing required directory: {rel_dir}"


def test_required_files_exist() -> None:
    for rel_file in REQUIRED_FILES:
        file_path = ROOT_DIR / rel_file
        assert file_path.is_file(), f"Missing required file: {rel_file}"


def test_env_example_contains_required_keys() -> None:
    env_map = _parse_env_file(ROOT_DIR / ".env.example")
    missing = REQUIRED_ENV_KEYS - set(env_map.keys())
    assert not missing, f"Missing keys in .env.example: {missing}"


def test_gitignore_excludes_dotenv() -> None:
    lines = [
        line.strip()
        for line in (ROOT_DIR / ".gitignore").read_text(encoding="utf-8").splitlines()
    ]
    assert ".env" in lines


def test_data_files_have_synthetic_header() -> None:
    data_dir = ROOT_DIR / "data"
    for file_path in data_dir.rglob("*"):
        if "__pycache__" in file_path.parts or file_path.suffix in {".pyc", ".pyo"}:
            continue
        if file_path.is_file():
            first_line = file_path.read_text(encoding="utf-8").splitlines()[0]
            assert "SYNTHETIC DATA - FOR DEMONSTRATION." in first_line, (
                f"Data file {file_path} missing mandatory header"
            )


def test_readme_wording_rules() -> None:
    readme_text = (ROOT_DIR / "README.md").read_text(encoding="utf-8").lower()
    assert "supports audit and explanation" in readme_text
    assert "tamper-evident against a retained reference" in readme_text
    assert "ensures compliance" not in readme_text
    assert "proves" not in readme_text


def test_todo_has_task_owner_status_columns() -> None:
    todo_text = (ROOT_DIR / "TODO.md").read_text(encoding="utf-8").lower()
    assert "task" in todo_text
    assert "owner" in todo_text
    assert "status" in todo_text


def test_postgres_connection() -> None:
    env_map = _parse_env_file(ROOT_DIR / ".env.example")
    db_url = os.environ.get("DB_URL", env_map["DB_URL"])
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1;")
            row = cur.fetchone()
            assert row == (1,)


"""Shared CSV helper for backend source readers."""

from __future__ import annotations

import csv
import io
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DATA_DIR = ROOT_DIR / "data"
SYNTHETIC_NOTICE = "SYNTHETIC DATA - FOR DEMONSTRATION."


def read_synthetic_csv(csv_path: Path) -> list[dict[str, str]]:
    """Read a synthetic CSV file, skipping the mandatory SYNTHETIC DATA comment header."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Source data file not found: {csv_path}")
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    data_lines = [
        line
        for line in lines
        if not line.lstrip().startswith("#") and line.strip() != SYNTHETIC_NOTICE
    ]
    return list(csv.DictReader(io.StringIO("\n".join(data_lines))))


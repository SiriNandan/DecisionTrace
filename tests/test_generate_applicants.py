"""Tests for data/generate_applicants.py and data/config.yaml."""

import csv
import hashlib
import io
from pathlib import Path

import numpy as np
import pytest

from data.generate_applicants import (
    DEFAULT_CONFIG_PATH,
    FORBIDDEN_COLUMNS,
    SYNTHETIC_HEADER,
    format_summary,
    generate_all,
    load_config,
)


def _read_synthetic_csv(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines, f"{path} is empty"
    assert lines[0] == SYNTHETIC_HEADER
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    return list(reader)


def test_config_has_required_fields() -> None:
    cfg = load_config(DEFAULT_CONFIG_PATH)
    assert "seed" in cfg
    assert cfg.get("num_applicants") == 2000
    profile_key = cfg["active_profile"]
    profile = cfg["profiles"][profile_key]
    assert "currency" in profile
    assert "income_distribution" in profile
    assert "region_profile" in profile


def test_reproducible_generation_and_correlations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dir_run1 = tmp_path / "run1"
    dir_run2 = tmp_path / "run2"

    paths1 = generate_all(output_dir=dir_run1, seed_override=42)
    out1 = capsys.readouterr().out
    paths2 = generate_all(output_dir=dir_run2, seed_override=42)
    out2 = capsys.readouterr().out

    # Summary prints distribution stats
    assert "Synthetic Applicant Dataset Summary (N=2000)" in out1
    assert out1 == out2

    # Re-running with same seed produces byte-for-byte identical files
    for key in ("applicants", "bureau_snapshots", "accounts"):
        bytes1 = paths1[key].read_bytes()
        bytes2 = paths2[key].read_bytes()
        assert hashlib.sha256(bytes1).hexdigest() == hashlib.sha256(bytes2).hexdigest(), (
            f"Non-deterministic output for {key}"
        )

    applicants = _read_synthetic_csv(paths1["applicants"])
    bureau = _read_synthetic_csv(paths1["bureau_snapshots"])
    accounts = _read_synthetic_csv(paths1["accounts"])

    assert len(applicants) == 2000
    assert len(bureau) == 2000
    assert len(accounts) == 2000

    # Verify forbidden columns are absent across all files
    for rows in (applicants, bureau, accounts):
        cols = {c.lower() for c in rows[0].keys()}
        assert not FORBIDDEN_COLUMNS.intersection(cols)

    # Verify age_band is stored in applicants
    assert "age_band" in applicants[0]

    # Verify bureau_snapshots columns
    for col in ("source_id", "source_timestamp", "credit_score", "delinquencies_24m"):
        assert col in bureau[0]

    # Check exact DTI formula and credit_score bounds (300-900)
    for row in applicants:
        monthly_inc = float(row["monthly_income"])
        debt = float(row["existing_debt_service"])
        dti = float(row["dti_ratio"])
        assert dti == pytest.approx(debt / monthly_inc, abs=1e-5)

        score = int(row["credit_score"])
        assert 300 <= score <= 900
        assert int(row["outcome_default_12m"]) in (0, 1)

    # Verify statistical correlations
    band_order = {"18-24": 0, "25-34": 1, "35-44": 2, "45-54": 3, "55-64": 4, "65+": 5}
    age_ranks = np.array([band_order[r["age_band"]] for r in applicants], dtype=float)
    emp_months = np.array([float(r["employment_months"]) for r in applicants])
    scores = np.array([float(r["credit_score"]) for r in applicants])
    delinq = np.array([float(r["delinquencies_24m"]) for r in applicants])
    acct_age = np.array([float(r["account_age_months"]) for r in applicants])
    incomes = np.array([float(r["annual_income"]) for r in applicants])
    req_amt = np.array([float(r["requested_amount"]) for r in applicants])

    assert np.corrcoef(emp_months, age_ranks)[0, 1] > 0.45
    assert np.corrcoef(scores, delinq)[0, 1] < -0.40
    assert np.corrcoef(scores, acct_age)[0, 1] > 0.35
    assert np.corrcoef(req_amt, incomes)[0, 1] > 0.50

    summary_str = format_summary(applicants)
    assert "corr(employment_months, age_band)" in summary_str


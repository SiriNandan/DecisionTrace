"""Tests for data/generate_fairness_scenario.py and eval/fairness.py."""

from __future__ import annotations

import psycopg
import pytest

from data.generate_applicants import DATA_DIR
from data.generate_fairness_scenario import (
    DEFAULT_FAIRNESS_POLICY,
    generate_fairness_scenario,
)
from data.load_policies import get_db_url
from decision_api.service import compute_model_score, evaluate_application
from eval.fairness import (
    DEFAULT_DIFFERENCE_THRESHOLD,
    DEFAULT_RATIO_THRESHOLD,
    _read_synthetic_csv,
    evaluate_fairness_from_files,
)


def test_fairness_groups_table_and_csv_generation_and_disparity_detection() -> None:
    """Verify fairness scenario generates >=300 per group, syncs table/CSV, and detects proxy disparity."""
    paths = generate_fairness_scenario(
        output_dir=DATA_DIR,
        seed=2026,
        group_size=400,
        sync_db=True,
    )

    groups_rows = _read_synthetic_csv(paths["fairness_groups"])
    outcome_rows = _read_synthetic_csv(paths["fairness_outcomes"])

    assert len(groups_rows) == 800
    assert list(groups_rows[0].keys()) == ["applicant_id", "test_group"]
    assert "test_group" not in outcome_rows[0]

    # Verify separate PostgreSQL table fairness_groups(applicant_id, test_group) was populated
    db_url = get_db_url()
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT test_group, COUNT(*) FROM fairness_groups GROUP BY test_group ORDER BY test_group;"
            )
            db_counts = dict(cur.fetchall())
    assert db_counts == {"A": 400, "B": 400}

    # Evaluate fairness report
    report = evaluate_fairness_from_files(
        data_dir=DATA_DIR,
        difference_threshold=DEFAULT_DIFFERENCE_THRESHOLD,
        ratio_threshold=DEFAULT_RATIO_THRESHOLD,
    )

    grp_a = report["groups"]["A"]
    grp_b = report["groups"]["B"]

    # Group sizes at least 300 each
    assert grp_a["count"] >= 300
    assert grp_b["count"] >= 300

    # Rates per group sum to 1.0
    for grp in (grp_a, grp_b):
        total_rate = grp["approval_rate"] + grp["decline_rate"] + grp["referral_rate"]
        assert total_rate == pytest.approx(1.0, abs=1e-5)

    # Group B has a higher share of short employment_months (< 12m) acting as proxy
    assert grp_b["short_employment_share"] > grp_a["short_employment_share"] + 0.50

    # Known disparity in approval rates is detected above stated threshold
    assert grp_a["approval_rate"] > grp_b["approval_rate"]
    assert report["selection_rate_difference"] > DEFAULT_DIFFERENCE_THRESHOLD
    assert report["selection_rate_ratio"] < DEFAULT_RATIO_THRESHOLD
    assert report["disparity_above_difference_threshold"] is True
    assert report["disparity_below_ratio_threshold"] is True
    assert report["disparity_detected"] is True

    # Limitations note adheres to project wording rules
    note_lower = report["limitations_note"].lower()
    assert "supports audit and explanation" in note_lower
    assert "employment_months" in note_lower
    assert "ensures compliance" not in note_lower
    assert "proves" not in note_lower


def test_test_group_is_rejected_as_model_feature() -> None:
    """Ensure test_group from fairness_groups cannot be passed as a feature to scoring or evaluation."""
    valid_features = {
        "annual_income": 54000.0,
        "monthly_income": 4500.0,
        "existing_debt_service": 1350.0,
        "dti_ratio": 0.30,
        "credit_score": 700,
        "delinquencies_24m": 0,
        "account_age_months": 48,
        "employment_months": 36,
        "requested_amount": 10000.0,
        "test_group": "B",
    }
    with pytest.raises(ValueError, match="test_group"):
        compute_model_score(valid_features)

    with pytest.raises(ValueError, match="test_group"):
        evaluate_application(valid_features, DEFAULT_FAIRNESS_POLICY)


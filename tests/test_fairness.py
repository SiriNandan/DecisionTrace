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
from decision_api.service import _resolve_model_registry_row, evaluate_rules_and_outcome
from eval.fairness import (
    DEFAULT_DIFFERENCE_THRESHOLD,
    DEFAULT_RATIO_THRESHOLD,
    _read_synthetic_csv,
    evaluate_fairness_from_files,
)


def _check_groups_and_report(paths: dict) -> dict:
    groups_rows = _read_synthetic_csv(paths["fairness_groups"])
    outcome_rows = _read_synthetic_csv(paths["fairness_outcomes"])

    assert len(groups_rows) == 800
    assert list(groups_rows[0].keys()) == ["applicant_id", "test_group"]
    assert "test_group" not in outcome_rows[0]

    report = evaluate_fairness_from_files(
        data_dir=paths["fairness_groups"].parent,
        difference_threshold=DEFAULT_DIFFERENCE_THRESHOLD,
        ratio_threshold=DEFAULT_RATIO_THRESHOLD,
    )
    grp_a = report["groups"]["A"]
    grp_b = report["groups"]["B"]
    assert grp_a["count"] >= 300
    assert grp_b["count"] >= 300
    for grp in (grp_a, grp_b):
        total_rate = grp["approval_rate"] + grp["decline_rate"] + grp["referral_rate"]
        assert total_rate == pytest.approx(1.0, abs=1e-5)
    # Group B has a higher share of short employment_months (< 12m) acting as proxy
    assert grp_b["short_employment_share"] > grp_a["short_employment_share"] + 0.50

    note_lower = report["limitations_note"].lower()
    assert "supports audit and explanation" in note_lower
    assert "employment_months" in note_lower
    assert "ensures compliance" not in note_lower
    assert "proves" not in note_lower
    return report


def test_fairness_scenario_with_registered_model() -> None:
    """The scenario is scored by the registered model (same path as live decisions) and syncs fairness_groups."""
    paths = generate_fairness_scenario(output_dir=DATA_DIR, seed=2026, group_size=400, sync_db=True)

    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT test_group, COUNT(*) FROM fairness_groups GROUP BY test_group ORDER BY test_group;"
            )
            assert dict(cur.fetchall()) == {"A": 400, "B": 400}
        registry_row = _resolve_model_registry_row(conn)

    report = _check_groups_and_report(paths)
    # Whatever the registered model does, the report flags must follow the thresholds exactly.
    assert report["disparity_detected"] == (
        report["disparity_above_difference_threshold"] or report["disparity_below_ratio_threshold"]
    )
    # The registered model puts almost no weight on employment_months, so the proxy gap is not planted.
    assert abs(float(registry_row["coefficients"]["employment_months"])) < 0.1


def test_fairness_detector_flags_proxy_when_model_relies_on_it(tmp_path) -> None:
    """Detector check: a registered-model copy with a strong employment_months weight must be flagged."""
    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        registry_row = _resolve_model_registry_row(conn)
    stressed = dict(registry_row)
    stressed["coefficients"] = {**registry_row["coefficients"], "employment_months": -4.0}

    paths = generate_fairness_scenario(
        output_dir=tmp_path, seed=2026, group_size=400, sync_db=False, registry_row=stressed
    )
    report = _check_groups_and_report(paths)
    assert report["groups"]["A"]["approval_rate"] > report["groups"]["B"]["approval_rate"]
    assert report["disparity_detected"] is True


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
    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        registry_row = _resolve_model_registry_row(conn)

    with pytest.raises(ValueError, match="test_group"):
        evaluate_rules_and_outcome(
            applicant_params=valid_features,
            policy=DEFAULT_FAIRNESS_POLICY,
            registry_row=registry_row,
        )


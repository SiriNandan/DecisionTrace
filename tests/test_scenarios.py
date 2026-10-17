"""Tests for data/scenarios/scenarios.yaml, data/seed_scenarios.py, and decision_api/service.py."""

from __future__ import annotations

import psycopg
import pytest

from data.load_policies import get_db_url
from data.seed_scenarios import (
    DEFAULT_SCENARIOS_PATH,
    load_scenarios_yaml,
    seed_scenarios,
)
from decision_api.service import create_decision


EXPECTED_SCENARIO_NAMES = [
    "clear_decline",
    "clear_approve",
    "boundary_dti_old_policy",
    "boundary_dti_new_policy",
    "missing_payslip_referral",
    "manual_review_rule_2026",
    "override_case",
    "tampered_payload",
    "deleted_final_row",
    "erased_evidence",
]


def test_scenarios_yaml_structure_and_names() -> None:
    """Verify scenarios.yaml defines all 10 named scenarios with required fields."""
    scenarios = load_scenarios_yaml(DEFAULT_SCENARIOS_PATH)
    names = [s["name"] for s in scenarios]
    assert names == EXPECTED_SCENARIO_NAMES

    for scen in scenarios:
        assert "applicant_params" in scen
        assert "decision_date" in scen
        assert "expected_outcome" in scen
        assert "expected_policy_version" in scen
        assert "expected_cited_sections" in scen
        assert isinstance(scen["expected_cited_sections"], list)
        assert len(scen["expected_cited_sections"]) >= 1

    # Verify boundary_dti_old_policy and boundary_dti_new_policy share identical applicant_params
    by_name = {s["name"]: s for s in scenarios}
    assert (
        by_name["boundary_dti_old_policy"]["applicant_params"]
        == by_name["boundary_dti_new_policy"]["applicant_params"]
    )
    assert by_name["boundary_dti_old_policy"]["applicant_params"]["dti_ratio"] == pytest.approx(0.43)


def test_seed_scenarios_outcomes_policies_citations_and_post_seed_mutation() -> None:
    """Seed all 10 scenarios and assert outcomes, policy versions, citations, and live record mutations."""
    results = seed_scenarios()
    assert list(results.keys()) == EXPECTED_SCENARIO_NAMES

    for name, entry in results.items():
        scen = entry["scenario"]
        rec = entry["reconstructed"]
        live = entry["current_live_applicant"]

        assert rec["effective_outcome"] == scen["expected_outcome"], f"Outcome mismatch on {name}"
        assert rec["policy_version"] == scen["expected_policy_version"], f"Policy mismatch on {name}"
        assert rec["cited_sections"] == scen["expected_cited_sections"], (
            f"Cited sections mismatch on {name}"
        )
        assert rec["status"] == scen["expected_status"], f"Verification status mismatch on {name}"

        # Prove reconstruction uses preserved decision-time evidence while current applicant records are mutated
        mutation = scen["post_seed_mutation"]
        assert live["annual_income"] == pytest.approx(float(mutation["annual_income"]))
        assert live["monthly_income"] == pytest.approx(float(mutation["monthly_income"]))
        assert live["dti_ratio"] == pytest.approx(float(mutation["dti_ratio"]))

        # For non-tampered scenarios, preserved snapshot params must match original decision-time params
        if name != "tampered_payload":
            orig = scen["applicant_params"]
            preserved = rec["preserved_applicant_params"]
            assert preserved["annual_income"] == pytest.approx(float(orig["annual_income"]))
            assert preserved["monthly_income"] == pytest.approx(float(orig["monthly_income"]))
            assert preserved["dti_ratio"] == pytest.approx(float(orig["dti_ratio"]))
            assert preserved["dti_ratio"] != pytest.approx(live["dti_ratio"])

    # Specific checks for boundary policy shift (LP-2025-v1 vs LP-2025-v2)
    assert results["boundary_dti_old_policy"]["reconstructed"]["effective_outcome"] == "APPROVE"
    assert results["boundary_dti_old_policy"]["reconstructed"]["policy_version"] == "LP-2025-v1"
    assert results["boundary_dti_new_policy"]["reconstructed"]["effective_outcome"] == "DECLINE"
    assert results["boundary_dti_new_policy"]["reconstructed"]["policy_version"] == "LP-2025-v2"

    # Specific check for override_case: decisions row stays DECLINE, overrides row has APPROVE + >=20 char reason
    override_rec = results["override_case"]["reconstructed"]
    assert override_rec["initial_outcome"] == "DECLINE"
    assert override_rec["effective_outcome"] == "APPROVE"
    assert override_rec["override"] is not None
    assert len(override_rec["override"]["reason"]) >= 20

    # Specific check for tampered_payload
    tampered_rec = results["tampered_payload"]["reconstructed"]
    assert tampered_rec["chain_valid"] is False
    assert tampered_rec["status"] == "tamper_detected"
    assert any("payload_sha256 mismatch" in r for r in tampered_rec["tamper_reasons"])

    # Specific check for deleted_final_row
    deleted_rec = results["deleted_final_row"]["reconstructed"]
    assert deleted_rec["chain_valid"] is False
    assert deleted_rec["status"] == "tamper_detected"
    assert any("snapshot_count" in r for r in deleted_rec["tamper_reasons"])

    # Specific check for erased_evidence
    erased_rec = results["erased_evidence"]["reconstructed"]
    assert erased_rec["chain_valid"] is True
    assert erased_rec["status"] == "evidence_unavailable"
    assert (
        erased_rec["evidence_statuses"].get("payslip:APP-0020") == "erased_by_retention"
    )


def test_backdating_rejected_outside_dev_without_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure backdating via decided_at is blocked when ENV=prod and allow_backdating=False."""
    monkeypatch.setenv("ENV", "prod")
    db_url = get_db_url()
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with pytest.raises(PermissionError, match="Backdating decisions is restricted"):
            create_decision(
                conn,
                applicant_id="APP-0011",
                application_id="LOAN-PROD-BACKDATE",
                applicant_params={
                    "annual_income": 50000.0,
                    "monthly_income": 4166.67,
                    "existing_debt_service": 1000.0,
                    "dti_ratio": 0.24,
                    "credit_score": 700,
                    "delinquencies_24m": 0,
                    "account_age_months": 48,
                    "employment_months": 36,
                    "requested_amount": 10000.0,
                },
                decided_at="2025-05-15T10:00:00Z",
                allow_backdating=False,
            )


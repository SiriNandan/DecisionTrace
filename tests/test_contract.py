"""Tests for backend/contract.py Pydantic v2 models and docs/contract_changelog.md."""

from __future__ import annotations

from pathlib import Path
import re

from pydantic import ValidationError
import pytest

import backend.contract as contract_module
from backend.contract import (
    Claim,
    Override,
    Reason,
    ReconstructionReport,
    RuleCheck,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = ROOT_DIR / "docs" / "contract_changelog.md"


def _extract_json_block(docstring: str | None) -> str:
    assert docstring is not None, "Docstring must not be None"
    match = re.search(r"```json\s*(\{.*?\})\s*```", docstring, flags=re.DOTALL)
    assert match is not None, "Expected a ```json ... ``` block in docstring"
    return match.group(1)


def test_docstring_json_examples_validate_with_pydantic_v2() -> None:
    """Validate the JSON examples embedded in module and class docstrings using Pydantic v2."""
    module_json = _extract_json_block(contract_module.__doc__)
    report_from_module = ReconstructionReport.model_validate_json(module_json)
    assert report_from_module.decision_id == "2c8d45f2-5317-5e6c-9d93-957f8a1e72c1"
    assert report_from_module.outcome == "DECLINE"
    assert report_from_module.chain_verified is True
    assert report_from_module.replay_verified is True
    assert report_from_module.completeness == pytest.approx(1.0)
    assert len(report_from_module.rule_checks) == 2
    assert len(report_from_module.reasons) == 1
    assert len(report_from_module.reasons[0].claims) == 2
    assert report_from_module.human_override is not None
    assert report_from_module.human_override.new_outcome == "APPROVE"
    assert report_from_module.guardrail_rejections == 0
    assert report_from_module.status == "ok"

    # Round-trip JSON serialization
    dumped = report_from_module.model_dump_json()
    reloaded = ReconstructionReport.model_validate_json(dumped)
    assert reloaded == report_from_module

    class_json = _extract_json_block(ReconstructionReport.__doc__)
    report_from_class = ReconstructionReport.model_validate_json(class_json)
    assert report_from_class.human_override is None
    assert report_from_class.guardrail_rejections == 0
    assert report_from_class.status == "ok"


def test_contract_constraints_and_defaults() -> None:
    """Verify completeness bounds (0-1), status literals, guardrail_rejections default, and Override validation."""
    base_payload = {
        "decision_id": "11111111-2222-3333-4444-555555555555",
        "outcome": "APPROVE",
        "score": 0.123456,
        "chain_verified": True,
        "replay_verified": True,
        "replay_tolerance": 1e-9,
        "completeness": 0.85,
        "missing_evidence": [],
        "principal_reasons": ["All policy rules satisfied and score below threshold"],
        "rule_checks": [
            {
                "rule_id": "dti_limit",
                "observed_value": 0.28,
                "operator": "<=",
                "threshold": 0.40,
                "passed": True,
                "section": "3.2 Debt-to-income limit",
                "evidence_ref": "rule:dti_limit",
            }
        ],
        "reasons": [
            {
                "claims": [
                    {
                        "text": "Observed DTI ratio was 0.28.",
                        "citation": "application:LOAN-2026-0002",
                        "field": "dti_ratio",
                        "value": 0.28,
                    }
                ]
            }
        ],
        "policy_note": "Evaluated under LP-2026-v1.",
        "human_override": None,
        "status": "ok",
    }

    report = ReconstructionReport.model_validate(base_payload)
    assert report.guardrail_rejections == 0
    assert isinstance(report.rule_checks[0], RuleCheck)
    assert isinstance(report.reasons[0], Reason)
    assert isinstance(report.reasons[0].claims[0], Claim)

    # Completeness out of [0, 1] must fail validation
    with pytest.raises(ValidationError):
        ReconstructionReport.model_validate({**base_payload, "completeness": 1.05})

    with pytest.raises(ValidationError):
        ReconstructionReport.model_validate({**base_payload, "completeness": -0.01})

    # Invalid status must fail validation
    with pytest.raises(ValidationError):
        ReconstructionReport.model_validate({**base_payload, "status": "unknown_status"})

    # All 4 allowed statuses validate cleanly
    for valid_status in ("ok", "timeout", "error", "evidence_unavailable"):
        r = ReconstructionReport.model_validate({**base_payload, "status": valid_status})
        assert r.status == valid_status

    # Override requires >= 20 character reason
    with pytest.raises(ValidationError):
        Override(
            new_outcome="APPROVE",
            reason="Too short",
            actor_role="senior_underwriter",
            created_at="2026-01-15T10:00:00Z",
        )


def test_contract_changelog_has_dated_entry() -> None:
    """Verify docs/contract_changelog.md exists, has a dated entry, and follows wording rules."""
    assert CHANGELOG_PATH.is_file(), f"Missing {CHANGELOG_PATH}"
    content = CHANGELOG_PATH.read_text(encoding="utf-8")
    assert re.search(r"##\s+\[\d+\.\d+\.\d+\]\s+-\s+\d{4}-\d{2}-\d{2}", content)
    for model_name in ("ReconstructionReport", "RuleCheck", "Reason", "Claim", "Override"):
        assert model_name in content
    lowered = content.lower()
    assert "ensures compliance" not in lowered
    assert "proves" not in lowered


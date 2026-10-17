"""Tests for eval/run_eval.py, eval/results.md, and the 30-report human rating CSV export."""

from __future__ import annotations

import csv
from pathlib import Path
import pytest

from eval.run_eval import (
    SEED,
    run_eval,
)


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        lines = [line for line in f if not line.lstrip().startswith("#")]
    return list(csv.DictReader(lines))


def test_run_eval_generates_results_md_and_30_human_rating_rows(tmp_path: Path) -> None:
    """Verify run_eval executes deterministically and writes results.md and 30 CSV rows."""
    results_md = tmp_path / "results.md"
    human_csv = tmp_path / "human_rating_30_reports.csv"
    summary = run_eval(
        seed=SEED,
        results_md_path=results_md,
        human_csv_path=human_csv,
        human_csv_alias_path=tmp_path / "human_rating.csv",
    )

    # 1. Completeness & untouched chain verification rate (target 100%)
    assert summary["untouched_decisions_count"] == 30
    assert summary["chain_verification"]["untouched_verification_rate"] == pytest.approx(1.0)
    assert summary["completeness"]["untouched_complete_mean"] == pytest.approx(1.0)
    assert summary["completeness"]["erased_evidence_completeness"] == pytest.approx(10.0 / 11.0, abs=1e-4)

    # 2. Tamper detection (DB copy altered payload + deleted final row + seeded tamper scenarios)
    tamper = summary["tamper_detection"]
    assert tamper["db_copy"]["untouched_copy_pass_count"] == 30
    assert tamper["db_copy"]["altered_payload_test"]["failure_detected"] is True
    assert tamper["db_copy"]["deleted_final_row_test"]["failure_detected"] is True
    assert tamper["seeded_tampered_payload"]["failure_detected"] is True
    assert tamper["seeded_deleted_final_row"]["failure_detected"] is True
    assert tamper["overall_detection_rate"] == pytest.approx(1.0)

    # 3. Replay match rate with tolerance
    replay = summary["replay"]
    assert replay["tolerance"] == pytest.approx(1e-9)
    assert replay["untouched_match_rate"] == pytest.approx(1.0)
    assert replay["tampered_payload_replay_verified"] is False

    # 4. Citation validity, claim support, and numerical accuracy reported separately
    grounding = summary["grounding"]
    assert grounding["total_claims"] >= 30
    assert grounding["citation_validity_rate"] == pytest.approx(1.0)
    assert grounding["claim_support_rate"] == pytest.approx(1.0)
    assert grounding["numerical_accuracy_rate"] == pytest.approx(1.0)

    # 5. Guardrail rejections
    guardrails = summary["guardrails"]
    assert guardrails["clean_runs_total_rejections"] == 0
    assert guardrails["probe"]["guardrail_rejections"] >= 4
    assert guardrails["probe"]["rejection_rate_on_unsupported"] == pytest.approx(1.0)

    # 6. Median and p95 latency
    latency = summary["latency"]
    assert latency["median_ms"] > 0.0
    assert latency["p95_ms"] >= latency["median_ms"]

    # 7. Fairness integration
    fairness = summary["fairness"]
    assert fairness["disparity_detected"] is True
    assert fairness["groups"]["A"]["count"] >= 300
    assert fairness["groups"]["B"]["count"] >= 300

    # 8. Verify 30-report CSV export for human rating (claim, source_text, supported)
    assert human_csv.exists()
    assert (tmp_path / "human_rating.csv").exists()
    csv_rows = _read_csv_rows(human_csv)
    assert len(csv_rows) == 30
    assert list(csv_rows[0].keys()) == ["claim", "source_text", "automated_check_passed", "human_supported"]
    for row in csv_rows:
        assert row["claim"].strip() != ""
        assert row["source_text"].strip() != ""
        assert row["automated_check_passed"].lower() in ("true", "false")
        assert row["human_supported"] == ""

    # 9. Verify eval/results.md exists and contains all required sections and governance wording
    assert results_md.exists()
    md_text = results_md.read_text(encoding="utf-8")
    assert "SYNTHETIC DATA - FOR DEMONSTRATION." in md_text
    assert "## 2. Completeness" in md_text
    assert "## 3. Hash-Chain Verification Rate" in md_text
    assert "## 4. Tamper Detection" in md_text
    assert "## 5. Score Replay Match Rate" in md_text
    assert "## 6. Citation Validity, Claim Support, and Numerical Accuracy" in md_text
    assert "## 7. Guardrail Rejections" in md_text
    assert "## 8. Reconstruction Latency" in md_text
    assert "## 9. Manual vs. Tool Timing Table" in md_text
    assert "## 10. Fairness Evaluation" in md_text
    assert "## 11. Human Rating CSV Export" in md_text

    # Governance language check
    md_lower = md_text.lower()
    assert "supports audit and explanation" in md_lower
    assert "tamper-evident against a retained reference" in md_lower
    assert "ensures compliance" not in md_lower
    assert "proves" not in md_lower

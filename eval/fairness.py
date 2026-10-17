"""Fairness evaluation module computing per-group outcome rates, selection-rate difference and ratio, and limitations."""

from __future__ import annotations

import csv
import io
from pathlib import Path
import sys
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.generate_applicants import DATA_DIR, SYNTHETIC_HEADER
from data.generate_documents import SYNTHETIC_NOTICE


DEFAULT_DIFFERENCE_THRESHOLD = 0.15
DEFAULT_RATIO_THRESHOLD = 0.80

LIMITATIONS_NOTE = (
    "Limitations: Group selection-rate difference and ratio measure observational outcome "
    "disparities across evaluation cohorts (fairness_groups) and help surface proxy effects "
    "such as short employment_months lowering automated approval rates in Group B. However, "
    "these aggregate metrics do not isolate causal mechanisms, control for legitimate credit "
    "risk differences, or evaluate calibration within risk score bands. The fairness_groups "
    "assignment is used strictly for post-decision evaluation and never as a model feature, "
    "and this evaluation report supports audit and explanation rather than automated "
    "remediation."
)


def _read_synthetic_csv(csv_path: Path) -> list[dict[str, str]]:
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    data_lines = [
        line for line in lines if not line.startswith("#") and line.strip() != SYNTHETIC_NOTICE
    ]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    return list(reader)


def evaluate_fairness(
    groups_rows: list[dict[str, Any]],
    outcome_rows: list[dict[str, Any]],
    *,
    reference_group: str = "A",
    comparison_group: str = "B",
    difference_threshold: float = DEFAULT_DIFFERENCE_THRESHOLD,
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
) -> dict[str, Any]:
    """Compute per-group approval/decline/referral rates, selection-rate difference and ratio, and limitations."""
    group_by_applicant: dict[str, str] = {
        str(r["applicant_id"]): str(r["test_group"]) for r in groups_rows
    }

    buckets: dict[str, list[dict[str, Any]]] = {reference_group: [], comparison_group: []}
    for row in outcome_rows:
        app_id = str(row["applicant_id"])
        grp = group_by_applicant.get(app_id)
        if grp in buckets:
            buckets[grp].append(row)

    per_group: dict[str, dict[str, Any]] = {}
    for grp_name, rows in buckets.items():
        n = len(rows)
        if n == 0:
            raise ValueError(f"Group {grp_name!r} has 0 matched outcome rows.")
        approvals = sum(1 for r in rows if str(r["outcome"]) == "APPROVE")
        declines = sum(1 for r in rows if str(r["outcome"]) == "DECLINE")
        referrals = sum(1 for r in rows if str(r["outcome"]) == "REFER")
        short_emp = sum(1 for r in rows if int(r.get("employment_months", 99)) < 12)

        per_group[grp_name] = {
            "count": n,
            "approvals": approvals,
            "declines": declines,
            "referrals": referrals,
            "approval_rate": round(approvals / n, 6),
            "decline_rate": round(declines / n, 6),
            "referral_rate": round(referrals / n, 6),
            "short_employment_share": round(short_emp / n, 6),
        }

    ref_approval = per_group[reference_group]["approval_rate"]
    cmp_approval = per_group[comparison_group]["approval_rate"]

    selection_rate_difference = round(ref_approval - cmp_approval, 6)
    selection_rate_ratio = (
        round(cmp_approval / ref_approval, 6) if ref_approval > 0 else 0.0
    )

    disparity_above_difference_threshold = selection_rate_difference > difference_threshold
    disparity_below_ratio_threshold = selection_rate_ratio < ratio_threshold
    disparity_detected = bool(
        disparity_above_difference_threshold or disparity_below_ratio_threshold
    )

    return {
        "reference_group": reference_group,
        "comparison_group": comparison_group,
        "groups": per_group,
        "selection_rate_difference": selection_rate_difference,
        "selection_rate_ratio": selection_rate_ratio,
        "difference_threshold": difference_threshold,
        "ratio_threshold": ratio_threshold,
        "disparity_above_difference_threshold": disparity_above_difference_threshold,
        "disparity_below_ratio_threshold": disparity_below_ratio_threshold,
        "disparity_detected": disparity_detected,
        "limitations_note": LIMITATIONS_NOTE,
    }


def evaluate_fairness_from_files(
    data_dir: Path = DATA_DIR,
    *,
    difference_threshold: float = DEFAULT_DIFFERENCE_THRESHOLD,
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
) -> dict[str, Any]:
    """Load fairness_groups.csv and fairness_scenario_outcomes.csv from data_dir and compute fairness metrics."""
    groups_path = data_dir / "fairness_groups.csv"
    outcomes_path = data_dir / "fairness_scenario_outcomes.csv"
    groups_rows = _read_synthetic_csv(groups_path)
    outcome_rows = _read_synthetic_csv(outcomes_path)
    return evaluate_fairness(
        groups_rows,
        outcome_rows,
        difference_threshold=difference_threshold,
        ratio_threshold=ratio_threshold,
    )


def main() -> None:
    report = evaluate_fairness_from_files()
    grp_a = report["groups"]["A"]
    grp_b = report["groups"]["B"]
    print(
        f"Group A (n={grp_a['count']}): approve={grp_a['approval_rate']:.3f}, "
        f"decline={grp_a['decline_rate']:.3f}, refer={grp_a['referral_rate']:.3f}, "
        f"short_employment_share={grp_a['short_employment_share']:.3f}"
    )
    print(
        f"Group B (n={grp_b['count']}): approve={grp_b['approval_rate']:.3f}, "
        f"decline={grp_b['decline_rate']:.3f}, refer={grp_b['referral_rate']:.3f}, "
        f"short_employment_share={grp_b['short_employment_share']:.3f}"
    )
    print(
        f"Selection-rate difference (A - B): {report['selection_rate_difference']:.4f} "
        f"(threshold > {report['difference_threshold']:.2f})"
    )
    print(
        f"Selection-rate ratio (B / A):      {report['selection_rate_ratio']:.4f} "
        f"(threshold < {report['ratio_threshold']:.2f})"
    )
    print(f"Disparity detected: {report['disparity_detected']}")
    print(report["limitations_note"])


if __name__ == "__main__":
    main()


# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Generate a controlled fairness evaluation scenario with a known proxy disparity via employment_months."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import numpy as np
import psycopg

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.generate_applicants import DATA_DIR, write_synthetic_csv
from data.load_policies import get_db_url
from decision_api.service import (
    EffectivePolicyRules,
    _resolve_model_registry_row,
    evaluate_rules_and_outcome,
)


DEFAULT_FAIRNESS_POLICY = EffectivePolicyRules(
    policy_id="LP-2026-v1",
    policy_family="consumer_loan",
    effective_from="2026-01-01",
    effective_to=None,
    text_sha256="fairness_eval_policy_ref",
    dti_limit=0.40,
    min_credit_score=640,
    required_documents=["income proof", "address proof"],
    delinquency_manual_review_min=2,
)


def _load_registered_model(db_url: str | None = None) -> dict[str, Any]:
    """Load the registered model (MODEL_VERSION, default loan_lr_v1) the live decision path scores with."""
    try:
        with psycopg.connect(get_db_url(db_url), connect_timeout=5) as conn:
            return _resolve_model_registry_row(conn)
    except psycopg.OperationalError:
        from data.train_model import train_and_register_model

        return train_and_register_model(sync_db=False)["registry_row"]


def _sync_fairness_groups_table(
    rows: list[dict[str, Any]],
    db_url: str | None = None,
) -> None:
    """Create and populate the separate fairness_groups(applicant_id, test_group) table in PostgreSQL."""
    target_url = get_db_url(db_url)
    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS fairness_groups (
                    applicant_id text PRIMARY KEY,
                    test_group text NOT NULL CHECK (test_group IN ('A', 'B'))
                );
                """
            )
            cur.execute("TRUNCATE TABLE fairness_groups;")
            cur.executemany(
                """
                INSERT INTO fairness_groups (applicant_id, test_group)
                VALUES (%s, %s);
                """,
                [(r["applicant_id"], r["test_group"]) for r in rows],
            )
        conn.commit()


def generate_fairness_scenario(
    output_dir: Path = DATA_DIR,
    seed: int = 2026,
    group_size: int = 400,
    sync_db: bool = True,
    db_url: str | None = None,
    registry_row: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Generate fairness_groups.csv and fairness_scenario_outcomes.csv with at least 300 applicants per group.

    Group B has a higher share of short employment_months (< 12 months), acting as a proxy
    feature. Applicants are scored with the registered model (or `registry_row` when given), so a gap
    in approval rates appears only if that model actually relies on employment_months.
    """
    if group_size < 300:
        raise ValueError("group_size must be at least 300 per group.")

    if registry_row is None:
        registry_row = _load_registered_model(db_url)
    rng = np.random.default_rng(seed)
    output_dir.mkdir(parents=True, exist_ok=True)

    fairness_groups_rows: list[dict[str, Any]] = []
    outcome_rows: list[dict[str, Any]] = []

    for group_label, short_tenure_prob in (("A", 0.10), ("B", 0.75)):
        for i in range(1, group_size + 1):
            applicant_id = f"FAIR-{group_label}-{i:04d}"
            application_id = f"LOAN-FAIR-{group_label}-{i:04d}"

            # Identical baseline financial distributions across both groups
            annual_income = float(np.round(rng.uniform(42000.0, 68000.0), 2))
            monthly_income = round(annual_income / 12.0, 2)
            dti_ratio = float(np.round(rng.normal(0.35, 0.045), 4))
            dti_ratio = float(np.clip(dti_ratio, 0.18, 0.52))
            existing_debt_service = round(monthly_income * dti_ratio, 2)

            credit_score = int(np.clip(rng.normal(662, 24), 560, 820))
            delinquencies_24m = int(rng.choice([0, 1, 2], p=[0.88, 0.09, 0.03]))
            account_age_months = int(rng.integers(24, 72))
            requested_amount = float(np.round(rng.uniform(8000.0, 14000.0), 2))

            # Proxy feature disparity: Group B has a much higher share of short employment_months (< 12m)
            is_short_tenure = bool(rng.random() < short_tenure_prob)
            if is_short_tenure:
                employment_months = int(rng.integers(1, 8))
            else:
                employment_months = int(rng.integers(36, 96))

            # Evaluate application WITHOUT test_group (test_group is strictly excluded from model features)
            applicant_params = {
                "annual_income": annual_income,
                "monthly_income": monthly_income,
                "existing_debt_service": existing_debt_service,
                "dti_ratio": dti_ratio,
                "credit_score": credit_score,
                "delinquencies_24m": delinquencies_24m,
                "account_age_months": account_age_months,
                "employment_months": employment_months,
                "requested_amount": requested_amount,
                "payslip_status": "received",
                "address_proof_status": "received",
            }
            eval_res = evaluate_rules_and_outcome(
                applicant_params=applicant_params,
                policy=DEFAULT_FAIRNESS_POLICY,
                registry_row=registry_row,
            )

            fairness_groups_rows.append(
                {
                    "applicant_id": applicant_id,
                    "test_group": group_label,
                }
            )
            outcome_rows.append(
                {
                    "applicant_id": applicant_id,
                    "application_id": application_id,
                    "employment_months": employment_months,
                    "dti_ratio": f"{dti_ratio:.4f}",
                    "credit_score": credit_score,
                    "delinquencies_24m": delinquencies_24m,
                    "account_age_months": account_age_months,
                    "requested_amount": f"{requested_amount:.2f}",
                    "score": f"{eval_res['score']:.6f}",
                    "outcome": eval_res["outcome"],
                    "policy_version": eval_res["policy_version"],
                }
            )

    groups_csv = output_dir / "fairness_groups.csv"
    outcomes_csv = output_dir / "fairness_scenario_outcomes.csv"
    write_synthetic_csv(groups_csv, fairness_groups_rows)
    write_synthetic_csv(outcomes_csv, outcome_rows)

    if sync_db:
        try:
            _sync_fairness_groups_table(fairness_groups_rows, db_url=db_url)
        except Exception:
            # Allow offline CSV generation if PostgreSQL is not currently reachable
            pass

    return {
        "fairness_groups": groups_csv,
        "fairness_outcomes": outcomes_csv,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic fairness_groups and controlled proxy disparity outcomes."
    )
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--group-size", type=int, default=400)
    args = parser.parse_args()

    paths = generate_fairness_scenario(
        output_dir=args.output_dir,
        seed=args.seed,
        group_size=args.group_size,
    )
    print(
        f"Generated {paths['fairness_groups'].name} and {paths['fairness_outcomes'].name} "
        f"(group_size={args.group_size} per group)."
    )


if __name__ == "__main__":
    main()


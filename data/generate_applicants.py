# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Deterministic synthetic loan applicant, bureau snapshot, and account generator."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any

import numpy as np
import yaml


SYNTHETIC_HEADER = "# SYNTHETIC DATA - FOR DEMONSTRATION."
DATA_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = DATA_DIR / "config.yaml"

FORBIDDEN_COLUMNS = {"gender", "religion", "ethnicity", "postcode"}


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load generator configuration from YAML."""
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise ValueError(f"Invalid configuration in {config_path}")
    return cfg


def _parse_iso_utc(ts_str: str) -> datetime:
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str).astimezone(timezone.utc)


def _format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_dataset(
    config: dict[str, Any],
    seed_override: int | None = None,
    num_applicants_override: int | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate deterministic rows for applicants, bureau_snapshots, and accounts."""
    seed = int(seed_override if seed_override is not None else config.get("seed", 42))
    n = int(
        num_applicants_override
        if num_applicants_override is not None
        else config.get("num_applicants", 2000)
    )
    profile_name = config.get("active_profile", "uk_retail")
    profile = config["profiles"][profile_name]

    currency = str(profile["currency"])
    base_ts = _parse_iso_utc(str(profile["reference_timestamp"]))
    bureau_source_id = str(profile.get("bureau_source_id", "bureau_syn_v1"))
    account_source_id = str(profile.get("account_source_id", "accounts_syn_v1"))

    inc_cfg = profile["income_distribution"]
    mean_log = float(inc_cfg["mean_log"])
    sigma_log = float(inc_cfg["sigma_log"])
    min_inc = float(inc_cfg["min_annual_income"])
    max_inc = float(inc_cfg["max_annual_income"])

    age_bands_cfg = profile["age_bands"]
    band_labels = [str(b["band"]) for b in age_bands_cfg]
    band_weights = np.array([float(b["weight"]) for b in age_bands_cfg], dtype=float)
    band_weights = band_weights / band_weights.sum()

    rng = np.random.default_rng(seed)
    loan_terms = np.array([12, 24, 36, 48, 60], dtype=int)

    applicants: list[dict[str, Any]] = []
    bureau_snapshots: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    for idx in range(1, n + 1):
        applicant_id = f"APP-{idx:04d}"
        application_id = f"APP-{idx:04d}"
        account_id = f"ACCT-{idx:04d}"

        band_idx = int(rng.choice(len(age_bands_cfg), p=band_weights))
        band_info = age_bands_cfg[band_idx]
        age_band = band_labels[band_idx]

        # 1. Income: lognormal distribution
        raw_income = float(rng.lognormal(mean=mean_log, sigma=sigma_log))
        annual_income = round(float(np.clip(raw_income, min_inc, max_inc)), 2)
        monthly_income = round(annual_income / 12.0, 2)

        # 2. Employment months correlated with age_band
        raw_emp = float(
            rng.normal(
                loc=float(band_info["emp_months_mean"]),
                scale=float(band_info["emp_months_std"]),
            )
        )
        employment_months = int(
            np.clip(round(raw_emp), 0, int(band_info["emp_months_max"]))
        )

        # 3. Account age months correlated with age_band
        raw_acct_age = float(
            rng.normal(
                loc=float(band_info["acct_age_mean"]),
                scale=float(band_info["acct_age_std"]),
            )
        )
        account_age_months = int(np.clip(round(raw_acct_age), 3, 480))

        # 4. Existing debt service & DTI ratio = existing_debt_service / monthly_income
        target_dti = float(
            np.clip(rng.beta(2.2, 5.2) * 0.82 + rng.normal(0.0, 0.025), 0.04, 0.78)
        )
        existing_debt_service = round(monthly_income * target_dti, 2)
        dti_ratio = round(existing_debt_service / monthly_income, 6)

        # 5. Delinquencies in last 24m & credit score (300-900) correlated with
        #    delinquencies_24m (negative) and account_age_months (positive)
        delinq_rate = float(
            np.clip(0.15 + 1.15 * max(0.0, dti_ratio - 0.22), 0.05, 2.50)
        )
        delinquencies_24m = int(rng.poisson(lam=delinq_rate))

        raw_score = (
            555.0
            + 0.72 * min(account_age_months, 240)
            - 78.0 * delinquencies_24m
            - 85.0 * dti_ratio
            + float(rng.normal(0.0, 35.0))
        )
        credit_score = int(np.clip(round(raw_score), 300, 900))

        # 6. Requested amount scaled to income
        income_multiplier = float(
            np.clip(rng.lognormal(mean=-1.20, sigma=0.40), 0.08, 1.20)
        )
        requested_amount = round(annual_income * income_multiplier, 2)
        loan_term_months = int(rng.choice(loan_terms))

        # 7. outcome_default_12m as a logistic function of dti_ratio, credit_score,
        #    delinquencies_24m, account_age_months, plus noise
        noise = float(rng.normal(0.0, 0.50))
        logit = (
            -1.15
            + 4.40 * (dti_ratio - 0.30)
            - 0.0085 * (credit_score - 620.0)
            + 0.80 * delinquencies_24m
            - 0.0045 * (account_age_months - 96.0)
            + noise
        )
        prob_default = 1.0 / (1.0 + math.exp(-logit))
        outcome_default_12m = 1 if prob_default >= 0.5 else 0

        # Deterministic timestamps relative to config reference_timestamp
        bureau_offset_mins = int(rng.integers(5, 1440))
        acct_offset_mins = int(rng.integers(5, 1440))
        bureau_ts = _format_iso_utc(base_ts - timedelta(minutes=bureau_offset_mins))
        acct_ts = _format_iso_utc(base_ts - timedelta(minutes=acct_offset_mins))

        avg_monthly_balance = round(
            max(50.0, monthly_income * float(rng.uniform(0.15, 1.80)) * (1.0 - 0.5 * dti_ratio)),
            2,
        )
        overdraft_count_12m = int(
            rng.poisson(lam=max(0.05, 0.6 * delinquencies_24m + 0.4 * max(0.0, dti_ratio - 0.35)))
        )

        applicants.append(
            {
                "applicant_id": applicant_id,
                "application_id": application_id,
                "currency": currency,
                "age_band": age_band,
                "annual_income": f"{annual_income:.2f}",
                "monthly_income": f"{monthly_income:.2f}",
                "employment_months": employment_months,
                "existing_debt_service": f"{existing_debt_service:.2f}",
                "dti_ratio": f"{dti_ratio:.6f}",
                "credit_score": credit_score,
                "delinquencies_24m": delinquencies_24m,
                "account_age_months": account_age_months,
                "requested_amount": f"{requested_amount:.2f}",
                "loan_term_months": loan_term_months,
                "outcome_default_12m": outcome_default_12m,
            }
        )

        bureau_snapshots.append(
            {
                "applicant_id": applicant_id,
                "application_id": application_id,
                "source_id": bureau_source_id,
                "source_timestamp": bureau_ts,
                "credit_score": credit_score,
                "delinquencies_24m": delinquencies_24m,
                "account_age_months": account_age_months,
            }
        )

        accounts.append(
            {
                "account_id": account_id,
                "applicant_id": applicant_id,
                "application_id": application_id,
                "source_id": account_source_id,
                "source_timestamp": acct_ts,
                "currency": currency,
                "account_age_months": account_age_months,
                "monthly_income": f"{monthly_income:.2f}",
                "existing_debt_service": f"{existing_debt_service:.2f}",
                "avg_monthly_balance": f"{avg_monthly_balance:.2f}",
                "overdraft_count_12m": overdraft_count_12m,
            }
        )

    return applicants, bureau_snapshots, accounts


def write_synthetic_csv(file_path: Path, rows: list[dict[str, Any]]) -> None:
    """Write rows to CSV with mandatory SYNTHETIC DATA comment header on line 1."""
    if not rows:
        raise ValueError(f"Cannot write empty rows to {file_path}")
    fieldnames = list(rows[0].keys())
    forbidden = FORBIDDEN_COLUMNS.intersection({c.lower() for c in fieldnames})
    if forbidden:
        raise ValueError(f"Forbidden columns detected: {forbidden}")

    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"{SYNTHETIC_HEADER}\n")
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def format_summary(applicants: list[dict[str, Any]]) -> str:
    """Produce a human-readable statistical summary of generated distributions and correlations."""
    n = len(applicants)
    incomes = np.array([float(r["annual_income"]) for r in applicants])
    emp_months = np.array([float(r["employment_months"]) for r in applicants])
    dti = np.array([float(r["dti_ratio"]) for r in applicants])
    scores = np.array([float(r["credit_score"]) for r in applicants])
    delinq = np.array([float(r["delinquencies_24m"]) for r in applicants])
    acct_age = np.array([float(r["account_age_months"]) for r in applicants])
    req_amt = np.array([float(r["requested_amount"]) for r in applicants])
    defaults = np.array([int(r["outcome_default_12m"]) for r in applicants])

    band_order = {"18-24": 0, "25-34": 1, "35-44": 2, "45-54": 3, "55-64": 4, "65+": 5}
    age_ranks = np.array([band_order.get(str(r["age_band"]), 0) for r in applicants], dtype=float)

    corr_emp_age = float(np.corrcoef(emp_months, age_ranks)[0, 1])
    corr_score_delinq = float(np.corrcoef(scores, delinq)[0, 1])
    corr_score_acct_age = float(np.corrcoef(scores, acct_age)[0, 1])
    corr_req_inc = float(np.corrcoef(req_amt, incomes)[0, 1])

    lines = [
        f"=== Synthetic Applicant Dataset Summary (N={n}) ===",
        f"annual_income       : mean={incomes.mean():.2f}, std={incomes.std():.2f}, min={incomes.min():.2f}, median={np.median(incomes):.2f}, max={incomes.max():.2f}",
        f"employment_months   : mean={emp_months.mean():.1f}, std={emp_months.std():.1f}, min={emp_months.min():.0f}, median={np.median(emp_months):.0f}, max={emp_months.max():.0f}",
        f"dti_ratio           : mean={dti.mean():.4f}, std={dti.std():.4f}, min={dti.min():.4f}, median={np.median(dti):.4f}, max={dti.max():.4f}",
        f"credit_score        : mean={scores.mean():.1f}, std={scores.std():.1f}, min={scores.min():.0f}, median={np.median(scores):.0f}, max={scores.max():.0f}",
        f"delinquencies_24m   : mean={delinq.mean():.2f}, std={delinq.std():.2f}, min={delinq.min():.0f}, max={delinq.max():.0f}",
        f"account_age_months  : mean={acct_age.mean():.1f}, std={acct_age.std():.1f}, min={acct_age.min():.0f}, median={np.median(acct_age):.0f}, max={acct_age.max():.0f}",
        f"requested_amount    : mean={req_amt.mean():.2f}, std={req_amt.std():.2f}, min={req_amt.min():.2f}, median={np.median(req_amt):.2f}, max={req_amt.max():.2f}",
        f"outcome_default_12m : default_rate={defaults.mean():.4f} ({defaults.sum()}/{n})",
        "--- Key Correlations ---",
        f"corr(employment_months, age_band)       = {corr_emp_age:+.4f}",
        f"corr(credit_score, delinquencies_24m)   = {corr_score_delinq:+.4f}",
        f"corr(credit_score, account_age_months)  = {corr_score_acct_age:+.4f}",
        f"corr(requested_amount, annual_income)   = {corr_req_inc:+.4f}",
    ]
    return "\n".join(lines)


def generate_all(
    config_path: Path = DEFAULT_CONFIG_PATH,
    output_dir: Path = DATA_DIR,
    seed_override: int | None = None,
    num_applicants_override: int | None = None,
) -> dict[str, Path]:
    """Generate and write applicants.csv, bureau_snapshots.csv, and accounts.csv."""
    cfg = load_config(config_path)
    applicants, bureau_snapshots, accounts = generate_dataset(
        cfg,
        seed_override=seed_override,
        num_applicants_override=num_applicants_override,
    )

    paths = {
        "applicants": output_dir / "applicants.csv",
        "bureau_snapshots": output_dir / "bureau_snapshots.csv",
        "accounts": output_dir / "accounts.csv",
    }
    write_synthetic_csv(paths["applicants"], applicants)
    write_synthetic_csv(paths["bureau_snapshots"], bureau_snapshots)
    write_synthetic_csv(paths["accounts"], accounts)

    print(format_summary(applicants))
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic loan applicant datasets.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--n", type=int, default=None)
    args = parser.parse_args()

    generate_all(
        config_path=args.config,
        output_dir=args.output_dir,
        seed_override=args.seed,
        num_applicants_override=args.n,
    )


if __name__ == "__main__":
    main()


"""Account history source reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.sources._csv_utils import DEFAULT_DATA_DIR, read_synthetic_csv


def read_account(
    applicant_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read account history for applicant_id, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    acct_rows = read_synthetic_csv(target_dir / "accounts.csv")
    row = next((r for r in acct_rows if r["applicant_id"] == applicant_id), None)
    if row is None:
        raise LookupError(f"Account history for applicant '{applicant_id}' not found")

    account_age_months = int(row["account_age_months"])
    monthly_income = round(float(row["monthly_income"]), 2)
    existing_debt_service = round(float(row["existing_debt_service"]), 2)

    applicants_file = target_dir / "applicants.csv"
    if applicants_file.is_file():
        app_rows = read_synthetic_csv(applicants_file)
        app_row = next((r for r in app_rows if r["applicant_id"] == applicant_id), None)
        if app_row is not None:
            account_age_months = int(app_row.get("account_age_months", account_age_months))
            monthly_income = round(float(app_row.get("monthly_income", monthly_income)), 2)
            existing_debt_service = round(
                float(app_row.get("existing_debt_service", existing_debt_service)), 2
            )

    account_id = str(row.get("account_id", f"ACCT-{applicant_id}"))
    return {
        "source_id": str(row.get("source_id", "core_banking_accounts_uk_syn_v1")),
        "source_timestamp": str(row.get("source_timestamp", "2026-01-15T11:30:00Z")),
        "evidence_ref": f"account:{account_id}",
        "payload": {
            "account_id": account_id,
            "applicant_id": applicant_id,
            "application_id": str(row.get("application_id", applicant_id)),
            "currency": str(row.get("currency", "GBP")),
            "account_age_months": account_age_months,
            "monthly_income": monthly_income,
            "existing_debt_service": existing_debt_service,
            "avg_monthly_balance": round(float(row.get("avg_monthly_balance", 0.0)), 2),
            "overdraft_count_12m": int(row.get("overdraft_count_12m", 0)),
        },
    }


read_account_history = read_account


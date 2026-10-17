"""Application source reader (reads applicant attributes excluding demographic/target columns)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.sources._csv_utils import DEFAULT_DATA_DIR, read_synthetic_csv


def read_application(
    applicant_id: str,
    requested_amount: float | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read applicant submission data for applicant_id, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    rows = read_synthetic_csv(target_dir / "applicants.csv")
    row = next((r for r in rows if r["applicant_id"] == applicant_id), None)
    if row is None:
        raise LookupError(f"Applicant '{applicant_id}' not found in applicants.csv")

    application_id = str(row.get("application_id") or applicant_id)
    annual_income = round(float(row["annual_income"]), 2)
    monthly_income = round(float(row["monthly_income"]), 2)
    existing_debt_service = round(float(row["existing_debt_service"]), 2)
    dti_ratio = round(float(row["dti_ratio"]), 4)
    employment_months = int(row["employment_months"])
    req_amt = (
        round(float(requested_amount), 2)
        if requested_amount is not None
        else round(float(row["requested_amount"]), 2)
    )
    currency = str(row.get("currency", "GBP"))
    loan_term_months = int(row.get("loan_term_months", 36))

    return {
        "source_id": "loan_origination_portal_v1",
        "source_timestamp": "2026-01-15T12:00:00Z",
        "evidence_ref": f"application:{application_id}",
        "payload": {
            "applicant_id": applicant_id,
            "application_id": application_id,
            "annual_income": annual_income,
            "income_annual": annual_income,
            "monthly_income": monthly_income,
            "existing_debt_service": existing_debt_service,
            "dti_ratio": dti_ratio,
            "employment_months": employment_months,
            "requested_amount": req_amt,
            "loan_term_months": loan_term_months,
            "currency": currency,
            "address_proof_status": "received",
        },
    }


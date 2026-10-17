"""Credit bureau snapshot source reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.sources._csv_utils import DEFAULT_DATA_DIR, read_synthetic_csv


def read_bureau(
    applicant_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read bureau snapshot for applicant_id, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    bureau_rows = read_synthetic_csv(target_dir / "bureau_snapshots.csv")
    row = next((r for r in bureau_rows if r["applicant_id"] == applicant_id), None)
    if row is None:
        raise LookupError(f"Bureau snapshot for applicant '{applicant_id}' not found")

    # Cross-check applicants.csv in case account_age_months was updated on the live applicant record
    account_age_months = int(row["account_age_months"])
    applicants_file = target_dir / "applicants.csv"
    if applicants_file.is_file():
        app_rows = read_synthetic_csv(applicants_file)
        app_row = next((r for r in app_rows if r["applicant_id"] == applicant_id), None)
        if app_row is not None and "account_age_months" in app_row:
            account_age_months = int(app_row["account_age_months"])

    return {
        "source_id": str(row.get("source_id", "credit_bureau_uk_syn_v1")),
        "source_timestamp": str(row.get("source_timestamp", "2026-01-15T11:30:00Z")),
        "evidence_ref": f"bureau:{applicant_id}",
        "payload": {
            "applicant_id": applicant_id,
            "application_id": str(row.get("application_id", applicant_id)),
            "credit_score": int(row["credit_score"]),
            "delinquencies_24m": int(row["delinquencies_24m"]),
            "account_age_months": account_age_months,
        },
    }


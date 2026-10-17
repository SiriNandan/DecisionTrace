"""Consent events source reader."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.sources._csv_utils import DEFAULT_DATA_DIR, read_synthetic_csv


def read_consent(
    applicant_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read consent event for applicant_id, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    consent_file = target_dir / "consent_events.csv"
    row = None
    if consent_file.is_file():
        rows = read_synthetic_csv(consent_file)
        row = next((r for r in rows if r["applicant_id"] == applicant_id), None)

    if row is None:
        row = {
            "applicant_id": applicant_id,
            "consent_type": "credit_and_income_verification",
            "granted_at": "2026-01-14T09:00:00Z",
            "reference": f"CONSENT-{applicant_id}-DEFAULT",
        }

    reference = str(row["reference"])
    granted_at = str(row["granted_at"])
    return {
        "source_id": "consent_registry_uk_syn_v1",
        "source_timestamp": granted_at,
        "evidence_ref": f"consent:{reference}",
        "payload": {
            "applicant_id": applicant_id,
            "consent_type": str(row["consent_type"]),
            "granted_at": granted_at,
            "reference": reference,
        },
    }


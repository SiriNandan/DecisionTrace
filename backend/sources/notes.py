"""Analyst notes source reader (applies mandatory PII redaction before snapshot storage)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.redaction import redact_note_for_snapshot
from backend.sources._csv_utils import DEFAULT_DATA_DIR, read_synthetic_csv


def read_notes(
    applicant_id: str,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Read and redact analyst notes for applicant_id, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    notes_file = target_dir / "analyst_notes.csv"
    matched_rows: list[dict[str, Any]] = []

    if notes_file.is_file():
        all_rows = read_synthetic_csv(notes_file)
        matched_rows = [
            redact_note_for_snapshot(r)
            for r in all_rows
            if r.get("applicant_id") == applicant_id
        ]

    if matched_rows:
        primary = matched_rows[0]
    else:
        primary = redact_note_for_snapshot(
            {
                "note_id": f"NOTE-{applicant_id}",
                "applicant_id": applicant_id,
                "application_id": applicant_id,
                "author_role": "intake_automated",
                "created_at": "2026-01-15T11:55:00Z",
                "has_embedded_pii": "false",
                "note_text": f"Automated underwriting intake note for {applicant_id}: standard verification checks queued.",
            }
        )
        matched_rows = [primary]

    note_id = str(primary.get("note_id", f"NOTE-{applicant_id}"))
    created_at = str(primary.get("created_at", "2026-01-15T11:55:00Z"))

    return {
        "source_id": "underwriter_workbench_v1",
        "source_timestamp": created_at,
        "evidence_ref": f"note:{note_id}",
        "payload": primary,
        "notes": matched_rows,
    }


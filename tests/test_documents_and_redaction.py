"""Tests for data/generate_documents.py and backend/redaction.py."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path

from backend.redaction import (
    ACCOUNT_TOKEN,
    PHONE_TOKEN,
    contains_unredacted_sensitive_patterns,
    redact_note_for_snapshot,
    redact_text,
)
from data.generate_applicants import (
    DATA_DIR,
    DEFAULT_CONFIG_PATH,
    SYNTHETIC_HEADER,
    generate_all as generate_applicants_all,
)
from data.generate_documents import (
    SYNTHETIC_NOTICE,
    generate_documents,
    prepare_note_for_snapshot,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == SYNTHETIC_HEADER
    reader = csv.DictReader(io.StringIO("\n".join(lines[1:])))
    return list(reader)


def test_redaction_masks_accounts_and_phones_and_preserves_financial_metrics() -> None:
    """Verify account-like and phone-like strings are masked while normal metrics remain intact."""
    raw = (
        "Referred case for APP-0042: DTI ratio is 0.3821 with credit score 645 and "
        "annual income 42500.50 GBP. Applicant confirmed account 4491-8820-1134, "
        "sort code 20-45-19, IBAN GB29NWBK60161331926819, contiguous acct 83920144, "
        "and callback +44 7700 900123 or (020) 7946 0921 or 555-234-5678."
    )
    assert contains_unredacted_sensitive_patterns(raw) is True

    redacted = redact_text(raw)
    assert contains_unredacted_sensitive_patterns(redacted) is False
    assert ACCOUNT_TOKEN in redacted
    assert PHONE_TOKEN in redacted

    # Sensitive substrings must be gone
    for sensitive in [
        "4491-8820-1134",
        "20-45-19",
        "GB29NWBK60161331926819",
        "83920144",
        "+44 7700 900123",
        "(020) 7946 0921",
        "555-234-5678",
    ]:
        assert sensitive not in redacted

    # Normal domain numbers and identifiers must be preserved
    for preserved in ["APP-0042", "0.3821", "645", "42500.50"]:
        assert preserved in redacted


def test_redact_note_for_snapshot_does_not_mutate_input() -> None:
    """Ensure snapshot note redaction returns a sanitized copy without mutating original dict."""
    original = {
        "note_id": "NOTE-0001",
        "applicant_id": "APP-0001",
        "note_text": "Verified account 4491-8820-1134 and phone 07700 900456.",
        "nested": {"comment": "Backup phone +44 20 7946 0555"},
    }
    snapshot_ready = redact_note_for_snapshot(original)

    assert "4491-8820-1134" in original["note_text"]
    assert "4491-8820-1134" not in snapshot_ready["note_text"]
    assert ACCOUNT_TOKEN in snapshot_ready["note_text"]
    assert PHONE_TOKEN in snapshot_ready["note_text"]
    assert PHONE_TOKEN in snapshot_ready["nested"]["comment"]


def test_generate_documents_determinism_and_contents(tmp_path: Path) -> None:
    """Re-running generate_documents with the same seed produces identical files and valid schemas."""
    run1_dir = tmp_path / "run1"
    run2_dir = tmp_path / "run2"

    # Use a small applicant subset in tmp_path for fast determinism test, plus verify full DATA_DIR
    generate_applicants_all(config_path=DEFAULT_CONFIG_PATH, output_dir=run1_dir, seed_override=42)
    generate_applicants_all(config_path=DEFAULT_CONFIG_PATH, output_dir=run2_dir, seed_override=42)

    paths1 = generate_documents(config_path=DEFAULT_CONFIG_PATH, output_dir=run1_dir, seed_override=42)
    paths2 = generate_documents(config_path=DEFAULT_CONFIG_PATH, output_dir=run2_dir, seed_override=42)

    assert _sha256(paths1["consent_events"]) == _sha256(paths2["consent_events"])
    assert _sha256(paths1["analyst_notes"]) == _sha256(paths2["analyst_notes"])
    assert _sha256(paths1["payslips_dir"] / "APP-0001.json") == _sha256(
        paths2["payslips_dir"] / "APP-0001.json"
    )
    assert _sha256(paths1["payslips_dir"] / "APP-2000.json") == _sha256(
        paths2["payslips_dir"] / "APP-2000.json"
    )


def test_workspace_generated_documents_and_note_redaction() -> None:
    """Verify the generated files in data/ meet all requirements and notes redact cleanly for snapshots."""
    paths = generate_documents(config_path=DEFAULT_CONFIG_PATH, output_dir=DATA_DIR)

    applicants = {
        row["applicant_id"]: row for row in _read_csv_rows(DATA_DIR / "applicants.csv")
    }
    assert len(applicants) == 2000

    # 1. Verify payslips/<applicant_id>.json
    payslip_files = sorted(paths["payslips_dir"].glob("*.json"))
    assert len(payslip_files) == 2000

    statuses_seen: set[str] = set()
    for sample_id in ["APP-0001", "APP-0500", "APP-1250", "APP-2000"]:
        p_file = paths["payslips_dir"] / f"{sample_id}.json"
        first_line = p_file.read_text(encoding="utf-8").splitlines()[0]
        assert SYNTHETIC_NOTICE in first_line
        payload = json.loads(p_file.read_text(encoding="utf-8"))
        app_row = applicants[sample_id]
        assert payload["applicant_id"] == sample_id
        assert payload["employer"]
        assert abs(float(payload["monthly_income"]) - float(app_row["monthly_income"])) < 1e-6
        assert abs(float(payload["income_annual"]) - float(app_row["annual_income"])) < 1e-6
        assert payload["status"] in {"received", "processing", "missing"}

    for p_file in payslip_files[:200]:
        payload = json.loads(p_file.read_text(encoding="utf-8"))
        statuses_seen.add(payload["status"])
    assert statuses_seen == {"received", "processing", "missing"}

    # 2. Verify consent_events.csv
    consent_rows = _read_csv_rows(paths["consent_events"])
    assert len(consent_rows) == 2000
    assert list(consent_rows[0].keys()) == [
        "applicant_id",
        "consent_type",
        "granted_at",
        "reference",
    ]

    # 3. Verify analyst_notes.csv (~60 notes, ~10 with fake account/phone strings) and redaction
    note_rows = _read_csv_rows(paths["analyst_notes"])
    assert len(note_rows) == 60

    unredacted_count = 0
    for row in note_rows:
        has_patterns = contains_unredacted_sensitive_patterns(row["note_text"])
        redacted_snapshot_note = prepare_note_for_snapshot(row)
        assert not contains_unredacted_sensitive_patterns(redacted_snapshot_note["note_text"])
        if has_patterns:
            unredacted_count += 1
            assert ACCOUNT_TOKEN in redacted_snapshot_note["note_text"]
            assert PHONE_TOKEN in redacted_snapshot_note["note_text"]

    assert unredacted_count == 10


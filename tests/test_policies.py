"""Tests for policy markdown files and data/load_policies.py."""

from datetime import date
import hashlib

import psycopg
import pytest

from data.load_policies import (
    DEFAULT_POLICIES_DIR,
    PolicyRecord,
    get_db_url,
    load_policies,
    load_policy_records,
    validate_non_overlapping_ranges,
)


def test_policy_markdown_files_content_and_sections() -> None:
    records = load_policy_records(DEFAULT_POLICIES_DIR)
    assert [r.policy_id for r in records] == ["LP-2025-v1", "LP-2025-v2", "LP-2026-v1"]

    by_id = {r.policy_id: r for r in records}

    # Check common requirements across all three policies
    required_sections = [
        "3.2 Debt-to-income limit",
        "3.3 Minimum credit score",
        "4.1 Required documents",
        "5.1 Manual review",
    ]
    for rec in records:
        assert rec.policy_family == "consumer_loan"
        assert "SYNTHETIC DATA - FOR DEMONSTRATION." in rec.body
        assert rec.text_sha256 == hashlib.sha256(rec.body.encode("utf-8")).hexdigest()
        for sec in required_sections:
            assert sec in rec.body, f"Missing section '{sec}' in {rec.policy_id}"
        lower_body = rec.body.lower()
        assert "ensures compliance" not in lower_body
        assert "proves" not in lower_body

    # LP-2025-v1 specifics
    p1 = by_id["LP-2025-v1"]
    assert p1.effective_from == date(2025, 1, 1)
    assert p1.effective_to == date(2025, 6, 30)
    assert "0.45" in p1.body
    assert "600" in p1.body
    assert "income proof" in p1.body

    # LP-2025-v2 specifics
    p2 = by_id["LP-2025-v2"]
    assert p2.effective_from == date(2025, 7, 1)
    assert p2.effective_to == date(2025, 12, 31)
    assert "0.40" in p2.body
    assert "620" in p2.body
    assert "income proof" in p2.body

    # LP-2026-v1 specifics
    p3 = by_id["LP-2026-v1"]
    assert p3.effective_from == date(2026, 1, 1)
    assert p3.effective_to is None
    assert "0.40" in p3.body
    assert "640" in p3.body
    assert "income proof" in p3.body
    assert "address proof" in p3.body
    assert "delinquencies_24m >= 2" in p3.body


def test_non_overlapping_effective_ranges_validation() -> None:
    records = load_policy_records(DEFAULT_POLICIES_DIR)
    # Valid non-overlapping ranges pass
    validate_non_overlapping_ranges(records)

    # Overlapping ranges within the same family raise ValueError
    overlapping = [
        PolicyRecord(
            policy_id="TEST-1",
            policy_family="consumer_loan",
            effective_from=date(2025, 1, 1),
            effective_to=date(2025, 7, 15),
            text_sha256="abc",
            body="SYNTHETIC DATA - FOR DEMONSTRATION.\n",
        ),
        PolicyRecord(
            policy_id="TEST-2",
            policy_family="consumer_loan",
            effective_from=date(2025, 7, 1),
            effective_to=date(2025, 12, 31),
            text_sha256="def",
            body="SYNTHETIC DATA - FOR DEMONSTRATION.\n",
        ),
    ]
    with pytest.raises(ValueError, match="Overlapping effective date ranges"):
        validate_non_overlapping_ranges(overlapping)


def test_load_policies_into_postgres_and_verify_non_overlapping() -> None:
    loaded = load_policies()
    assert len(loaded) == 3

    db_url = get_db_url()
    with psycopg.connect(db_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT policy_id, policy_family, effective_from, effective_to, text_sha256, body
                FROM policies
                WHERE policy_family = 'consumer_loan'
                ORDER BY effective_from ASC;
                """
            )
            rows = cur.fetchall()

    assert len(rows) == 3
    db_records = [
        PolicyRecord(
            policy_id=row[0],
            policy_family=row[1],
            effective_from=row[2],
            effective_to=row[3],
            text_sha256=row[4],
            body=row[5],
        )
        for row in rows
    ]
    validate_non_overlapping_ranges(db_records)
    for rec in db_records:
        assert rec.text_sha256 == hashlib.sha256(rec.body.encode("utf-8")).hexdigest()



def test_unparseable_policy_limits_fail_loudly() -> None:
    from decision_api.service import PolicyParseError, _extract_policy_rules

    row = ("LP-X", "consumer_loan", date(2026, 1, 1), None, "0" * 64, "No numeric limits here.")
    with pytest.raises(PolicyParseError):
        _extract_policy_rules(row)

    good_dti_only = (
        "LP-Y", "consumer_loan", date(2026, 1, 1), None, "0" * 64,
        "The maximum allowable debt-to-income ratio (`dti_ratio`) is `0.40` (`40%`).",
    )
    with pytest.raises(PolicyParseError):
        _extract_policy_rules(good_dti_only)

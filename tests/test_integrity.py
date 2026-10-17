"""Unit tests for backend/integrity.py hash-chain construction and verification."""

from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import uuid

import pytest

from backend.integrity import (
    GENESIS,
    build_chain,
    canonical_json,
    entry_hash,
    payload_sha256,
    verify_chain,
)


def _sample_items() -> list[dict]:
    """Return 4 realistic decision-time snapshot items for testing."""
    return [
        {
            "evidence_type": "applicant_features",
            "evidence_ref": "applicant:APP-0100",
            "source_id": "core_banking_los",
            "source_timestamp": "2026-02-10T10:00:00Z",
            "captured_at": "2026-02-10T10:00:01Z",
            "payload": {
                "applicant_id": "APP-0100",
                "income_annual": 75000.0,
                "dti_ratio": 0.31,
                "requested_amount": 18000.0,
            },
        },
        {
            "evidence_type": "bureau_snapshot",
            "evidence_ref": "bureau:APP-0100",
            "source_id": "equifax_sim_v1",
            "source_timestamp": "2026-02-10T09:59:55Z",
            "captured_at": "2026-02-10T10:00:01Z",
            "payload": {
                "applicant_id": "APP-0100",
                "credit_score": 710,
                "delinquencies_24m": 0,
            },
        },
        {
            "evidence_type": "document",
            "evidence_ref": "payslips/APP-0100.json",
            "source_id": "payroll_verifier_v1",
            "source_timestamp": "2026-02-10T09:55:00Z",
            "captured_at": "2026-02-10T10:00:01Z",
            "payload": {
                "applicant_id": "APP-0100",
                "status": "received",
                "monthly_income": 6250.0,
            },
        },
        {
            "evidence_type": "policy_evaluation",
            "evidence_ref": "policy:LP-2026-v1",
            "source_id": "decision_engine_v1",
            "source_timestamp": "2026-02-10T10:00:01Z",
            "captured_at": "2026-02-10T10:00:01Z",
            "payload": {
                "policy_id": "LP-2026-v1",
                "outcome": "APPROVE",
                "cited_sections": ["3.2", "3.3", "4.1"],
            },
        },
    ]


def test_canonical_json_formatting_utf8_and_nan_rejection() -> None:
    """Verify sorted keys, no whitespace, UTF-8 preservation, number formatting, and NaN/Infinity rejection."""
    assert GENESIS == "GENESIS"

    obj = {"z": 1, "a": "café €", "m": [True, False, None, -0.0, Decimal("12.5")]}
    rendered = canonical_json(obj)
    assert rendered == '{"a":"café €","m":[true,false,null,0.0,12.5],"z":1}'
    assert " " not in rendered.replace("café €", "cafe_euro")

    for bad_value in [float("nan"), float("inf"), float("-inf"), Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")]:
        with pytest.raises(ValueError, match="NaN and Infinity"):
            canonical_json({"bad": bad_value})


def test_determinism() -> None:
    """Repeated calls with identical inputs produce identical canonical JSON, payload hashes, and chains."""
    decision_id = uuid.UUID("11111111-2222-3333-4444-555555555555")
    items_1 = _sample_items()
    items_2 = _sample_items()

    rows_1, len_1, head_1 = build_chain(decision_id, items_1)
    rows_2, len_2, head_2 = build_chain(decision_id, items_2)

    assert len_1 == len_2 == 4
    assert head_1 == head_2
    assert rows_1 == rows_2
    assert rows_1[0]["seq"] == 1
    assert rows_1[0]["prev_hash"] == GENESIS
    for idx in range(1, len_1):
        assert rows_1[idx]["seq"] == idx + 1
        assert rows_1[idx]["prev_hash"] == rows_1[idx - 1]["entry_hash"]

    ok, broken_seq, reason = verify_chain(rows_1, len_1, head_1)
    assert ok is True
    assert broken_seq is None
    assert reason is None


def test_key_order_independence() -> None:
    """Dictionaries with identical key-value pairs in different insertion orders hash identically."""
    payload_a = {
        "dti_ratio": 0.31,
        "applicant_id": "APP-0100",
        "nested": {"beta": 2, "alpha": 1, "gamma": [10, 20]},
        "income_annual": 75000.0,
    }
    payload_b = {
        "income_annual": 75000.0,
        "nested": {"gamma": [10, 20], "alpha": 1, "beta": 2},
        "applicant_id": "APP-0100",
        "dti_ratio": 0.31,
    }

    assert canonical_json(payload_a) == canonical_json(payload_b)
    assert payload_sha256(payload_a) == payload_sha256(payload_b)

    # Also verify entry_hash matches exact SHA-256 of canonical_json of the 9-field envelope
    decision_id = "11111111-2222-3333-4444-555555555555"
    p_sha = payload_sha256(payload_a)
    expected_envelope = {
        "prev_hash": GENESIS,
        "payload_sha256": p_sha,
        "captured_at": "2026-02-10T10:00:01Z",
        "source_timestamp": "2026-02-10T10:00:00Z",
        "source_id": "core_banking_los",
        "evidence_ref": "applicant:APP-0100",
        "evidence_type": "applicant_features",
        "seq": 1,
        "decision_id": decision_id,
    }
    expected_digest = hashlib.sha256(canonical_json(expected_envelope).encode("utf-8")).hexdigest()
    computed = entry_hash(
        decision_id=decision_id,
        seq=1,
        evidence_type="applicant_features",
        evidence_ref="applicant:APP-0100",
        source_id="core_banking_los",
        source_timestamp="2026-02-10T10:00:00Z",
        captured_at="2026-02-10T10:00:01Z",
        payload_sha256=p_sha,
        prev_hash=GENESIS,
    )
    assert computed == expected_digest


def test_modified_payload_detected() -> None:
    """Modifying a stored payload without updating hashes is caught at the exact seq."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    tampered = copy.deepcopy(rows)
    tampered[1]["payload"]["credit_score"] = 850  # seq 2 altered

    ok, first_broken_seq, reason = verify_chain(tampered, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 2
    assert reason is not None
    assert "payload_sha256 mismatch" in reason


def test_modified_metadata_detected() -> None:
    """Modifying snapshot metadata (e.g. source_id or evidence_ref) breaks entry_hash at that seq."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    tampered = copy.deepcopy(rows)
    tampered[2]["source_id"] = "untrusted_source"  # seq 3 metadata altered

    ok, first_broken_seq, reason = verify_chain(tampered, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 3
    assert reason is not None
    assert "entry_hash mismatch" in reason


def test_reordered_rows_detected() -> None:
    """Swapping two rows in the snapshot sequence is caught by verify_chain."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    # Case A: swap rows[1] and rows[2] directly (seqs become 1, 3, 2, 4)
    swapped_direct = copy.deepcopy(rows)
    swapped_direct[1], swapped_direct[2] = swapped_direct[2], swapped_direct[1]
    ok, first_broken_seq, reason = verify_chain(swapped_direct, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 2
    assert reason is not None

    # Case B: swap rows[1] and rows[2] and renumber their seq fields to 2 and 3 (breaks prev_hash / entry_hash)
    swapped_renumbered = copy.deepcopy(rows)
    swapped_renumbered[1], swapped_renumbered[2] = swapped_renumbered[2], swapped_renumbered[1]
    swapped_renumbered[1]["seq"] = 2
    swapped_renumbered[2]["seq"] = 3
    ok_b, broken_seq_b, reason_b = verify_chain(swapped_renumbered, chain_length, chain_head)
    assert ok_b is False
    assert broken_seq_b == 2
    assert reason_b is not None


def test_deleted_middle_row_detected() -> None:
    """Deleting a middle row (e.g. seq 2) is caught at the gap in sequence / prev_hash."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    tampered = copy.deepcopy(rows)
    del tampered[1]  # remove seq 2; remaining seqs are 1, 3, 4

    ok, first_broken_seq, reason = verify_chain(tampered, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 2
    assert reason is not None


def test_deleted_final_row_caught_via_expected_length_and_head() -> None:
    """Deleting the final row leaves a self-consistent prefix but is caught via expected_length and expected_head."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    truncated = copy.deepcopy(rows[:-1])  # remove final row (seq 4)

    # 1. Caught when both expected_length and expected_head are the original chain's values
    ok, first_broken_seq, reason = verify_chain(truncated, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 4
    assert reason is not None
    assert "chain_length" in reason or "chain_head" in reason

    # 2. Also caught if expected_length was somehow spoofed to len(truncated) but expected_head is retained
    ok_head, broken_seq_head, reason_head = verify_chain(truncated, len(truncated), chain_head)
    assert ok_head is False
    assert broken_seq_head == 3
    assert reason_head is not None
    assert "chain_head mismatch" in reason_head


def test_duplicate_seq_detected() -> None:
    """Duplicate seq values in rows are explicitly rejected."""
    decision_id = uuid.uuid4()
    rows, chain_length, chain_head = build_chain(decision_id, _sample_items())

    duplicated = copy.deepcopy(rows)
    duplicated[2] = copy.deepcopy(duplicated[1])  # two rows with seq == 2

    ok, first_broken_seq, reason = verify_chain(duplicated, chain_length, chain_head)
    assert ok is False
    assert first_broken_seq == 2
    assert reason is not None
    assert "duplicate seq" in reason


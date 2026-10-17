"""Tamper-evident hash chain construction and verification for decision-time snapshots.

Number Formatting & Canonical JSON Rules:
- Object keys must be strings and are sorted lexicographically by Unicode code point (`sort_keys=True`).
- Separators use `,` and `:` with zero whitespace (`separators=(",", ":")`).
- Output is UTF-8 encoded text (`ensure_ascii=False`).
- Numbers:
  - Booleans (`True`, `False`) serialize as JSON `true` and `false`.
  - Integers (`int`) serialize as base-10 integer literals with no decimal point or leading zeros.
  - Finite floating-point values (`float`, `Decimal`) normalize `-0.0` to `0.0` and serialize using
    Python's shortest round-trip IEEE-754 decimal representation.
  - `NaN`, `+Infinity`, and `-Infinity` (whether `float` or `Decimal`) are strictly rejected with `ValueError`.
"""

from __future__ import annotations

import copy
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from typing import Any
import uuid


#: Documented sentinel previous-hash constant for the first entry (seq = 1) in a decision snapshot hash chain.
GENESIS: str = "GENESIS"


class ChainBuildResult(dict):
    """Dictionary result of build_chain() supporting both key/attribute access and 3-tuple unpacking.

    Keys / attributes:
      - rows: list[dict[str, Any]]
      - chain_length: int
      - chain_head: str
    Unpacking:
      `rows, chain_length, chain_head = build_chain(decision_id, items)`
    """

    @property
    def rows(self) -> list[dict[str, Any]]:
        return list(self["rows"])

    @property
    def chain_length(self) -> int:
        return int(self["chain_length"])

    @property
    def chain_head(self) -> str:
        return str(self["chain_head"])

    def __iter__(self):
        yield self["rows"]
        yield self["chain_length"]
        yield self["chain_head"]


def _normalize_timestamp(value: Any) -> str:
    """Normalize a datetime or ISO-8601 timestamp string into canonical UTC ISO-8601 format."""
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    raw = str(value).strip()
    if not raw:
        return raw
    try:
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        if "T" in candidate or " " in candidate:
            dt = datetime.fromisoformat(candidate)
            dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
            return dt.isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    return raw


def _normalize_for_canonical_json(obj: Any) -> Any:
    """Recursively validate and normalize values for canonical JSON serialization."""
    if obj is None or isinstance(obj, (bool, str)):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            raise ValueError("NaN and Infinity are not permitted in canonical JSON")
        return 0.0 if obj == 0.0 else obj
    if isinstance(obj, Decimal):
        if obj.is_nan() or obj.is_infinite():
            raise ValueError("NaN and Infinity are not permitted in canonical JSON")
        f_val = float(obj)
        if math.isnan(f_val) or math.isinf(f_val):
            raise ValueError("NaN and Infinity are not permitted in canonical JSON")
        return 0.0 if f_val == 0.0 else f_val
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return _normalize_timestamp(obj)
    if isinstance(obj, (list, tuple)):
        return [_normalize_for_canonical_json(item) for item in obj]
    if isinstance(obj, dict):
        normalized: dict[str, Any] = {}
        for k, v in obj.items():
            if not isinstance(k, str):
                raise TypeError(f"Canonical JSON object keys must be strings, got {type(k).__name__}")
            normalized[k] = _normalize_for_canonical_json(v)
        return normalized
    raise TypeError(f"Object of type {type(obj).__name__} is not canonical-JSON serializable")


def canonical_json(obj: Any) -> str:
    """Serialize obj to canonical JSON (sorted keys, no whitespace, UTF-8, rejecting NaN and Infinity)."""
    normalized = _normalize_for_canonical_json(obj)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def payload_sha256(payload: Any) -> str:
    """Compute the SHA-256 hex digest of canonical_json(payload) encoded as UTF-8."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def entry_hash(
    decision_id: uuid.UUID | str | dict[str, Any],
    seq: int | None = None,
    evidence_type: str | None = None,
    evidence_ref: str | None = None,
    source_id: str | None = None,
    source_timestamp: datetime | str | None = None,
    captured_at: datetime | str | None = None,
    payload_sha256: str | None = None,
    prev_hash: str | None = None,
) -> str:
    """Compute SHA-256 of canonical_json({decision_id, seq, evidence_type, evidence_ref, source_id, source_timestamp, captured_at, payload_sha256, prev_hash}).

    Can be called either with a single dictionary containing those 9 keys or with explicit arguments.
    """
    if isinstance(decision_id, dict) and seq is None:
        row = decision_id
        envelope = {
            "decision_id": str(row["decision_id"]),
            "seq": int(row["seq"]),
            "evidence_type": str(row["evidence_type"]),
            "evidence_ref": str(row["evidence_ref"]),
            "source_id": str(row["source_id"]),
            "source_timestamp": _normalize_timestamp(row["source_timestamp"]),
            "captured_at": _normalize_timestamp(row["captured_at"]),
            "payload_sha256": str(row["payload_sha256"]),
            "prev_hash": str(row["prev_hash"]),
        }
    else:
        if (
            seq is None
            or evidence_type is None
            or evidence_ref is None
            or source_id is None
            or source_timestamp is None
            or captured_at is None
            or payload_sha256 is None
            or prev_hash is None
        ):
            raise ValueError("All 9 entry_hash fields must be provided")
        envelope = {
            "decision_id": str(decision_id),
            "seq": int(seq),
            "evidence_type": str(evidence_type),
            "evidence_ref": str(evidence_ref),
            "source_id": str(source_id),
            "source_timestamp": _normalize_timestamp(source_timestamp),
            "captured_at": _normalize_timestamp(captured_at),
            "payload_sha256": str(payload_sha256),
            "prev_hash": str(prev_hash),
        }
    return hashlib.sha256(canonical_json(envelope).encode("utf-8")).hexdigest()


compute_entry_hash = entry_hash


def build_chain(
    decision_id: uuid.UUID | str,
    items: list[dict[str, Any]],
) -> ChainBuildResult:
    """Build a hash-chained sequence of snapshot rows for decision_id.

    Returns ChainBuildResult with:
      - rows: list of snapshot dicts with seq starting at 1, prev_hash (GENESIS first), payload_sha256, and entry_hash
      - chain_length: len(rows)
      - chain_head: final entry_hash (or GENESIS if items is empty)
    Supports both dict/attribute access and `rows, chain_length, chain_head = build_chain(...)`.
    """
    dec_id_str = str(decision_id)
    rows: list[dict[str, Any]] = []
    current_prev_hash = GENESIS

    for seq, item in enumerate(items, start=1):
        ev_type = str(item["evidence_type"])
        ev_ref = str(item["evidence_ref"])
        src_id = str(item["source_id"])
        src_ts = _normalize_timestamp(item["source_timestamp"])
        cap_ts = _normalize_timestamp(item.get("captured_at", item["source_timestamp"]))

        raw_payload = copy.deepcopy(item["payload"])
        p_sha = payload_sha256(raw_payload)

        e_hash = entry_hash(
            decision_id=dec_id_str,
            seq=seq,
            evidence_type=ev_type,
            evidence_ref=ev_ref,
            source_id=src_id,
            source_timestamp=src_ts,
            captured_at=cap_ts,
            payload_sha256=p_sha,
            prev_hash=current_prev_hash,
        )

        row_dict: dict[str, Any] = {
            "decision_id": dec_id_str,
            "seq": seq,
            "evidence_type": ev_type,
            "evidence_ref": ev_ref,
            "source_id": src_id,
            "source_timestamp": src_ts,
            "captured_at": cap_ts,
            "payload": raw_payload,
            "payload_sha256": p_sha,
            "prev_hash": current_prev_hash,
            "entry_hash": e_hash,
        }
        rows.append(row_dict)
        current_prev_hash = e_hash

    chain_length = len(rows)
    chain_head = rows[-1]["entry_hash"] if rows else GENESIS

    return ChainBuildResult(
        rows=rows,
        chain_length=chain_length,
        chain_head=chain_head,
    )


def verify_chain(
    rows: list[dict[str, Any]],
    expected_length: int,
    expected_head: str,
) -> tuple[bool, int | None, str | None]:
    """Verify hash-chained snapshot rows against expected_length and expected_head.

    Returns (ok, first_broken_seq, reason):
      - When valid: (True, None, None)
      - When invalid: (False, first_broken_seq, descriptive_reason)
    """
    seen_seqs: set[int] = set()
    all_seqs = [int(r["seq"]) for r in rows if "seq" in r]
    expected_prev_hash = GENESIS

    for idx, row in enumerate(rows):
        expected_seq = idx + 1
        row_seq = int(row["seq"])

        # 1. Duplicate seq check
        if row_seq in seen_seqs:
            return (
                False,
                row_seq,
                f"duplicate seq {row_seq} detected at position {expected_seq}",
            )
        seen_seqs.add(row_seq)

        # 2. Sequence continuity (reordered rows vs deleted middle row)
        if row_seq != expected_seq:
            if expected_seq in all_seqs:
                return (
                    False,
                    expected_seq,
                    f"reordered rows at seq {expected_seq}: found seq {row_seq}",
                )
            return (
                False,
                expected_seq,
                f"deleted or missing row at seq {expected_seq} (found seq {row_seq}; snapshot_count {len(rows)} != expected chain_length {expected_length})",
            )

        # 3. Previous hash continuity check
        stored_prev_hash = str(row.get("prev_hash", ""))
        if stored_prev_hash != expected_prev_hash:
            return (
                False,
                row_seq,
                f"prev_hash mismatch at seq {row_seq}: expected {expected_prev_hash}, got {stored_prev_hash}",
            )

        # 4. Payload hash verification (if payload is present)
        stored_payload_sha = str(row.get("payload_sha256", ""))
        if "payload" in row and row["payload"] is not None:
            recomputed_payload_sha = payload_sha256(row["payload"])
            if recomputed_payload_sha != stored_payload_sha:
                return (
                    False,
                    row_seq,
                    f"payload_sha256 mismatch at seq {row_seq}",
                )

        # 5. Entry hash verification over canonical metadata + payload_sha256 + prev_hash
        stored_entry_hash = str(row.get("entry_hash", ""))
        recomputed_entry_hash = entry_hash(
            decision_id=row["decision_id"],
            seq=row_seq,
            evidence_type=row["evidence_type"],
            evidence_ref=row["evidence_ref"],
            source_id=row["source_id"],
            source_timestamp=row["source_timestamp"],
            captured_at=row["captured_at"],
            payload_sha256=stored_payload_sha,
            prev_hash=stored_prev_hash,
        )
        if recomputed_entry_hash != stored_entry_hash:
            return (
                False,
                row_seq,
                f"entry_hash mismatch at seq {row_seq}",
            )

        expected_prev_hash = stored_entry_hash

    # 6. Check chain length and head against retained reference (catches deleted final row)
    actual_length = len(rows)
    actual_head = str(rows[-1]["entry_hash"]) if rows else GENESIS

    if actual_length != expected_length:
        broken_seq = actual_length + 1 if actual_length < expected_length else expected_length + 1
        head_note = (
            f" and chain_head mismatch ({actual_head} != {expected_head})"
            if actual_head != expected_head
            else ""
        )
        return (
            False,
            broken_seq,
            f"snapshot_count {actual_length} != expected chain_length {expected_length}{head_note}",
        )

    if actual_head != expected_head:
        broken_seq = actual_length if actual_length > 0 else 1
        return (
            False,
            broken_seq,
            f"chain_head mismatch at seq {broken_seq}: got {actual_head}, expected {expected_head}",
        )

    return (True, None, None)


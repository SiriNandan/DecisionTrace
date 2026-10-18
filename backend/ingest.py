"""Record-mode ingest: a lender's own decision plus its evidence, stored in the hash chain (envelope schema v1).

DecisionTrace does not score here. The lender's engine decided; we preserve what it saw and said.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.integrity import build_chain, canonical_json, payload_sha256

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024
DEV_ID_SECRET = "dev-only-id-secret"


class Reason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=500)


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_type: Literal["application", "bureau", "account_history", "document", "consent", "other"]
    evidence_ref: str = Field(..., min_length=1, max_length=200)
    source_id: str = Field(..., min_length=1, max_length=100)
    source_timestamp: datetime
    payload: dict[str, Any]

    @field_validator("payload")
    @classmethod
    def _no_reserved_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        if "_enc" in value:
            raise ValueError("The name '_enc' is reserved and can't be used in evidence.")
        return value


class SourceMapping(BaseModel):
    """Which approved field mapping turned the lender's file row into this envelope."""

    model_config = ConfigDict(extra="forbid")
    mapping_id: int
    version: int
    spec_sha256: str = Field(..., min_length=64, max_length=64)


class DecisionEnvelopeV1(BaseModel):
    """What a lender sends for one decision. `subject_ref` must be a pseudonymous ID, not a name or contact detail."""

    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1]
    external_id: str = Field(..., min_length=1, max_length=128, description="The lender's own application reference")
    subject_ref: str = Field(..., min_length=1, max_length=128)
    decided_at: datetime
    outcome: Literal["APPROVE", "DECLINE", "REFER"]
    score: float = Field(..., ge=0, le=1, description="The lender's risk estimate, 0 to 1")
    model_version: str = Field(..., min_length=1, max_length=100)
    policy_version: str = Field(..., min_length=1, max_length=100)
    reasons: list[Reason] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceItem] = Field(..., min_length=1, max_length=50)
    source_mapping: SourceMapping | None = None
    portfolio: str | None = Field(default=None, min_length=1, max_length=64, description="Optional group label (branch, product, region) used to limit who can see the decision")

    @model_validator(mode="after")
    def _unique_evidence_refs(self) -> "DecisionEnvelopeV1":
        refs = [e.evidence_ref for e in self.evidence]
        dupes = sorted({r for r in refs if refs.count(r) > 1})
        if dupes:
            raise ValueError(f"Each piece of evidence needs its own evidence_ref; repeated: {', '.join(dupes)}.")
        return self


def _id_secret() -> bytes:
    secret = os.environ.get("DT_ID_SECRET", "").strip()
    if secret:
        return secret.encode("utf-8")
    if os.environ.get("ENV", "dev").lower() == "dev":
        return DEV_ID_SECRET.encode("utf-8")
    raise RuntimeError("DT_ID_SECRET must be set outside ENV=dev.")


def keyed_decision_id(tenant_id: str, idempotency_key: str) -> uuid.UUID:
    """Unguessable, repeatable decision ID: HMAC over (tenant, key) with a server-side secret."""
    digest = hmac.new(_id_secret(), f"{tenant_id}\x00{idempotency_key}".encode("utf-8"), hashlib.sha256).digest()
    return uuid.UUID(bytes=digest[:16], version=4)


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _chain_items(env: DecisionEnvelopeV1, decided_at: datetime, captured_at: datetime) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {
            "evidence_type": "lender_decision",
            "evidence_ref": f"lender_decision:{env.external_id}",
            "source_id": "lender_engine",
            "source_timestamp": decided_at,
            "captured_at": captured_at,
            "payload": {
                "external_id": env.external_id,
                "subject_ref": env.subject_ref,
                "outcome": env.outcome,
                "score": env.score,
                "model_version": env.model_version,
                "policy_version": env.policy_version,
                "reasons": [r.model_dump() for r in env.reasons],
                "schema_version": env.schema_version,
            },
        }
    ]
    if env.source_mapping is not None:  # only present for imports, so direct API payload hashes are unchanged
        items[0]["payload"]["source_mapping"] = env.source_mapping.model_dump()
    for ev in env.evidence:
        items.append(
            {
                "evidence_type": ev.evidence_type,
                "evidence_ref": ev.evidence_ref,
                "source_id": ev.source_id,
                "source_timestamp": _utc(ev.source_timestamp),
                "captured_at": captured_at,
                "payload": ev.payload,
            }
        )
    return items


def _same_content(conn: psycopg.Connection[Any], decision_id: str, stored_protected: bool,
                  plain_items: list[dict[str, Any]], new_snapshots: list[dict[str, Any]]) -> bool:
    """Is a replayed request exactly what was recorded? For protected records the stored fingerprints are salted, so
    compare the content itself, which needs the key (a record whose content was removed can't be matched)."""
    from backend.crypto import Reveal, is_encrypted

    rows = conn.execute(
        "SELECT evidence_ref, payload, payload_sha256 FROM decision_snapshots WHERE decision_id = %s ORDER BY seq", (decision_id,)
    ).fetchall()
    if len(rows) != len(plain_items):
        return False
    if not stored_protected:
        return [r[2] for r in rows] == [s["payload_sha256"] for s in new_snapshots] or all(
            r[2] == payload_sha256(i["payload"]) for r, i in zip(rows, plain_items)
        )
    reveal = Reveal(conn)
    for (ref, payload, _), item in zip(rows, plain_items):
        if ref != item["evidence_ref"] or not is_encrypted(payload):
            return False
        opened = reveal.payload(decision_id, ref, payload)
        if opened is None or canonical_json(opened) != canonical_json(item["payload"]):
            return False
    return True


def record_decision(
    conn: psycopg.Connection[Any],
    *,
    tenant_id: str,
    envelope: DecisionEnvelopeV1,
    idempotency_key: str,
) -> tuple[dict[str, Any], bool]:
    """Store the envelope atomically. Returns (summary, replayed). Raises ValueError (422) or FileExistsError (409)."""
    from decision_api.service import _insert_snapshot_row  # deferred: decision_api imports this module

    for ev in envelope.evidence:
        if len(json.dumps(ev.payload, default=str)) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"Evidence '{ev.evidence_ref}' is larger than {MAX_PAYLOAD_BYTES // 1024} KB.")

    from backend.crypto import Protector, tenant_encrypts  # deferred: only needed for protected lenders

    dec_uuid = keyed_decision_id(tenant_id, idempotency_key)
    decided_at = _utc(envelope.decided_at)
    captured_at = datetime.now(timezone.utc)
    plain_items = _chain_items(envelope, decided_at, captured_at)
    protector = Protector(tenant_id, str(dec_uuid)) if tenant_encrypts(conn, tenant_id) else None
    items = [protector.protect_item(i) for i in plain_items] if protector is not None else plain_items
    snapshots, chain_length, chain_head = build_chain(dec_uuid, items)

    def replay() -> tuple[dict[str, Any], bool] | None:
        existing = conn.execute(
            "SELECT chain_length, chain_head, decided_at, encrypted FROM decisions WHERE decision_id = %s AND tenant_id = %s",
            (dec_uuid, tenant_id),
        ).fetchone()
        if existing is None:
            return None
        if not _same_content(conn, str(dec_uuid), bool(existing[3]), plain_items, snapshots):
            raise FileExistsError("That Idempotency-Key was already used with different content.")
        return (
            {
                "decision_id": str(dec_uuid),
                "chain_length": int(existing[0]),
                "chain_head": str(existing[1]),
                "decided_at": existing[2].isoformat(),
            },
            True,
        )

    try:
        with conn.transaction():
            already = replay()
            if already is not None:
                return already
            _store(
                conn, dec_uuid=dec_uuid, tenant_id=tenant_id, envelope=envelope, decided_at=decided_at,
                captured_at=captured_at, chain_length=chain_length, chain_head=chain_head,
                snapshots=snapshots, protector=protector, insert_snapshot_row=_insert_snapshot_row,
            )
    except psycopg.errors.UniqueViolation:
        # A concurrent request with the same Idempotency-Key committed first: this one is a replay, not a failure.
        with conn.transaction():
            already = replay()
        if already is None:
            raise
        return already

    return (
        {
            "decision_id": str(dec_uuid),
            "chain_length": chain_length,
            "chain_head": chain_head,
            "decided_at": decided_at.isoformat(),
        },
        False,
    )


def _store(
    conn: psycopg.Connection[Any],
    *,
    dec_uuid: uuid.UUID,
    tenant_id: str,
    envelope: DecisionEnvelopeV1,
    decided_at: datetime,
    captured_at: datetime,
    chain_length: int,
    chain_head: str,
    snapshots: list[dict[str, Any]],
    protector: Any,
    insert_snapshot_row: Any,
) -> None:
    """Write the decision row, its snapshots and evidence status (the caller owns the transaction)."""
    conn.execute(
        """
        INSERT INTO decisions (
            decision_id, application_id, decided_at, outcome, model_version, policy_version,
            score, chain_length, chain_head, tenant_id, recording_mode, encrypted, portfolio
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'recorded', %s, %s)
        """,
        (
            dec_uuid, envelope.external_id, decided_at, envelope.outcome, envelope.model_version,
            envelope.policy_version, envelope.score, chain_length, chain_head, tenant_id, protector is not None,
            envelope.portfolio,
        ),
    )
    if protector is not None:
        protector.store_wrap(conn)
    with conn.cursor() as cur:
        for snap in snapshots:
            insert_snapshot_row(cur, snap)
            cur.execute(
                "INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at) "
                "VALUES (%s, %s, 'available', %s)",
                (dec_uuid, snap["evidence_ref"], captured_at),
            )

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
from pydantic import BaseModel, ConfigDict, Field

from backend.integrity import build_chain

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

    dec_uuid = keyed_decision_id(tenant_id, idempotency_key)
    decided_at = _utc(envelope.decided_at)
    captured_at = datetime.now(timezone.utc)
    items = _chain_items(envelope, decided_at, captured_at)
    snapshots, chain_length, chain_head = build_chain(dec_uuid, items)

    with conn.transaction():
        existing = conn.execute(
            "SELECT chain_length, chain_head, decided_at FROM decisions WHERE decision_id = %s AND tenant_id = %s",
            (dec_uuid, tenant_id),
        ).fetchone()
        if existing is not None:
            stored = [
                r[0]
                for r in conn.execute(
                    "SELECT payload_sha256 FROM decision_snapshots WHERE decision_id = %s ORDER BY seq", (dec_uuid,)
                ).fetchall()
            ]
            if stored != [s["payload_sha256"] for s in snapshots]:
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

        conn.execute(
            """
            INSERT INTO decisions (
                decision_id, application_id, decided_at, outcome, model_version, policy_version,
                score, chain_length, chain_head, tenant_id, recording_mode
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'recorded')
            """,
            (
                dec_uuid, envelope.external_id, decided_at, envelope.outcome, envelope.model_version,
                envelope.policy_version, envelope.score, chain_length, chain_head, tenant_id,
            ),
        )
        with conn.cursor() as cur:
            for snap in snapshots:
                _insert_snapshot_row(cur, snap)
                cur.execute(
                    "INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at) "
                    "VALUES (%s, %s, 'available', %s)",
                    (dec_uuid, snap["evidence_ref"], captured_at),
                )

    return (
        {
            "decision_id": str(dec_uuid),
            "chain_length": chain_length,
            "chain_head": chain_head,
            "decided_at": decided_at.isoformat(),
        },
        False,
    )

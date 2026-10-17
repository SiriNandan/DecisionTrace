"""Sealing in the database: build a signed batch of record fingerprints, make proofs, and check everything still matches."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg

from backend.merkle import (
    GENESIS_ROOT,
    MAX_BATCH,
    FilePublisher,
    Signer,
    inclusion_path,
    leaf_data_bytes,
    leaf_hash,
    sign_head,
    tree_root,
    verify_head,
)


class SealConflict(Exception):
    """Someone else sealed this lender's records at the same moment."""


def decision_leaf(decision_id: str, chain_length: int, chain_head: str) -> dict[str, Any]:
    return {"kind": "decision", "decision_id": decision_id, "chain_length": int(chain_length), "chain_head": chain_head}


def corrections_leaf(decision_id: str, count: int, head: str) -> dict[str, Any]:
    return {"kind": "corrections", "decision_id": decision_id, "count": int(count), "head": head}


CORRECTION_RESERVE = 500  # each batch keeps room for corrections, so a big backlog of decisions can't hold them back


def pending_count(conn: psycopg.Connection[Any], tenant_id: str) -> int:
    """How many records are not in any batch yet (decisions never sealed, plus corrections added since)."""
    decisions = conn.execute(
        "SELECT count(*) FROM decisions d WHERE d.tenant_id = %s AND NOT EXISTS "
        "(SELECT 1 FROM anchor_leaves l WHERE l.decision_id = d.decision_id AND l.kind = 'decision')", (tenant_id,)).fetchone()[0]
    corrections = conn.execute(
        "SELECT count(*) FROM corrections c WHERE c.tenant_id = %s AND c.seq = (SELECT max(seq) FROM corrections c2 WHERE c2.decision_id = c.decision_id) "
        "AND NOT EXISTS (SELECT 1 FROM anchor_leaves l WHERE l.decision_id = c.decision_id AND l.kind = 'corrections' AND l.leaf_data->>'head' = c.entry_hash)",
        (tenant_id,)).fetchone()[0]
    return int(decisions) + int(corrections)


def pending_leaves(conn: psycopg.Connection[Any], tenant_id: str) -> list[dict[str, Any]]:
    """Records not yet in any batch: up to MAX_BATCH, with room kept for corrections added since the last seal."""
    corrections = [
        corrections_leaf(str(r[0]), r[1], str(r[2]))
        for r in conn.execute(
            "SELECT c.decision_id, c.seq, c.entry_hash FROM corrections c WHERE c.tenant_id = %s "
            "AND c.seq = (SELECT max(seq) FROM corrections c2 WHERE c2.decision_id = c.decision_id) AND NOT EXISTS "
            "(SELECT 1 FROM anchor_leaves l WHERE l.decision_id = c.decision_id AND l.kind = 'corrections' "
            "AND l.leaf_data->>'head' = c.entry_hash) ORDER BY c.decision_id LIMIT %s", (tenant_id, CORRECTION_RESERVE),
        ).fetchall()
    ]
    decisions = [
        decision_leaf(str(r[0]), r[1], str(r[2]))
        for r in conn.execute(
            "SELECT d.decision_id, d.chain_length, d.chain_head FROM decisions d WHERE d.tenant_id = %s AND NOT EXISTS "
            "(SELECT 1 FROM anchor_leaves l WHERE l.decision_id = d.decision_id AND l.kind = 'decision') "
            "ORDER BY d.decided_at, d.decision_id LIMIT %s", (tenant_id, MAX_BATCH - len(corrections)),
        ).fetchall()
    ]
    return decisions + corrections


def seal_batch(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    signer: Signer,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Seal everything not yet sealed (up to one batch). Returns the signed head, or None when there is nothing new.

    Publishing the head outside the database is a separate step (`publish_missing`) that runs after this is committed, so a
    published head always has a matching row. Overlapping runs for one lender queue behind each other."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dt-seal:{tenant_id}",))
    leaves = pending_leaves(conn, tenant_id)
    if not leaves:
        return None
    last = conn.execute(
        "SELECT seq, root FROM anchor_batches WHERE tenant_id = %s ORDER BY seq DESC LIMIT 1", (tenant_id,)
    ).fetchone()
    seq, prev_root = (1, GENESIS_ROOT) if last is None else (int(last[0]) + 1, str(last[1]))
    hashes = [leaf_hash(leaf_data_bytes(leaf)) for leaf in leaves]
    head = {
        "tenant_id": tenant_id, "seq": seq, "root": tree_root(hashes).hex(), "leaf_count": len(leaves),
        "prev_root": prev_root, "signed_at": (now or datetime.now(timezone.utc)).isoformat(), "key_id": signer.key_id,
    }
    head["signature"] = sign_head(signer, head)
    try:
        with conn.transaction():
            row = conn.execute(
                "INSERT INTO anchor_batches (tenant_id, seq, leaf_count, root, prev_root, key_id, signature, signed_at, signed_at_text, published) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false) RETURNING batch_id",
                (tenant_id, seq, len(leaves), head["root"], prev_root, signer.key_id, head["signature"], head["signed_at"], head["signed_at"]),
            ).fetchone()
            for i, (leaf, h) in enumerate(zip(leaves, hashes)):
                conn.execute(
                    "INSERT INTO anchor_leaves (batch_id, tenant_id, leaf_index, kind, decision_id, leaf_data, leaf_hash) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
                    (row[0], tenant_id, i, leaf["kind"], leaf["decision_id"], json.dumps(leaf), h.hex()),
                )
    except psycopg.errors.UniqueViolation as exc:
        raise SealConflict() from exc
    return head


def publish_missing(conn: psycopg.Connection[Any], tenant_id: str, publisher: FilePublisher) -> int:
    """Copy every committed batch that isn't in the published file yet, oldest first. Safe to repeat: it is how a copy that
    failed to write the first time is made good. Returns how many were added."""
    have = {h["seq"] for h in publisher.read(tenant_id)[0]}
    added = 0
    for r in conn.execute(
        "SELECT seq, root, leaf_count, prev_root, signed_at, key_id, signature, signed_at_text FROM anchor_batches "
        "WHERE tenant_id = %s ORDER BY seq", (tenant_id,),
    ).fetchall():
        if int(r[0]) not in have:
            publisher.publish(_head_row(tenant_id, r))
            added += 1
    return added


def list_batches(conn: psycopg.Connection[Any], tenant_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT seq, leaf_count, root, prev_root, key_id, signature, signed_at, published FROM anchor_batches "
        "WHERE tenant_id = %s ORDER BY seq DESC LIMIT %s", (tenant_id, limit),
    ).fetchall()
    return [
        {"seq": r[0], "leaf_count": r[1], "root": r[2], "prev_root": r[3], "key_id": r[4], "signature": r[5],
         "signed_at": r[6].isoformat(), "published": r[7]}
        for r in rows
    ]


def _head_row(tenant_id: str, r: tuple[Any, ...]) -> dict[str, Any]:
    """A head exactly as it was signed: the stored text, not a time re-rendered in whatever zone the session uses."""
    text = r[7] if len(r) > 7 and r[7] else r[4].astimezone(timezone.utc).isoformat()
    return {"tenant_id": tenant_id, "seq": r[0], "root": r[1], "leaf_count": r[2], "prev_root": r[3],
            "signed_at": text, "key_id": r[5], "signature": r[6]}


def proof_for(conn: psycopg.Connection[Any], tenant_id: str, decision_id: str, signer: Signer,
              kind: str = "decision") -> dict[str, Any]:
    """An inclusion proof for the newest seal of this record, or {'sealed': False} if it hasn't been sealed yet."""
    row = conn.execute(
        "SELECT l.batch_id, l.leaf_index, l.leaf_data, l.leaf_hash FROM anchor_leaves l WHERE l.decision_id = %s "
        "AND l.kind = %s AND l.tenant_id = %s ORDER BY l.leaf_id DESC LIMIT 1", (decision_id, kind, tenant_id),
    ).fetchone()
    if row is None:
        return {"sealed": False}
    batch_id, index, leaf_data, lh = row
    hashes = [bytes.fromhex(r[0]) for r in conn.execute(
        "SELECT leaf_hash FROM anchor_leaves WHERE batch_id = %s ORDER BY leaf_index", (batch_id,)).fetchall()]
    b = conn.execute(
        "SELECT seq, root, leaf_count, prev_root, signed_at, key_id, signature, signed_at_text FROM anchor_batches WHERE batch_id = %s", (batch_id,)
    ).fetchone()
    return {
        "sealed": True, "kind": kind, "leaf": leaf_data, "leaf_hash": lh, "leaf_index": index,
        "path": inclusion_path(hashes, index), "head": _head_row(tenant_id, b),
        "public_key": signer.public_b64 if b[5] == signer.key_id else None, "sealed_at": b[4].isoformat(),
    }


def verify_seals(conn: psycopg.Connection[Any], tenant_id: str, signer: Signer, publisher: FilePublisher | None = None) -> dict[str, Any]:
    """Recompute every batch and compare it with the signed heads, with the live records, and with what was published."""
    problems: list[dict[str, Any]] = []
    batches = conn.execute(
        "SELECT batch_id, seq, root, leaf_count, prev_root, signed_at, key_id, signature, signed_at_text FROM anchor_batches "
        "WHERE tenant_id = %s ORDER BY seq", (tenant_id,),
    ).fetchall()
    prev_root, leaves_checked = GENESIS_ROOT, 0
    published: dict[int, dict[str, Any]] | None = None
    if publisher:
        heads, unreadable = publisher.read(tenant_id)
        published = {}
        for h in heads:
            if h["seq"] in published and published[h["seq"]] != h:
                problems.append({"batch": int(h["seq"]), "problem": "The published copy has two different versions of this batch."})
            published.setdefault(h["seq"], h)
        if unreadable:
            problems.append({"batch": 0, "problem": f"{unreadable} line(s) in the published copy can't be read."})
    for i, (batch_id, seq, root, count, prev, signed_at, key_id, signature, signed_text) in enumerate(batches, start=1):
        def bad(msg: str) -> None:
            problems.append({"batch": int(seq), "problem": msg})

        if seq != i:
            bad(f"Batch {i} is missing from the database.")
        if prev != prev_root:
            bad("This batch doesn't follow on from the one before it.")
        head = _head_row(tenant_id, (seq, root, count, prev, signed_at, key_id, signature, signed_text))
        if key_id == signer.key_id and not verify_head(head, signer.public_b64):
            bad("The signature on this batch isn't valid.")
        elif key_id != signer.key_id:
            bad("This batch was signed with a different key than the one in use, so its signature wasn't checked.")
        rows = conn.execute(
            "SELECT leaf_index, kind, decision_id, leaf_data, leaf_hash FROM anchor_leaves WHERE batch_id = %s ORDER BY leaf_index", (batch_id,)
        ).fetchall()
        if len(rows) != count:
            bad(f"The batch says it has {count} records but {len(rows)} are stored.")
        hashes = []
        for idx, kind, dec, data, stored in rows:
            computed = leaf_hash(leaf_data_bytes(data)).hex()
            hashes.append(bytes.fromhex(stored))
            if computed != stored:
                bad(f"A stored record fingerprint was altered (item {idx}).")
        if hashes and tree_root(hashes).hex() != root:
            bad("The records in this batch no longer add up to its sealed root.")
        leaves_checked += len(rows)
        for idx, kind, dec, data, stored in rows:
            if kind == "decision":
                cur = conn.execute("SELECT chain_length, chain_head FROM decisions WHERE decision_id = %s", (dec,)).fetchone()
                if cur is None:
                    bad(f"Decision {dec} was removed after it was sealed.")
                elif (int(cur[0]), str(cur[1])) != (data["chain_length"], data["chain_head"]):
                    bad(f"Decision {dec} changed after it was sealed.")
            else:
                cur = conn.execute("SELECT entry_hash FROM corrections WHERE decision_id = %s AND seq = %s", (dec, data["count"])).fetchone()
                if cur is None or str(cur[0]) != data["head"]:
                    bad(f"The corrections for decision {dec} changed after they were sealed.")
        if published is not None:
            pub = published.get(seq)
            if pub is None:
                bad("This batch was never published, or its published copy is gone.")
            elif (pub["root"], pub["signature"], pub["prev_root"]) != (root, signature, prev):
                bad("This batch differs from the copy published outside the database.")
        prev_root = root
    if published is not None:
        stored_seqs = {int(b[1]) for b in batches}
        for seq in sorted(set(published) - stored_seqs):
            problems.append({"batch": seq, "problem": "A batch that was published is missing from the database."})
    waiting = pending_count(conn, tenant_id)
    return {"ok": not problems, "batches": len(batches), "records": leaves_checked, "checked_published_copy": published is not None,
            "not_yet_sealed": waiting, "problems": problems}

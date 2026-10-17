"""Protected evidence: encrypt a decision's evidence, and make it unreadable on request without touching the record's chain.

How it works
- Each protected decision gets its own random data key. The data key is wrapped (encrypted) with a per-lender key and the
  wrapped copy is stored. Evidence payloads are stored only as AES-256-GCM ciphertext.
- The hash chain commits to a keyed fingerprint (HMAC-SHA256) of each plaintext payload rather than a plain hash, so a
  fingerprint reveals nothing about low-entropy values such as an income, yet a replay of the same record gives the same
  fingerprint (idempotent ingest still works).
- Erasing a decision (operator command, owner database role) deletes its wrapped data keys. The ciphertext and the
  fingerprints stay, so the chain and the seals still verify. The content can no longer be read by anyone.

Not built: a hardware or cloud key service. `LocalKeyProvider` derives lender keys from one master secret (DT_MASTER_KEY);
`KeyProvider` is the seam where a KMS/HSM goes. With the local provider, rotating a version re-wraps keys but does not
retire the old version's key material, which a real KMS would.

    python -m backend.crypto erase <decision_id> --reason "..." [--by name]
    python -m backend.crypto rotate <tenant_id> --to 2 [--drop-old]
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import uuid
from typing import Any, Protocol

import psycopg
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend.integrity import canonical_json, is_envelope
from data.load_policies import get_db_url

ENC = "_enc"
DEV_MASTER = hashlib.sha256(b"decisiontrace-dev-only-master-key").digest()  # development only: anyone can derive this


def master_key() -> bytes:
    raw = os.environ.get("DT_MASTER_KEY", "").strip()
    if raw:
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise ValueError("DT_MASTER_KEY must be 32 bytes, base64 encoded.")
        return key
    if os.environ.get("ENV", "dev").lower() == "dev":
        return DEV_MASTER
    raise RuntimeError("DT_MASTER_KEY must be set outside ENV=dev to use protected evidence.")


def _hkdf(info: str, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=info.encode("utf-8")).derive(master_key())


class KeyProvider(Protocol):
    def current_version(self) -> int: ...
    def wrap(self, tenant_id: str, version: int, decision_id: str, dek: bytes) -> bytes: ...
    def unwrap(self, tenant_id: str, version: int, decision_id: str, wrapped: bytes) -> bytes: ...


class LocalKeyProvider:
    """Lender keys derived from one master secret. A stand-in for a key service (see the module note)."""

    def current_version(self) -> int:
        return max(1, int(os.environ.get("DT_KEK_VERSION", "1")))

    def _aead(self, tenant_id: str, version: int) -> AESGCM:
        return AESGCM(_hkdf(f"dt-kek:{tenant_id}:{version}"))

    def wrap(self, tenant_id: str, version: int, decision_id: str, dek: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return nonce + self._aead(tenant_id, version).encrypt(nonce, dek, f"{tenant_id}|{version}|{decision_id}".encode())

    def unwrap(self, tenant_id: str, version: int, decision_id: str, wrapped: bytes) -> bytes:
        return self._aead(tenant_id, version).decrypt(wrapped[:12], wrapped[12:], f"{tenant_id}|{version}|{decision_id}".encode())


_provider: KeyProvider | None = None


def get_provider() -> KeyProvider:
    return _provider or LocalKeyProvider()


def set_provider(provider: KeyProvider | None) -> None:
    global _provider
    _provider = provider


# ---------- encrypting and fingerprinting ----------

def is_encrypted(payload: Any) -> bool:
    return is_envelope(payload)


def _commit_key(tenant_id: str) -> bytes:
    return _hkdf(f"dt-commit:{tenant_id}")


def commit_for(tenant_id: str, decision_id: str, evidence_ref: str, salt: str, payload: Any) -> str:
    """Keyed fingerprint of one item. It is bound to the decision and item, and includes a random salt that is kept only
    inside the encrypted content, so once the data key is destroyed nothing can be confirmed by guessing the content,
    even by someone holding the master secret."""
    msg = f"{decision_id}|{evidence_ref}|{salt}|{canonical_json(payload)}"
    return hmac.new(_commit_key(tenant_id), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_payload(dek: bytes, aad: str, payload: Any) -> dict[str, Any]:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(dek).encrypt(nonce, canonical_json(payload).encode("utf-8"), aad.encode("utf-8"))
    return {ENC: {"v": 1, "alg": "A256GCM", "nonce": base64.b64encode(nonce).decode(), "ct": base64.b64encode(ct).decode()}}


def decrypt_payload(dek: bytes, aad: str, envelope: dict[str, Any]) -> Any:
    e = envelope[ENC]
    return json.loads(AESGCM(dek).decrypt(base64.b64decode(e["nonce"]), base64.b64decode(e["ct"]), aad.encode("utf-8")))


def seal_payload(tenant_id: str, decision_id: str, evidence_ref: str, dek: bytes, payload: Any) -> tuple[dict[str, Any], str]:
    """(ciphertext envelope, keyed fingerprint) for one item. The salt travels inside the ciphertext."""
    salt = secrets.token_hex(16)
    envelope = encrypt_payload(dek, payload_aad(decision_id, evidence_ref), {"p": payload, "s": salt})
    return envelope, commit_for(tenant_id, decision_id, evidence_ref, salt, payload)


def tenant_encrypts(conn: psycopg.Connection[Any], tenant_id: str) -> bool:
    row = conn.execute("SELECT encrypt_evidence FROM tenants WHERE tenant_id = %s", (tenant_id,)).fetchone()
    return bool(row and row[0])


def payload_aad(decision_id: str, evidence_ref: str) -> str:
    return f"{decision_id}|{evidence_ref}"


class Protector:
    """Encrypts the items of one new decision (and, later, its corrections) with a fresh data key."""

    def __init__(self, tenant_id: str, decision_id: str, dek: bytes | None = None) -> None:
        self.tenant_id, self.decision_id = tenant_id, str(decision_id)
        self.dek = dek or secrets.token_bytes(32)

    def protect_item(self, item: dict[str, Any]) -> dict[str, Any]:
        envelope, commit = seal_payload(self.tenant_id, self.decision_id, item["evidence_ref"], self.dek, item["payload"])
        return {**item, "payload": envelope, "payload_sha256_override": commit}

    def store_wrap(self, conn: psycopg.Connection[Any]) -> None:
        provider = get_provider()
        version = provider.current_version()
        conn.execute(
            "INSERT INTO data_key_wraps (tenant_id, decision_id, kek_version, wrapped_dek) VALUES (%s, %s, %s, %s)",
            (self.tenant_id, self.decision_id, version, provider.wrap(self.tenant_id, version, self.decision_id, self.dek)),
        )


class Reveal:
    """Reads protected payloads.

    A key can be missing for two very different reasons, and they must not look alike: it was destroyed on purpose (an
    erasure is on record) or something is wrong (wrapped keys deleted without a record, a wrong master secret, a key
    version that can't be opened). Only the first is treated as "removed"; the second makes verification fail."""

    def __init__(self, conn: psycopg.Connection[Any], provider: KeyProvider | None = None) -> None:
        self.conn = conn
        self.provider = provider or get_provider()
        self._cache: dict[str, tuple[str, tuple[str, bytes] | None]] = {}

    def _resolve(self, decision_id: str) -> tuple[str, tuple[str, bytes] | None]:
        if decision_id in self._cache:
            return self._cache[decision_id]
        wraps = self.conn.execute(
            "SELECT tenant_id, kek_version, wrapped_dek FROM data_key_wraps WHERE decision_id = %s ORDER BY kek_version DESC", (decision_id,)
        ).fetchall()
        result: tuple[str, tuple[str, bytes] | None]
        if not wraps:
            erased = self.conn.execute("SELECT 1 FROM erasures WHERE decision_id = %s", (decision_id,)).fetchone()
            result = ("erased" if erased else "missing", None)
        else:
            result = ("unreadable", None)
            for tenant_id, version, wrapped in wraps:
                try:
                    result = ("ok", (str(tenant_id), self.provider.unwrap(str(tenant_id), int(version), decision_id, bytes(wrapped))))
                    break
                except Exception:
                    continue
        self._cache[decision_id] = result
        return result

    def state(self, decision_id: str) -> str:
        """'ok', 'erased' (destroyed on purpose), 'missing' (no key and no record of why) or 'unreadable'."""
        return self._resolve(str(decision_id))[0]

    def dek(self, decision_id: str) -> tuple[str, bytes] | None:
        return self._resolve(str(decision_id))[1]

    def opened(self, decision_id: str, evidence_ref: str, payload: Any) -> tuple[Any, str] | None:
        """(plaintext, salt), or None if it can't be opened."""
        key = self.dek(str(decision_id))
        if key is None:
            return None
        try:
            wrapper = decrypt_payload(key[1], payload_aad(str(decision_id), evidence_ref), payload)
            return wrapper["p"], wrapper["s"]
        except Exception:
            return None

    def payload(self, decision_id: str, evidence_ref: str, payload: Any) -> Any:
        if not is_encrypted(payload):
            return payload
        got = self.opened(decision_id, evidence_ref, payload)
        return None if got is None else got[0]

    def checker(self):  # -> Callable[[dict], bool | None]
        def check(row: dict[str, Any]) -> bool | None:
            decision_id = str(row["decision_id"])
            state = self.state(decision_id)
            if state == "erased":
                return None  # destroyed on purpose, on record: the links are still checked, the content can't be
            if state != "ok":
                return False  # keys missing or unreadable with no erasure on record: that is a problem, not a removal
            got = self.opened(decision_id, str(row["evidence_ref"]), row["payload"])
            if got is None:
                return False  # a key exists but the content doesn't open: it was changed
            tenant_id = self.dek(decision_id)[0]  # type: ignore[index]
            return hmac.compare_digest(commit_for(tenant_id, decision_id, str(row["evidence_ref"]), got[1], got[0]), str(row["payload_sha256"]))

        return check


ERASED_PAYLOAD = {"status": "erased_by_retention"}
UNAVAILABLE_PAYLOAD = {"status": "key_unavailable"}


# ---------- operator commands (owner database role) ----------

REMOVED_TEXT = "[removed under an erasure request]"  # long enough for the minimum length of a stored reason


def _lock_tenant_keys(conn: psycopg.Connection[Any], tenant_id: str) -> None:
    """Erasing and re-wrapping a lender's keys must not interleave, or a destroyed key could be written back."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"dt-keys:{tenant_id}",))


def erase_decision(conn: psycopg.Connection[Any], decision_id: str, reason: str, by: str) -> dict[str, Any]:
    """Destroy a decision's data keys and remove what was written about its content. Run with the owner role.

    Removed: the wrapped keys (so the evidence and corrections can't be read by anyone), and the wording and values of data
    checks, plus the free-text notes and reasons people typed about this decision. Kept: the outcome, score, dates, model
    and policy names on the decision, the access log, the fingerprints and the seals."""
    dec = str(uuid.UUID(str(decision_id)))
    row = conn.execute("SELECT tenant_id, encrypted FROM decisions WHERE decision_id = %s", (dec,)).fetchone()
    if row is None:
        raise LookupError(f"Decision {dec} not found")
    if not row[1]:
        raise ValueError("That decision's evidence isn't protected, so there is no key to destroy.")
    tenant_id = str(row[0])
    with conn.transaction():
        _lock_tenant_keys(conn, tenant_id)
        deleted = conn.execute("DELETE FROM data_key_wraps WHERE decision_id = %s", (dec,)).rowcount
        conn.execute("INSERT INTO erasures (tenant_id, decision_id, erased_by, reason) VALUES (%s, %s, %s, %s)", (tenant_id, dec, by, reason))
        refs = [r[0] for r in conn.execute("SELECT evidence_ref FROM decision_snapshots WHERE decision_id = %s", (dec,)).fetchall()]
        for ref in refs:  # the existing screens and reports already know how to describe removed evidence
            # "Newest status wins", and the database's clock may disagree with the clock of whatever wrote the earlier rows,
            # so make this one strictly later than any status already on file for the item.
            conn.execute(
                "INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at, tenant_id) VALUES (%s, %s, 'erased_by_retention', "
                "GREATEST(now(), COALESCE((SELECT max(changed_at) FROM evidence_status WHERE decision_id = %s AND evidence_ref = %s), now()) "
                "+ interval '1 millisecond'), %s)",
                (dec, ref, dec, ref, tenant_id),
            )
        scrubbed = conn.execute(
            "UPDATE quality_findings SET message = %s, observed = '{}'::jsonb, suggested_fix = NULL, ai_note = NULL, enc_detail = NULL "
            "WHERE decision_id = %s", (REMOVED_TEXT, dec),
        ).rowcount
        conn.execute("UPDATE finding_reviews SET note = %s WHERE finding_id IN (SELECT finding_id FROM quality_findings WHERE decision_id = %s)", (REMOVED_TEXT, dec))
        conn.execute("UPDATE overrides SET reason = %s WHERE decision_id = %s", (REMOVED_TEXT, dec))
        conn.execute(
            "INSERT INTO access_log (tenant_id, role, action, object_type, object_id, outcome) VALUES (%s, 'operator', 'erase', 'decision', %s, 'allowed')",
            (tenant_id, dec),
        )
    return {"decision_id": dec, "keys_destroyed": deleted, "items": len(refs), "findings_scrubbed": scrubbed}


def rotate_tenant(conn: psycopg.Connection[Any], tenant_id: str, to_version: int, drop_old: bool = False) -> dict[str, Any]:
    """Re-wrap every data key of a lender under key version `to_version` (owner role).

    `drop_old` removes the older wraps, but only for decisions that already have one at the new version, and only when
    new decisions are already being written at that version (DT_KEK_VERSION), so a key can't be lost while this runs."""
    provider = get_provider()
    if drop_old and provider.current_version() != to_version:
        raise ValueError(f"Set DT_KEK_VERSION={to_version} (and restart the API) before dropping the old key versions.")
    rewrapped = dropped = 0
    with conn.transaction():
        _lock_tenant_keys(conn, tenant_id)
        for dec, version, wrapped in conn.execute(
            "SELECT w.decision_id, w.kek_version, w.wrapped_dek FROM data_key_wraps w WHERE w.tenant_id = %s AND w.kek_version <> %s "
            "AND NOT EXISTS (SELECT 1 FROM data_key_wraps n WHERE n.decision_id = w.decision_id AND n.kek_version = %s) "
            "AND NOT EXISTS (SELECT 1 FROM erasures e WHERE e.decision_id = w.decision_id)",
            (tenant_id, to_version, to_version),
        ).fetchall():
            dek = provider.unwrap(tenant_id, int(version), str(dec), bytes(wrapped))
            conn.execute(
                "INSERT INTO data_key_wraps (tenant_id, decision_id, kek_version, wrapped_dek) VALUES (%s, %s, %s, %s)",
                (tenant_id, dec, to_version, provider.wrap(tenant_id, to_version, str(dec), dek)),
            )
            rewrapped += 1
        if drop_old:
            dropped = conn.execute(
                "DELETE FROM data_key_wraps w WHERE w.tenant_id = %s AND w.kek_version <> %s AND EXISTS "
                "(SELECT 1 FROM data_key_wraps n WHERE n.decision_id = w.decision_id AND n.kek_version = %s)",
                (tenant_id, to_version, to_version),
            ).rowcount
    return {"tenant_id": tenant_id, "rewrapped": rewrapped, "old_wraps_dropped": dropped}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operator commands for protected evidence (use the owner DB_URL).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("erase")
    p.add_argument("decision_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", default=os.environ.get("USER", "operator"))
    p = sub.add_parser("rotate")
    p.add_argument("tenant_id")
    p.add_argument("--to", type=int, required=True)
    p.add_argument("--drop-old", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        out = erase_decision(conn, args.decision_id, args.reason, args.by) if args.cmd == "erase" else rotate_tenant(conn, args.tenant_id, args.to, args.drop_old)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

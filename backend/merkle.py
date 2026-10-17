"""Sealing: a Merkle tree over record fingerprints, signed, with proofs anyone can check offline.

Tree shape and hashing follow RFC 6962 (leaf = SHA-256(0x00 || data), node = SHA-256(0x01 || left || right),
split at the largest power of two below the size). A batch's root is signed with Ed25519, so someone who can rewrite
the database cannot produce a head that verifies without the signing key. Publishing signed heads to storage the
database administrator cannot write (see FilePublisher) is what lets a later change be noticed.

    python -m backend.merkle verify-proof proof.json
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from backend.integrity import canonical_json

GENESIS_ROOT = "SEAL_GENESIS"
DEV_SEED = b"decisiontrace-dev-only-signing-seed!"  # development only: anyone can derive this key
MAX_BATCH = 5000


# ---------- the tree ----------

def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def tree_root(leaves: list[bytes]) -> bytes:
    """Root over already-hashed leaves (RFC 6962 MTH)."""
    n = len(leaves)
    if n == 0:
        return hashlib.sha256(b"").digest()
    if n == 1:
        return leaves[0]
    k = _split(n)
    return node_hash(tree_root(leaves[:k]), tree_root(leaves[k:]))


def inclusion_path(leaves: list[bytes], index: int) -> list[tuple[str, str]]:
    """Sibling hashes from the leaf up to the root, each with the side the sibling sits on ('L' or 'R')."""
    n = len(leaves)
    if not 0 <= index < n:
        raise IndexError("leaf index out of range")
    if n == 1:
        return []
    k = _split(n)
    if index < k:
        return inclusion_path(leaves[:k], index) + [("R", tree_root(leaves[k:]).hex())]
    return inclusion_path(leaves[k:], index - k) + [("L", tree_root(leaves[:k]).hex())]


def root_from_path(leaf: bytes, path: list[tuple[str, str]]) -> bytes:
    h = leaf
    for side, sibling in path:
        s = bytes.fromhex(sibling)
        h = node_hash(s, h) if side == "L" else node_hash(h, s)
    return h


def leaf_data_bytes(leaf: dict[str, Any]) -> bytes:
    return canonical_json(leaf).encode("utf-8")


# ---------- keys and signed heads ----------

@dataclass(frozen=True)
class Signer:
    private: Ed25519PrivateKey
    key_id: str

    @property
    def public_b64(self) -> str:
        raw = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")


def load_signer() -> Signer:
    """The sealing key: DT_SEAL_KEY (base64, 32 bytes). In development a fixed, public key is used so things work."""
    raw = os.environ.get("DT_SEAL_KEY", "").strip()
    if raw:
        seed = base64.b64decode(raw)
        if len(seed) != 32:
            raise ValueError("DT_SEAL_KEY must be 32 bytes, base64 encoded.")
    elif os.environ.get("ENV", "dev").lower() == "dev":
        seed = hashlib.sha256(DEV_SEED).digest()
    else:
        raise RuntimeError("DT_SEAL_KEY must be set outside ENV=dev.")
    key = Ed25519PrivateKey.from_private_bytes(seed)
    pub = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return Signer(key, hashlib.sha256(pub).hexdigest()[:16])


def head_message(head: dict[str, Any]) -> bytes:
    fields = {k: head[k] for k in ("tenant_id", "seq", "root", "leaf_count", "prev_root", "signed_at", "key_id")}
    return canonical_json(fields).encode("utf-8")


def sign_head(signer: Signer, head: dict[str, Any]) -> str:
    return base64.b64encode(signer.private.sign(head_message(head))).decode("ascii")


def verify_head(head: dict[str, Any], public_b64: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(
            base64.b64decode(head["signature"]), head_message(head)
        )
        return True
    except (InvalidSignature, ValueError, KeyError):
        return False


def verify_proof(proof: dict[str, Any], public_b64: str | None = None) -> tuple[bool, str]:
    """Check one proof from the API without any database: the path reaches the root, and the root was signed.

    Without `public_b64` the key inside the proof is used, which only shows the proof is internally consistent: anyone
    can make a proof with their own key. To know the seal is the lender's, pass the key you got from `/v1/seals/public-key`
    (or from the lender) yourself."""
    try:
        head = proof["head"]
        leaf = leaf_hash(leaf_data_bytes(proof["leaf"]))
        if leaf.hex() != proof["leaf_hash"]:
            return False, "The record fingerprint doesn't match its hash."
        if root_from_path(leaf, [(s, h) for s, h in proof["path"]]).hex() != head["root"]:
            return False, "The proof doesn't lead to the sealed root."
        if not verify_head(head, public_b64 or proof["public_key"]):
            return False, "The seal's signature isn't valid."
    except (KeyError, ValueError, TypeError) as exc:
        return False, f"The proof is incomplete or damaged ({type(exc).__name__})."
    return True, "ok" if public_b64 else UNAUTHENTICATED


UNAUTHENTICATED = "consistent, but the signer was not checked against a key you trust"


# ---------- publishing ----------

class FilePublisher:
    """Appends each signed head to a file per lender. Point DT_SEAL_PUBLISH_DIR at storage the database's
    administrator cannot rewrite (write-once, or a separately permissioned volume); on the same disk and under the
    same administrator it adds little."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def from_env(cls) -> "FilePublisher | None":
        raw = os.environ.get("DT_SEAL_PUBLISH_DIR", "").strip()
        return cls(Path(raw)) if raw else None

    def path(self, tenant_id: str) -> Path:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,62}", tenant_id):
            raise ValueError("Invalid lender id for a published file name.")
        return self.directory / f"{tenant_id}.seals.jsonl"

    def publish(self, head: dict[str, Any]) -> None:
        """Append one head: whole line, exclusive lock, flushed to disk before returning."""
        self.directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(head, sort_keys=True, separators=(",", ":")) + "\n"
        with self.path(head["tenant_id"]).open("a", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def read(self, tenant_id: str) -> tuple[list[dict[str, Any]], int]:
        """(heads, number of unreadable lines). A damaged line is reported by the caller, never a crash."""
        p = self.path(tenant_id)
        if not p.is_file():
            return [], 0
        heads, bad = [], 0
        for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if not ln.strip():
                continue
            try:
                item = json.loads(ln)
                heads.append(item) if isinstance(item, dict) and "seq" in item else (bad := bad + 1)
            except json.JSONDecodeError:
                bad += 1
        return heads, bad


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2 or args[0] != "verify-proof":
        print("usage: python -m backend.merkle verify-proof proof.json [public_key_base64]")
        return 2
    proof = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    ok, why = verify_proof(proof, args[2] if len(args) > 2 else None)
    if not ok:
        print(f"The proof is NOT valid: {why}")
        return 1
    if why == UNAUTHENTICATED:
        print("The proof is internally consistent, but its signer was not checked. Pass the lender's public key "
              "(from /v1/seals/public-key, obtained separately) as the second argument to be sure whose seal it is.")
        return 3
    print("The proof is valid and the signature matches the key you gave.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

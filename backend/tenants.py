"""Operator commands for tenants and API credentials (run with the owner DB_URL, not the API role).

    python -m backend.tenants create-tenant acme "Acme Bank"
    python -m backend.tenants issue-key acme lender_ingest --label "prod ingest"
    python -m backend.tenants revoke-key 3
"""

from __future__ import annotations

import argparse

import psycopg

from backend.auth import ROLE_SCOPES, generate_api_key, hash_api_key
from data.load_policies import get_db_url


def create_tenant(conn: psycopg.Connection, tenant_id: str, display_name: str) -> None:
    conn.execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) ON CONFLICT (tenant_id) DO NOTHING",
        (tenant_id, display_name),
    )
    conn.commit()


def issue_key(conn: psycopg.Connection, tenant_id: str, role: str, label: str = "") -> tuple[int, str]:
    """Create a credential and return (credential_id, key). The key cannot be recovered later."""
    if role not in ROLE_SCOPES:
        raise ValueError(f"Unknown role {role!r}; use one of {sorted(ROLE_SCOPES)}.")
    key = generate_api_key()
    row = conn.execute(
        "INSERT INTO api_credentials (tenant_id, key_sha256, role, label) VALUES (%s, %s, %s, %s) RETURNING credential_id",
        (tenant_id, hash_api_key(key), role, label),
    ).fetchone()
    conn.commit()
    return int(row[0]), key


def revoke_key(conn: psycopg.Connection, credential_id: int) -> None:
    conn.execute("UPDATE api_credentials SET revoked_at = now() WHERE credential_id = %s", (credential_id,))
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage DecisionTrace tenants and API keys.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-tenant")
    p.add_argument("tenant_id")
    p.add_argument("display_name")
    p = sub.add_parser("issue-key")
    p.add_argument("tenant_id")
    p.add_argument("role", choices=sorted(ROLE_SCOPES))
    p.add_argument("--label", default="")
    p = sub.add_parser("revoke-key")
    p.add_argument("credential_id", type=int)
    args = parser.parse_args()

    with psycopg.connect(get_db_url(), connect_timeout=5) as conn:
        if args.cmd == "create-tenant":
            create_tenant(conn, args.tenant_id, args.display_name)
            print(f"Tenant '{args.tenant_id}' is ready.")
        elif args.cmd == "issue-key":
            cred_id, key = issue_key(conn, args.tenant_id, args.role, args.label)
            print(f"Credential {cred_id} ({args.role}). Copy this key now; it is not stored:\n{key}")
        else:
            revoke_key(conn, args.credential_id)
            print(f"Credential {args.credential_id} revoked.")


if __name__ == "__main__":
    main()

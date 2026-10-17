"""Customer choices about their own decision, kept as an append-only ledger.

Today there is one choice: whether an AI model may write a short plain-language summary of the explanation. The
model is only ever given the facts already in the explanation (figures, reasons), never a name or contact detail.
Each lender sets a policy: 'ask' (needs the customer's yes, the default), 'allow' (on unless the customer says no)
or 'never'. A customer's "no" always wins unless the policy is 'never' anyway.
"""

from __future__ import annotations

from typing import Any

import psycopg

from data.load_policies import get_db_url

WORDING_VERSION = "ai-summary-2026-10"
PURPOSES = ("ai_summary",)


def latest_choice(conn: psycopg.Connection[Any], decision_id: str, purpose: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT granted, recorded_at, given_by, wording_version FROM consents WHERE decision_id = %s AND purpose = %s "
        "ORDER BY consent_id DESC LIMIT 1", (decision_id, purpose),
    ).fetchone()
    return None if row is None else {"granted": bool(row[0]), "recorded_at": row[1].isoformat(), "given_by": row[2], "wording_version": row[3]}


def effective(policy: str, choice: dict[str, Any] | None) -> bool:
    if policy == "never":
        return False
    if choice is not None:
        return bool(choice["granted"])
    return policy == "allow"


def state(conn: psycopg.Connection[Any], decision_id: str) -> dict[str, Any]:
    """Everything the screen needs: the lender's policy, the current choice, whether the AI summary is on, and the history."""
    policy = str((conn.execute(
        "SELECT t.ai_summary_policy FROM decisions d JOIN tenants t ON t.tenant_id = d.tenant_id WHERE d.decision_id = %s", (decision_id,)
    ).fetchone() or ["ask"])[0])
    choice = latest_choice(conn, decision_id, "ai_summary")
    history = [
        {"granted": bool(r[0]), "recorded_at": r[1].isoformat(), "given_by": r[2]}
        for r in conn.execute(
            "SELECT granted, recorded_at, given_by FROM consents WHERE decision_id = %s AND purpose = 'ai_summary' ORDER BY consent_id DESC",
            (decision_id,),
        ).fetchall()
    ]
    return {"purpose": "ai_summary", "policy": policy, "choice": choice, "ai_summary_on": effective(policy, choice),
            "wording_version": WORDING_VERSION, "history": history}


def ai_summary_allowed(decision_id: str, db_url: str | None = None) -> bool:
    """Used by the explanation service (owner connection, no lender selected) to decide whether a model may be asked."""
    with psycopg.connect(get_db_url(db_url), connect_timeout=5) as conn:
        s = state(conn, str(decision_id))
    return bool(s["ai_summary_on"])

"""Explanation for a decision the lender's own system made (recording_mode='recorded').

DecisionTrace did not score these, so there is nothing to re-work and no model is asked to write anything:
the report is built directly from the preserved `lender_decision` snapshot, and every claim cites it.
"""

from __future__ import annotations

from typing import Any

import psycopg

from backend.contract import ReconstructionReport

NO_REASONS = "The lender's system recorded this outcome without giving reasons."


def build_recorded_report(decision_id: str, db_url: str) -> ReconstructionReport:
    """Raises KeyError when the decision does not exist (same as the scored path)."""
    from decision_api.service import reconstruct_decision_from_snapshots  # deferred: avoids an import cycle

    with psycopg.connect(db_url, connect_timeout=10) as conn:
        try:
            rec = reconstruct_decision_from_snapshots(conn, decision_id)
        except LookupError as exc:
            raise KeyError(str(exc)) from exc

    snap = next((s for s in rec["snapshots"] if s["evidence_type"] == "lender_decision"), None)
    payload: dict[str, Any] = dict(snap["payload"]) if snap else {}
    ref = str(snap["evidence_ref"]) if snap else ""

    statuses: dict[str, str] = rec["evidence_statuses"]
    missing = sorted(r for r, st in statuses.items() if st != "available")
    completeness = round((len(statuses) - len(missing)) / len(statuses), 6) if statuses else 1.0
    lender_erased = ref in missing or snap is None

    claims: list[dict[str, Any]] = []
    principal: list[str] = []
    if not lender_erased:
        outcome = str(payload.get("outcome"))
        claims.append(
            {
                "text": f"The lender's system recorded the outcome {outcome}.",
                "citation": ref,
                "field": "outcome",
                "value": outcome,
            }
        )
        for reason in payload.get("reasons", []):
            text = str(reason.get("text", "")).strip()
            code = str(reason.get("code", "")).strip()
            if not text or not code:
                continue
            principal.append(text)
            claims.append({"text": text, "citation": ref, "field": "reason_code", "value": code})
        if not principal:
            principal.append(NO_REASONS)
        claims.append(
            {
                "text": f"The lender's system recorded a risk estimate of {payload.get('score')}.",
                "citation": ref,
                "field": "score",
                "value": payload.get("score"),
            }
        )

    integrity = (
        "hash chain verified against retained reference"
        if rec["chain_valid"]
        else f"hash chain verification failed ({rec['broken_reason'] or 'tamper_detected'})"
    )
    report = {
        "decision_id": str(rec["decision_id"]),
        "outcome": rec["effective_outcome"],
        "score": float(rec["score"]),
        "chain_verified": bool(rec["chain_valid"]),
        "replay_verified": False,
        "completeness": completeness,
        "missing_evidence": missing,
        "principal_reasons": principal,
        "rule_checks": [],
        "reasons": [{"claims": claims}] if claims else [],
        "policy_note": (
            f"Recorded from the lender's own system under policy {rec['policy_version']} and model "
            f"{rec['model_version']}; {integrity}. DecisionTrace did not make or re-check this decision. "
            "This record supports audit and explanation and is tamper-evident against a retained reference."
        ),
        "human_override": rec["human_override"],
        "recording_mode": "recorded",
        "guardrail_rejections": 0,
        "status": "evidence_unavailable" if missing else "ok",
    }
    return ReconstructionReport.model_validate(report)

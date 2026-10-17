"""Reconstruction Agent orchestration service for DecisionTrace.

Enforces the 8-tool-call limit, 30-second wall-clock timeout, Pydantic `ReconstructionReport`
validation with a single retry before returning `status='error'`, and `status='evidence_unavailable'`
when preserved evidence is marked `erased_by_retention` or `missing` in `evidence_status`.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import time
from typing import Any, overload, Literal
import uuid

import psycopg
from pydantic import ValidationError

from backend.contract import ReconstructionReport, ReconstructionStatus
from recon_agent.backends import LLMBackend, get_llm_backend
from recon_agent.guardrails import apply_claim_guardrails, check_narrative
from recon_agent.recorded import build_recorded_report, correction_notes
from recon_agent.tools import (
    DEFAULT_MAX_TOOL_CALLS,
    DEFAULT_REPLAY_TOLERANCE,
    DEFAULT_TIMEOUT_SECONDS,
    ReconstructionTimeoutError,
    ToolLimitExceededError,
    ToolSession,
    fast_db_url,
)


def _fetch_fallback_decision_meta(decision_id: str, db_url: str) -> dict[str, Any]:
    """Read minimal decision metadata from PostgreSQL to construct a valid fallback report on timeout/error."""
    dec_uuid = uuid.UUID(str(decision_id))
    with psycopg.connect(db_url, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT decision_id, outcome, policy_version, score
                FROM decisions
                WHERE decision_id = %s;
                """,
                (dec_uuid,),
            )
            row = cur.fetchone()
            if row is None:
                raise KeyError(f"Decision {decision_id} not found")

            cur.execute(
                """
                SELECT new_outcome, reason, actor_role, created_at
                FROM overrides
                WHERE decision_id = %s AND kind = 'override'
                ORDER BY created_at DESC
                LIMIT 1;
                """,
                (dec_uuid,),
            )
            ov_row = cur.fetchone()

    outcome = str(ov_row[0]) if ov_row is not None else str(row[1])
    human_override = None
    if ov_row is not None:
        human_override = {
            "new_outcome": str(ov_row[0]),
            "reason": str(ov_row[1]),
            "actor_role": str(ov_row[2]),
            "created_at": (
                ov_row[3].isoformat() if hasattr(ov_row[3], "isoformat") else str(ov_row[3])
            ),
        }

    return {
        "decision_id": str(row[0]),
        "outcome": outcome,
        "policy_version": str(row[2]),
        "score": float(row[3]),
        "human_override": human_override,
    }


def _build_fallback_report(
    decision_id: str,
    meta: dict[str, Any],
    status: ReconstructionStatus,
    reason_msg: str,
    session: ToolSession | None = None,
) -> ReconstructionReport:
    """Construct a valid ReconstructionReport with status='error' or status='timeout'."""
    missing = sorted(session.erased_ids) if session is not None else []
    policy_version = meta.get("policy_version", "unknown")
    return ReconstructionReport(
        decision_id=str(decision_id),
        outcome=meta["outcome"],
        score=float(meta["score"]),
        chain_verified=False,
        replay_verified=False,
        replay_tolerance=DEFAULT_REPLAY_TOLERANCE,
        completeness=0.0,
        missing_evidence=missing,
        principal_reasons=[reason_msg],
        rule_checks=[],
        reasons=[],
        policy_note=(
            f"Evaluated under policy {policy_version}; reconstruction terminated with "
            f"status='{status}' ({reason_msg}). Record supports audit and explanation "
            f"and is tamper-evident against a retained reference."
        ),
        human_override=meta.get("human_override"),
        guardrail_rejections=0,
        status=status,
    )


def _correction_notes(decision_id: str, db_url: str) -> list[dict[str, Any]]:
    from backend.quality import load_corrections

    with psycopg.connect(db_url, connect_timeout=10) as conn:
        items, _, _ = load_corrections(conn, str(uuid.UUID(str(decision_id))))
    return correction_notes(items)


def _recording_mode(decision_id: str, db_url: str) -> str:
    with psycopg.connect(db_url, connect_timeout=10) as conn:
        row = conn.execute(
            "SELECT recording_mode FROM decisions WHERE decision_id = %s", (uuid.UUID(str(decision_id)),)
        ).fetchone()
    return str(row[0]) if row else "scored"


@overload
def reconstruct_decision(
    decision_id: str,
    *,
    backend: LLMBackend | None = None,
    backend_name: str | None = None,
    db_url: str | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    return_session: Literal[False] = False,
) -> ReconstructionReport: ...


@overload
def reconstruct_decision(
    decision_id: str,
    *,
    backend: LLMBackend | None = None,
    backend_name: str | None = None,
    db_url: str | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    return_session: Literal[True],
) -> tuple[ReconstructionReport, ToolSession]: ...


def reconstruct_decision(
    decision_id: str,
    *,
    backend: LLMBackend | None = None,
    backend_name: str | None = None,
    db_url: str | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    return_session: bool = False,
) -> ReconstructionReport | tuple[ReconstructionReport, ToolSession]:
    """Reconstruct a cited explanation for `decision_id` from preserved decision-time snapshots.

    - Limits each attempt to `max_tool_calls` (default 8) and total runtime to `timeout_seconds` (default 30s).
    - Validates the output against `ReconstructionReport`; retries once on validation failure, then returns `status="error"`.
    - Sets `status="evidence_unavailable"` whenever `evidence_status` marks needed evidence as erased or missing.
    """
    resolved_db_url = fast_db_url(db_url)
    meta = _fetch_fallback_decision_meta(decision_id, resolved_db_url)

    active_backend = backend or get_llm_backend(backend_name)

    # Decisions the lender made itself have nothing to re-score; explain them straight from the preserved record.
    if _recording_mode(decision_id, resolved_db_url) == "recorded":
        recorded_report = build_recorded_report(decision_id, resolved_db_url, active_backend)
        if return_session:
            return recorded_report, ToolSession(db_url=resolved_db_url)
        return recorded_report

    start_time = clock()
    overall_deadline = start_time + float(timeout_seconds)

    last_session = ToolSession(
        db_url=resolved_db_url,
        max_tool_calls=max_tool_calls,
        timeout_seconds=timeout_seconds,
        clock=clock,
    )
    last_session.deadline = overall_deadline

    last_error: str | None = None

    for attempt in (1, 2):
        remaining_seconds = overall_deadline - clock()
        if remaining_seconds <= 0:
            timeout_report = _build_fallback_report(
                decision_id,
                meta,
                status="timeout",
                reason_msg=f"Reconstruction timed out after {timeout_seconds:.1f}s.",
                session=last_session,
            )
            return (timeout_report, last_session) if return_session else timeout_report

        session = ToolSession(
            db_url=resolved_db_url,
            max_tool_calls=max_tool_calls,
            timeout_seconds=remaining_seconds,
            clock=clock,
        )
        session.deadline = overall_deadline
        last_session = session

        try:
            raw_report = active_backend.generate_report(
                decision_id,
                session,
                attempt=attempt,
                validation_error=last_error,
            )
            session.check_time_budget()

            if not isinstance(raw_report, dict):
                raise ValueError("Backend generate_report must return a dictionary")

            # Enforce evidence_unavailable status if any evidence was marked erased_by_retention/missing
            if session.erased_ids:
                raw_report = dict(raw_report)
                raw_report["status"] = "evidence_unavailable"
                existing_missing = list(raw_report.get("missing_evidence") or [])
                for erased_ref in sorted(session.erased_ids):
                    if erased_ref not in existing_missing:
                        existing_missing.append(erased_ref)
                raw_report["missing_evidence"] = existing_missing

            # Runtime grounding guardrail: drop claims whose citation, field/value, or numbers
            # are not backed by tool results returned in this session.
            raw_report = dict(raw_report)
            kept_reasons, rejected = apply_claim_guardrails(
                list(raw_report.get("reasons") or []), session
            )
            raw_report["reasons"] = kept_reasons
            raw_report["guardrail_rejections"] = int(
                raw_report.get("guardrail_rejections") or 0
            ) + rejected

            # Optional LLM plain-language summary; kept only if it passes the same grounding checks.
            if kept_reasons and raw_report.get("status", "ok") == "ok":
                try:
                    summary = active_backend.generate_summary(raw_report, session)
                except Exception:
                    summary = None
                if summary is not None:
                    if check_narrative(summary, session):
                        raw_report["plain_language_summary"] = summary
                    else:
                        raw_report["guardrail_rejections"] += 1

            raw_report["corrections"] = _correction_notes(decision_id, resolved_db_url)
            validated = ReconstructionReport.model_validate(raw_report)
            return (validated, session) if return_session else validated

        except ReconstructionTimeoutError as exc:
            timeout_report = _build_fallback_report(
                decision_id,
                meta,
                status="timeout",
                reason_msg=str(exc),
                session=session,
            )
            return (timeout_report, session) if return_session else timeout_report

        except ToolLimitExceededError as exc:
            error_report = _build_fallback_report(
                decision_id,
                meta,
                status="error",
                reason_msg=str(exc),
                session=session,
            )
            return (error_report, session) if return_session else error_report

        except (ValidationError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            continue

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            break

    error_report = _build_fallback_report(
        decision_id,
        meta,
        status="error",
        reason_msg=f"ReconstructionReport validation failed after retry: {last_error}",
        session=last_session,
    )
    return (error_report, last_session) if return_session else error_report


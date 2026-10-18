"""Risk signals: patterns across decisions and across who looks at them, for a person to review.

A signal is an indicator, never a verdict. Nothing here changes a decision, blocks a customer or accuses anyone;
wording is deliberately cautious ("may", "worth a look") and signals are only shown to a lender's staff.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from backend.quality import DATA_TYPES, DecisionView, canon, load_views, num

VELOCITY_DAYS = 7
VELOCITY_MIN = 3
FLIP_DAYS = 30
STREAK_MIN = 3
STREAK_DAYS = 90
STACK_DAYS = 30
STACK_MIN = 3
WALK_DAYS = 14
WALK_RATIO = 0.7
SHARED_MIN_SUBJECTS = 3
DRIFT_RECENT_DAYS = 7
DRIFT_BASELINE_DAYS = 90
DRIFT_MIN_N = 30
DRIFT_Z = 3.0
ENUMERATION_WINDOW_MIN = 10
ENUMERATION_MIN = 10
BULK_MIN = 200


def signal_facts(kind: str, details: dict[str, Any]) -> dict[str, Any] | None:
    """The signal's own stored facts plus the fixed rule thresholds; the only numbers an explanation may use."""
    d = details or {}
    try:
        if kind == "applicant_velocity":
            return {"decisions_in_window": int(d["count"]), "window_days": VELOCITY_DAYS, "rule_minimum": VELOCITY_MIN}
        if kind == "outcome_flip":
            return {"window_days": FLIP_DAYS}
        if kind == "decline_streak_then_approval":
            return {"declines_before": int(d["declines"]), "window_days": STREAK_DAYS, "rule_minimum": STREAK_MIN}
        if kind == "loan_stacking":
            return {"approvals_in_window": int(d["approvals"]), "window_days": STACK_DAYS, "rule_minimum": STACK_MIN}
        if kind == "amount_walk_down":
            return {"declined_amount": float(d["declined_amount"]), "approved_amount": float(d["approved_amount"]), "window_days": WALK_DAYS}
        if kind == "shared_details":
            return {"distinct_customer_references": int(d["distinct_references"]), "rule_minimum": SHARED_MIN_SUBJECTS}
        if kind == "decline_rate_shift":
            return {"recent_decline_rate": d["recent_rate"], "baseline_decline_rate": d["baseline_rate"],
                    "recent_decisions": int(d["recent_n"]), "baseline_decisions": int(d["baseline_n"])}
        if kind == "score_shift":
            return {"recent_average_score": d["recent_mean"], "baseline_average_score": d["baseline_mean"]}
        if kind == "id_enumeration":
            return {"missing_lookups": int(d["not_found"]), "window_minutes": ENUMERATION_WINDOW_MIN, "rule_minimum": ENUMERATION_MIN}
        if kind == "bulk_reading":
            return {"decisions_opened": int(d["distinct"]), "window_minutes": ENUMERATION_WINDOW_MIN, "rule_minimum": BULK_MIN}
    except (KeyError, TypeError, ValueError):
        return None
    return None


def why_template(kind: str, details: dict[str, Any]) -> str | None:
    """Deterministic one-sentence 'why this fired', built only from the signal's stored facts. Default when no model is used."""
    f = signal_facts(kind, details)
    if f is None:
        return None
    if kind == "applicant_velocity":
        return (f"This rule fired because the same customer reference has {f['decisions_in_window']} decisions within "
                f"{f['window_days']} days, and it looks for {f['rule_minimum']} or more.")
    if kind == "outcome_flip":
        return f"This rule fired because a decline was followed by an approval for the same customer reference within {f['window_days']} days."
    if kind == "decline_streak_then_approval":
        return (f"This rule fired because {f['declines_before']} declines in a row (within {f['window_days']} days) were followed by an approval "
                f"for the same customer reference, and it looks for {f['rule_minimum']} or more.")
    if kind == "loan_stacking":
        return (f"This rule fired because {f['approvals_in_window']} loans were approved for the same customer reference within "
                f"{f['window_days']} days, and it looks for {f['rule_minimum']} or more.")
    if kind == "amount_walk_down":
        return (f"This rule fired because an approval for {f['approved_amount']:,.0f} followed declines of up to {f['declined_amount']:,.0f} "
                f"for the same customer reference within {f['window_days']} days.")
    if kind == "shared_details":
        return (f"This rule fired because the same income, loan amount and credit score appear for {f['distinct_customer_references']} "
                f"different customer references, and it looks for {f['rule_minimum']} or more.")
    if kind == "decline_rate_shift":
        return (f"This rule fired because the share of declines is {f['recent_decline_rate']:.0%} over {f['recent_decisions']} recent decisions, "
                f"against {f['baseline_decline_rate']:.0%} over {f['baseline_decisions']} earlier ones.")
    if kind == "score_shift":
        return (f"This rule fired because risk scores average {f['recent_average_score']:.2f} in the latest week, "
                f"against {f['baseline_average_score']:.2f} before.")
    if kind == "id_enumeration":
        return (f"This rule fired because {f['missing_lookups']} lookups for decisions that don't exist happened within "
                f"{f['window_minutes']} minutes, and it looks for {f['rule_minimum']} or more.")
    return (f"This rule fired because {f['decisions_opened']} different decisions were opened within "
            f"{f['window_minutes']} minutes, and it looks for {f['rule_minimum']} or more.")


def _fact_numbers(value: Any, out: set[float]) -> set[float]:
    if isinstance(value, dict):
        for v in value.values():
            _fact_numbers(v, out)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        out.add(float(value))
    return out


def grounded_why(text: str | None, facts: dict[str, Any]) -> str | None:
    """Keep a model-written sentence only if it is one short sentence whose every number is in the signal's facts."""
    from recon_agent.guardrails import _extract_numbers_from_text, _number_matches_set  # same grounding rule as the report summary

    if not text:
        return None
    text = " ".join(text.split())
    if not text or len(text) > 300 or text.count(". ") or text[-1] not in ".!":
        return None
    allowed = _fact_numbers(facts, set())
    return text if all(_number_matches_set(n, allowed) for n in _extract_numbers_from_text(text)) else None


def _ai_why(conn: psycopg.Connection[Any], backend: Any, sig: dict[str, Any]) -> str | None:
    """Model-written sentence for one decision-level signal, or None. Never for protected decisions or without consent."""
    from backend.consent import state  # deferred: only needed when a model is configured

    did = sig["decision_id"]
    facts = signal_facts(sig["signal_type"], sig["details"])
    if sig["scope"] != "decision" or not did or facts is None:
        return None
    row = conn.execute("SELECT encrypted FROM decisions WHERE decision_id = %s", (did,)).fetchone()
    if row is None or row[0] or not state(conn, did)["ai_summary_on"]:
        return None
    if conn.execute("SELECT 1 FROM risk_signals WHERE signal_type = %s AND dedupe_key = %s", (sig["signal_type"], sig["dedupe_key"])).fetchone():
        return None  # already on file; don't ask again on every re-run
    try:
        return grounded_why(backend.generate_signal_why(sig["signal_type"], facts), facts)
    except Exception:
        return None  # the rule's own sentence stands on its own


def _sig(decision_id: str | None, scope: str, kind: str, severity: str, message: str, key: str, **details: Any) -> dict[str, Any]:
    return {"decision_id": decision_id, "scope": scope, "signal_type": kind, "severity": severity,
            "message": message, "dedupe_key": key, "details": details}


def subject_of(view: DecisionView) -> str | None:
    lender = view.lender_payload()
    if lender and lender.get("subject_ref"):
        return str(lender["subject_ref"])
    for item in view.items:
        if item.evidence_type == "application" and item.payload.get("applicant_id"):
            return str(item.payload["applicant_id"])
    return None


def amount_of(view: DecisionView) -> float | None:
    for item in view.items:
        if item.evidence_type not in DATA_TYPES:
            continue
        for key, raw in item.payload.items():
            if canon(key) == "requested_amount" and num(raw) is not None:
                return float(num(raw))
    return None


def fingerprint(view: DecisionView) -> tuple[float, float, float] | None:
    got: dict[str, float] = {}
    for item in view.items:
        if item.evidence_type not in DATA_TYPES:
            continue
        for key, raw in item.payload.items():
            c, n = canon(key), num(raw)
            if c in ("income_annual", "requested_amount", "credit_score") and n is not None and c not in got:
                got[c] = round(n, 2)
    if len(got) < 3:
        return None
    return got["income_annual"], got["requested_amount"], got["credit_score"]


def decision_signals(views: list[DecisionView]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    by_subject: dict[str, list[DecisionView]] = defaultdict(list)
    by_print: dict[tuple[float, float, float], list[DecisionView]] = defaultdict(list)
    for v in views:
        s = subject_of(v)
        if s:
            by_subject[s].append(v)
        fp = fingerprint(v)
        if fp and s:
            by_print[fp].append(v)

    for subject, group in by_subject.items():
        group.sort(key=lambda v: v.decided_at)
        for i, v in enumerate(group):
            window = [g for g in group[: i + 1] if g.decided_at >= v.decided_at - timedelta(days=VELOCITY_DAYS)]
            if len(window) >= VELOCITY_MIN:
                out.append(_sig(v.decision_id, "decision", "applicant_velocity", "high" if len(window) >= 5 else "warning",
                                f"This customer reference has {len(window)} decisions within {VELOCITY_DAYS} days, which is more than usual.",
                                v.decision_id, count=len(window)))
            if v.outcome == "APPROVE" and any(
                g.outcome == "DECLINE" and v.decided_at - timedelta(days=FLIP_DAYS) <= g.decided_at < v.decided_at for g in group[:i]
            ):
                out.append(_sig(v.decision_id, "decision", "outcome_flip", "warning",
                                f"An approval soon after a decline for the same customer reference (within {FLIP_DAYS} days). Worth a look.",
                                v.decision_id))

    for subject, group in by_subject.items():
        group.sort(key=lambda v: v.decided_at)
        for i, v in enumerate(group):
            if v.outcome != "APPROVE":
                continue
            streak = 0  # declines immediately before this approval, within the window
            for g in reversed(group[:i]):
                if g.outcome != "DECLINE" or g.decided_at < v.decided_at - timedelta(days=STREAK_DAYS):
                    break
                streak += 1
            if streak >= STREAK_MIN:
                out.append(_sig(v.decision_id, "decision", "decline_streak_then_approval", "high" if streak >= 5 else "warning",
                                f"This approval came after {streak} declines in a row for the same customer reference. A person may want to confirm "
                                "what changed between the last decline and this approval.", v.decision_id, declines=streak))
            stack = [g for g in group[: i + 1] if g.outcome == "APPROVE" and g.decided_at >= v.decided_at - timedelta(days=STACK_DAYS)]
            if len(stack) >= STACK_MIN:
                out.append(_sig(v.decision_id, "decision", "loan_stacking", "high" if len(stack) >= 4 else "warning",
                                f"{len(stack)} loans were approved for this customer reference within {STACK_DAYS} days, which may mean borrowing "
                                "is being stacked before the first loan shows up elsewhere.", v.decision_id, approvals=len(stack)))
            near = [g for g in group[:i] if g.outcome == "DECLINE" and g.decided_at >= v.decided_at - timedelta(days=WALK_DAYS)]
            amts = [a for a in (amount_of(g) for g in near) if a]
            mine = amount_of(v)
            if len(near) >= 2 and mine and amts and mine <= max(amts) * WALK_RATIO:
                out.append(_sig(v.decision_id, "decision", "amount_walk_down", "warning",
                                "The requested amount was lowered after repeated declines until one was approved. That can be a customer finding "
                                "their limit, or someone testing where the lender's threshold is.", v.decision_id,
                                declined_amount=max(amts), approved_amount=mine))

    for fp, group in by_print.items():
        refs = {subject_of(v) for v in group}
        if len(refs) >= SHARED_MIN_SUBJECTS:
            for v in group:
                out.append(_sig(v.decision_id, "decision", "shared_details", "warning",
                                f"The same income, loan amount and credit score appear for {len(refs)} different customer references. "
                                "That can happen by chance, but it may be worth a look.", v.decision_id, distinct_references=len(refs)))
    return out


def _z_two_proportions(x1: int, n1: int, x0: int, n0: int) -> float:
    p = (x1 + x0) / (n1 + n0)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n0))
    return 0.0 if se == 0 else (x1 / n1 - x0 / n0) / se


def drift_signals(views: list[DecisionView], now: datetime | None = None) -> list[dict[str, Any]]:
    """Has the mix of outcomes or scores moved sharply in the latest week compared with the previous three months?"""
    if not views:
        return []
    # Dates come from the lender, so ignore anything dated after tomorrow when deciding what "this week" is.
    ceiling = (now or datetime.now(timezone.utc)) + timedelta(days=1)
    dated = [v.decided_at for v in views if v.decided_at <= ceiling]
    if not dated:
        return []
    anchor = max(dated)
    views = [v for v in views if v.decided_at <= ceiling]
    recent_from = anchor - timedelta(days=DRIFT_RECENT_DAYS)
    base_from = recent_from - timedelta(days=DRIFT_BASELINE_DAYS)
    recent = [v for v in views if v.decided_at > recent_from]
    base = [v for v in views if base_from < v.decided_at <= recent_from]
    if len(recent) < DRIFT_MIN_N or len(base) < DRIFT_MIN_N:
        return []
    out: list[dict[str, Any]] = []
    key = anchor.date().isoformat()
    d1, d0 = sum(v.outcome == "DECLINE" for v in recent), sum(v.outcome == "DECLINE" for v in base)
    z = _z_two_proportions(d1, len(recent), d0, len(base))
    diff = d1 / len(recent) - d0 / len(base)
    if abs(z) > DRIFT_Z and abs(diff) > 0.15:
        out.append(_sig(None, "batch", "decline_rate_shift", "warning",
                        f"In the week to {anchor:%d %b %Y}, the share of declines ({d1 / len(recent):.0%}) is quite different from the previous months "
                        f"({d0 / len(base):.0%}). This can be real or a data problem, so it is worth a look.",
                        key, recent_rate=round(d1 / len(recent), 4), baseline_rate=round(d0 / len(base), 4), recent_n=len(recent), baseline_n=len(base)))
    s1 = [v.score for v in recent if v.score is not None]
    s0 = [v.score for v in base if v.score is not None]
    if len(s1) >= DRIFT_MIN_N and len(s0) >= DRIFT_MIN_N:
        sd = math.sqrt(statistics.pvariance(s1) / len(s1) + statistics.pvariance(s0) / len(s0))
        shift = statistics.fmean(s1) - statistics.fmean(s0)
        if abs(shift) > 0.1 and (sd == 0 or abs(shift / sd) > DRIFT_Z):
            out.append(_sig(None, "batch", "score_shift", "warning",
                            f"In the week to {anchor:%d %b %Y}, risk scores average {statistics.fmean(s1):.2f}, compared with {statistics.fmean(s0):.2f} before. Worth a look.",
                            key, recent_mean=round(statistics.fmean(s1), 4), baseline_mean=round(statistics.fmean(s0), 4)))
    return out


def access_signals(conn: psycopg.Connection[Any], tenant_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    """Who is asking for lots of decisions that aren't there, or lots of decisions in a short time?

    The ten-minute window is measured on the database's clock (which stamped the log) unless a time is given."""
    rows = conn.execute(
        "SELECT credential_id, claim_id, role, "
        "count(*) FILTER (WHERE outcome = 'not_found'), "
        "count(DISTINCT object_id) FILTER (WHERE outcome = 'allowed' AND object_type = 'decision' AND action IN ('read', 'verify', 'reconstruct')) "
        "FROM access_log WHERE tenant_id = %s AND logged_at >= COALESCE(%s::timestamptz, now()) - make_interval(mins => %s) "
        "GROUP BY credential_id, claim_id, role",
        (tenant_id, now, ENUMERATION_WINDOW_MIN),
    ).fetchall()
    out: list[dict[str, Any]] = []
    bucket = int((now or datetime.now(timezone.utc)).timestamp() // (ENUMERATION_WINDOW_MIN * 60))
    for cred, claim, role, missed, distinct in rows:
        who = f"customer link {claim}" if claim is not None else f"credential {cred}" if cred is not None else "the shared demo key"
        key = f"{who}:{bucket}"
        if missed >= ENUMERATION_MIN:
            out.append(_sig(None, "access", "id_enumeration", "high",
                            f"{who.capitalize()} asked for {missed} decisions that don't exist in {ENUMERATION_WINDOW_MIN} minutes. "
                            "That can be a script guessing reference numbers.", key, actor=who, role=role, not_found=missed))
        if distinct >= BULK_MIN:
            out.append(_sig(None, "access", "bulk_reading", "warning",
                            f"{who.capitalize()} opened {distinct} different decisions in {ENUMERATION_WINDOW_MIN} minutes. "
                            "That is far more than a person usually reads.", key, actor=who, role=role, distinct=distinct))
    return out


def flag_enumeration(conn: psycopg.Connection[Any], tenant_id: str, credential_id: int | None, claim_id: int | None,
                     role: str, now: datetime | None = None) -> bool:
    """Called each time someone asks for a decision that isn't there, so guessing is noticed as it happens
    rather than only when staff next run the checks. Returns True if a new signal was saved."""
    missed = conn.execute(
        "SELECT count(*) FROM access_log WHERE tenant_id = %s AND outcome = 'not_found' "
        "AND logged_at >= COALESCE(%s::timestamptz, now()) - make_interval(mins => %s) "
        "AND credential_id IS NOT DISTINCT FROM %s AND claim_id IS NOT DISTINCT FROM %s",
        (tenant_id, now, ENUMERATION_WINDOW_MIN, credential_id, claim_id),
    ).fetchone()[0]
    now = now or datetime.now(timezone.utc)
    if missed < ENUMERATION_MIN:
        return False
    who = f"customer link {claim_id}" if claim_id is not None else f"credential {credential_id}" if credential_id is not None else "the shared demo key"
    sig = _sig(None, "access", "id_enumeration", "high",
               f"{who.capitalize()} asked for {missed} decisions that don't exist in {ENUMERATION_WINDOW_MIN} minutes. "
               "That can be a script guessing reference numbers.", f"{who}:{int(now.timestamp() // (ENUMERATION_WINDOW_MIN * 60))}",
               actor=who, role=role, not_found=missed)
    return bool(conn.execute(
        "INSERT INTO risk_signals (tenant_id, decision_id, scope, signal_type, severity, message, details, dedupe_key) "
        "VALUES (%s, NULL, 'access', %s, %s, %s, %s::jsonb, %s) ON CONFLICT (tenant_id, signal_type, dedupe_key) DO NOTHING",
        (tenant_id, sig["signal_type"], sig["severity"], sig["message"], json.dumps(sig["details"]), sig["dedupe_key"]),
    ).rowcount)


def _llm_backend() -> Any:
    """The configured model backend, or None when it is the offline one (the deterministic sentence is used)."""
    from recon_agent.backends import get_llm_backend

    try:
        backend = get_llm_backend()
    except Exception:
        return None
    return None if getattr(backend, "backend_name", "fake") == "fake" else backend


def scan_signals(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    *,
    decision_ids: list[str] | None = None,
    recent: int = 5000,
    now: datetime | None = None,
    include_access: bool = True,
) -> dict[str, Any]:
    """Look for signals and save the ones not already on file. Re-running adds nothing twice."""
    given_now = now
    now = now or datetime.now(timezone.utc)
    views = load_views(conn, tenant_id, None, recent, also=decision_ids)  # always includes what was just saved
    found = decision_signals(views)
    if decision_ids is not None:
        keep = set(decision_ids)
        found = [f for f in found if f["decision_id"] in keep]
    found += drift_signals(views, now)
    if include_access and decision_ids is None:
        found += access_signals(conn, tenant_id, given_now)
    backend = _llm_backend()
    for f in found:
        why = _ai_why(conn, backend, f) if backend is not None else None
        if why:
            f["details"] = {**f["details"], "why_ai": why}
    saved = 0
    for f in found:
        saved += conn.execute(
            "INSERT INTO risk_signals (tenant_id, decision_id, scope, signal_type, severity, message, details, dedupe_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s) ON CONFLICT (tenant_id, signal_type, dedupe_key) DO NOTHING",
            (tenant_id, f["decision_id"], f["scope"], f["signal_type"], f["severity"], f["message"], json.dumps(f["details"]),
             f["dedupe_key"]),
        ).rowcount
    return {"decisions_checked": len(views), "new_signals": saved,
            "by_severity": {s: sum(1 for f in found if f["severity"] == s) for s in ("high", "warning", "info")}}


SIGNAL_COLS = "signal_id, decision_id, scope, signal_type, severity, message, details, created_at"


def signal_row(r: tuple[Any, ...], status: str, conn: psycopg.Connection[Any] | None = None) -> dict[str, Any]:
    """`why` is the rule's own sentence; a model-written one is shown only if a connection is given to confirm consent still holds."""
    from backend.consent import state

    details = dict(r[6] or {})
    ai = details.pop("why_ai", None)
    why = {"text": why_template(r[3], details), "source": "rule"}
    if ai and conn is not None and r[1] and state(conn, str(r[1]))["ai_summary_on"]:
        why = {"text": ai, "source": "ai"}
    return {"signal_id": int(r[0]), "decision_id": str(r[1]) if r[1] else None, "scope": r[2], "signal_type": r[3],
            "severity": r[4], "message": r[5], "details": details, "created_at": r[7].isoformat(), "status": status,
            "why": why if why["text"] else None}


def signals_for_decision(conn: psycopg.Connection[Any], tenant_id: str, decision_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {SIGNAL_COLS}, COALESCE((SELECT action FROM signal_reviews r WHERE r.signal_id = s.signal_id "
        "ORDER BY review_id DESC LIMIT 1), 'open') FROM risk_signals s WHERE s.tenant_id = %s AND s.decision_id = %s "
        "ORDER BY signal_id", (tenant_id, decision_id),
    ).fetchall()
    return [signal_row(r[:8], str(r[8]), conn) for r in rows]

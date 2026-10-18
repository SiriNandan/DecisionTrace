"""Customer-wise view: every decision grouped by customer reference, with the patterns across a customer's applications."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.auth import Principal, log_access, require_scope, tenant_connection
from backend.quality import DecisionView, load_views
from backend.signals import amount_of, decision_signals, subject_of

router = APIRouter()

SEVERITY_RANK = {"info": 0, "warning": 1, "high": 2}


def _group(views: list[DecisionView]) -> dict[str, list[DecisionView]]:
    groups: dict[str, list[DecisionView]] = defaultdict(list)
    for v in views:
        s = subject_of(v)
        if s:
            groups[s].append(v)
    for g in groups.values():
        g.sort(key=lambda v: v.decided_at)
    return groups


def _flags(views: list[DecisionView]) -> dict[str, list[dict[str, Any]]]:
    by_decision = {v.decision_id: subject_of(v) for v in views}
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in decision_signals(views):
        subject = by_decision.get(s["decision_id"])
        if subject:
            out[subject].append(s)
    return out


def _count(group: list[DecisionView], outcome: str) -> int:
    return sum(1 for v in group if v.outcome == outcome)


@router.get("/v1/customers")
def list_customers(
    limit: int = Query(default=200, ge=1, le=1000),
    principal: Principal = Depends(require_scope("signals:read")),
) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        views = load_views(conn, principal.tenant_id, None, 5000)
        log_access(conn, principal, "customers_list", "customer", f"decisions={len(views)}")
    flags = _flags(views)
    rows = []
    for subject, group in _group(views).items():
        kinds: dict[str, dict[str, Any]] = {}
        for f in flags.get(subject, []):
            k = kinds.setdefault(f["signal_type"], {"signal_type": f["signal_type"], "severity": f["severity"], "count": 0})
            k["count"] += 1
            if SEVERITY_RANK[f["severity"]] > SEVERITY_RANK[k["severity"]]:
                k["severity"] = f["severity"]
        level = max((k["severity"] for k in kinds.values()), key=SEVERITY_RANK.__getitem__, default=None)
        rows.append({
            "subject_ref": subject, "applications": len(group),
            "approved": _count(group, "APPROVE"), "declined": _count(group, "DECLINE"), "referred": _count(group, "REFER"),
            "first_at": group[0].decided_at.isoformat(), "last_at": group[-1].decided_at.isoformat(), "last_outcome": group[-1].outcome,
            "history": [v.outcome for v in group][-20:],
            "flags": sorted(kinds.values(), key=lambda k: -SEVERITY_RANK[k["severity"]]), "flag_level": level,
        })
    rows.sort(key=lambda r: (-SEVERITY_RANK.get(r["flag_level"] or "", -1), -r["applications"], r["subject_ref"]))
    return rows[:limit]


@router.get("/v1/customers/{subject_ref}")
def get_customer(subject_ref: str, principal: Principal = Depends(require_scope("signals:read"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        views = load_views(conn, principal.tenant_id, None, 5000)
        log_access(conn, principal, "customer_read", "customer", subject_ref[:80])
    group = _group(views).get(subject_ref)
    if not group:
        raise HTTPException(status_code=404, detail="No decisions are on file for that customer reference.")
    flagged: dict[str, list[str]] = {}
    signals = _flags(views).get(subject_ref, [])
    for s in signals:
        flagged.setdefault(s["decision_id"], []).append(s["signal_type"])
    timeline = []
    for v in group:
        lender = v.lender_payload() or {}
        timeline.append({
            "decision_id": v.decision_id, "decided_at": v.decided_at.isoformat(), "outcome": v.outcome, "score": v.score,
            "requested_amount": amount_of(v), "flags": flagged.get(v.decision_id, []),
            "reasons": [r.get("code") if isinstance(r, dict) else str(r) for r in (lender.get("reasons") or [])][:6],
        })
    return {
        "subject_ref": subject_ref, "applications": len(group),
        "approved": _count(group, "APPROVE"), "declined": _count(group, "DECLINE"), "referred": _count(group, "REFER"),
        "timeline": timeline,
        "signals": [{"decision_id": s["decision_id"], "signal_type": s["signal_type"], "severity": s["severity"], "message": s["message"]}
                    for s in sorted(signals, key=lambda s: -SEVERITY_RANK[s["severity"]])],
    }

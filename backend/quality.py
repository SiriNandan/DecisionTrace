"""Data-quality checks on saved decisions, and corrections that never touch the original record.

Checks are plain rules (missing, implausible, conflicting, inconsistent, outlying values). An AI model may add a
short plain-language guess and propose a corrected value, but a proposed value is dropped unless it can be traced
to numbers already in the same record. A person decides; accepting writes a new hash-linked correction row, and the
original decision and its chain stay exactly as they were.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import psycopg

from backend.crypto import Reveal, decrypt_payload, encrypt_payload, is_encrypted, payload_aad, seal_payload
from backend.integrity import GENESIS, entry_hash, payload_sha256, verify_chain
from backend.mapping import RowError, to_number
from backend.mapping_ai import LLMCall, norm
from backend.redaction import redact_text

PROTECTED_MESSAGE = "The details of this item are protected."
REMOVED_MESSAGE = "The details of this item were removed."
MAX_AI_FINDINGS = 50
OUTLIER_MIN_VALUES = 30
OUTLIER_Z = 6.0
# Evidence that records what the decision was made *with*. Policy limits, model output and rule results are
# thresholds and derived numbers, not observations about the applicant, so they are never compared with them.
DATA_TYPES = {"application", "bureau", "account_history", "document", "consent", "other"}
MISSING_DOC_WORDS = {"missing", "not received", "not_received", "no", "failed", "rejected", "outstanding", "pending"}


@dataclass
class Item:
    ref: str
    evidence_type: str
    payload: dict[str, Any]
    source_ts: datetime | None
    entry_hash: str


@dataclass
class DecisionView:
    decision_id: str
    outcome: str
    score: float | None
    decided_at: datetime
    items: list[Item] = field(default_factory=list)

    def lender_payload(self) -> dict[str, Any] | None:
        return next((i.payload for i in self.items if i.evidence_type == "lender_decision"), None)


# ---------- field names ----------

def canon(key: str) -> str | None:
    """Group the many spellings of the same thing (income, loan amount, credit score…) under one name."""
    n = norm(key)
    if re.search(r"ratio|dti|debttoincome", n):
        return "dti_ratio"
    if re.search(r"debt|existing", n):
        return None
    if re.search(r"monthly.*income|income.*monthly", n):
        return "monthly_income"
    if re.search(r"income|salary", n):
        return "income_annual"
    if re.search(r"requested|loanamount|^amount$", n):
        return "requested_amount"
    if re.search(r"creditscore|fico|bureauscore", n):
        return "credit_score"
    if re.search(r"delinq|missedpay|arrear", n):
        return "delinquencies"
    return None


def num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = to_number(value)
    except RowError:
        return None
    return out if math.isfinite(out) else None


def fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.2f}"


def _finding(rule_id: str, kind: str, severity: str, item: Item | None, key: str, observed: Any, message: str,
             fix: tuple[Any, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rule_id": rule_id, "kind": kind, "severity": severity,
        "evidence_ref": item.ref if item else "", "field": key,
        "observed": {"value": observed}, "message": message, "suggested_fix": None,
    }
    if fix is not None and item is not None:
        out["suggested_fix"] = {"evidence_ref": item.ref, "field": key, "new_value": fix[0], "basis": fix[1]}
    return out


# ---------- the rules ----------

def check_decision(view: DecisionView, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    out: list[dict[str, Any]] = []
    seen: dict[str, list[tuple[Item, str, float]]] = {}

    for item in view.items:
        if item.evidence_type not in DATA_TYPES:
            continue
        for key, raw in item.payload.items():
            c = canon(key)
            n = num(raw)
            if c is None or n is None:
                continue
            seen.setdefault(c, []).append((item, key, n))

    for item, key, v in seen.get("income_annual", []):
        if v <= 0:
            out.append(_finding("income_not_positive", "implausible", "error", item, key, v,
                                f"The income recorded ({fmt(v)}) isn't a positive amount."))
        elif v > 1_000_000:
            out.append(_finding("income_very_high", "implausible", "warning", item, key, v,
                                f"The income recorded ({fmt(v)}) is unusually high. Please check the amount and its units."))
    for item, key, v in seen.get("requested_amount", []):
        if v <= 0:
            out.append(_finding("amount_not_positive", "implausible", "error", item, key, v,
                                f"The loan amount recorded ({fmt(v)}) isn't a positive amount."))
    incomes = [(i, k, v) for i, k, v in seen.get("income_annual", []) if v > 0]
    for item, key, v in seen.get("requested_amount", []):
        if incomes and v > 10 * incomes[0][2]:
            out.append(_finding("amount_vs_income", "implausible", "warning", item, key, v,
                                f"The loan amount ({fmt(v)}) is more than 10 times the yearly income ({fmt(incomes[0][2])}). "
                                "Please check that both use the same units."))
    for item, key, v in seen.get("credit_score", []):
        if v < 0 or v > 1000:
            out.append(_finding("credit_score_range", "implausible", "error", item, key, v,
                                f"The credit score recorded ({fmt(v)}) is outside the range any credit agency uses."))
    for item, key, v in seen.get("dti_ratio", []):
        if 1 < v <= 100:
            out.append(_finding("dti_looks_like_percentage", "implausible", "warning", item, key, v,
                                f"The debt-to-income figure ({v:g}) looks like a percentage rather than a fraction.",
                                (round(v / 100, 6), "A fraction is expected, so a percentage is divided by 100.")))
        elif v < 0 or v > 100:
            out.append(_finding("dti_range", "implausible", "error", item, key, v,
                                f"The debt-to-income figure ({v:g}) is outside any believable range."))
    for item, key, v in seen.get("delinquencies", []):
        if v < 0:
            out.append(_finding("negative_count", "implausible", "error", item, key, v,
                                f"The count recorded for {key.replace('_', ' ')} ({v:g}) can't be negative."))

    # the same thing said two ways by two sources
    annual, monthly = seen.get("income_annual", []), seen.get("monthly_income", [])
    handled = False
    for a_item, a_key, a in annual:
        for m_item, m_key, m in monthly:
            if a <= 0 or m <= 0:
                continue
            if abs(a - m) / m < 0.05 and m * 12 > a * 1.5:
                handled = True
                out.append(_finding(
                    "income_unit_mismatch", "conflict", "warning", a_item, a_key, a,
                    f"The yearly income ({fmt(a)}) matches the monthly figure from {m_item.ref} ({fmt(m)}), "
                    "so it may be a monthly amount entered as a yearly one.",
                    (round(m * 12, 2), f"Monthly income of {fmt(m)} from {m_item.ref} times 12."),
                ))
            elif abs(a - m * 12) / a > 0.15:
                handled = True
                out.append(_finding(
                    "income_sources_differ", "conflict", "warning", a_item, a_key, a,
                    f"Income differs between sources: {fmt(a)} a year here, but {fmt(m)} a month ({fmt(m * 12)} a year) in {m_item.ref}."))
    for c, entries in seen.items():
        if c in ("monthly_income",) or (handled and c == "income_annual"):
            continue
        for i in range(len(entries)):
            for j in range(i + 1, len(entries)):
                (ia, ka, va), (ib, kb, vb) = entries[i], entries[j]
                if ia.ref != ib.ref and va != 0 and abs(va - vb) / abs(va) > 0.05:
                    out.append(_finding(
                        "sources_differ", "conflict", "warning", ia, ka, va,
                        f"{ka.replace('_', ' ').capitalize()} is {fmt(va)} in {ia.ref} but {fmt(vb)} in {ib.ref}."))

    # things that should be there
    apps = [i for i in view.items if i.evidence_type == "application"]
    for app in apps:
        have = {canon(k) for k in app.payload}
        for want, label in (("income_annual", "income"), ("requested_amount", "loan amount")):
            if want not in have:
                out.append(_finding("missing_expected", "missing", "warning", app, want, None,
                                    f"The application evidence doesn't include the {label}."))
    for bureau in (i for i in view.items if i.evidence_type == "bureau"):
        if "credit_score" not in {canon(k) for k in bureau.payload}:
            out.append(_finding("missing_expected", "missing", "warning", bureau, "credit_score", None,
                                "The credit report evidence doesn't include a credit score."))

    # things that don't add up
    docs_missing = [
        (i, k) for i in view.items if i.evidence_type == "document"
        for k, v in i.payload.items() if isinstance(v, str) and v.strip().casefold() in MISSING_DOC_WORDS
    ]
    if view.outcome == "APPROVE" and docs_missing:
        item, key = docs_missing[0]
        out.append(_finding("approved_document_missing", "inconsistent", "warning", item, key, item.payload[key],
                            f"The decision is an approval, but the document check in {item.ref} says “{item.payload[key]}”."))
    lender = view.lender_payload()
    if lender is not None and view.outcome == "DECLINE" and not lender.get("reasons"):
        lender_item = next(i for i in view.items if i.evidence_type == "lender_decision")
        out.append(_finding("decline_without_reason", "inconsistent", "info", lender_item, "reasons", [],
                            "This is a decline, but the lender gave no reason with it."))
    for item in view.items:
        if item.source_ts and item.source_ts > view.decided_at + timedelta(minutes=5) and item.evidence_type != "lender_decision":
            out.append(_finding("evidence_after_decision", "inconsistent", "warning", item, "source_timestamp",
                                item.source_ts.isoformat(),
                                f"{item.ref} is dated after the decision was made, so the decision can't have used it."))
    if view.decided_at > now + timedelta(days=1):
        first = view.items[0] if view.items else None
        out.append(_finding("decision_in_future", "implausible", "error", first, "decided_at", view.decided_at.isoformat(),
                            "The decision date is in the future."))
    if view.score is not None and ((view.outcome == "APPROVE" and view.score >= 0.8) or (view.outcome == "DECLINE" and view.score <= 0.02)):
        out.append(_finding("score_outcome_unusual", "inconsistent", "info", None, "score", view.score,
                            f"The outcome ({view.outcome.lower()}) is unusual for a risk score of {view.score:g}. Worth a look."))
    return out


def find_outliers(views: list[DecisionView]) -> list[dict[str, Any]]:
    """Values far from the rest of this lender's decisions (median and spread, so one huge value can't hide itself)."""
    groups: dict[str, list[tuple[DecisionView, Item, str, float]]] = {}
    for v in views:
        for item in (i for i in v.items if i.evidence_type in DATA_TYPES):
            for key, raw in item.payload.items():
                c, n = canon(key), num(raw)
                if c and n is not None and c in ("income_annual", "requested_amount", "credit_score", "monthly_income"):
                    groups.setdefault(c, []).append((v, item, key, n))
    out: list[dict[str, Any]] = []
    for c, rows in groups.items():
        if len(rows) < OUTLIER_MIN_VALUES:
            continue
        vals = [r[3] for r in rows]
        med = statistics.median(vals)
        mad = statistics.median(abs(x - med) for x in vals)
        if mad == 0:
            continue
        for v, item, key, n in rows:
            z = abs(n - med) / (1.4826 * mad)
            if z > OUTLIER_Z:
                f = _finding("numeric_outlier", "outlier", "info", item, key, n,
                             f"{key.replace('_', ' ').capitalize()} ({fmt(n)}) is far from what's usual for this lender (around {fmt(med)}).")
                f["decision_id"] = v.decision_id
                out.append(f)
    return out


# ---------- optional AI help ----------

def _record_numbers(view: DecisionView) -> list[float]:
    nums: list[float] = []
    for item in view.items:
        for raw in item.payload.values():
            n = num(raw)
            if n is not None:
                nums.append(n)
    return nums


def grounded(value: Any, numbers: list[float]) -> bool:
    """True if a proposed number can be traced to the record: present in it, or a unit change (×12, ÷12, ×100, ÷100) of it."""
    v = num(value)
    if v is None:
        return False
    for n in numbers:
        for cand in (n, n * 12, n / 12, n * 100, n / 100):
            if math.isclose(v, cand, rel_tol=1e-6, abs_tol=1e-9):
                return True
    return False


TRIAGE_PROMPT = """You help a lender's analyst check data problems in saved loan decisions. Reply with JSON only: a list with one
object per finding: {{"index": int, "likely_cause": "one plain sentence, max 200 characters",
"suggested_value": number or null}}.
Only suggest a value if it follows from numbers already shown for that decision; otherwise use null. Never invent data.

Findings:
{findings}
"""


def ai_triage(findings: list[dict[str, Any]], views: dict[str, DecisionView], llm: LLMCall) -> list[str]:
    """Add a short likely cause and, when it can be traced to the record, a suggested value. Returns notes about drops."""
    notes: list[str] = []
    batch = findings[:MAX_AI_FINDINGS]
    shown = [
        {"index": i, "rule": f["rule_id"], "field": f["field"], "observed": f["observed"]["value"], "message": f["message"],
         "other_numbers": sorted({round(n, 4) for n in _record_numbers(views[f["decision_id"]])})[:12]}
        for i, f in enumerate(batch)
    ]
    try:
        raw = llm(TRIAGE_PROMPT.format(findings=json.dumps(shown)))
        answer = json.loads(raw[raw.index("["): raw.rindex("]") + 1])
    except Exception as exc:  # the checks stand on their own without the model
        return [f"The AI model couldn't be used ({type(exc).__name__}); the checks were not affected."]
    for entry in answer if isinstance(answer, list) else []:
        try:
            f = batch[int(entry["index"])]
        except (KeyError, ValueError, IndexError, TypeError):
            continue
        cause = entry.get("likely_cause")
        if isinstance(cause, str) and cause.strip():
            f["ai_note"] = redact_text(cause.strip())[:200]
        sv = entry.get("suggested_value")
        if sv is not None and f.get("suggested_fix") is None and f["evidence_ref"] and f["field"] and f["kind"] != "missing":
            if grounded(sv, _record_numbers(views[f["decision_id"]])):
                f["suggested_fix"] = {"evidence_ref": f["evidence_ref"], "field": f["field"], "new_value": num(sv),
                                      "basis": "Suggested by AI, and traced to numbers already in this record."}
                f["found_by"] = f.get("found_by", "rules") + "+ai"
            else:
                notes.append(f"An AI-suggested value for finding {entry['index']} wasn't backed by the record, so it was dropped.")
    return notes


# ---------- reading and writing ----------

def _utc(v: datetime) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


def load_views(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    decision_ids: list[str] | None,
    recent: int,
    also: list[str] | None = None,
) -> list[DecisionView]:
    """Decisions with their evidence: just `decision_ids`, or the newest `recent` (plus `also`, always)."""
    if decision_ids is None and also:
        have = load_views(conn, tenant_id, None, recent)
        missing = [d for d in also if d not in {v.decision_id for v in have}]
        return have + (load_views(conn, tenant_id, missing, recent) if missing else [])
    if decision_ids:
        rows = conn.execute(
            "SELECT decision_id::text, outcome, score, decided_at FROM decisions WHERE tenant_id = %s AND decision_id = ANY(%s::uuid[])",
            (tenant_id, decision_ids),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT decision_id::text, outcome, score, decided_at FROM decisions WHERE tenant_id = %s "
            "ORDER BY decided_at DESC LIMIT %s", (tenant_id, recent),
        ).fetchall()
    views = {str(r[0]): DecisionView(str(r[0]), str(r[1]), float(r[2]) if r[2] is not None else None, _utc(r[3])) for r in rows}
    if not views:
        return []
    snaps = conn.execute(
        "SELECT decision_id::text, evidence_ref, evidence_type, payload, source_timestamp, entry_hash "
        "FROM decision_snapshots WHERE decision_id = ANY(%s::uuid[]) ORDER BY decision_id, seq", (list(views),),
    ).fetchall()
    reveal = Reveal(conn)
    protected = {str(r[0]) for r in conn.execute(
        "SELECT decision_id::text FROM decisions WHERE decision_id = ANY(%s::uuid[]) AND encrypted", (list(views),)).fetchall()}
    for d, ref, etype, payload, ts, eh in snaps:
        opened = reveal.payload(str(d), str(ref), payload) if str(d) in protected and is_encrypted(payload) else payload
        if opened is None:  # its key was destroyed: nothing to check, and no reason to flag it as missing
            continue
        views[str(d)].items.append(Item(str(ref), str(etype), dict(opened or {}), _utc(ts) if ts else None, str(eh)))
    return list(views.values())


def scan(
    conn: psycopg.Connection[Any],
    tenant_id: str,
    *,
    decision_ids: list[str] | None = None,
    recent: int = 200,
    llm: LLMCall | None = None,
) -> dict[str, Any]:
    """Check decisions and save only findings that aren't already on file. Re-running adds nothing twice."""
    views = load_views(conn, tenant_id, decision_ids, recent)
    by_id = {v.decision_id: v for v in views}
    candidates: list[dict[str, Any]] = []
    for v in views:
        for f in check_decision(v):
            f["decision_id"] = v.decision_id
            f["found_by"] = "rules"
            candidates.append(f)
    population = views if decision_ids is None else load_views(conn, tenant_id, None, 2000)
    for f in find_outliers(population):
        if f["decision_id"] in by_id:
            f["found_by"] = "rules"
            candidates.append(f)

    existing = set()
    if by_id:
        existing = {
            (str(r[0]), r[1], r[2], r[3]) for r in conn.execute(
                "SELECT decision_id::text, rule_id, evidence_ref, field FROM quality_findings WHERE decision_id = ANY(%s::uuid[])",
                (list(by_id),)).fetchall()
        }
    fresh = [f for f in candidates if (f["decision_id"], f["rule_id"], f["evidence_ref"], f["field"]) not in existing]
    protected = {str(r[0]) for r in conn.execute(
        "SELECT decision_id::text FROM decisions WHERE decision_id = ANY(%s::uuid[]) AND encrypted", (list(by_id),)).fetchall()} if by_id else set()
    policy = str((conn.execute("SELECT ai_summary_policy FROM tenants WHERE tenant_id = %s", (tenant_id,)).fetchone() or ["ask"])[0])
    notes: list[str] = []
    # A model only ever sees records the lender allows it to see: never when the lender's policy is 'never', and never
    # for protected decisions, whose content is meant to stay inside the encrypted store.
    for_model = [f for f in fresh if f["decision_id"] not in protected]
    if llm is not None and for_model and policy != "never":
        notes = ai_triage(for_model, by_id, llm)

    reveal = Reveal(conn)
    saved = 0
    for f in fresh:
        message, observed, fix, ai_note, enc = f["message"], f["observed"], f["suggested_fix"], f.get("ai_note"), None
        if f["decision_id"] in protected:  # values and wording are personal data: keep them under the decision's key
            key = reveal.dek(f["decision_id"])
            if key is None:
                continue  # nothing to protect them with (content already removed): don't write them in the clear
            enc = encrypt_payload(key[1], f"{f['decision_id']}|finding:{f['rule_id']}:{f['evidence_ref']}:{f['field']}",
                                  {"message": message, "observed": observed, "suggested_fix": fix, "ai_note": ai_note})
            message, observed, fix, ai_note = PROTECTED_MESSAGE, {}, None, None
        cur = conn.execute(
            "INSERT INTO quality_findings (tenant_id, decision_id, rule_id, kind, severity, evidence_ref, field, observed, "
            "message, suggested_fix, ai_note, found_by, enc_detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %s, %s, %s::jsonb) "
            "ON CONFLICT (decision_id, rule_id, evidence_ref, field) DO NOTHING",
            (tenant_id, f["decision_id"], f["rule_id"], f["kind"], f["severity"], f["evidence_ref"], f["field"],
             json.dumps(observed), message, json.dumps(fix) if fix else None, ai_note, f["found_by"],
             json.dumps(enc) if enc else None),
        )
        saved += cur.rowcount
    by_sev = {s: sum(1 for f in fresh if f["severity"] == s) for s in ("error", "warning", "info")}
    return {"decisions_checked": len(views), "new_findings": saved, "by_severity": by_sev, "notes": notes}


def open_finding(reveal: Reveal, f: dict[str, Any], enc_detail: Any) -> dict[str, Any]:
    """Fill in the wording and values of a finding about a protected decision (or say they were removed)."""
    if not enc_detail:
        return f
    key = reveal.dek(f["decision_id"])
    if key is None:
        return {**f, "message": REMOVED_MESSAGE, "observed": None, "suggested_fix": None, "ai_note": None}
    try:
        d = decrypt_payload(key[1], payload_aad(f["decision_id"], f"finding:{f['rule_id']}:{f['evidence_ref']}:{f['field']}"), enc_detail)
    except Exception:
        return {**f, "message": REMOVED_MESSAGE, "observed": None, "suggested_fix": None, "ai_note": None}
    return {**f, "message": d["message"], "observed": d["observed"].get("value") if isinstance(d["observed"], dict) else d["observed"],
            "suggested_fix": d["suggested_fix"], "ai_note": d["ai_note"]}


def finding_status(conn: psycopg.Connection[Any], finding_id: int) -> str:
    row = conn.execute(
        "SELECT action FROM finding_reviews WHERE finding_id = %s ORDER BY review_id DESC LIMIT 1", (finding_id,)
    ).fetchone()
    return "open" if row is None else str(row[0])


# ---------- corrections ----------

class CorrectionConflict(Exception):
    """Another correction was added to the same decision at the same moment."""


def _correction_row(decision_id: str, seq: int, ref: str, key: str, role: str, ts: datetime,
                    payload: dict[str, Any], prev: str, p_sha: str | None = None) -> dict[str, Any]:
    p_sha = p_sha or payload_sha256(payload)
    row = {
        "decision_id": decision_id, "seq": seq, "evidence_type": "correction",
        "evidence_ref": f"correction:{seq}:{ref}:{key}", "source_id": role,
        "source_timestamp": ts, "captured_at": ts, "payload": payload, "payload_sha256": p_sha, "prev_hash": prev,
    }
    row["entry_hash"] = entry_hash(**{k: row[k] for k in (
        "decision_id", "seq", "evidence_type", "evidence_ref", "source_id", "source_timestamp", "captured_at",
        "payload_sha256", "prev_hash")})
    return row


def append_correction(
    conn: psycopg.Connection[Any], *, tenant_id: str, decision_id: str, evidence_ref: str, field_name: str,
    new_value: Any, reason: str, source: str, role: str, credential_id: int | None, finding_id: int | None,
) -> dict[str, Any]:
    """Add one correction to the decision's correction chain. The original snapshot is only read, never changed."""
    snap = conn.execute(
        "SELECT payload, entry_hash FROM decision_snapshots WHERE decision_id = %s AND evidence_ref = %s ORDER BY seq LIMIT 1",
        (decision_id, evidence_ref),
    ).fetchone()
    if snap is None:
        raise LookupError("That item isn't part of this decision's record.")
    reveal = Reveal(conn)
    protected = bool((conn.execute("SELECT encrypted FROM decisions WHERE decision_id = %s", (decision_id,)).fetchone() or [False])[0])
    opened = reveal.payload(decision_id, evidence_ref, snap[0]) if protected and is_encrypted(snap[0]) else snap[0]
    if opened is None:
        raise LookupError("That item was removed, so it can't be corrected.")
    original = dict(opened or {})
    old = original.get(field_name)
    last = conn.execute(
        "SELECT seq, entry_hash FROM corrections WHERE decision_id = %s ORDER BY seq DESC LIMIT 1", (decision_id,)
    ).fetchone()
    seq, prev = (1, GENESIS) if last is None else (int(last[0]) + 1, str(last[1]))
    ts = datetime.now(timezone.utc)
    payload = {
        "evidence_ref": evidence_ref, "field": field_name, "old_value": old, "new_value": new_value,
        "reason": reason, "source": source, "original_entry_hash": str(snap[1]), "finding_id": finding_id,
    }
    stored, p_sha = payload, None
    if protected:  # the old and new values are personal data too: same key as the decision, so erasing it erases them
        key = reveal.dek(decision_id)
        if key is None:
            raise LookupError("That decision's content was removed, so it can't be corrected.")
        stored, p_sha = seal_payload(key[0], decision_id, f"correction:{seq}:{evidence_ref}:{field_name}", key[1], payload)
    row = _correction_row(decision_id, seq, evidence_ref, field_name, role, ts, stored, prev, p_sha)
    try:
        with conn.transaction():
            conn.execute(
                "INSERT INTO corrections (tenant_id, decision_id, seq, finding_id, evidence_ref, field, original_entry_hash, "
                "payload, payload_sha256, prev_hash, entry_hash, corrected_by_role, corrected_by_credential, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s)",
                (tenant_id, decision_id, seq, finding_id, evidence_ref, field_name, str(snap[1]), json.dumps(stored),
                 row["payload_sha256"], prev, row["entry_hash"], role, credential_id, ts),
            )
    except psycopg.errors.UniqueViolation as exc:
        raise CorrectionConflict() from exc
    return {"seq": seq, "evidence_ref": evidence_ref, "field": field_name, "old_value": old, "new_value": new_value,
            "reason": reason, "source": source, "created_at": ts.isoformat(), "entry_hash": row["entry_hash"]}


def load_corrections(conn: psycopg.Connection[Any], decision_id: str) -> tuple[list[dict[str, Any]], bool, str | None]:
    """(corrections, chain_ok, why_not). With no corrections the chain is trivially fine."""
    rows = conn.execute(
        "SELECT seq, evidence_ref, field, payload, payload_sha256, prev_hash, entry_hash, corrected_by_role, created_at "
        "FROM corrections WHERE decision_id = %s ORDER BY seq", (decision_id,),
    ).fetchall()
    chain, shown = [], []
    reveal = Reveal(conn)
    protected = bool((conn.execute("SELECT encrypted FROM decisions WHERE decision_id = %s", (decision_id,)).fetchone() or [False])[0])
    for seq, ref, key, payload, p_sha, prev, eh, role, ts in rows:
        stored_payload = payload
        if protected and is_encrypted(payload):
            payload = reveal.payload(decision_id, f"correction:{seq}:{ref}:{key}", payload) or {}
        chain.append({
            "decision_id": decision_id, "seq": int(seq), "evidence_type": "correction",
            "evidence_ref": f"correction:{seq}:{ref}:{key}", "source_id": str(role), "source_timestamp": ts,
            "captured_at": ts, "payload": stored_payload, "payload_sha256": str(p_sha), "prev_hash": str(prev), "entry_hash": str(eh),
        })
        shown.append({
            "seq": int(seq), "evidence_ref": str(ref), "field": str(key), "old_value": payload.get("old_value"),
            "new_value": payload.get("new_value"), "reason": payload.get("reason"), "source": payload.get("source"),
            "created_at": ts.isoformat(),
        })
    if not chain:
        return [], True, None
    ok, _, why = verify_chain(chain, len(chain), chain[-1]["entry_hash"], payload_check=reveal.checker(), protected=protected)
    return shown, ok, why

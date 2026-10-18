"""Propose a field mapping for an unfamiliar file: built-in rules first, optionally an AI model on top.

Whatever proposes it, the result is only a draft. Every column the AI names must exist in the file, every rule is
re-run over the real rows to measure how often it works, and a person approves before anything is imported.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from backend.mapping import (
    EvidenceRule,
    PLACEHOLDER,
    FieldRule,
    MappingSpec,
    ReasonsRule,
    RowError,
    eval_rule,
    to_number,
)
from backend.redaction import redact_text

LLMCall = Callable[[str], str]  # prompt in, JSON text out

SAMPLE_ROWS = 5
MAX_DISTINCT = 8
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
NI_NUMBER = re.compile(r"\b[A-Za-z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-Za-z]\b")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "value"


PERSONAL_RE = re.compile(
    r"name|email|phone|telephone|mobile|address|postcode|zipcode|dob|birth|ssn|nino|nationalins|passport|iban|"
    r"accountnumber|accountno|sortcode|cardnumber|licen[cs]e"
)
NOT_PERSONAL_RE = re.compile(r"model|policy|scorecard|ruleset|product|filename")


def is_personal(column: str) -> bool:
    """Columns that look like they hold names, contact details, identifiers or dates of birth."""
    n = norm(column)
    if NOT_PERSONAL_RE.search(n):
        return False
    return bool(PERSONAL_RE.search(n))


SYNONYMS: dict[str, list[str]] = {
    "external_id": ["applicationid", "appref", "applicationref", "applicationreference", "loanid", "reference", "ref",
                    "caseid", "applicationnumber", "appno", "loannumber", "id"],
    "subject_ref": ["customerid", "customerref", "borrowerid", "applicantid", "clientid", "subjectid", "custid",
                    "memberid", "userid", "customernumber"],
    "decided_at": ["decisiondate", "decidedat", "decisiontime", "decisiontimestamp", "dateofdecision", "decisionts",
                   "decided", "decisiondatetime", "when", "decidedon", "date"],
    "outcome": ["decision", "outcome", "result", "decisionoutcome", "decisionresult", "approved", "approvedflag",
                "status", "finaldecision"],
    "score": ["pd", "probabilityofdefault", "riskscore", "score", "probability", "defaultprobability", "risk",
              "modelscore", "pdscore"],
    "model_version": ["modelversion", "model", "modelid", "scorecard", "scorecardversion", "modelname"],
    "policy_version": ["policyversion", "policy", "policyid", "ruleset", "rulesetversion", "policyname"],
    "reasons": ["reasoncodes", "reasoncode", "reasons", "declinereasons", "declinereason", "adversereasons", "reason",
                "adverseactioncodes", "principalreasons"],
}

EVIDENCE_KEYWORDS: list[tuple[str, str, str]] = [
    ("bureau", "bureau_api", r"credit|fico|bureau|delinq|arrear|missedpay|enquir|utilis|utiliz"),
    ("document", "document_check", r"document|doc|payslip|proof|kyc|idcheck|verification"),
    ("consent", "consent_record", r"consent|optin"),
    ("account_history", "account_system", r"balance|accountage|overdraft|tenure|monthsonbook"),
    ("application", "application_form", r"income|salary|loan|amount|requested|term|dti|debt|employ|ltv|deposit|purpose"),
]

OUTCOME_WORDS = {
    "APPROVE": {"approve", "approved", "accept", "accepted", "yes", "y", "1", "true", "granted", "pass", "passed"},
    "DECLINE": {"decline", "declined", "deny", "denied", "reject", "rejected", "no", "n", "0", "false", "refuse",
                "refused", "fail", "failed"},
    "REFER": {"refer", "referred", "referral", "manual", "review", "manual review", "manual_review", "pending review",
              "underwriter", "refer to underwriter"},
}

DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y", "%Y/%m/%d", "%d.%m.%Y",
]
DATE_FORMATS += [f"{f} {t}" for f in list(DATE_FORMATS) for t in ("%H:%M:%S", "%H:%M")]


@dataclass
class Proposal:
    spec: MappingSpec
    issues: list[str] = field(default_factory=list)
    proposed_by: str = "rules"


# ---------- what an AI model is allowed to see ----------

def mask_sample(column: str, value: Any) -> str:
    """A sample cell safe to show a model: personal columns are withheld, contact and account patterns are masked."""
    if is_personal(column):
        return "[withheld]"
    text = str(value if value is not None else "")
    text = EMAIL.sub("[email]", text)
    text = NI_NUMBER.sub("[id number]", text)
    return redact_text(text)[:40]


def masked_profile(headers: list[str], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    profile = []
    for h in headers:
        col: dict[str, Any] = {"column": h, "samples": [mask_sample(h, r.get(h)) for r in rows[:SAMPLE_ROWS]]}
        if not is_personal(h):
            distinct = sorted({str(r.get(h)).strip() for r in rows if r.get(h) not in (None, "")})
            if 0 < len(distinct) <= MAX_DISTINCT and all(len(d) <= 40 for d in distinct):
                col["distinct_values"] = [mask_sample(h, d) for d in distinct]
        profile.append(col)
    return profile


PROMPT = """You map one lender's decision file onto a fixed layout. Reply with JSON only.

Rules:
- Use only column names from the list below. Never invent a column.
- Personal details (names, contact details, addresses, dates of birth, account numbers) must not be used.
- If a field is not in the file, leave it null instead of guessing.

Layout to fill:
{{"external_id": {{"column": str}}, "subject_ref": {{"column": str}}, "decided_at": {{"column": str, "date_formats": [strptime formats]}},
 "outcome": {{"column": str, "value_map": {{"<value in file>": "APPROVE"|"DECLINE"|"REFER"}}}},
 "score": {{"column": str, "unit": "fraction"|"percent"}},
 "model_version": {{"column": str}} or {{"const": str}} or null, "policy_version": same,
 "reasons": {{"column": str, "separator": str, "code_text": {{"<code>": "<plain sentence>"}}}} or null,
 "evidence": [{{"evidence_type": "application"|"bureau"|"account_history"|"document"|"consent"|"other",
   "columns": [{{"column": str, "kind": "number"|"text"|"percent"|"date"}}]}}]}}

Columns (with a few masked sample values):
{profile}
"""


def build_prompt(headers: list[str], rows: list[dict[str, Any]]) -> str:
    return PROMPT.format(profile=json.dumps(masked_profile(headers, rows), indent=1))


# ---------- built-in rules ----------

def _values(rows: list[dict[str, Any]], col: str) -> list[str]:
    return [str(r.get(col)).strip() for r in rows if r.get(col) not in (None, "") and str(r.get(col)).strip()]


def _numbers(vals: list[str]) -> list[float]:
    out = []
    for v in vals:
        try:
            out.append(to_number(v))
        except RowError:
            pass
    return out


def _numeric_share(vals: list[str]) -> float:
    if not vals:
        return 0.0
    ok = 0
    for v in vals:
        try:
            to_number(v)
            ok += 1
        except RowError:
            pass
    return ok / len(vals)


def detect_date_formats(vals: list[str]) -> tuple[list[str], float, str]:
    """(formats, confidence, note). ISO values need no format; dd/mm versus mm/dd is decided from the data."""
    if not vals:
        return [], 0.3, "No dates to look at."
    try:
        for v in vals:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        return [], 0.95, ""
    except ValueError:
        pass
    fits = []
    for f in DATE_FORMATS:
        try:
            for v in vals:
                datetime.strptime(v, f)
            fits.append(f)
        except ValueError:
            continue
    if not fits:
        return [], 0.3, "We couldn't recognise the date format."
    day_first = [f for f in fits if f.startswith("%d/%m/%Y")]
    month_first = [f for f in fits if f.startswith("%m/%d/%Y")]
    if day_first and month_first:
        return day_first[:1], 0.6, "Day and month could be either way round; we assumed day first. Please check."
    return fits[:1], 0.9, ""


def _guess_outcome_map(vals: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in sorted(set(vals)):
        key = v.casefold()
        for target, words in OUTCOME_WORDS.items():
            if key in words:
                out[v] = target
                break
    return out


def _tails(column: str) -> list[str]:
    """The full name, then its last two dotted parts, then its last part (nested JSON like decision.result)."""
    parts = column.split(".")
    return [norm(column), norm(".".join(parts[-2:])), norm(parts[-1])]


def _candidates(headers: list[str], role: str, used: set[str]):
    for tier, conf in enumerate((0.9, 0.75, 0.6)):
        for syn in SYNONYMS[role]:  # in priority order
            for h in headers:
                if h not in used and _tails(h)[tier] == syn and not is_personal(h):
                    yield h, conf


def _find(headers: list[str], role: str, used: set[str]) -> tuple[str, float] | None:
    return next(_candidates(headers, role, used), None)


def heuristic_spec(headers: list[str], rows: list[dict[str, Any]], fmt: str) -> tuple[MappingSpec, list[str]]:
    issues: list[str] = []
    used: set[str] = set()
    spec = MappingSpec(source_format=fmt)  # type: ignore[arg-type]

    # the outcome first: its values decide whether the column is really the decision
    hit = _find(headers, "outcome", used)
    if hit:
        col, conf = hit
        vals = _values(rows, col)
        vmap = _guess_outcome_map(vals)
        if vals and len(vmap) < len({v for v in vals}):
            left = sorted({v for v in vals if v not in vmap})[:5]
            issues.append(f"Some values in '{col}' aren't recognised as approved, declined or referred: {left}.")
            conf = min(conf, 0.6)
        if vals and not vmap:
            hit = None
        else:
            spec.outcome = FieldRule(column=col, transform="enum", value_map=vmap, confidence=conf,
                                     note="Matched by column name and the values in it.")
            used.add(col)

    for role in ("external_id", "subject_ref", "decided_at", "model_version", "policy_version"):
        found = _find(headers, role, used)
        if not found:
            continue
        col, conf = found
        rule = FieldRule(column=col, confidence=conf, note="Matched by column name.")
        if role == "decided_at":
            formats, fconf, fnote = detect_date_formats(_values(rows, col))
            rule = FieldRule(column=col, transform="date", date_formats=formats, confidence=min(conf, fconf),
                             note=fnote or "Matched by column name.")
        setattr(spec, role, rule)
        used.add(col)

    # score: a risk score or probability, never a credit score
    for col, conf in _candidates(headers, "score", used):
        if re.search(r"credit|fico|bureau", norm(col)):
            continue
        vals = _values(rows, col)
        if _numeric_share(vals) < 0.9:
            issues.append(f"'{col}' looks like the risk score, but many of its values aren't numbers.")
            continue
        nums = _numbers(vals)
        if nums and max(nums) <= 1.0 and min(nums) >= 0:
            spec.score = FieldRule(column=col, transform="number", confidence=conf, note="Values are between 0 and 1.")
        elif nums and max(nums) <= 100 and min(nums) >= 0:
            spec.score = FieldRule(column=col, transform="percent_to_fraction", confidence=min(conf, 0.75),
                                   note="Values look like percentages, so they are divided by 100.")
        else:
            issues.append(f"'{col}' has values outside 0 to 100, so it doesn't look like a risk score.")
            continue
        used.add(col)
        break

    if spec.model_version is None:
        spec.model_version = FieldRule(const=PLACEHOLDER, confidence=0.2,
                                       note="No model version column was found. Replace this with the real one.")
        issues.append("No model version column found; a placeholder is used until you set one.")
    if spec.policy_version is None:
        spec.policy_version = FieldRule(const=PLACEHOLDER, confidence=0.2,
                                        note="No policy version column was found. Replace this with the real one.")
        issues.append("No policy version column found; a placeholder is used until you set one.")

    reason_col = _find(headers, "reasons", used)
    if reason_col:
        col, conf = reason_col
        vals = _values(rows, col)
        sep = next((s for s in (";", "|", ",") if any(s in v for v in vals)), ";")
        spec.reasons = ReasonsRule(column=col, separator=sep, confidence=conf,
                                   note="Codes are shown as 'Reason code X' until you add plain wording for them.")
        used.add(col)

    # everything else: evidence, or ignored
    groups: dict[str, dict[str, FieldRule]] = {}
    for h in headers:
        if h in used:
            continue
        if is_personal(h):
            spec.ignored_columns[h] = "Personal detail, so it isn't stored."
            continue
        n = norm(h)
        placed = False
        for etype, source, pattern in EVIDENCE_KEYWORDS:
            if re.search(pattern, n):
                vals = _values(rows, h)
                is_num = _numeric_share(vals) >= 0.9
                rule = FieldRule(column=h, transform="number" if is_num else "text", confidence=0.6,
                                 note="Placed by column name.")
                groups.setdefault(etype, {})[snake(h)] = rule
                placed = True
                break
        if not placed:
            spec.ignored_columns[h] = "Not needed to explain the decision, so it isn't stored."
    sources = {etype: source for etype, source, _ in EVIDENCE_KEYWORDS}
    spec.evidence = [
        EvidenceRule(evidence_type=etype, source_id=sources[etype], fields=fields)  # type: ignore[arg-type]
        for etype, fields in groups.items()
    ]
    return spec, issues


# ---------- optional AI proposal ----------

def _col(headers: set[str], d: Any, issues: list[str], what: str) -> str | None:
    col = d.get("column") if isinstance(d, dict) else None
    if col is None:
        return None
    if col not in headers:
        issues.append(f"The AI suggested a column '{col}' for {what} that isn't in your file, so it was ignored.")
        return None
    if is_personal(col):
        issues.append(f"The AI suggested '{col}' for {what}, but it holds personal details, so it was ignored.")
        return None
    return col


def spec_from_ai(raw: dict[str, Any], headers: list[str], rows: list[dict[str, Any]], fmt: str) -> tuple[MappingSpec, list[str]]:
    hs = set(headers)
    issues: list[str] = []
    spec = MappingSpec(source_format=fmt)  # type: ignore[arg-type]
    for role in ("external_id", "subject_ref", "model_version", "policy_version"):
        d = raw.get(role)
        col = _col(hs, d, issues, role.replace("_", " "))
        if col:
            setattr(spec, role, FieldRule(column=col, confidence=0.8, note="Suggested by AI."))
        elif isinstance(d, dict) and isinstance(d.get("const"), str) and d["const"].strip():
            setattr(spec, role, FieldRule(const=d["const"].strip()[:100], confidence=0.5, note="Fixed value suggested by AI."))
    d = raw.get("decided_at")
    col = _col(hs, d, issues, "the decision date")
    if col:
        fmts = [f for f in d.get("date_formats", []) if isinstance(f, str)][:10]
        spec.decided_at = FieldRule(column=col, transform="date", date_formats=fmts, confidence=0.8, note="Suggested by AI.")
    d = raw.get("outcome")
    col = _col(hs, d, issues, "the decision")
    if col:
        vm = {str(k): str(v) for k, v in (d.get("value_map") or {}).items() if str(v) in ("APPROVE", "DECLINE", "REFER")}
        spec.outcome = FieldRule(column=col, transform="enum", value_map=vm, confidence=0.8, note="Suggested by AI.")
    d = raw.get("score")
    col = _col(hs, d, issues, "the risk score")
    if col:
        pct = isinstance(d, dict) and d.get("unit") == "percent"
        spec.score = FieldRule(column=col, transform="percent_to_fraction" if pct else "number", confidence=0.8,
                               note="Suggested by AI.")
    d = raw.get("reasons")
    col = _col(hs, d, issues, "the reasons")
    if col:
        ct = {str(k): str(v)[:500] for k, v in (d.get("code_text") or {}).items()}
        sep = d.get("separator") if isinstance(d.get("separator"), str) and 0 < len(d["separator"]) <= 3 else ";"
        spec.reasons = ReasonsRule(column=col, separator=sep, code_text=ct, confidence=0.8, note="Suggested by AI.")
    seen: set[str] = set()
    for ev in raw.get("evidence") or []:
        etype = ev.get("evidence_type") if isinstance(ev, dict) else None
        if etype not in ("application", "bureau", "account_history", "document", "consent", "other"):
            continue
        fields: dict[str, FieldRule] = {}
        for c in ev.get("columns") or []:
            col = _col(hs, c, issues, "supporting evidence")
            if not col or col in seen:
                continue
            kind = c.get("kind")
            tr = {"number": "number", "percent": "percent_to_fraction", "date": "date"}.get(kind, "text")
            fields[snake(col)] = FieldRule(column=col, transform=tr, confidence=0.7, note="Suggested by AI.")  # type: ignore[arg-type]
            seen.add(col)
        if fields:
            spec.evidence.append(EvidenceRule(evidence_type=etype, source_id=f"{etype}_source", fields=fields))
    return spec, issues


def _merge(ai: MappingSpec, base: MappingSpec) -> MappingSpec:
    """AI choices win where it gave a usable one; the built-in rules fill the rest."""
    merged = ai.model_copy(deep=True)
    for role in ("external_id", "subject_ref", "decided_at", "outcome", "score", "model_version", "policy_version", "reasons"):
        if getattr(merged, role) is None:
            setattr(merged, role, getattr(base, role))
    if not merged.evidence:
        merged.evidence = base.evidence
    used = {c for c in _spec_cols(merged)}
    merged.ignored_columns = {k: v for k, v in base.ignored_columns.items() if k not in used}
    return merged


def _spec_cols(spec: MappingSpec) -> set[str]:
    from backend.mapping import spec_columns

    return spec_columns(spec)


# ---------- checking a proposal against the real rows ----------

def assess(spec: MappingSpec, rows: list[dict[str, Any]], limit: int = 200) -> list[str]:
    """Re-run every rule on real rows; lower the confidence of rules that often fail. Returns new issues."""
    issues: list[str] = []
    sample = rows[:limit]
    if not sample:
        return ["The file has no data rows."]

    def check(label: str, rule: FieldRule) -> None:
        if rule.column is None:
            return
        bad = 0
        blank = 0
        for r in sample:
            try:
                if eval_rule(rule, r) is None:
                    blank += 1
            except RowError:
                bad += 1
        rate = 1 - (bad / len(sample))
        if bad:
            rule.confidence = round(min(rule.confidence, rate), 3)
            issues.append(f"{bad} of {len(sample)} values in '{rule.column}' (used for {label}) couldn't be read.")
        if blank == len(sample):
            rule.confidence = 0.1
            issues.append(f"'{rule.column}' (used for {label}) is empty in every row checked.")

    for name in ("external_id", "subject_ref", "decided_at", "outcome", "score", "model_version", "policy_version"):
        rule = getattr(spec, name)
        if rule:
            check(name.replace("_", " "), rule)
    for ev in spec.evidence:
        for key, rule in ev.fields.items():
            check(key.replace("_", " "), rule)
    if spec.external_id and spec.external_id.column:
        vals = _values(rows, spec.external_id.column)
        if len(set(vals)) < len(vals):
            issues.append(f"'{spec.external_id.column}' has repeated values, so it may not be a unique reference.")
            spec.external_id.confidence = min(spec.external_id.confidence, 0.4)
    return issues


def default_llm() -> LLMCall | None:
    """A model call when LLM_BACKEND=gemini_api and a key is set; otherwise None (built-in rules only)."""
    if os.environ.get("LLM_BACKEND", "fake").strip().lower() != "gemini_api":
        return None
    from google.genai import types

    from recon_agent.backends import GeminiAPIBackend, _extract_json_dict  # noqa: F401

    backend = GeminiAPIBackend()
    if not backend.api_key:
        return None

    def call(prompt: str) -> str:
        client = backend.new_client()
        resp = backend._generate_content_with_fallback(
            client, [prompt], types.GenerateContentConfig(response_mime_type="application/json", temperature=0.0)
        )
        call.model = backend.model_name  # type: ignore[attr-defined]
        return str(resp.text)

    call.model = backend.model_name  # type: ignore[attr-defined]
    return call


def propose(
    headers: list[str],
    rows: list[dict[str, Any]],
    fmt: str,
    *,
    llm: LLMCall | None = None,
    use_ai: bool = True,
) -> Proposal:
    """Draft a mapping. Falls back to the built-in rules if the AI is off, unreachable or unusable."""
    base, issues = heuristic_spec(headers, rows, fmt)
    spec, proposed_by = base, "rules"
    call = None if not use_ai else (llm if llm is not None else default_llm())
    if call is not None:
        try:
            raw_text = call(build_prompt(headers, rows))
            raw = json.loads(raw_text[raw_text.index("{"): raw_text.rindex("}") + 1])
            ai_spec, ai_issues = spec_from_ai(raw, headers, rows, fmt)
            spec = _merge(ai_spec, base)
            issues = ai_issues + [i for i in issues if "placeholder" in i or "aren't recognised" in i]
            proposed_by = f"ai:{getattr(call, 'model', 'model')}"
        except Exception as exc:  # any failure: keep the built-in draft and say so
            issues.append(f"The AI model couldn't be used ({type(exc).__name__}), so the built-in rules were used.")
    issues += assess(spec, rows)
    return Proposal(spec=spec, issues=issues, proposed_by=proposed_by)

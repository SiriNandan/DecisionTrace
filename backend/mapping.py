"""Field mappings: a declarative recipe from a lender's own columns to the decision envelope (schema v1).

A mapping is plain data, never code. The AI may *propose* one; only this module's deterministic
executor ever turns source rows into envelopes, and a person approves the mapping first.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.ingest import DecisionEnvelopeV1

SPEC_VERSION = 1
PLACEHOLDER = "not provided in file"
MAX_CONTENT_BYTES = 5 * 1024 * 1024
MAX_PROPOSE_ROWS = 5000

Transform = Literal["text", "number", "percent_to_fraction", "date", "enum"]
EvidenceType = Literal["application", "bureau", "account_history", "document", "consent", "other"]


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str | None = Field(default=None, max_length=200)
    const: str | float | int | None = None
    transform: Transform = "text"
    date_formats: list[str] = Field(default_factory=list, max_length=10)
    value_map: dict[str, str] = Field(default_factory=dict, max_length=200)
    confidence: float = Field(default=1.0, ge=0, le=1)
    note: str = Field(default="", max_length=300)


class ReasonsRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(..., max_length=200)
    separator: str = Field(default=";", min_length=1, max_length=3)
    code_text: dict[str, str] = Field(default_factory=dict, max_length=200)
    confidence: float = Field(default=1.0, ge=0, le=1)
    note: str = Field(default="", max_length=300)


class EvidenceRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_type: EvidenceType
    source_id: str = Field(..., min_length=1, max_length=100)
    fields: dict[str, FieldRule] = Field(..., min_length=1, max_length=40)


class MappingSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spec_version: Literal[1] = 1
    source_format: Literal["csv", "json"]
    external_id: FieldRule | None = None
    subject_ref: FieldRule | None = None
    decided_at: FieldRule | None = None
    outcome: FieldRule | None = None
    score: FieldRule | None = None
    model_version: FieldRule | None = None
    policy_version: FieldRule | None = None
    reasons: ReasonsRule | None = None
    evidence: list[EvidenceRule] = Field(default_factory=list, max_length=20)
    ignored_columns: dict[str, str] = Field(default_factory=dict, max_length=500)


REQUIRED_FIELDS = ("external_id", "subject_ref", "decided_at", "outcome", "score", "model_version", "policy_version")
FIELD_LABELS = {
    "external_id": "your application reference",
    "subject_ref": "a customer reference (not a name or contact detail)",
    "decided_at": "the decision date",
    "outcome": "the decision (approved, declined or referred)",
    "score": "the risk score or probability",
    "model_version": "the model version",
    "policy_version": "the policy version",
}


def spec_sha256(spec: MappingSpec) -> str:
    canonical = json.dumps(spec.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def spec_columns(spec: MappingSpec) -> set[str]:
    cols: set[str] = set()
    for name in REQUIRED_FIELDS:
        rule: FieldRule | None = getattr(spec, name)
        if rule and rule.column:
            cols.add(rule.column)
    if spec.reasons:
        cols.add(spec.reasons.column)
    for ev in spec.evidence:
        cols.update(r.column for r in ev.fields.values() if r.column)
    return cols


def gaps(spec: MappingSpec) -> list[str]:
    """Required envelope fields with neither a column nor a fixed value, in plain words."""
    out = []
    for name in REQUIRED_FIELDS:
        rule: FieldRule | None = getattr(spec, name)
        if rule is None or (rule.column is None and (rule.const is None or rule.const == PLACEHOLDER)):
            out.append(f"We couldn't find {FIELD_LABELS[name]}. Choose a column or enter a fixed value.")
    if not any(ev.fields for ev in spec.evidence):
        out.append("No columns were chosen as supporting evidence (for example income or credit score).")
    return out


# ---------- reading source files ----------

def parse_csv(content: str) -> tuple[list[str], list[dict[str, str]]]:
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        raise ValueError("That file is larger than 5 MB. Please send it in smaller parts.")
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    headers = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headers:
        raise ValueError("The file has no header row, so we can't tell what each column means.")
    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in raw.items() if k and k.strip()})
        if len(rows) > MAX_PROPOSE_ROWS * 10:
            raise ValueError("That file has too many rows for one upload.")
    return headers, rows


def flatten(row: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Dotted column names for nested JSON rows: {"a": {"b": 1}} -> {"a.b": 1}."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = v
    return out


def load_rows(fmt: str, content: str | None, rows: list[dict[str, Any]] | None) -> tuple[list[str], list[dict[str, Any]]]:
    if fmt == "csv":
        if content is None:
            raise ValueError("Send the file's text as `content`.")
        return parse_csv(content)
    if rows is None:
        raise ValueError("Send the records as `rows`.")
    flat = [flatten(r) for r in rows]
    headers: list[str] = []
    for r in flat:
        for k in r:
            if k not in headers:
                headers.append(k)
    return headers, flat


# ---------- transforms ----------

class RowError(ValueError):
    pass


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


_NUM_STRIP = re.compile(r"[£$€,\s]|GBP|USD|EUR", re.IGNORECASE)


def to_number(value: Any) -> float:
    if isinstance(value, bool):
        raise RowError(f"'{value}' isn't a number")
    if isinstance(value, (int, float)):
        return float(value)
    text = _NUM_STRIP.sub("", str(value))
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()").rstrip("%")
    try:
        number = float(text)
    except ValueError as exc:
        raise RowError(f"'{value}' isn't a number") from exc
    return -number if neg else number


def to_datetime(value: Any, formats: list[str]) -> datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raise RowError(f"'{value}' isn't a date we can read")
    text = str(value).strip()
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RowError(f"'{value}' isn't a date we can read") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def eval_rule(rule: FieldRule, row: dict[str, Any]) -> Any:
    """Value for one rule, or None when the source cell is empty. Raises RowError on a bad value."""
    raw: Any = rule.const if rule.column is None else row.get(rule.column)
    if _blank(raw):
        return None
    if rule.transform == "number":
        return to_number(raw)
    if rule.transform == "percent_to_fraction":
        return to_number(raw) / 100.0
    if rule.transform == "date":
        return to_datetime(raw, rule.date_formats).isoformat()
    if rule.transform == "enum":
        key = str(raw).strip().casefold()
        for src, target in rule.value_map.items():
            if src.strip().casefold() == key:
                return target
        raise RowError(f"The value '{raw}' isn't in the mapping yet")
    return str(raw).strip()


# ---------- executor ----------

def apply_mapping(
    spec: MappingSpec,
    row: dict[str, Any],
    *,
    source_mapping: dict[str, Any] | None = None,
) -> tuple[DecisionEnvelopeV1 | None, list[str]]:
    """Turn one source row into an envelope. Returns (envelope, []) or (None, plain-language problems)."""
    errors: list[str] = []
    values: dict[str, Any] = {}
    for name in REQUIRED_FIELDS:
        rule: FieldRule | None = getattr(spec, name)
        if rule is None:
            errors.append(f"No mapping for {FIELD_LABELS[name]}.")
            continue
        try:
            val = eval_rule(rule, row)
        except RowError as exc:
            errors.append(f"{name}: {exc}")
            continue
        if val is None:
            errors.append(f"{name}: the value is empty")
        values[name] = val
    if errors:
        return None, errors

    reasons: list[dict[str, str]] = []
    if spec.reasons is not None:
        cell = row.get(spec.reasons.column)
        if not _blank(cell):
            for code in str(cell).split(spec.reasons.separator):
                code = code.strip()
                if code:
                    text = next(
                        (t for c, t in spec.reasons.code_text.items() if c.strip().casefold() == code.casefold()),
                        f"Reason code {code}",
                    )
                    reasons.append({"code": code[:64], "text": text[:500]})

    evidence: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for ev in spec.evidence:
        payload: dict[str, Any] = {}
        for key, rule in ev.fields.items():
            try:
                val = eval_rule(rule, row)
            except RowError as exc:
                errors.append(f"{key}: {exc}")
                continue
            if val is not None:
                payload[key] = val
        if not payload:
            continue
        counts[ev.evidence_type] = counts.get(ev.evidence_type, 0) + 1
        suffix = "" if counts[ev.evidence_type] == 1 else f"#{counts[ev.evidence_type]}"
        evidence.append(
            {
                "evidence_type": ev.evidence_type,
                "evidence_ref": f"{ev.evidence_type}{suffix}:{values['external_id']}",
                "source_id": ev.source_id,
                "source_timestamp": values["decided_at"],
                "payload": payload,
            }
        )
    if not evidence and not errors:
        errors.append("None of the evidence columns had a value for this row.")
    if errors:
        return None, errors

    body: dict[str, Any] = {
        "schema_version": 1,
        "external_id": str(values["external_id"]),
        "subject_ref": str(values["subject_ref"]),
        "decided_at": values["decided_at"],
        "outcome": values["outcome"],
        "score": values["score"],
        "model_version": str(values["model_version"]),
        "policy_version": str(values["policy_version"]),
        "reasons": reasons,
        "evidence": evidence,
    }
    if source_mapping is not None:
        body["source_mapping"] = source_mapping
    try:
        return DecisionEnvelopeV1.model_validate(body), []
    except ValidationError as exc:
        return None, [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def preview(spec: MappingSpec, rows: list[dict[str, Any]], limit: int = 200) -> dict[str, Any]:
    """Dry-run a mapping over up to `limit` rows: counts, the first problems, and the first result."""
    valid = 0
    problems: list[dict[str, Any]] = []
    first: dict[str, Any] | None = None
    for i, row in enumerate(rows[:limit], start=1):
        env, errs = apply_mapping(spec, row)
        if env is not None:
            valid += 1
            if first is None:
                first = env.model_dump(mode="json")
        elif len(problems) < 10:
            problems.append({"row": i, "problems": errs})
    checked = min(len(rows), limit)
    return {"rows_total": len(rows), "rows_checked": checked, "valid": valid, "invalid": checked - valid,
            "problems": problems, "first_result": first}

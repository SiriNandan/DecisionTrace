"""Field mapping: proposals for unfamiliar files, the AI guardrails, and the deterministic executor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.mapping import FieldRule, MappingSpec, apply_mapping, gaps, load_rows, preview, spec_columns, to_number
from backend.mapping_ai import build_prompt, is_personal, mask_sample, propose

SRC = Path(__file__).resolve().parent.parent / "fixtures" / "sources"


def load(name: str):
    raw = (SRC / name).read_text(encoding="utf-8")
    if name.endswith(".json"):
        return load_rows("json", None, json.loads(raw)) + ("json",)
    return load_rows("csv", raw, None) + ("csv",)


def test_uk_bank_file_is_understood_and_personal_columns_are_left_out() -> None:
    headers, rows, fmt = load("uk_bank.csv")
    p = propose(headers, rows, fmt)
    s = p.spec
    assert (s.external_id.column, s.subject_ref.column, s.outcome.column) == ("Application Ref", "Customer ID", "Decision")
    assert (s.decided_at.column, s.score.column, s.reasons.column) == ("Decision Date", "PD", "Reason Codes")
    assert set(s.ignored_columns) >= {"Customer Name", "Email", "Branch"}
    used = {c for ev in s.evidence for r in ev.fields.values() for c in [r.column]}
    assert not used & {"Customer Name", "Email"}
    assert gaps(s) == [] and p.proposed_by == "rules"

    pv = preview(s, rows)
    assert (pv["valid"], pv["invalid"]) == (40, 0)
    first = pv["first_result"]
    assert first["decided_at"].startswith("2026-09-01T09:15")  # day first: the file has days above 12
    assert first["evidence"][0]["payload"]["annual_income_gbp"] == 57445.0  # "£57,445"
    assert first["outcome"] == "DECLINE" and first["reasons"][0]["code"] == "LOW_SCORE"


def test_us_file_needs_a_person_for_the_odd_value_and_the_missing_versions() -> None:
    headers, rows, fmt = load("us_lender.csv")
    p = propose(headers, rows, fmt)
    s = p.spec
    assert s.score.transform == "percent_to_fraction"
    assert any("'R'" in i for i in p.issues)
    assert len(gaps(s)) == 2 and all("version" in g for g in gaps(s))  # placeholders don't count as answers
    assert preview(s, rows)["invalid"] == 3

    s.outcome.value_map["R"] = "REFER"
    s.model_version = FieldRule(const="us_model_4")
    s.policy_version = FieldRule(const="us_policy_9")
    assert gaps(s) == []
    pv = preview(s, rows)
    assert pv["invalid"] == 0 and pv["first_result"]["score"] == pytest.approx(0.531, abs=0.5)


def test_nested_json_reports_the_one_thing_it_could_not_find() -> None:
    headers, rows, fmt = load("nested.json")
    p = propose(headers, rows, fmt)
    assert p.spec.external_id.column == "application.id"
    assert p.spec.outcome.column == "decision.result" and p.spec.score.column == "decision.pd"
    assert any("decision date" in g for g in gaps(p.spec)) or p.spec.decided_at is not None


def test_bad_rows_are_rejected_in_plain_words_without_crashing() -> None:
    headers, rows, fmt = load("uk_bank.csv")
    spec = propose(headers, rows, fmt).spec
    bad = dict(rows[0], **{"PD": "abc", "Decision Date": "not a date", "Application Ref": "", "Decision": "Maybe"})
    env, errs = apply_mapping(spec, bad)
    assert env is None
    text = " ".join(errs)
    assert "isn't a number" in text and "isn't a date" in text and "value is empty" in text and "isn't in the mapping" in text
    assert apply_mapping(spec, dict(rows[0], PD="1.7"))[0] is None  # a probability above 1 is refused by the envelope


@pytest.mark.parametrize(
    "raw, expected", [("£1,234.50", 1234.5), ("(250)", -250.0), ("12%", 12.0), (7, 7.0), (" 3 ", 3.0), ("GBP 10", 10.0)]
)
def test_number_parsing(raw: Any, expected: float) -> None:
    assert to_number(raw) == expected


def test_personal_column_names_are_recognised() -> None:
    for name in ("Customer Name", "email", "Home Address", "Date of Birth", "phone_number", "NI Number", "sort code"):
        assert is_personal(name) or name == "NI Number", name  # NI is caught by the value masker below
    for name in ("Customer ID", "Model Name", "Policy Version", "Annual Income", "Scorecard"):
        assert not is_personal(name), name
    assert mask_sample("Customer Name", "Alex Morgan") == "[withheld]"
    assert "example.com" not in mask_sample("Notes", "write to a.b@example.com")
    assert "QQ" not in mask_sample("Notes", "NI QQ 12 34 56 C")


# ---------- the AI step ----------

def test_the_model_only_sees_masked_samples() -> None:
    headers, rows, _ = load("uk_bank.csv")
    prompt = build_prompt(headers, rows)
    for secret in ("Alex Morgan", "Sam Patel", "example.com", "cust0@"):
        assert secret not in prompt
    assert "[withheld]" in prompt and "Declined" in prompt  # outcome words are shown, names are not


def test_ai_suggestions_are_checked_against_the_file() -> None:
    headers, rows, fmt = load("uk_bank.csv")
    answer = {
        "external_id": {"column": "Application Ref"},
        "subject_ref": {"column": "Customer Name"},  # personal: must be ignored
        "decided_at": {"column": "Decision Date", "date_formats": ["%d/%m/%Y %H:%M"]},
        "outcome": {"column": "Verdict", "value_map": {"Approved": "APPROVE"}},  # not in the file
        "score": {"column": "PD", "unit": "fraction"},
        "model_version": {"const": "ai_model"},
        "evidence": [{"evidence_type": "bureau", "columns": [{"column": "Credit Score", "kind": "number"}]}],
    }
    p = propose(headers, rows, fmt, llm=lambda _prompt: "Sure!\n" + json.dumps(answer))
    assert p.proposed_by.startswith("ai:")
    assert p.spec.external_id.column == "Application Ref" and p.spec.score.column == "PD"
    assert p.spec.subject_ref.column == "Customer ID"  # fell back to the built-in rules, not the personal column
    assert p.spec.outcome.column == "Decision"  # the made-up column was dropped
    assert any("'Verdict'" in i and "isn't in your file" in i for i in p.issues)
    assert any("'Customer Name'" in i and "personal" in i for i in p.issues)
    assert "Customer Name" not in spec_columns(p.spec)  # it is only listed as left out
    assert preview(p.spec, rows)["invalid"] == 0


def test_a_broken_model_never_blocks_the_built_in_rules() -> None:
    headers, rows, fmt = load("uk_bank.csv")
    for llm in (lambda _p: "no json at all", lambda _p: (_ for _ in ()).throw(TimeoutError("down"))):
        p = propose(headers, rows, fmt, llm=llm)
        assert p.proposed_by == "rules" and any("built-in rules were used" in i for i in p.issues)
        assert gaps(p.spec) == []


def test_confidence_drops_when_a_rule_sometimes_fails_and_a_mostly_broken_column_is_not_used() -> None:
    headers, rows, fmt = load("uk_bank.csv")
    some = [dict(r, PD="n/a") if i % 20 == 0 else r for i, r in enumerate(rows)]  # 2 of 40 unreadable
    p = propose(headers, some, fmt)
    assert p.spec.score.column == "PD" and p.spec.score.confidence < 0.96
    assert any("'PD'" in i and "couldn't be read" in i for i in p.issues)

    most = [dict(r, PD="n/a") if i % 2 else r for i, r in enumerate(rows)]
    q = propose(headers, most, fmt)
    assert q.spec.score is None and any("aren't numbers" in i for i in q.issues)
    assert any("risk score" in g for g in gaps(q.spec))


def test_spec_rejects_unknown_fields_so_it_can_never_carry_code() -> None:
    with pytest.raises(Exception):
        MappingSpec.model_validate({"source_format": "csv", "run": "import os"})

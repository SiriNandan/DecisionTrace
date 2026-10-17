"""Runtime claim-grounding guardrails for the DecisionTrace Reconstruction Agent.

Every claim must cite evidence returned by a tool in the current session, carry a field/value that
matches that evidence, and contain only numbers that appeared in tool results. Claims failing any
check are removed and counted in `ReconstructionReport.guardrail_rejections`.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from recon_agent.tools import ToolSession


def _extract_numbers_from_text(text: str) -> list[float]:
    """Extract numeric literals from a claim text string while ignoring identifier tokens and section numbers."""
    # Strip known identifier patterns (e.g., APP-0011, LOAN-SCEN-0001, LP-2026-v1, loan_lr_v1, 24m)
    cleaned = re.sub(r"\b[A-Za-z_]+-\d+(?:-[A-Za-z0-9]+)*\b", " ", text)
    cleaned = re.sub(r"\b[A-Za-z0-9_]+_v\d+\b", " ", cleaned)
    cleaned = re.sub(r"\b\d+m\b", " ", cleaned)
    # Strip quoted policy section titles like '3.2 Debt-to-income limit' or (3.2 Debt-to-income limit)
    cleaned = re.sub(r"\b\d+\.\d+\s+[A-Z][A-Za-z\s\-]+", " ", cleaned)

    matches = re.findall(r"(?<![A-Za-z0-9_.-])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])", cleaned)
    nums: list[float] = []
    for m in matches:
        try:
            val = float(m)
            if math.isfinite(val):
                nums.append(val)
        except ValueError:
            continue
    return nums


def _number_matches_set(val: float, allowed_numbers: set[float], tol: float = 1e-6) -> bool:
    for candidate in allowed_numbers:
        if abs(val - candidate) <= tol:
            return True
    return False


def _values_equal(val_a: Any, val_b: Any, tol: float = 1e-9) -> bool:
    if isinstance(val_a, bool) or isinstance(val_b, bool):
        return bool(val_a) is bool(val_b)
    if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
        return abs(float(val_a) - float(val_b)) <= tol
    return str(val_a) == str(val_b)


def evaluate_claim_grounding(
    claim_dict: dict[str, Any],
    session: ToolSession,
) -> dict[str, Any]:
    """Evaluate a single Claim for citation validity, claim support (field/value match), and numerical accuracy."""
    citation = str(claim_dict.get("citation", ""))
    field = str(claim_dict.get("field", ""))
    value = claim_dict.get("value")
    text = str(claim_dict.get("text", ""))

    # 1. Citation validity: citation must be in session.returned_ids and not in session.erased_ids
    citation_valid = (
        bool(citation)
        and citation in session.returned_ids
        and citation not in session.erased_ids
    )

    # 2. Claim support: cited item has `field` and its stored value equals `claim.value`
    cited_item = session.returned_items_by_id.get(citation)
    claim_supported = False
    source_text = ""
    if citation_valid and isinstance(cited_item, dict):
        source_text = json.dumps(cited_item, sort_keys=True)
        if field in cited_item and _values_equal(cited_item[field], value):
            claim_supported = True

    # 3. Numerical accuracy: any numeric value in `value` and `text` appears in session.returned_numbers
    extracted_nums = _extract_numbers_from_text(text)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        extracted_nums.append(float(value))

    if not extracted_nums:
        numerical_accurate = True
    else:
        numerical_accurate = all(
            _number_matches_set(num, session.returned_numbers) for num in extracted_nums
        )

    return {
        "citation": citation,
        "field": field,
        "value": value,
        "text": text,
        "citation_valid": citation_valid,
        "claim_supported": claim_supported,
        "numerical_accurate": numerical_accurate,
        "source_text": source_text,
    }


def apply_claim_guardrails(
    raw_reasons: list[dict[str, Any]],
    session: ToolSession,
) -> tuple[list[dict[str, Any]], int]:
    """Filter out unsupported claims using ToolSession state and count guardrail rejections."""
    kept_reasons: list[dict[str, Any]] = []
    rejected_count = 0

    for reason_group in raw_reasons:
        kept_claims: list[dict[str, Any]] = []
        for claim in reason_group.get("claims", []):
            eval_res = evaluate_claim_grounding(claim, session)
            if (
                eval_res["citation_valid"]
                and eval_res["claim_supported"]
                and eval_res["numerical_accurate"]
            ):
                kept_claims.append(claim)
            else:
                rejected_count += 1
        if kept_claims:
            kept_reasons.append({"claims": kept_claims})

    return kept_reasons, rejected_count


_CITATION_RE = re.compile(r"\[([A-Za-z_]+:[^\]\s]+)\]")


def check_narrative(text: str, session: ToolSession) -> bool:
    """Return True only if a plain-language narrative is grounded in this session's tool results.

    Requires: non-empty text, at least one `[source:id]` citation, every cited id returned by a tool
    and not erased, and every number in the text present among tool-returned numbers.
    """
    if not text or not text.strip():
        return False
    cited = _CITATION_RE.findall(text)
    if not cited:
        return False
    if any(c not in session.returned_ids or c in session.erased_ids for c in cited):
        return False
    body = _CITATION_RE.sub(" ", text)
    return all(
        _number_matches_set(n, session.returned_numbers)
        for n in _extract_numbers_from_text(body)
    )

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


REASON_NOT_IN_RECORD = "It pointed to evidence that is not in the preserved record."
REASON_ERASED = "It relied on evidence that has since been removed."
REASON_VALUE_MISMATCH = "The value it stated did not match the preserved evidence."
REASON_NUMBER = "It contained a figure that does not appear in the preserved evidence."
REASON_SUMMARY = "The written summary used a source or figure that is not in the preserved evidence."


def rejection_reason(eval_res: dict[str, Any], session: ToolSession) -> str | None:
    """Plain-language reason a claim failed grounding, or None if it passed. Never echoes claim content."""
    if not eval_res["citation_valid"]:
        return REASON_ERASED if eval_res["citation"] in session.erased_ids else REASON_NOT_IN_RECORD
    if not eval_res["claim_supported"]:
        return REASON_VALUE_MISMATCH
    if not eval_res["numerical_accurate"]:
        return REASON_NUMBER
    return None


def apply_claim_guardrails_detailed(
    raw_reasons: list[dict[str, Any]],
    session: ToolSession,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Filter out unsupported claims; return the kept reasons and one plain-language reason per removed claim."""
    kept_reasons: list[dict[str, Any]] = []
    removed: list[str] = []

    for reason_group in raw_reasons:
        kept_claims: list[dict[str, Any]] = []
        for claim in reason_group.get("claims", []):
            why = rejection_reason(evaluate_claim_grounding(claim, session), session)
            if why is None:
                kept_claims.append(claim)
            else:
                removed.append(why)
        if kept_claims:
            kept_reasons.append({"claims": kept_claims})

    return kept_reasons, removed


def apply_claim_guardrails(
    raw_reasons: list[dict[str, Any]],
    session: ToolSession,
) -> tuple[list[dict[str, Any]], int]:
    """Filter out unsupported claims using ToolSession state and count guardrail rejections."""
    kept_reasons, removed = apply_claim_guardrails_detailed(raw_reasons, session)
    return kept_reasons, len(removed)


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

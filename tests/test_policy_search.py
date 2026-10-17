"""Tests for backend/policy_search.py (search_policies and get_policy_as_of)."""

from __future__ import annotations

from datetime import date
import inspect

import pytest

from backend.policy_search import get_policy_as_of, search_policies, split_policy_sections
from data.load_policies import load_policies


@pytest.fixture(scope="module", autouse=True)
def seeded_policies() -> None:
    """Ensure the three policies are loaded into the policies table."""
    load_policies()


def test_stable_signatures() -> None:
    """Verify search_policies(query, as_of_date, top_k=5) and get_policy_as_of(family, as_of) signatures."""
    search_sig = inspect.signature(search_policies)
    params = list(search_sig.parameters.values())
    assert params[0].name == "query"
    assert params[1].name == "as_of_date"
    assert params[2].name == "top_k"
    assert params[2].default == 5

    get_sig = inspect.signature(get_policy_as_of)
    get_params = list(get_sig.parameters.values())
    assert get_params[0].name == "family"
    assert get_params[1].name == "as_of"


def test_dates_before_2026_01_01_never_return_lp_2026_v1() -> None:
    """Dates prior to 2026-01-01 must never return LP-2026-v1 even when querying 2026-specific terms."""
    pre_2026_dates = [
        "2025-01-01",
        "2025-03-15",
        "2025-06-30",
        "2025-07-01",
        "2025-10-20",
        "2025-12-31",
        date(2025, 12, 31),
    ]
    queries = [
        "address proof delinquencies_24m >= 2 manual review LP-2026-v1",
        "debt-to-income limit dti_ratio",
        "minimum credit score 640",
        "required documents",
    ]

    for check_date in pre_2026_dates:
        pol = get_policy_as_of("consumer_loan", check_date)
        assert pol is not None
        assert pol["policy_version"] != "LP-2026-v1"
        assert pol["policy_id"] in ("LP-2025-v1", "LP-2025-v2")

        for q in queries:
            hits = search_policies(q, check_date, top_k=5)
            assert len(hits) > 0
            for hit in hits:
                assert hit["policy_version"] != "LP-2026-v1"
                assert hit["document_id"] != "LP-2026-v1"
                assert set(hit.keys()) >= {
                    "document_id",
                    "section_id",
                    "snippet",
                    "policy_version",
                }


def test_boundary_dates_2025_06_30_and_2025_07_01_return_right_version() -> None:
    """Boundary dates 2025-06-30 and 2025-07-01 return LP-2025-v1 and LP-2025-v2 respectively."""
    pol_june_30 = get_policy_as_of("consumer_loan", "2025-06-30")
    assert pol_june_30 is not None
    assert pol_june_30["policy_id"] == "LP-2025-v1"
    assert pol_june_30["policy_version"] == "LP-2025-v1"

    hits_june_30 = search_policies("debt-to-income limit dti_ratio", "2025-06-30", top_k=5)
    assert len(hits_june_30) == 5
    assert all(h["policy_version"] == "LP-2025-v1" for h in hits_june_30)
    assert hits_june_30[0]["section_id"] == "3.2 Debt-to-income limit"
    assert "0.45" in hits_june_30[0]["snippet"]

    pol_july_01 = get_policy_as_of("consumer_loan", "2025-07-01")
    assert pol_july_01 is not None
    assert pol_july_01["policy_id"] == "LP-2025-v2"
    assert pol_july_01["policy_version"] == "LP-2025-v2"

    hits_july_01 = search_policies("debt-to-income limit dti_ratio", "2025-07-01", top_k=5)
    assert len(hits_july_01) == 5
    assert all(h["policy_version"] == "LP-2025-v2" for h in hits_july_01)
    assert hits_july_01[0]["section_id"] == "3.2 Debt-to-income limit"
    assert "0.40" in hits_july_01[0]["snippet"]

    # Also check 2025-12-31 vs 2026-01-01 boundary
    pol_dec_31 = get_policy_as_of("consumer_loan", "2025-12-31")
    assert pol_dec_31 is not None and pol_dec_31["policy_version"] == "LP-2025-v2"

    pol_jan_01 = get_policy_as_of("consumer_loan", "2026-01-01")
    assert pol_jan_01 is not None and pol_jan_01["policy_version"] == "LP-2026-v1"


def test_tfidf_ranking_by_numbered_section() -> None:
    """Verify numbered section splitting and TF-IDF ranking accuracy across queries."""
    pol_2026 = get_policy_as_of("consumer_loan", "2026-02-15")
    assert pol_2026 is not None
    sections = split_policy_sections(pol_2026["body"])
    section_ids = [s["section_id"] for s in sections]
    assert "3.2 Debt-to-income limit" in section_ids
    assert "3.3 Minimum credit score" in section_ids
    assert "4.1 Required documents" in section_ids
    assert "5.1 Manual review" in section_ids

    credit_hits = search_policies("minimum credit score 640", "2026-02-15", top_k=3)
    assert credit_hits[0]["section_id"] == "3.3 Minimum credit score"
    assert credit_hits[0]["policy_version"] == "LP-2026-v1"
    assert credit_hits[0]["document_id"] == "LP-2026-v1"

    doc_hits = search_policies("required documents income proof address proof", "2026-02-15", top_k=3)
    assert doc_hits[0]["section_id"] == "4.1 Required documents"

    review_hits = search_policies("manual review delinquencies_24m", "2026-02-15", top_k=3)
    assert review_hits[0]["section_id"] == "5.1 Manual review"


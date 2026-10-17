"""Date-scoped policy retrieval and TF-IDF section search over the PostgreSQL policies table."""

from __future__ import annotations

from datetime import date, datetime
import os
import re
from typing import Any

import psycopg
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from data.load_policies import get_db_url, load_policies


_NUMBERED_SECTION_RE = re.compile(
    r"^#{1,6}\s+(?P<section_id>(?P<section_number>\d+(?:\.\d+)+)\s+(?P<section_title>.+?))\s*$"
)


def _coerce_as_of_date(as_of: date | datetime | str) -> date:
    """Convert a date, datetime, or ISO-8601 date/timestamp string into a date."""
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    raw = str(as_of).strip()
    if not raw:
        raise ValueError("as_of date cannot be empty")
    if "T" in raw or " " in raw:
        norm = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        return datetime.fromisoformat(norm).date()
    return date.fromisoformat(raw)


def split_policy_sections(body: str) -> list[dict[str, str]]:
    """Split a policy markdown body into numbered sections (e.g. '3.2 Debt-to-income limit')."""
    lines = body.replace("\r\n", "\n").splitlines()
    sections: list[dict[str, str]] = []
    current_id: str | None = None
    current_num: str | None = None
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = _NUMBERED_SECTION_RE.match(line.strip())
        if match:
            if current_id is not None:
                snippet_body = "\n".join(current_lines).strip()
                snippet = f"{current_id}\n{snippet_body}".strip() if snippet_body else current_id
                sections.append(
                    {
                        "section_id": current_id,
                        "section_number": current_num or "",
                        "section_title": current_title or "",
                        "snippet": snippet,
                    }
                )
            current_id = match.group("section_id").strip()
            current_num = match.group("section_number").strip()
            current_title = match.group("section_title").strip()
            current_lines = []
        else:
            if current_id is not None:
                current_lines.append(line)

    if current_id is not None:
        snippet_body = "\n".join(current_lines).strip()
        snippet = f"{current_id}\n{snippet_body}".strip() if snippet_body else current_id
        sections.append(
            {
                "section_id": current_id,
                "section_number": current_num or "",
                "section_title": current_title or "",
                "snippet": snippet,
            }
        )

    return sections


def _fetch_effective_policy_rows(
    as_of_dt: date,
    *,
    family: str | None = None,
    db_url: str | None = None,
) -> list[tuple[Any, ...]]:
    """Query the policies table for rows effective on as_of_dt, auto-seeding if the table is empty."""
    target_url = get_db_url(db_url)
    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM policies;")
            total_count = int(cur.fetchone()[0])

    if total_count == 0:
        load_policies(db_url=target_url)

    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            if family is not None:
                cur.execute(
                    """
                    SELECT policy_id, policy_family, effective_from, effective_to, text_sha256, body
                    FROM policies
                    WHERE policy_family = %s
                      AND effective_from <= %s
                      AND (effective_to IS NULL OR effective_to >= %s)
                    ORDER BY effective_from DESC, policy_id ASC;
                    """,
                    (family, as_of_dt, as_of_dt),
                )
            else:
                cur.execute(
                    """
                    SELECT policy_id, policy_family, effective_from, effective_to, text_sha256, body
                    FROM policies
                    WHERE effective_from <= %s
                      AND (effective_to IS NULL OR effective_to >= %s)
                    ORDER BY effective_from DESC, policy_id ASC;
                    """,
                    (as_of_dt, as_of_dt),
                )
            return list(cur.fetchall())


def get_policy_as_of(
    family: str,
    as_of: date | datetime | str,
    *,
    db_url: str | None = None,
) -> dict[str, Any] | None:
    """Return the active policy for `family` effective on `as_of`, or None if none is effective."""
    as_of_dt = _coerce_as_of_date(as_of)
    rows = _fetch_effective_policy_rows(as_of_dt, family=family, db_url=db_url)
    if not rows:
        return None

    policy_id, policy_family, eff_from, eff_to, text_sha256, body = rows[0]
    body_str = str(body)
    return {
        "policy_id": str(policy_id),
        "policy_version": str(policy_id),
        "document_id": str(policy_id),
        "policy_family": str(policy_family),
        "effective_from": eff_from.isoformat() if hasattr(eff_from, "isoformat") else str(eff_from),
        "effective_to": (
            eff_to.isoformat()
            if (eff_to and hasattr(eff_to, "isoformat"))
            else (str(eff_to) if eff_to else None)
        ),
        "text_sha256": str(text_sha256),
        "body": body_str,
        "sections": split_policy_sections(body_str),
    }


def search_policies(
    query: str,
    as_of_date: date | datetime | str,
    top_k: int = 5,
    *,
    family: str | None = None,
    db_url: str | None = None,
) -> list[dict[str, Any]]:
    """Search numbered sections of policies effective on `as_of_date`, ranked by TF-IDF similarity.

    Returns a list of dicts containing `document_id`, `section_id`, `snippet`, and `policy_version`.
    """
    search_backend = os.environ.get("SEARCH_BACKEND", "local").lower()
    if search_backend != "local":
        raise ValueError(f"Unsupported SEARCH_BACKEND={search_backend!r}; expected 'local'")

    if top_k <= 0:
        return []

    as_of_dt = _coerce_as_of_date(as_of_date)
    rows = _fetch_effective_policy_rows(as_of_dt, family=family, db_url=db_url)
    if not rows:
        return []

    candidate_chunks: list[dict[str, Any]] = []
    for row in rows:
        policy_id = str(row[0])
        body_str = str(row[5])
        for order_idx, sec in enumerate(split_policy_sections(body_str)):
            candidate_chunks.append(
                {
                    "document_id": policy_id,
                    "section_id": sec["section_id"],
                    "snippet": sec["snippet"],
                    "policy_version": policy_id,
                    "_order": order_idx,
                }
            )

    if not candidate_chunks:
        return []

    clean_query = str(query).strip()
    if not clean_query:
        return [
            {
                "document_id": c["document_id"],
                "section_id": c["section_id"],
                "snippet": c["snippet"],
                "policy_version": c["policy_version"],
            }
            for c in candidate_chunks[:top_k]
        ]

    corpus = [f"{c['section_id']} {c['snippet']}" for c in candidate_chunks]
    vectorizer = TfidfVectorizer(
        lowercase=True,
        token_pattern=r"(?u)\b[a-zA-Z0-9]+(?:\.[0-9]+)?\b",
        ngram_range=(1, 2),
        stop_words="english",
    )
    tfidf_matrix = vectorizer.fit_transform(corpus)
    query_vec = vectorizer.transform([clean_query])
    similarities = cosine_similarity(query_vec, tfidf_matrix)[0]

    ranked = sorted(
        zip(similarities, candidate_chunks),
        key=lambda item: (-float(item[0]), int(item[1]["_order"])),
    )

    results: list[dict[str, Any]] = []
    for sim_score, chunk in ranked[:top_k]:
        results.append(
            {
                "document_id": chunk["document_id"],
                "section_id": chunk["section_id"],
                "snippet": chunk["snippet"],
                "policy_version": chunk["policy_version"],
                "score": round(float(sim_score), 6),
            }
        )
    return results


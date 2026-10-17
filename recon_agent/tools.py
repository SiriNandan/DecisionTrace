"""Preserved-snapshot tool implementations and session tracking for the Reconstruction Agent.

All tools read EXCLUSIVELY from preserved decision-time tables in PostgreSQL
(`decisions`, `decision_snapshots`, `evidence_status`, `overrides`, `policies`, `model_registry`)
and NEVER read live mutable data files in `data/`.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import math
import time
from typing import Any
import uuid

import psycopg

from backend.integrity import verify_chain as verify_hash_chain
from backend.policy_search import (
    get_policy_as_of as query_policy_as_of,
    search_policies as query_search_policies,
)
from backend.scoring import score as compute_model_score
from data.load_policies import get_db_url


DEFAULT_MAX_TOOL_CALLS = 8
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_REPLAY_TOLERANCE = 1e-9


class ToolLimitExceededError(RuntimeError):
    """Raised when a reconstruction run exceeds the configured tool call limit."""


class ReconstructionTimeoutError(TimeoutError):
    """Raised when a reconstruction run exceeds the configured wall-clock timeout."""


def _iso_or_str(value: Any) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _extract_numbers(obj: Any, bucket: set[float]) -> None:
    """Recursively collect finite numbers from tool output structures for guardrail checks."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        val = float(obj)
        if math.isfinite(val):
            bucket.add(val)
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _extract_numbers(v, bucket)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            _extract_numbers(item, bucket)


def fast_db_url(db_url: str | None = None) -> str:
    """Resolve DB_URL from argument, environment, or .env.example."""
    return get_db_url(db_url)


class ToolSession:
    """Tracks tool execution limits, timeout, and returned IDs/values for guardrails."""

    def __init__(
        self,
        *,
        db_url: str | None = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.db_url = fast_db_url(db_url)
        self.max_tool_calls = max_tool_calls
        self.timeout_seconds = timeout_seconds
        self.clock = clock
        self.started_at = self.clock()
        self.deadline = self.started_at + float(timeout_seconds)

        self.tool_call_count: int = 0
        self.tool_trace: list[dict[str, Any]] = []

        # Set of citable IDs returned by tools in this run (excludes erased_by_retention items)
        self.returned_ids: set[str] = set()
        self.available_ids: set[str] = set()
        self.erased_ids: set[str] = set()
        self.returned_policy_sections: set[str] = set()
        self.returned_items_by_id: dict[str, dict[str, Any]] = {}
        self.returned_numbers: set[float] = set()

    def check_time_budget(self) -> None:
        """Raise ReconstructionTimeoutError if the wall-clock deadline has passed."""
        if self.clock() > self.deadline:
            raise ReconstructionTimeoutError(
                f"Reconstruction exceeded timeout of {self.timeout_seconds:.1f}s"
            )

    def _begin_tool_call(self, tool_name: str, args: dict[str, Any]) -> None:
        self.check_time_budget()
        if self.tool_call_count >= self.max_tool_calls:
            raise ToolLimitExceededError(
                f"Tool call limit ({self.max_tool_calls}) exceeded when calling '{tool_name}'"
            )
        self.tool_call_count += 1
        self.tool_trace.append({"tool": tool_name, "args": dict(args)})

    def _finish_tool_call(self, result: dict[str, Any]) -> dict[str, Any]:
        self.check_time_budget()
        _extract_numbers(result, self.returned_numbers)
        if self.tool_trace:
            self.tool_trace[-1]["result"] = result
        return result

    def _record_citable_item(self, item_id: str, payload: dict[str, Any]) -> None:
        if not item_id or item_id in self.erased_ids:
            return
        self.returned_ids.add(item_id)
        self.available_ids.add(item_id)
        existing = self.returned_items_by_id.get(item_id)
        if existing is None:
            self.returned_items_by_id[item_id] = dict(payload)
        else:
            merged = dict(existing)
            merged.update(payload)
            self.returned_items_by_id[item_id] = merged

    def _fetch_evidence_status_map(
        self, cur: psycopg.Cursor[Any], dec_uuid: uuid.UUID
    ) -> dict[str, str]:
        cur.execute(
            """
            SELECT DISTINCT ON (evidence_ref) evidence_ref, status
            FROM evidence_status
            WHERE decision_id = %s
            ORDER BY evidence_ref, changed_at DESC;
            """,
            (dec_uuid,),
        )
        status_map: dict[str, str] = {}
        for ref, st in cur.fetchall():
            ref_str = str(ref)
            st_str = str(st)
            status_map[ref_str] = st_str
            if st_str in ("erased_by_retention", "missing"):
                self.erased_ids.add(ref_str)
                self.returned_ids.discard(ref_str)
                self.available_ids.discard(ref_str)
                self.returned_items_by_id.pop(ref_str, None)
        return status_map

    def get_decision(self, decision_id: str) -> dict[str, Any]:
        """Read preserved decision metadata, human override, and evidence retention status."""
        self._begin_tool_call("get_decision", {"decision_id": decision_id})
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT decision_id, application_id, decided_at, outcome,
                           model_version, policy_version, score, chain_length, chain_head
                    FROM decisions
                    WHERE decision_id = %s;
                    """,
                    (dec_uuid,),
                )
                row = cur.fetchone()
                if row is None:
                    raise KeyError(f"Decision {decision_id} not found")

                (
                    db_dec_id,
                    application_id,
                    decided_at,
                    initial_outcome,
                    model_version,
                    policy_version,
                    score,
                    chain_length,
                    chain_head,
                ) = row

                cur.execute(
                    """
                    SELECT override_id, actor_role, new_outcome, reason, created_at
                    FROM overrides
                    WHERE decision_id = %s
                    ORDER BY created_at DESC, override_id DESC;
                    """,
                    (dec_uuid,),
                )
                override_row = cur.fetchone()

                status_map = self._fetch_evidence_status_map(cur, dec_uuid)

                cur.execute(
                    """
                    SELECT seq, evidence_type, evidence_ref, source_id,
                           source_timestamp, captured_at, payload_sha256, payload
                    FROM decision_snapshots
                    WHERE decision_id = %s
                    ORDER BY seq ASC;
                    """,
                    (dec_uuid,),
                )
                snap_rows = cur.fetchall()

        human_override: dict[str, Any] | None = None
        override_detail: dict[str, Any] | None = None
        effective_outcome = str(initial_outcome)
        if override_row is not None:
            effective_outcome = str(override_row[2])
            human_override = {
                "new_outcome": str(override_row[2]),
                "reason": str(override_row[3]),
                "actor_role": str(override_row[1]),
                "created_at": _iso_or_str(override_row[4]),
            }
            override_detail = {
                "override_id": int(override_row[0]),
                "new_outcome": str(override_row[2]),
                "reason": str(override_row[3]),
                "actor_role": str(override_row[1]),
                "created_at": _iso_or_str(override_row[4]),
            }

        snapshot_summaries: list[dict[str, Any]] = []
        missing_evidence: list[str] = []
        erased_evidence: list[str] = []
        applicant_id = ""

        for seq, ev_type, ev_ref, src_id, src_ts, cap_at, p_sha, payload in snap_rows:
            ref_str = str(ev_ref)
            type_str = str(ev_type)
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            if type_str == "application" and not applicant_id:
                applicant_id = str(payload_dict.get("applicant_id", ""))

            retention_status = status_map.get(ref_str, "available")
            if retention_status in ("erased_by_retention", "missing"):
                self.erased_ids.add(ref_str)
                if ref_str not in erased_evidence:
                    erased_evidence.append(ref_str)
                if ref_str not in missing_evidence:
                    missing_evidence.append(ref_str)
            elif type_str == "document":
                doc_status = str(payload_dict.get("status", "received")).lower()
                if doc_status == "erased_by_retention":
                    self.erased_ids.add(ref_str)
                    if ref_str not in erased_evidence:
                        erased_evidence.append(ref_str)
                    if ref_str not in missing_evidence:
                        missing_evidence.append(ref_str)
                else:
                    self._record_citable_item(ref_str, payload_dict)
                    if doc_status in ("missing", "processing"):
                        if ref_str not in missing_evidence:
                            missing_evidence.append(ref_str)
            else:
                self._record_citable_item(ref_str, payload_dict)
                if type_str == "application" and applicant_id:
                    self._record_citable_item(f"application:{applicant_id}", payload_dict)
                elif type_str == "policy":
                    self._record_citable_item(str(policy_version), payload_dict)
                    for sec_id in payload_dict.get("cited_sections") or []:
                        self.returned_policy_sections.add(str(sec_id))
                        self._record_citable_item(str(sec_id), payload_dict)
                elif type_str == "rule_check":
                    sec_str = str(payload_dict.get("section", ""))
                    if sec_str:
                        self.returned_policy_sections.add(sec_str)
                        self._record_citable_item(sec_str, payload_dict)

            snapshot_summaries.append(
                {
                    "seq": int(seq),
                    "evidence_type": type_str,
                    "evidence_ref": ref_str,
                    "source_id": str(src_id),
                    "source_timestamp": _iso_or_str(src_ts),
                    "captured_at": _iso_or_str(cap_at),
                    "payload_sha256": str(p_sha),
                    "retention_status": retention_status,
                }
            )

        for ref_str, st_str in status_map.items():
            if st_str in ("erased_by_retention", "missing"):
                if ref_str not in erased_evidence:
                    erased_evidence.append(ref_str)
                if ref_str not in missing_evidence:
                    missing_evidence.append(ref_str)

        expected_total = max(int(chain_length), len(snap_rows), 1)
        unavailable_count = len(missing_evidence) + max(0, int(chain_length) - len(snap_rows))
        completeness = round(
            max(0.0, min(1.0, (expected_total - unavailable_count) / float(expected_total))),
            6,
        )

        result = {
            "decision_id": str(db_dec_id),
            "application_id": str(application_id),
            "applicant_id": applicant_id,
            "decided_at": _iso_or_str(decided_at),
            "outcome": str(initial_outcome),
            "original_outcome": str(initial_outcome),
            "initial_outcome": str(initial_outcome),
            "effective_outcome": effective_outcome,
            "model_version": str(model_version),
            "policy_version": str(policy_version),
            "score": float(score),
            "chain_length": int(chain_length),
            "chain_head": str(chain_head),
            "override": override_detail,
            "human_override": human_override,
            "evidence_status": status_map,
            "erased_evidence": erased_evidence,
            "missing_evidence": missing_evidence,
            "evidence_unavailable": len(erased_evidence) > 0,
            "completeness": completeness,
            "snapshots": snapshot_summaries,
        }
        return self._finish_tool_call(result)

    def get_snapshot(
        self,
        decision_id: str,
        evidence_ref: str | None = None,
    ) -> dict[str, Any]:
        """Read preserved snapshot payload(s) for `decision_id`.

        If `evidence_ref` is provided (exact ref like 'application:APP-0011' or evidence_type
        like 'application'), returns the matching snapshot(s). If `evidence_ref` is None or
        'all', returns all available preserved snapshots for the decision.
        Snapshots marked `erased_by_retention` in `evidence_status` are redacted (`payload=None`)
        and never added to `returned_ids`.
        """
        self._begin_tool_call(
            "get_snapshot",
            {"decision_id": decision_id, "evidence_ref": evidence_ref},
        )
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                status_map = self._fetch_evidence_status_map(cur, dec_uuid)
                cur.execute(
                    """
                    SELECT seq, evidence_type, evidence_ref, source_id,
                           source_timestamp, captured_at, payload_sha256, payload
                    FROM decision_snapshots
                    WHERE decision_id = %s
                    ORDER BY seq ASC;
                    """,
                    (dec_uuid,),
                )
                rows = cur.fetchall()

        target = (evidence_ref or "").strip()
        filter_all = target in ("", "*", "all")

        matched_snapshots: list[dict[str, Any]] = []
        for seq, ev_type, ev_ref, src_id, src_ts, cap_at, p_sha, payload in rows:
            ref_str = str(ev_ref)
            type_str = str(ev_type)
            if not filter_all and ref_str != target and type_str != target:
                continue

            retention_status = status_map.get(ref_str, "available")
            payload_dict = dict(payload) if isinstance(payload, dict) else {}
            is_erased = (
                retention_status in ("erased_by_retention", "missing")
                or str(payload_dict.get("status", "")).lower() == "erased_by_retention"
            )

            if is_erased:
                self.erased_ids.add(ref_str)
                self.returned_ids.discard(ref_str)
                self.available_ids.discard(ref_str)
                self.returned_items_by_id.pop(ref_str, None)
                matched_snapshots.append(
                    {
                        "seq": int(seq),
                        "evidence_type": type_str,
                        "evidence_ref": ref_str,
                        "source_id": str(src_id),
                        "source_timestamp": _iso_or_str(src_ts),
                        "captured_at": _iso_or_str(cap_at),
                        "payload_sha256": str(p_sha),
                        "retention_status": (
                            retention_status
                            if retention_status != "available"
                            else "erased_by_retention"
                        ),
                        "payload": None,
                        "error": "Evidence unavailable (erased_by_retention)",
                    }
                )
            else:
                self._record_citable_item(ref_str, payload_dict)
                if type_str == "application" and payload_dict.get("applicant_id"):
                    self._record_citable_item(
                        f"application:{payload_dict['applicant_id']}", payload_dict
                    )
                if type_str == "policy":
                    pol_id = str(
                        payload_dict.get("policy_id")
                        or payload_dict.get("policy_version")
                        or ""
                    )
                    if pol_id:
                        self._record_citable_item(pol_id, payload_dict)
                matched_snapshots.append(
                    {
                        "seq": int(seq),
                        "evidence_type": type_str,
                        "evidence_ref": ref_str,
                        "source_id": str(src_id),
                        "source_timestamp": _iso_or_str(src_ts),
                        "captured_at": _iso_or_str(cap_at),
                        "payload_sha256": str(p_sha),
                        "retention_status": retention_status,
                        "payload": payload_dict,
                    }
                )

        if not filter_all and len(matched_snapshots) == 1:
            result: dict[str, Any] = {
                "decision_id": str(decision_id),
                "snapshot": matched_snapshots[0],
                "snapshots": matched_snapshots,
            }
        else:
            result = {
                "decision_id": str(decision_id),
                "snapshots": matched_snapshots,
            }
        return self._finish_tool_call(result)

    def verify_chain(self, decision_id: str) -> dict[str, Any]:
        """Verify the preserved hash chain against the retained chain_length and chain_head."""
        self._begin_tool_call("verify_chain", {"decision_id": decision_id})
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chain_length, chain_head
                    FROM decisions
                    WHERE decision_id = %s;
                    """,
                    (dec_uuid,),
                )
                dec_row = cur.fetchone()
                if dec_row is None:
                    raise KeyError(f"Decision {decision_id} not found")
                expected_length, expected_head = int(dec_row[0]), str(dec_row[1])

                cur.execute(
                    """
                    SELECT decision_id, seq, evidence_type, evidence_ref, source_id,
                           source_timestamp, captured_at, payload, payload_sha256,
                           prev_hash, entry_hash
                    FROM decision_snapshots
                    WHERE decision_id = %s
                    ORDER BY seq ASC;
                    """,
                    (dec_uuid,),
                )
                rows = cur.fetchall()

        chain_rows = [
            {
                "decision_id": str(r[0]),
                "seq": int(r[1]),
                "evidence_type": str(r[2]),
                "evidence_ref": str(r[3]),
                "source_id": str(r[4]),
                "source_timestamp": _iso_or_str(r[5]),
                "captured_at": _iso_or_str(r[6]),
                "payload": r[7],
                "payload_sha256": str(r[8]),
                "prev_hash": str(r[9]),
                "entry_hash": str(r[10]),
            }
            for r in rows
        ]

        ok, first_broken_seq, reason = verify_hash_chain(
            chain_rows,
            expected_length,
            expected_head,
        )
        result = {
            "decision_id": str(decision_id),
            "chain_verified": bool(ok),
            "first_broken_seq": first_broken_seq,
            "reason": reason,
            "expected_length": expected_length,
            "actual_length": len(chain_rows),
            "expected_head": expected_head,
        }
        return self._finish_tool_call(result)

    def get_policy_as_of(
        self,
        family: str = "consumer_loan",
        as_of: str = "",
    ) -> dict[str, Any]:
        """Return the policy document and numbered sections effective on `as_of`."""
        self._begin_tool_call(
            "get_policy_as_of",
            {"family": family, "as_of": as_of},
        )
        policy = query_policy_as_of(family, as_of, db_url=self.db_url)
        if policy is None:
            return self._finish_tool_call(
                {"policy": None, "error": f"No policy found for family={family!r} as_of={as_of!r}"}
            )

        pol_id = str(policy["policy_id"])
        pol_meta = {
            "policy_id": pol_id,
            "policy_version": pol_id,
            "policy_family": policy["policy_family"],
            "effective_from": policy["effective_from"],
            "effective_to": policy["effective_to"],
            "text_sha256": policy["text_sha256"],
        }
        self._record_citable_item(pol_id, pol_meta)
        self._record_citable_item(f"policy:{pol_id}", pol_meta)
        for sec in policy.get("sections", []):
            sec_id = str(sec["section_id"])
            self.returned_policy_sections.add(sec_id)
            self._record_citable_item(sec_id, dict(sec))

        return self._finish_tool_call({"policy": policy})

    def search_policies(
        self,
        query: str,
        as_of_date: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Search numbered sections of policies effective on `as_of_date` using TF-IDF."""
        self._begin_tool_call(
            "search_policies",
            {"query": query, "as_of_date": as_of_date, "top_k": top_k},
        )
        matches = query_search_policies(
            query=query,
            as_of_date=as_of_date,
            top_k=int(top_k),
            db_url=self.db_url,
        )
        for match in matches:
            doc_id = str(match["document_id"])
            sec_id = str(match["section_id"])
            self.returned_policy_sections.add(sec_id)
            self._record_citable_item(sec_id, dict(match))
            self._record_citable_item(doc_id, {"policy_version": doc_id, "document_id": doc_id})
            self._record_citable_item(
                f"policy:{doc_id}", {"policy_version": doc_id, "document_id": doc_id}
            )

        return self._finish_tool_call(
            {
                "query": query,
                "as_of_date": as_of_date,
                "results": matches,
            }
        )

    def get_rule_checks(self, decision_id: str) -> dict[str, Any]:
        """Return all preserved `rule_check` snapshots recorded for `decision_id`."""
        self._begin_tool_call("get_rule_checks", {"decision_id": decision_id})
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                status_map = self._fetch_evidence_status_map(cur, dec_uuid)
                cur.execute(
                    """
                    SELECT seq, evidence_ref, payload
                    FROM decision_snapshots
                    WHERE decision_id = %s AND evidence_type = 'rule_check'
                    ORDER BY seq ASC;
                    """,
                    (dec_uuid,),
                )
                rows = cur.fetchall()

        rule_checks: list[dict[str, Any]] = []
        for _seq, ev_ref, payload in rows:
            ref_str = str(ev_ref)
            if status_map.get(ref_str) in ("erased_by_retention", "missing"):
                self.erased_ids.add(ref_str)
                continue
            p_dict = dict(payload) if isinstance(payload, dict) else {}
            passed_val = bool(
                p_dict.get("passed")
                if "passed" in p_dict
                else (str(p_dict.get("result", "")).upper() == "PASS")
            )
            section_str = str(p_dict.get("section", ""))
            rule_entry = {
                "rule_id": str(p_dict.get("rule_id", "")),
                "observed_value": p_dict.get("observed_value"),
                "operator": str(p_dict.get("operator", "")),
                "threshold": p_dict.get("threshold"),
                "passed": passed_val,
                "section": section_str,
                "evidence_ref": ref_str,
            }
            rule_checks.append(rule_entry)

            # Record both the rule evidence_ref and the policy section with threshold fields
            enriched_payload = dict(p_dict)
            enriched_payload.update(rule_entry)
            rule_id = rule_entry["rule_id"]
            if rule_id:
                enriched_payload[rule_id] = rule_entry["threshold"]
                enriched_payload[f"{rule_id}_threshold"] = rule_entry["threshold"]
            self._record_citable_item(ref_str, enriched_payload)
            if section_str:
                self.returned_policy_sections.add(section_str)
                self._record_citable_item(section_str, enriched_payload)

        return self._finish_tool_call(
            {
                "decision_id": str(decision_id),
                "rule_checks": rule_checks,
            }
        )

    def get_model_contributions(self, decision_id: str) -> dict[str, Any]:
        """Return the preserved `model` snapshot containing score, features, and log-odds contributions."""
        self._begin_tool_call("get_model_contributions", {"decision_id": decision_id})
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                status_map = self._fetch_evidence_status_map(cur, dec_uuid)
                cur.execute(
                    """
                    SELECT seq, evidence_ref, payload
                    FROM decision_snapshots
                    WHERE decision_id = %s AND evidence_type = 'model'
                    ORDER BY seq ASC
                    LIMIT 1;
                    """,
                    (dec_uuid,),
                )
                row = cur.fetchone()

        if row is None:
            return self._finish_tool_call(
                {
                    "decision_id": str(decision_id),
                    "model_snapshot": None,
                    "error": "No preserved model snapshot found",
                }
            )

        seq, ev_ref, payload = row
        ref_str = str(ev_ref)
        if status_map.get(ref_str) in ("erased_by_retention", "missing"):
            self.erased_ids.add(ref_str)
            return self._finish_tool_call(
                {
                    "decision_id": str(decision_id),
                    "evidence_ref": ref_str,
                    "model_snapshot": None,
                    "error": "Model snapshot erased_by_retention",
                }
            )

        p_dict = dict(payload) if isinstance(payload, dict) else {}
        model_score = float(p_dict.get("score", p_dict.get("probability", 0.0)))
        flat_fields = dict(p_dict)
        flat_fields["score"] = model_score
        if isinstance(p_dict.get("contributions"), dict):
            for k, v in p_dict["contributions"].items():
                flat_fields[str(k)] = v
        if isinstance(p_dict.get("features"), dict):
            for k, v in p_dict["features"].items():
                flat_fields.setdefault(str(k), v)

        self._record_citable_item(ref_str, flat_fields)

        return self._finish_tool_call(
            {
                "decision_id": str(decision_id),
                "seq": int(seq),
                "evidence_ref": ref_str,
                "model_version": str(p_dict.get("model_version", "")),
                "score": model_score,
                "approve_score_threshold": p_dict.get("approve_score_threshold"),
                "features": p_dict.get("features", {}),
                "contributions": p_dict.get("contributions", {}),
                "artifact_sha256": p_dict.get("artifact_sha256"),
            }
        )

    def replay_score(
        self,
        decision_id: str,
        tolerance: float = DEFAULT_REPLAY_TOLERANCE,
    ) -> dict[str, Any]:
        """Recompute the model score from the stored model_registry row and preserved inputs.

        Compares the recomputed probability with the stored decision score within `tolerance`.
        Reads input features from the preserved `application` and `bureau` snapshots so that
        any tampering of preserved input snapshots is detected on replay.
        """
        self._begin_tool_call(
            "replay_score",
            {"decision_id": decision_id, "tolerance": tolerance},
        )
        dec_uuid = uuid.UUID(str(decision_id))

        with psycopg.connect(self.db_url, connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT model_version, score
                    FROM decisions
                    WHERE decision_id = %s;
                    """,
                    (dec_uuid,),
                )
                dec_row = cur.fetchone()
                if dec_row is None:
                    raise KeyError(f"Decision {decision_id} not found")
                model_version, stored_score = str(dec_row[0]), float(dec_row[1])

                cur.execute(
                    """
                    SELECT model_version, coefficients, intercept, feature_means, feature_stds,
                           feature_order, preprocessing_version, artifact_sha256
                    FROM model_registry
                    WHERE model_version = %s;
                    """,
                    (model_version,),
                )
                reg_row = cur.fetchone()

                cur.execute(
                    """
                    SELECT evidence_type, evidence_ref, payload
                    FROM decision_snapshots
                    WHERE decision_id = %s
                      AND evidence_type IN ('application', 'bureau', 'model')
                    ORDER BY seq ASC;
                    """,
                    (dec_uuid,),
                )
                snap_rows = cur.fetchall()

        if reg_row is None:
            return self._finish_tool_call(
                {
                    "decision_id": str(decision_id),
                    "replay_verified": False,
                    "replay_tolerance": float(tolerance),
                    "stored_score": stored_score,
                    "recomputed_score": None,
                    "score_diff": None,
                    "error": f"Model version {model_version!r} not found in model_registry",
                }
            )

        registry_dict = {
            "model_version": str(reg_row[0]),
            "coefficients": reg_row[1],
            "intercept": float(reg_row[2]),
            "means": reg_row[3],
            "stds": reg_row[4],
            "feature_means": reg_row[3],
            "feature_stds": reg_row[4],
            "feature_order": list(reg_row[5]),
            "preprocessing_version": str(reg_row[6]),
            "artifact_sha256": str(reg_row[7]),
        }

        app_payload: dict[str, Any] = {}
        bureau_payload: dict[str, Any] = {}
        model_payload: dict[str, Any] = {}
        for ev_type, _ev_ref, payload in snap_rows:
            p_dict = dict(payload) if isinstance(payload, dict) else {}
            if ev_type == "application" and not app_payload:
                app_payload = p_dict
            elif ev_type == "bureau" and not bureau_payload:
                bureau_payload = p_dict
            elif ev_type == "model" and not model_payload:
                model_payload = p_dict

        fallback_features = (
            dict(model_payload.get("features", {}))
            if isinstance(model_payload.get("features"), dict)
            else {}
        )

        # Prefer preserved application + bureau snapshots so payload tampering is caught
        features: dict[str, float] = {
            "income_annual": float(
                app_payload.get(
                    "income_annual",
                    app_payload.get("annual_income", fallback_features.get("income_annual", 0.0)),
                )
            ),
            "requested_amount": float(
                app_payload.get("requested_amount", fallback_features.get("requested_amount", 0.0))
            ),
            "dti_ratio": float(
                app_payload.get("dti_ratio", fallback_features.get("dti_ratio", 0.0))
            ),
            "credit_score": float(
                bureau_payload.get("credit_score", fallback_features.get("credit_score", 0.0))
            ),
            "delinquencies_24m": float(
                bureau_payload.get(
                    "delinquencies_24m", fallback_features.get("delinquencies_24m", 0.0)
                )
            ),
            "account_age_months": float(
                bureau_payload.get(
                    "account_age_months", fallback_features.get("account_age_months", 0.0)
                )
            ),
            "employment_months": float(
                app_payload.get(
                    "employment_months", fallback_features.get("employment_months", 0.0)
                )
            ),
        }

        recomputed_prob, recomputed_contributions = compute_model_score(features, registry_dict)
        recomputed_rounded = round(float(recomputed_prob), 6)
        if "probability" in model_payload and model_payload["probability"] is not None:
            effective_diff = abs(float(recomputed_prob) - float(model_payload["probability"]))
        else:
            diff_raw = abs(float(recomputed_prob) - stored_score)
            diff_rounded = abs(recomputed_rounded - stored_score)
            effective_diff = min(diff_raw, diff_rounded)
        verified = bool(effective_diff <= float(tolerance))

        return self._finish_tool_call(
            {
                "decision_id": str(decision_id),
                "model_version": model_version,
                "stored_score": stored_score,
                "recomputed_score": recomputed_rounded,
                "raw_recomputed_score": float(recomputed_prob),
                "score_diff": float(effective_diff),
                "replay_tolerance": float(tolerance),
                "replay_verified": verified,
                "features_used": features,
                "recomputed_contributions": recomputed_contributions,
            }
        )


def get_decision(
    decision_id: str,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.get_decision."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.get_decision(decision_id)


def get_snapshot(
    decision_id: str,
    evidence_ref: str | None = None,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.get_snapshot."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.get_snapshot(decision_id, evidence_ref)


def verify_chain(
    decision_id: str,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.verify_chain."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.verify_chain(decision_id)


def get_policy_as_of(
    family: str = "consumer_loan",
    as_of: str = "",
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.get_policy_as_of."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.get_policy_as_of(family, as_of)


def search_policies(
    query: str,
    as_of_date: str,
    top_k: int = 5,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.search_policies."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.search_policies(query, as_of_date, top_k=top_k)


def get_rule_checks(
    decision_id: str,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.get_rule_checks."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.get_rule_checks(decision_id)


def get_model_contributions(
    decision_id: str,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.get_model_contributions."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.get_model_contributions(decision_id)


def replay_score(
    decision_id: str,
    tolerance: float = DEFAULT_REPLAY_TOLERANCE,
    *,
    session: ToolSession | None = None,
    db_url: str | None = None,
) -> dict[str, Any]:
    """Standalone wrapper around ToolSession.replay_score."""
    active_session = session or ToolSession(db_url=db_url)
    return active_session.replay_score(decision_id, tolerance=tolerance)


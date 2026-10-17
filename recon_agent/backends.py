"""LLMBackend interface and implementations ('gemini_api' and 'fake') for DecisionTrace.

Uses the current Google Gen AI SDK (`from google import genai` and `from google.genai import types`)
with function calling for `'gemini_api'`, and a deterministic scripted backend for `'fake'`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import re
from typing import Any

from google import genai
from google.genai import types

from backend.contract import ReconstructionReport
from recon_agent.tools import (
    DEFAULT_REPLAY_TOLERANCE,
    ReconstructionTimeoutError,
    ToolLimitExceededError,
    ToolSession,
)


SYSTEM_PROMPT = """You are the DecisionTrace Reconstruction Agent.
Your role is to reconstruct a grounded explanation of a historical loan decision that supports audit and explanation and is tamper-evident against a retained reference.

Strict Rules:
1. Explain ONLY from tool results returned in this session. Never read or assume current live applicant data.
2. Every claim in `reasons[].claims[]` MUST carry `citation`, `field`, and `value` copied directly from a tool result returned in this session.
3. Never state a number in any claim or reason that was not returned in a tool result.
4. List all missing or erased evidence references in `missing_evidence`. Never cite an evidence item marked `erased_by_retention` in a claim.
5. Do NOT infer which policy rule fired; use ONLY the recorded `rule_checks` returned by `get_rule_checks`.
6. If `evidence_status` or `get_decision` marks needed evidence as erased (`erased_by_retention`) or missing, set `status` to `"evidence_unavailable"` and explain only what remains.
7. Use governance wording: state that the record "supports audit and explanation" and is "tamper-evident against a retained reference". Never claim it "ensures compliance" or "proves" compliance.
8. Stay within 8 tool calls total and return a single valid JSON object matching the `ReconstructionReport` schema.
"""


def _is_placeholder_key(value: str) -> bool:
    """True for template values such as the `.env.example` placeholder, which are not real keys."""
    lowered = value.strip().strip("'").strip('"').lower()
    return lowered.startswith("your_") or "api_key_here" in lowered or lowered in {"changeme", "xxx"}


def _resolve_gemini_api_key(explicit_key: str | None = None) -> str | None:
    """Resolve GEMINI_API_KEY from argument, environment, or local .env file (ignoring placeholders)."""
    if explicit_key and explicit_key.strip():
        return explicit_key.strip()

    for env_name in ("GEMINI_API_KEY", "GEMINI_API_KEY:", "GOOGLE_API_KEY"):
        val = os.environ.get(env_name)
        if val and val.strip() and not _is_placeholder_key(val):
            return val.strip()

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            if (
                k.strip().rstrip(":") in ("GEMINI_API_KEY", "GOOGLE_API_KEY")
                and v.strip()
                and not _is_placeholder_key(v)
            ):
                return v.strip().strip("'").strip('"')
    return None


def _extract_json_dict(raw_text: str) -> dict[str, Any]:
    """Extract a JSON object from model text output (handling optional markdown code fences)."""
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()
    else:
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            cleaned = cleaned[first_brace : last_brace + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object at top level of ReconstructionReport")
    return parsed


def synthesize_report_from_session_data(
    *,
    decision_id: str,
    dec_info: dict[str, Any],
    chain_info: dict[str, Any],
    snaps_info: dict[str, Any],
    rules_info: dict[str, Any],
    model_info: dict[str, Any],
    replay_info: dict[str, Any],
    session: ToolSession,
) -> dict[str, Any]:
    """Synthesize a strictly grounded ReconstructionReport dict from preserved tool results."""
    policy_version = str(dec_info.get("policy_version", ""))
    effective_outcome = str(dec_info.get("effective_outcome") or dec_info.get("outcome"))
    initial_outcome = str(dec_info.get("initial_outcome") or effective_outcome)
    score = float(dec_info.get("score", 0.0))
    chain_verified = bool(chain_info.get("chain_verified", False))
    replay_verified = bool(replay_info.get("replay_verified", False))
    replay_tolerance = float(replay_info.get("replay_tolerance", DEFAULT_REPLAY_TOLERANCE))
    missing_evidence = list(dec_info.get("missing_evidence", []))
    erased_evidence = list(dec_info.get("erased_evidence", []))
    completeness = float(dec_info.get("completeness", 1.0))
    human_override = dec_info.get("human_override")

    if not chain_verified and completeness == 1.0:
        actual_len = int(chain_info.get("actual_length", 0))
        expected_len = int(chain_info.get("expected_length", 0))
        if expected_len > 0 and actual_len < expected_len:
            completeness = round(max(0.0, actual_len / float(expected_len)), 6)

    snapshots_by_type: dict[str, dict[str, Any]] = {}
    documents_by_ref: dict[str, dict[str, Any]] = {}
    for snap in snaps_info.get("snapshots", []):
        if snap.get("payload") is None:
            continue
        ev_type = str(snap.get("evidence_type", ""))
        ev_ref = str(snap.get("evidence_ref", ""))
        if ev_ref in session.erased_ids or ev_ref not in session.returned_ids:
            continue
        if ev_type == "document":
            documents_by_ref[ev_ref] = snap
        elif ev_type not in snapshots_by_type:
            snapshots_by_type[ev_type] = snap

    app_snap = snapshots_by_type.get("application")
    bureau_snap = snapshots_by_type.get("bureau")

    raw_rules = rules_info.get("rule_checks", [])
    contract_rule_checks: list[dict[str, Any]] = []
    for r in raw_rules:
        contract_rule_checks.append(
            {
                "rule_id": str(r["rule_id"]),
                "observed_value": r["observed_value"],
                "operator": str(r["operator"]),
                "threshold": r["threshold"],
                "passed": bool(r["passed"]),
                "section": str(r["section"]),
                "evidence_ref": str(r["evidence_ref"]),
            }
        )

    failed_rules = [r for r in contract_rule_checks if not r["passed"]]
    if failed_rules:
        active_rules = failed_rules
    else:
        # For clean approvals, include the core eligibility rules (sections 3.2, 3.3, 4.1)
        core_rules = [
            r
            for r in contract_rule_checks
            if r["rule_id"] in ("dti_limit", "min_credit_score", "required_documents")
        ]
        active_rules = core_rules if core_rules else contract_rule_checks

    principal_reasons: list[str] = []
    reasons: list[dict[str, Any]] = []

    for rule in active_rules:
        rule_id = rule["rule_id"]
        obs = rule["observed_value"]
        op = rule["operator"]
        thresh = rule["threshold"]
        passed = rule["passed"]
        section = rule["section"]
        rule_ref = rule["evidence_ref"]

        claims: list[dict[str, Any]] = []

        # 1. Input snapshot claim (only if available and not erased)
        if rule_id == "dti_limit" and app_snap is not None:
            app_ref = str(app_snap["evidence_ref"])
            app_payload = app_snap["payload"]
            if "dti_ratio" in app_payload and app_ref in session.returned_ids:
                dti_val = app_payload["dti_ratio"]
                claims.append(
                    {
                        "text": f"Preserved application snapshot {app_ref} recorded dti_ratio of {dti_val}.",
                        "citation": app_ref,
                        "field": "dti_ratio",
                        "value": dti_val,
                    }
                )
        elif rule_id == "min_credit_score" and bureau_snap is not None:
            bureau_ref = str(bureau_snap["evidence_ref"])
            bureau_payload = bureau_snap["payload"]
            if "credit_score" in bureau_payload and bureau_ref in session.returned_ids:
                cs_val = bureau_payload["credit_score"]
                claims.append(
                    {
                        "text": f"Preserved bureau snapshot {bureau_ref} recorded credit_score of {cs_val}.",
                        "citation": bureau_ref,
                        "field": "credit_score",
                        "value": cs_val,
                    }
                )
        elif rule_id == "manual_review_delinquencies_24m" and bureau_snap is not None:
            bureau_ref = str(bureau_snap["evidence_ref"])
            bureau_payload = bureau_snap["payload"]
            if "delinquencies_24m" in bureau_payload and bureau_ref in session.returned_ids:
                delinq_val = bureau_payload["delinquencies_24m"]
                claims.append(
                    {
                        "text": f"Preserved bureau snapshot {bureau_ref} recorded delinquencies_24m of {delinq_val}.",
                        "citation": bureau_ref,
                        "field": "delinquencies_24m",
                        "value": delinq_val,
                    }
                )
        elif rule_id == "required_documents":
            for doc_ref, doc_snap in documents_by_ref.items():
                doc_payload = doc_snap["payload"]
                if "status" in doc_payload and doc_ref in session.returned_ids:
                    doc_st = doc_payload["status"]
                    claims.append(
                        {
                            "text": f"Preserved document snapshot {doc_ref} recorded status '{doc_st}'.",
                            "citation": doc_ref,
                            "field": "status",
                            "value": doc_st,
                        }
                    )

        # 2. Recorded rule_check snapshot claim
        if rule_ref in session.returned_ids:
            claims.append(
                {
                    "text": (
                        f"Rule check {rule_id} observed {obs} {op} threshold {thresh} "
                        f"(passed={passed})."
                    ),
                    "citation": rule_ref,
                    "field": "observed_value",
                    "value": obs,
                }
            )

        # 3. Cited policy section claim
        if section in session.returned_ids:
            claims.append(
                {
                    "text": (
                        f"Policy {policy_version} section '{section}' specifies threshold "
                        f"{thresh} for rule {rule_id}."
                    ),
                    "citation": section,
                    "field": "threshold",
                    "value": thresh,
                }
            )

        if claims:
            reasons.append({"claims": claims})

        status_word = "satisfied" if passed else "failed"
        principal_reasons.append(
            f"Rule {rule_id} ({section}) {status_word}: observed {obs} {op} {thresh}."
        )

    # Include section "5.1 Manual review" when referral or human override applies
    manual_review_section = "5.1 Manual review"
    already_cited_manual = any(
        c.get("citation") == manual_review_section
        for r in reasons
        for c in r.get("claims", [])
    )
    if (
        (effective_outcome == "REFER" or initial_outcome == "REFER" or human_override is not None)
        and not already_cited_manual
        and manual_review_section in session.returned_ids
    ):
        reasons.append(
            {
                "claims": [
                    {
                        "text": (
                            f"Policy {policy_version} section '{manual_review_section}' governs "
                            f"manual underwriter review for referred or overridden decisions."
                        ),
                        "citation": manual_review_section,
                        "field": "section_id",
                        "value": manual_review_section,
                    }
                ]
            }
        )
        if human_override is not None:
            principal_reasons.append(
                f"Initial outcome {initial_outcome} was overridden to "
                f"{human_override['new_outcome']} under {manual_review_section}."
            )
        else:
            principal_reasons.append(
                f"Application routed to {effective_outcome} under {manual_review_section}."
            )

    if model_info.get("evidence_ref") in session.returned_ids:
        model_ref = str(model_info["evidence_ref"])
        model_score = float(model_info.get("score", score))
        reasons.append(
            {
                "claims": [
                    {
                        "text": f"Preserved model snapshot {model_ref} recorded probability score {model_score}.",
                        "citation": model_ref,
                        "field": "score",
                        "value": model_score,
                    }
                ]
            }
        )

    has_erased = bool(dec_info.get("evidence_unavailable")) or len(erased_evidence) > 0
    status_val = "evidence_unavailable" if has_erased else "ok"

    integrity_clause = (
        "hash chain verified against retained reference"
        if chain_verified
        else f"hash chain verification failed ({chain_info.get('reason') or 'tamper_detected'})"
    )
    policy_note = (
        f"Evaluated under policy {policy_version}; {integrity_clause}. "
        f"This reconstruction supports audit and explanation and is tamper-evident against a retained reference."
    )

    return {
        "decision_id": str(decision_id),
        "outcome": effective_outcome,
        "score": score,
        "chain_verified": chain_verified,
        "replay_verified": replay_verified,
        "replay_tolerance": replay_tolerance,
        "completeness": completeness,
        "missing_evidence": missing_evidence,
        "principal_reasons": principal_reasons,
        "rule_checks": contract_rule_checks,
        "reasons": reasons,
        "policy_note": policy_note,
        "human_override": human_override,
        "guardrail_rejections": 0,
        "status": status_val,
    }


class LLMBackend(ABC):
    """Abstract interface for DecisionTrace reconstruction LLM backends."""

    backend_name: str

    @abstractmethod
    def generate_report(
        self,
        decision_id: str,
        session: ToolSession,
        *,
        attempt: int = 1,
        validation_error: str | None = None,
    ) -> dict[str, Any]:
        """Run tool calls via `session` and return a raw dict for `ReconstructionReport` validation."""

    def generate_summary(self, report: dict[str, Any], session: ToolSession) -> str | None:
        """Optionally write a plain-language explanation of the verified `report`; None = not offered.

        The service runs the result through the grounding guardrail before attaching it.
        """
        return None

    def generate_signal_why(self, kind: str, facts: dict[str, Any]) -> str | None:
        """Optionally write one plain sentence on why a rule-based signal fired; None = use the rule's own sentence."""
        return None


class FakeLLMBackend(LLMBackend):
    """Deterministic scripted backend executing preserved-snapshot tools for tests and offline use."""

    backend_name = "fake"

    def __init__(
        self,
        *,
        scripted_calls: list[tuple[str, dict[str, Any]]] | None = None,
        scripted_outputs: list[dict[str, Any] | str | Exception] | None = None,
    ) -> None:
        self.scripted_calls = scripted_calls
        self.scripted_outputs = list(scripted_outputs) if scripted_outputs is not None else None
        self.call_attempts: int = 0

    def generate_report(
        self,
        decision_id: str,
        session: ToolSession,
        *,
        attempt: int = 1,
        validation_error: str | None = None,
    ) -> dict[str, Any]:
        self.call_attempts += 1
        session.check_time_budget()

        if self.scripted_calls is not None:
            for tool_name, tool_kwargs in self.scripted_calls:
                fn = getattr(session, tool_name)
                fn(**tool_kwargs)
        else:
            # Execute the 8 preserved-snapshot tools in order (8 tool calls total)
            dec_info = session.get_decision(decision_id)
            chain_info = session.verify_chain(decision_id)
            snaps_info = session.get_snapshot(decision_id, "all")
            rules_info = session.get_rule_checks(decision_id)
            model_info = session.get_model_contributions(decision_id)
            replay_info = session.replay_score(decision_id)
            session.get_policy_as_of("consumer_loan", dec_info["decided_at"])
            session.search_policies(
                "debt-to-income limit minimum credit score required documents manual review",
                dec_info["decided_at"],
                top_k=5,
            )

        if self.scripted_outputs is not None and len(self.scripted_outputs) > 0:
            idx = min(attempt - 1, len(self.scripted_outputs) - 1)
            item = self.scripted_outputs[idx]
            if isinstance(item, Exception):
                raise item
            if isinstance(item, str):
                return _extract_json_dict(item)
            return dict(item)

        # If custom scripted_calls ran, make sure we still have data to synthesize if needed
        if self.scripted_calls is not None:
            # Pull from recorded tool trace or return minimal dict
            trace_by_name = {t["tool"]: t.get("result", {}) for t in session.tool_trace}
            dec_info = trace_by_name.get("get_decision", {"decision_id": decision_id})
            chain_info = trace_by_name.get("verify_chain", {"chain_verified": True})
            snaps_info = trace_by_name.get("get_snapshot", {"snapshots": []})
            rules_info = trace_by_name.get("get_rule_checks", {"rule_checks": []})
            model_info = trace_by_name.get("get_model_contributions", {})
            replay_info = trace_by_name.get("replay_score", {"replay_verified": True})

        return synthesize_report_from_session_data(
            decision_id=decision_id,
            dec_info=dec_info,
            chain_info=chain_info,
            snaps_info=snaps_info,
            rules_info=rules_info,
            model_info=model_info,
            replay_info=replay_info,
            session=session,
        )


class GeminiAPIBackend(LLMBackend):
    """Google Gen AI SDK (`google-genai`) backend using function calling over preserved snapshots."""

    backend_name = "gemini_api"
    FALLBACK_MODELS: tuple[str, ...] = (
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-flash-latest",
    )

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_name: str | None = None,
    ) -> None:
        self.api_key = _resolve_gemini_api_key(api_key)
        self.model_name = model_name or os.environ.get("GEMINI_MODEL", "gemini-2.5-flash-lite")

    def _generate_content_with_fallback(
        self,
        client: genai.Client,
        contents: list[Any],
        config: types.GenerateContentConfig,
    ) -> Any:
        """Call client.models.generate_content, falling back across Flash models on 429 quota errors."""
        candidate_models: list[str] = [self.model_name]
        for m in self.FALLBACK_MODELS:
            if m not in candidate_models:
                candidate_models.append(m)

        last_exc: Exception | None = None
        for model_id in candidate_models:
            try:
                resp = client.models.generate_content(
                    model=model_id,
                    contents=contents,
                    config=config,
                )
                self.model_name = model_id
                return resp
            except Exception as exc:
                last_exc = exc
                err_str = str(exc)
                if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str or "NOT_FOUND" in err_str:
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("No Gemini model available")

    def generate_report(
        self,
        decision_id: str,
        session: ToolSession,
        *,
        attempt: int = 1,
        validation_error: str | None = None,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured in environment")

        client = genai.Client(api_key=self.api_key)

        # Define bound tool functions with docstrings for Google Gen AI SDK function declarations
        def get_decision(decision_id: str) -> dict[str, Any]:
            """Get preserved decision metadata, outcome, policy_version, human_override, and evidence_status."""
            return session.get_decision(decision_id)

        def get_snapshot(decision_id: str, evidence_ref: str = "all") -> dict[str, Any]:
            """Get preserved evidence snapshot(s) for decision_id. Pass 'all' to retrieve all available snapshots at once."""
            return session.get_snapshot(decision_id, evidence_ref)

        def verify_chain(decision_id: str) -> dict[str, Any]:
            """Verify the preserved SHA-256 hash chain against the retained chain_length and chain_head."""
            return session.verify_chain(decision_id)

        def get_policy_as_of(family: str, as_of: str) -> dict[str, Any]:
            """Get the active loan policy and numbered sections effective on `as_of` (ISO-8601 date/timestamp)."""
            return session.get_policy_as_of(family=family, as_of=as_of)

        def search_policies(query: str, as_of_date: str, top_k: int = 5) -> dict[str, Any]:
            """Search numbered sections of policies effective on `as_of_date` ranked by TF-IDF."""
            return session.search_policies(query=query, as_of_date=as_of_date, top_k=top_k)

        def get_rule_checks(decision_id: str) -> dict[str, Any]:
            """Get all preserved rule_check snapshots recorded at decision time."""
            return session.get_rule_checks(decision_id)

        def get_model_contributions(decision_id: str) -> dict[str, Any]:
            """Get the preserved model snapshot including score, features, and log-odds contributions."""
            return session.get_model_contributions(decision_id)

        def replay_score(decision_id: str) -> dict[str, Any]:
            """Recompute the model probability score from preserved inputs and model_registry and compare within tolerance."""
            return session.replay_score(decision_id)

        tool_map = {
            "get_decision": get_decision,
            "get_snapshot": get_snapshot,
            "verify_chain": verify_chain,
            "get_policy_as_of": get_policy_as_of,
            "search_policies": search_policies,
            "get_rule_checks": get_rule_checks,
            "get_model_contributions": get_model_contributions,
            "replay_score": replay_score,
        }

        user_prompt = (
            f"Reconstruct decision_id='{decision_id}' using the provided tools.\n"
            f"IMPORTANT: In your FIRST turn, call all 6 of these tools together in parallel:\n"
            f"1. get_decision(decision_id='{decision_id}')\n"
            f"2. verify_chain(decision_id='{decision_id}')\n"
            f"3. get_snapshot(decision_id='{decision_id}', evidence_ref='all')\n"
            f"4. get_rule_checks(decision_id='{decision_id}')\n"
            f"5. get_model_contributions(decision_id='{decision_id}')\n"
            f"6. replay_score(decision_id='{decision_id}')\n"
            f"\nAfter receiving those tool results, return ONLY a valid JSON object conforming to ReconstructionReport:\n"
            f"{{\n"
            f'  "decision_id": "{decision_id}",\n'
            f'  "outcome": "APPROVE" | "DECLINE" | "REFER",\n'
            f'  "score": <float from get_decision>,\n'
            f'  "chain_verified": <bool from verify_chain>,\n'
            f'  "replay_verified": <bool from replay_score>,\n'
            f'  "replay_tolerance": 1e-09,\n'
            f'  "completeness": <float 0.0-1.0 from get_decision>,\n'
            f'  "missing_evidence": <list[str] from get_decision>,\n'
            f'  "principal_reasons": <list[str]>,\n'
            f'  "rule_checks": [\n'
            f'    {{"rule_id": str, "observed_value": scalar, "operator": str, "threshold": scalar, "passed": bool, "section": str, "evidence_ref": str}}\n'
            f"  ],\n"
            f'  "reasons": [\n'
            f'    {{"claims": [{{"text": str, "citation": str, "field": str, "value": scalar}}]}}\n'
            f"  ],\n"
            f'  "policy_note": "<note stating the policy_version and that the record supports audit and explanation and is tamper-evident against a retained reference>",\n'
            f'  "human_override": null | {{"new_outcome": str, "reason": str, "actor_role": str, "created_at": str}},\n'
            f'  "guardrail_rejections": 0,\n'
            f'  "status": "ok" | "evidence_unavailable"\n'
            f"}}"
        )
        if validation_error:
            user_prompt += (
                f"\n\nIMPORTANT: Your previous response failed schema validation with error:\n"
                f"{validation_error}\nPlease fix the JSON so it strictly matches ReconstructionReport."
            )

        contents: list[Any] = [
            types.Content(role="user", parts=[types.Part.from_text(text=user_prompt)])
        ]

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            tools=list(tool_map.values()),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.0,
        )

        last_text = ""
        for _turn in range(session.max_tool_calls + 2):
            session.check_time_budget()
            response = self._generate_content_with_fallback(
                client=client,
                contents=contents,
                config=config,
            )
            session.check_time_budget()

            if not response.function_calls:
                last_text = response.text or ""
                break

            if response.candidates and response.candidates[0].content:
                contents.append(response.candidates[0].content)

            fn_response_parts: list[types.Part] = []
            for fc in response.function_calls:
                fn_name = fc.name or ""
                fn_args = dict(fc.args) if fc.args else {}
                if session.tool_call_count >= session.max_tool_calls:
                    fn_response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={
                                "error": (
                                    f"Tool limit of {session.max_tool_calls} reached; "
                                    "produce final ReconstructionReport JSON now."
                                )
                            },
                        )
                    )
                    continue

                tool_fn = tool_map.get(fn_name)
                if tool_fn is None:
                    fn_response_parts.append(
                        types.Part.from_function_response(
                            name=fn_name,
                            response={"error": f"Unknown tool {fn_name!r}"},
                        )
                    )
                    continue

                tool_output = tool_fn(**fn_args)
                fn_response_parts.append(
                    types.Part.from_function_response(
                        name=fn_name,
                        response={"result": tool_output},
                    )
                )

            contents.append(types.Content(role="user", parts=fn_response_parts))

            # If all 8 tool calls have been used, request final JSON response without tools
            if session.tool_call_count >= session.max_tool_calls:
                final_config = types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.0,
                )
                final_resp = self._generate_content_with_fallback(
                    client=client,
                    contents=contents,
                    config=final_config,
                )
                last_text = final_resp.text or ""
                break

        return _extract_json_dict(last_text)

    def generate_summary(self, report: dict[str, Any], session: ToolSession) -> str | None:
        if not self.api_key:
            return None
        facts = {
            "outcome": report.get("outcome"),
            "claims": [c for g in report.get("reasons", []) for c in g.get("claims", [])],
            "rule_checks": report.get("rule_checks", []),
        }
        client = genai.Client(api_key=self.api_key)
        resp = self._generate_content_with_fallback(
            client=client,
            contents=[types.Content(role="user", parts=[types.Part.from_text(
                text=SUMMARY_PROMPT + "\n\n" + json.dumps(facts, default=str))])],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (resp.text or "").strip() or None


    def generate_signal_why(self, kind: str, facts: dict[str, Any]) -> str | None:
        if not self.api_key:
            return None
        client = genai.Client(api_key=self.api_key)
        resp = self._generate_content_with_fallback(
            client=client,
            contents=[types.Content(role="user", parts=[types.Part.from_text(
                text=SIGNAL_WHY_PROMPT + "\n\n" + json.dumps({"rule": kind, "facts": facts}, default=str))])],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return (resp.text or "").strip() or None


SIGNAL_WHY_PROMPT = """Write ONE short plain-language sentence saying why a rule-based risk signal fired.
Use ONLY the rule name and facts in the JSON below. Rules:
- Write each number exactly as it appears in the JSON and do not introduce any other numbers.
- Do not say or imply fraud, guilt or a conclusion; this is a pattern for a person to look at.
Return the sentence only."""


SUMMARY_PROMPT = """Write a plain-language explanation (3-5 sentences) of a loan decision for an auditor or applicant.
Use ONLY the verified facts in the JSON below. Rules:
- Every sentence that states a fact must end with the evidence id in square brackets exactly as given in a claim's "citation", e.g. [application:APP-0001].
- Write each number exactly as it appears in the JSON (no percentages, rounding, or conversions) and do not introduce any other numbers.
- Do not speculate about causes, fairness, or what the applicant should do.
Return plain text only."""


def get_llm_backend(backend_name: str | None = None) -> LLMBackend:
    """Instantiate the configured LLMBackend ('fake' or 'gemini_api')."""
    resolved = (backend_name or os.environ.get("LLM_BACKEND", "fake")).strip().lower()
    if resolved == "fake":
        return FakeLLMBackend()
    if resolved == "gemini_api":
        return GeminiAPIBackend()
    raise ValueError(f"Unsupported LLM_BACKEND={resolved!r}; expected 'fake' or 'gemini_api'")


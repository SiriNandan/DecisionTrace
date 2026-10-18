"""Decision service for evaluating loan applications, recording hash-chained snapshots, and verifying preserved evidence."""

from __future__ import annotations

import base64

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
from typing import Any
import uuid

import psycopg

from backend.integrity import (
    GENESIS,
    build_chain,
    canonical_json,
    entry_hash,
    payload_sha256 as compute_payload_sha256,
    verify_chain,
)
from backend.redaction import redact_note_for_snapshot
from backend.scoring import FEATURE_ORDER, score as score_from_registry
from backend.sources import (
    read_account,
    read_application,
    read_bureau,
    read_consent,
    read_documents,
    read_notes,
)
from backend.sources._csv_utils import DEFAULT_DATA_DIR


FORBIDDEN_MODEL_FEATURES = {
    "gender",
    "ethnicity",
    "religion",
    "postcode",
    "age_band",
    "test_group",
}
ALLOWED_MODEL_FEATURES = tuple(FEATURE_ORDER)
GENESIS_PREV_HASH = GENESIS
MODEL_SCORE_THRESHOLD = 0.50

_FALLBACK_REGISTRY_CACHE: dict[str, dict[str, Any]] = {}


@dataclass(frozen=True)
class EffectivePolicyRules:
    policy_id: str
    policy_family: str
    effective_from: str
    effective_to: str | None
    text_sha256: str
    dti_limit: float
    min_credit_score: int
    required_documents: list[str]
    delinquency_manual_review_min: int | None


def parse_iso_timestamp(value: datetime | str) -> datetime:
    """Parse an ISO-8601 timestamp string or datetime into a UTC-aware datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_payload_json(payload: dict[str, Any]) -> str:
    """Serialize a snapshot payload deterministically for SHA-256 hashing."""
    return canonical_json(payload)


class PolicyParseError(RuntimeError):
    """Raised when a stored policy's text does not yield the limits the decision engine needs."""


def _extract_policy_rules(row: tuple[Any, ...]) -> EffectivePolicyRules:
    policy_id, policy_family, eff_from, eff_to, text_sha256, body = row

    dti_match = re.search(r"dti_ratio`?\)\s+is\s+`?(0\.\d+)`?", body)
    if dti_match is None:
        raise PolicyParseError(f"Policy {policy_id}: could not parse the DTI limit from the policy text.")
    dti_limit = float(dti_match.group(1))

    score_match = re.search(r"automated approval is\s+`?(\d{3})`?", body)
    if score_match is None:
        raise PolicyParseError(f"Policy {policy_id}: could not parse the minimum credit score from the policy text.")
    min_credit_score = int(score_match.group(1))

    required_docs = ["income proof"]
    if "address proof" in body.lower():
        required_docs.append("address proof")

    delinq_match = re.search(r"delinquencies_24m\s*>=\s*(\d+)", body)
    delinq_min = int(delinq_match.group(1)) if delinq_match else None

    return EffectivePolicyRules(
        policy_id=str(policy_id),
        policy_family=str(policy_family),
        effective_from=eff_from.isoformat() if hasattr(eff_from, "isoformat") else str(eff_from),
        effective_to=(
            eff_to.isoformat()
            if (eff_to and hasattr(eff_to, "isoformat"))
            else (str(eff_to) if eff_to else None)
        ),
        text_sha256=str(text_sha256),
        dti_limit=dti_limit,
        min_credit_score=min_credit_score,
        required_documents=required_docs,
        delinquency_manual_review_min=delinq_min,
    )


def resolve_policy_for_timestamp(
    conn: psycopg.Connection[Any],
    decided_at: datetime,
    policy_family: str = "consumer_loan",
) -> EffectivePolicyRules:
    """Resolve the active policy from the policies table at decided_at."""
    decision_date = decided_at.date()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT policy_id, policy_family, effective_from, effective_to, text_sha256, body
            FROM policies
            WHERE policy_family = %s
              AND effective_from <= %s
              AND (effective_to IS NULL OR effective_to >= %s)
            ORDER BY effective_from DESC
            LIMIT 1;
            """,
            (policy_family, decision_date, decision_date),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(
            f"No effective policy found for family={policy_family!r} on date={decision_date.isoformat()}"
        )
    return _extract_policy_rules(row)


def _resolve_model_registry_row(
    conn: psycopg.Connection[Any],
    model_version: str | None = None,
) -> dict[str, Any]:
    """Load the active model_registry row inside the current transaction."""
    target_version = model_version or os.environ.get("MODEL_VERSION", "loan_lr_v1")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                model_version,
                artifact_sha256,
                preprocessing_version,
                feature_order,
                intercept,
                coefficients,
                feature_means,
                feature_stds,
                created_at
            FROM model_registry
            WHERE model_version = %s;
            """,
            (target_version,),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                SELECT
                    model_version,
                    artifact_sha256,
                    preprocessing_version,
                    feature_order,
                    intercept,
                    coefficients,
                    feature_means,
                    feature_stds,
                    created_at
                FROM model_registry
                ORDER BY created_at DESC
                LIMIT 1;
                """
            )
            row = cur.fetchone()

    if row is not None:
        (
            ver,
            artifact_sha256,
            preprocessing_version,
            feature_order,
            intercept,
            coefficients,
            feature_means,
            feature_stds,
            created_at,
        ) = row
        return {
            "model_version": str(ver),
            "artifact_sha256": str(artifact_sha256),
            "preprocessing_version": str(preprocessing_version),
            "feature_order": list(feature_order),
            "intercept": float(intercept),
            "coefficients": coefficients,
            "feature_means": feature_means,
            "feature_stds": feature_stds,
            "means": feature_means,
            "stds": feature_stds,
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else str(created_at),
        }

    if target_version not in _FALLBACK_REGISTRY_CACHE:
        from data.train_model import train_and_register_model

        trained = train_and_register_model(model_version=target_version, sync_db=False)
        _FALLBACK_REGISTRY_CACHE[target_version] = trained["registry_row"]
    return dict(_FALLBACK_REGISTRY_CACHE[target_version])


def evaluate_rules_and_outcome(
    *,
    applicant_params: dict[str, Any],
    policy: EffectivePolicyRules,
    registry_row: dict[str, Any],
    score_threshold: float = MODEL_SCORE_THRESHOLD,
) -> dict[str, Any]:
    """Score from the registry, execute rule checks, and compute outcome and cited sections."""
    forbidden_present = FORBIDDEN_MODEL_FEATURES.intersection(
        {k.lower() for k in applicant_params.keys()}
    )
    if forbidden_present:
        raise ValueError(
            f"Forbidden features present in model evaluation input: {sorted(forbidden_present)}"
        )

    income_annual = float(
        applicant_params.get("income_annual", applicant_params.get("annual_income", 0.0))
    )
    requested_amount = float(applicant_params["requested_amount"])
    dti_ratio = float(applicant_params["dti_ratio"])
    credit_score = int(applicant_params["credit_score"])
    delinquencies_24m = int(applicant_params["delinquencies_24m"])
    account_age_months = int(applicant_params["account_age_months"])
    employment_months = int(applicant_params["employment_months"])

    model_features: dict[str, float] = {
        "income_annual": round(income_annual, 2),
        "requested_amount": round(requested_amount, 2),
        "dti_ratio": round(dti_ratio, 6),
        "credit_score": float(credit_score),
        "delinquencies_24m": float(delinquencies_24m),
        "account_age_months": float(account_age_months),
        "employment_months": float(employment_months),
    }

    score_result = score_from_registry(model_features, registry_row)
    probability = float(score_result["probability"])
    rounded_score = round(probability, 6)
    contributions = {
        k: round(float(v), 8) for k, v in score_result["contributions"].items()
    }

    payslip_status = str(applicant_params.get("payslip_status", "received"))
    address_proof_status = str(applicant_params.get("address_proof_status", "received"))

    # Determine document check status across required documents
    doc_observed_status = "received"
    if "income proof" in policy.required_documents and payslip_status != "received":
        doc_observed_status = payslip_status
    elif "address proof" in policy.required_documents and address_proof_status != "received":
        doc_observed_status = address_proof_status

    dti_passed = dti_ratio <= policy.dti_limit + 1e-9
    credit_passed = credit_score >= policy.min_credit_score
    docs_passed = doc_observed_status == "received"

    rule_checks: list[dict[str, Any]] = [
        {
            "rule_id": "dti_limit",
            "observed_value": round(dti_ratio, 4),
            "operator": "<=",
            "threshold": float(policy.dti_limit),
            "result": "PASS" if dti_passed else "FAIL",
            "policy_version": policy.policy_id,
            "section": "3.2 Debt-to-income limit",
        },
        {
            "rule_id": "min_credit_score",
            "observed_value": credit_score,
            "operator": ">=",
            "threshold": int(policy.min_credit_score),
            "result": "PASS" if credit_passed else "FAIL",
            "policy_version": policy.policy_id,
            "section": "3.3 Minimum credit score",
        },
        {
            "rule_id": "required_documents",
            "observed_value": doc_observed_status,
            "operator": "==",
            "threshold": "received",
            "result": "PASS" if docs_passed else "REFER",
            "policy_version": policy.policy_id,
            "section": "4.1 Required documents",
        },
    ]

    manual_review_fired = False
    if policy.delinquency_manual_review_min is not None:
        manual_review_fired = delinquencies_24m >= policy.delinquency_manual_review_min
        rule_checks.append(
            {
                "rule_id": "delinquencies_manual_review",
                "observed_value": delinquencies_24m,
                "operator": "<",
                "threshold": int(policy.delinquency_manual_review_min),
                "result": "REFER" if manual_review_fired else "PASS",
                "policy_version": policy.policy_id,
                "section": "5.1 Manual review",
            }
        )

    # Outcome rule:
    # REFER if a required document is missing/processing or a manual-review rule fires;
    # APPROVE if score under threshold and all rules pass;
    # else DECLINE.
    all_rules_pass = dti_passed and credit_passed and docs_passed and (not manual_review_fired)
    score_under_threshold = probability < score_threshold

    if (not docs_passed) or manual_review_fired:
        outcome = "REFER"
        cited_sections: list[str] = []
        if not docs_passed:
            cited_sections.append("4.1 Required documents")
            cited_sections.append("5.1 Manual review")
        if manual_review_fired and "5.1 Manual review" not in cited_sections:
            cited_sections.append("5.1 Manual review")
    elif score_under_threshold and all_rules_pass:
        outcome = "APPROVE"
        cited_sections = [
            "3.2 Debt-to-income limit",
            "3.3 Minimum credit score",
            "4.1 Required documents",
        ]
    else:
        outcome = "DECLINE"
        cited_sections = []
        if not dti_passed:
            cited_sections.append("3.2 Debt-to-income limit")
        if not credit_passed:
            cited_sections.append("3.3 Minimum credit score")
        if not cited_sections:
            cited_sections.append("3.3 Minimum credit score")

    return {
        "outcome": outcome,
        "policy_version": policy.policy_id,
        "score": rounded_score,
        "probability": probability,
        "threshold": score_threshold,
        "intercept": float(score_result["intercept"]),
        "log_odds": float(score_result["log_odds"]),
        "contributions": contributions,
        "model_features": model_features,
        "rule_checks": rule_checks,
        "cited_sections": cited_sections,
    }


def _insert_snapshot_row(cur: psycopg.Cursor[Any], snap: dict[str, Any]) -> None:
    """Insert a single snapshot row into decision_snapshots (extracted for transactional failure testing)."""
    cur.execute(
        """
        INSERT INTO decision_snapshots (
            decision_id, seq, evidence_type, evidence_ref,
            source_id, source_timestamp, payload, payload_sha256,
            prev_hash, entry_hash, captured_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s);
        """,
        (
            snap["decision_id"],
            snap["seq"],
            snap["evidence_type"],
            snap["evidence_ref"],
            snap["source_id"],
            snap["source_timestamp"],
            canonical_payload_json(snap["payload"]),
            snap["payload_sha256"],
            snap["prev_hash"],
            snap["entry_hash"],
            snap["captured_at"],
        ),
    )


def compute_idempotent_decision_id(
    *,
    applicant_id: str,
    requested_amount: float | None,
    document_refs: list[str] | None,
    decision_date_key: str,
    idempotency_key: str | None = None,
) -> uuid.UUID:
    """Compute a deterministic UUIDv5 for idempotent request handling."""
    if idempotency_key and str(idempotency_key).strip():
        token = f"idemp_key:{str(idempotency_key).strip()}"
    else:
        token = canonical_json(
            {
                "applicant_id": str(applicant_id),
                "requested_amount": (
                    round(float(requested_amount), 2) if requested_amount is not None else None
                ),
                "document_refs": list(document_refs) if document_refs is not None else None,
                "decision_date": str(decision_date_key),
            }
        )
    return uuid.uuid5(uuid.NAMESPACE_OID, f"decisiontrace:decide:{token}")


def create_decision(
    conn: psycopg.Connection[Any],
    *,
    applicant_id: str,
    requested_amount: float | None = None,
    document_refs: list[str] | None = None,
    application_id: str | None = None,
    applicant_params: dict[str, Any] | None = None,
    decided_at: datetime | str | None = None,
    allow_backdating: bool = False,
    model_version: str | None = None,
    analyst_note: dict[str, Any] | None = None,
    decision_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    """Evaluate and record an append-only decision and its hash-chained snapshots in ONE transaction."""
    env_mode = os.environ.get("ENV", "dev").lower()
    if decided_at is not None:
        if not allow_backdating and env_mode != "dev":
            raise PermissionError("Backdating decisions is restricted to ENV=dev.")
        decision_ts = parse_iso_timestamp(decided_at)
    else:
        decision_ts = datetime.now(timezone.utc)

    target_data_dir = data_dir or DEFAULT_DATA_DIR

    # 1. Read application, bureau, account, documents, consent, notes from separate source readers
    req_amt_override = (
        float(requested_amount)
        if requested_amount is not None
        else (
            float(applicant_params["requested_amount"])
            if (applicant_params and "requested_amount" in applicant_params)
            else None
        )
    )
    app_src = read_application(
        applicant_id, requested_amount=req_amt_override, data_dir=target_data_dir
    )
    bureau_src = read_bureau(applicant_id, data_dir=target_data_dir)
    account_src = read_account(applicant_id, data_dir=target_data_dir)
    addr_status = (
        str(applicant_params.get("address_proof_status", "received"))
        if applicant_params
        else "received"
    )
    docs_src = read_documents(
        applicant_id,
        document_refs=document_refs,
        data_dir=target_data_dir,
        address_proof_status=addr_status,
    )
    consent_src = read_consent(applicant_id, data_dir=target_data_dir)
    notes_src = read_notes(applicant_id, data_dir=target_data_dir)

    resolved_app_id = str(
        application_id
        or (applicant_params.get("application_id") if applicant_params else None)
        or app_src["payload"].get("application_id")
        or applicant_id
    )

    # Merge any explicit test/scenario overrides into source payloads
    app_payload = dict(app_src["payload"])
    app_payload["application_id"] = resolved_app_id
    bureau_payload = dict(bureau_src["payload"])
    bureau_payload["application_id"] = resolved_app_id
    account_payload = dict(account_src["payload"])
    account_payload["application_id"] = resolved_app_id
    doc_payload = dict(docs_src["payload"])
    doc_payload["application_id"] = resolved_app_id

    if applicant_params:
        forbidden_check = FORBIDDEN_MODEL_FEATURES.intersection(
            {k.lower() for k in applicant_params.keys()}
        )
        if forbidden_check:
            raise ValueError(
                f"Forbidden features present in applicant_params: {sorted(forbidden_check)}"
            )
        for k in (
            "annual_income",
            "monthly_income",
            "existing_debt_service",
            "dti_ratio",
            "employment_months",
            "requested_amount",
            "currency",
            "address_proof_status",
        ):
            if k in applicant_params:
                app_payload[k] = applicant_params[k]
        if "annual_income" in applicant_params:
            app_payload["income_annual"] = round(float(applicant_params["annual_income"]), 2)
            doc_payload["income_annual"] = round(float(applicant_params["annual_income"]), 2)
        if "monthly_income" in applicant_params:
            doc_payload["monthly_income"] = round(float(applicant_params["monthly_income"]), 2)
            account_payload["monthly_income"] = round(float(applicant_params["monthly_income"]), 2)
        if "existing_debt_service" in applicant_params:
            account_payload["existing_debt_service"] = round(
                float(applicant_params["existing_debt_service"]), 2
            )
        for k in ("credit_score", "delinquencies_24m", "account_age_months"):
            if k in applicant_params:
                bureau_payload[k] = int(applicant_params[k])
        if "account_age_months" in applicant_params:
            account_payload["account_age_months"] = int(applicant_params["account_age_months"])
        if "payslip_status" in applicant_params:
            doc_payload["status"] = str(applicant_params["payslip_status"])
        if "address_proof_status" in applicant_params:
            doc_payload["address_proof_status"] = str(applicant_params["address_proof_status"])

    combined_params: dict[str, Any] = {
        "applicant_id": applicant_id,
        "application_id": resolved_app_id,
        "annual_income": round(float(app_payload["annual_income"]), 2),
        "income_annual": round(float(app_payload["annual_income"]), 2),
        "monthly_income": round(float(app_payload["monthly_income"]), 2),
        "existing_debt_service": round(float(app_payload["existing_debt_service"]), 2),
        "dti_ratio": round(float(app_payload["dti_ratio"]), 4),
        "employment_months": int(app_payload["employment_months"]),
        "requested_amount": round(float(app_payload["requested_amount"]), 2),
        "currency": str(app_payload.get("currency", "GBP")),
        "credit_score": int(bureau_payload["credit_score"]),
        "delinquencies_24m": int(bureau_payload["delinquencies_24m"]),
        "account_age_months": int(bureau_payload["account_age_months"]),
        "payslip_status": str(doc_payload["status"]),
        "address_proof_status": str(doc_payload.get("address_proof_status", "received")),
    }

    if decision_id is not None:
        dec_uuid = decision_id
    elif idempotency_key is not None or decided_at is None:
        date_key = (
            parse_iso_timestamp(decided_at).isoformat()
            if decided_at is not None
            else decision_ts.date().isoformat()
        )
        dec_uuid = compute_idempotent_decision_id(
            applicant_id=applicant_id,
            requested_amount=combined_params["requested_amount"],
            document_refs=document_refs,
            decision_date_key=date_key,
            idempotency_key=idempotency_key,
        )
    else:
        dec_uuid = uuid.uuid5(
            uuid.NAMESPACE_OID,
            f"decisiontrace:{resolved_app_id}:{decision_ts.isoformat()}",
        )

    # Execute everything in ONE atomic transaction
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM decisions WHERE decision_id = %s;",
                (dec_uuid,),
            )
            if cur.fetchone() is not None:
                return get_decision_detail(conn, dec_uuid)

        # 2. Select policy by effective date
        policy = resolve_policy_for_timestamp(conn, decision_ts)

        # 3. Load model from registry and score; 4. Execute rule checks; 5. Determine outcome
        registry_row = _resolve_model_registry_row(conn, model_version)
        active_model_version = str(registry_row["model_version"])
        eval_result = evaluate_rules_and_outcome(
            applicant_params=combined_params,
            policy=policy,
            registry_row=registry_row,
        )

        if analyst_note is not None:
            redacted_note = redact_note_for_snapshot(analyst_note)
            note_ref = f"note:{redacted_note.get('note_id', applicant_id)}"
            note_src_id = "underwriter_workbench_v1"
            note_src_ts = str(redacted_note.get("created_at", decision_ts.isoformat()))
        else:
            redacted_note = redact_note_for_snapshot(notes_src["payload"])
            note_ref = str(notes_src["evidence_ref"])
            note_src_id = str(notes_src["source_id"])
            note_src_ts = str(notes_src["source_timestamp"])

        # Build snapshot items across all 9 required evidence types:
        # application, bureau, account_history, document, consent, policy, model, rule_check, analyst_note
        chain_items: list[dict[str, Any]] = [
            {
                "evidence_type": "application",
                "evidence_ref": f"application:{resolved_app_id}",
                "source_id": str(app_src["source_id"]),
                "source_timestamp": decision_ts,
                "captured_at": decision_ts,
                "payload": {
                    "applicant_id": applicant_id,
                    "application_id": resolved_app_id,
                    "annual_income": combined_params["annual_income"],
                    "income_annual": combined_params["income_annual"],
                    "monthly_income": combined_params["monthly_income"],
                    "existing_debt_service": combined_params["existing_debt_service"],
                    "dti_ratio": combined_params["dti_ratio"],
                    "employment_months": combined_params["employment_months"],
                    "requested_amount": combined_params["requested_amount"],
                    "currency": combined_params["currency"],
                    "address_proof_status": combined_params["address_proof_status"],
                },
            },
            {
                "evidence_type": "bureau",
                "evidence_ref": str(bureau_src["evidence_ref"]),
                "source_id": str(bureau_src["source_id"]),
                "source_timestamp": str(bureau_src["source_timestamp"]),
                "captured_at": decision_ts,
                "payload": bureau_payload,
            },
            {
                "evidence_type": "account_history",
                "evidence_ref": str(account_src["evidence_ref"]),
                "source_id": str(account_src["source_id"]),
                "source_timestamp": str(account_src["source_timestamp"]),
                "captured_at": decision_ts,
                "payload": account_payload,
            },
            {
                "evidence_type": "document",
                "evidence_ref": str(docs_src["evidence_ref"]),
                "source_id": str(docs_src["source_id"]),
                "source_timestamp": str(docs_src["source_timestamp"]),
                "captured_at": decision_ts,
                "payload": doc_payload,
            },
            {
                "evidence_type": "consent",
                "evidence_ref": str(consent_src["evidence_ref"]),
                "source_id": str(consent_src["source_id"]),
                "source_timestamp": str(consent_src["source_timestamp"]),
                "captured_at": decision_ts,
                "payload": dict(consent_src["payload"]),
            },
            {
                "evidence_type": "policy",
                "evidence_ref": f"policy:{policy.policy_id}",
                "source_id": "policy_registry_v1",
                "source_timestamp": decision_ts,
                "captured_at": decision_ts,
                "payload": {
                    "policy_id": policy.policy_id,
                    "policy_family": policy.policy_family,
                    "effective_from": policy.effective_from,
                    "effective_to": policy.effective_to,
                    "text_sha256": policy.text_sha256,
                    "dti_limit": policy.dti_limit,
                    "min_credit_score": policy.min_credit_score,
                    "required_documents": policy.required_documents,
                    "delinquency_manual_review_min": policy.delinquency_manual_review_min,
                    "cited_sections": eval_result["cited_sections"],
                    "outcome": eval_result["outcome"],
                },
            },
            {
                "evidence_type": "model",
                "evidence_ref": f"model:{active_model_version}",
                "source_id": "model_registry_v1",
                "source_timestamp": decision_ts,
                "captured_at": decision_ts,
                "payload": {
                    "model_version": active_model_version,
                    "artifact_sha256": str(registry_row["artifact_sha256"]),
                    "preprocessing_version": str(registry_row["preprocessing_version"]),
                    "score": eval_result["score"],
                    "probability": eval_result["probability"],
                    "threshold": eval_result["threshold"],
                    "intercept": eval_result["intercept"],
                    "log_odds": eval_result["log_odds"],
                    "features": eval_result["model_features"],
                    "contributions": eval_result["contributions"],
                    "outcome": eval_result["outcome"],
                },
            },
        ]

        for rule_check in eval_result["rule_checks"]:
            chain_items.append(
                {
                    "evidence_type": "rule_check",
                    "evidence_ref": f"rule:{rule_check['rule_id']}",
                    "source_id": "policy_rule_engine_v1",
                    "source_timestamp": decision_ts,
                    "captured_at": decision_ts,
                    "payload": dict(rule_check),
                }
            )

        chain_items.append(
            {
                "evidence_type": "analyst_note",
                "evidence_ref": note_ref,
                "source_id": note_src_id,
                "source_timestamp": note_src_ts,
                "captured_at": decision_ts,
                "payload": redacted_note,
            }
        )

        # 6. Build the chain; insert the decision with chain_length and chain_head, then the snapshots
        chained_snapshots, chain_length, chain_head = build_chain(dec_uuid, chain_items)

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO decisions (
                    decision_id, application_id, decided_at, outcome,
                    model_version, policy_version, score, chain_length, chain_head
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
                """,
                (
                    dec_uuid,
                    resolved_app_id,
                    decision_ts,
                    eval_result["outcome"],
                    active_model_version,
                    policy.policy_id,
                    eval_result["score"],
                    chain_length,
                    chain_head,
                ),
            )

            for snap in chained_snapshots:
                _insert_snapshot_row(cur, snap)
                cur.execute(
                    """
                    INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at)
                    VALUES (%s, %s, 'available', %s);
                    """,
                    (dec_uuid, snap["evidence_ref"], decision_ts),
                )

    return {
        "decision_id": str(dec_uuid),
        "application_id": resolved_app_id,
        "applicant_id": applicant_id,
        "decided_at": decision_ts.isoformat(),
        "outcome": eval_result["outcome"],
        "initial_outcome": eval_result["outcome"],
        "effective_outcome": eval_result["outcome"],
        "policy_version": policy.policy_id,
        "model_version": active_model_version,
        "score": eval_result["score"],
        "contributions": eval_result["contributions"],
        "rule_checks": eval_result["rule_checks"],
        "cited_sections": eval_result["cited_sections"],
        "chain_length": chain_length,
        "chain_head": chain_head,
    }


def record_override(
    conn: psycopg.Connection[Any],
    *,
    decision_id: uuid.UUID | str,
    new_outcome: str,
    reason: str,
    actor_role: str,
    created_at: datetime | str | None = None,
    kind: str = "override",
) -> dict[str, Any]:
    """Append an override row into `overrides` only without mutating `decisions` or `decision_snapshots`.

    `kind='review_request'` is a customer asking for a person to look again: it is recorded but never changes the outcome.
    """
    clean_outcome = str(new_outcome).strip().upper()
    if clean_outcome not in ("APPROVE", "DECLINE", "REFER"):
        raise ValueError(
            f"Invalid new_outcome {new_outcome!r}; must be one of APPROVE, DECLINE, REFER."
        )
    clean_reason = reason.strip()
    if len(clean_reason) < 20:
        raise ValueError("Override reason must be at least 20 characters long.")
    clean_role = actor_role.strip()
    if not clean_role:
        raise ValueError("Override actor_role must be non-empty.")

    dec_uuid = uuid.UUID(str(decision_id))
    override_ts = (
        parse_iso_timestamp(created_at)
        if created_at is not None
        else datetime.now(timezone.utc)
    )

    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM decisions WHERE decision_id = %s;",
                (dec_uuid,),
            )
            if cur.fetchone() is None:
                raise LookupError(f"Decision {dec_uuid} not found")

            cur.execute(
                """
                INSERT INTO overrides (decision_id, new_outcome, reason, actor_role, created_at, kind)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING override_id;
                """,
                (dec_uuid, clean_outcome, clean_reason, clean_role, override_ts, kind),
            )
            override_id = int(cur.fetchone()[0])

    return {
        "override_id": override_id,
        "decision_id": str(dec_uuid),
        "new_outcome": clean_outcome,
        "reason": clean_reason,
        "actor_role": clean_role,
        "created_at": override_ts.isoformat(),
    }


def encode_cursor(decided_at: datetime, decision_id: Any) -> str:
    raw = json.dumps({"t": decided_at.isoformat(), "i": str(decision_id)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return parse_iso_timestamp(data["t"]), str(uuid.UUID(data["i"]))
    except Exception as exc:  # any damage to the cursor is the caller's problem, not a server error
        raise ValueError("That page marker isn't valid. Start again from the first page.") from exc


def list_decisions_page(
    conn: psycopg.Connection[Any],
    *,
    applicant_id: str | None = None,
    limit: int = 100,
    tenant_id: str | None = None,
    only_decision_id: str | None = None,
    cursor: str | None = None,
    portfolios: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of decisions, newest first, and a marker for the next page (None when this is the last).

    Paging is by position in the list (date, then id), not by offset, so it stays fast on a long list and does not skip
    or repeat anything when new decisions arrive while someone is reading.
    """
    after_t, after_i = decode_cursor(cursor) if cursor else (None, None)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM (
                SELECT
                    d.decision_id, d.application_id, d.decided_at, d.outcome, d.model_version, d.policy_version,
                    d.score, d.chain_length, d.chain_head,
                    (
                        SELECT o.new_outcome FROM overrides o
                        WHERE o.decision_id = d.decision_id AND o.kind = 'override'
                        ORDER BY o.created_at DESC, o.override_id DESC LIMIT 1
                    ) AS override_outcome,
                    COALESCE((
                        SELECT ds.payload->>'applicant_id' FROM decision_snapshots ds
                        WHERE ds.decision_id = d.decision_id AND ds.evidence_type IN ('application', 'application_submission')
                        ORDER BY ds.seq ASC LIMIT 1
                    ), d.application_id) AS applicant
                FROM decisions d
                WHERE (%s::text IS NULL OR d.tenant_id = %s)
                  AND (%s::uuid IS NULL OR d.decision_id = %s::uuid)
                  AND (%s::text[] IS NULL OR d.portfolio = ANY(%s::text[]))
                  AND (%s::timestamptz IS NULL OR d.decided_at < %s OR (d.decided_at = %s AND d.decision_id > %s::uuid))
            ) q
            WHERE (%s::text IS NULL OR q.applicant = %s)
            ORDER BY q.decided_at DESC, q.decision_id ASC
            LIMIT %s;
            """,
            (tenant_id, tenant_id, only_decision_id, only_decision_id, portfolios, portfolios, after_t, after_t, after_t, after_i,
             applicant_id, applicant_id, limit + 1),
        )
        rows = cur.fetchall()

    more = len(rows) > limit
    rows = rows[:limit]
    results: list[dict[str, Any]] = []
    for (dec_id, application_id, decided_at, initial_outcome, model_version, policy_version, score_val, chain_length,
         chain_head, override_outcome, applicant) in rows:
        effective_outcome = str(override_outcome) if override_outcome else str(initial_outcome)
        results.append(
            {
                "decision_id": str(dec_id),
                "application_id": str(application_id),
                "applicant_id": str(applicant),
                "decided_at": decided_at.isoformat() if hasattr(decided_at, "isoformat") else str(decided_at),
                "outcome": effective_outcome,
                "initial_outcome": str(initial_outcome),
                "effective_outcome": effective_outcome,
                "model_version": str(model_version),
                "policy_version": str(policy_version),
                "score": float(score_val),
                "chain_length": int(chain_length),
                "chain_head": str(chain_head),
            }
        )
    next_cursor = encode_cursor(rows[-1][2], rows[-1][0]) if more and rows else None
    return results, next_cursor


def list_decisions(
    conn: psycopg.Connection[Any],
    *,
    applicant_id: str | None = None,
    limit: int = 100,
    tenant_id: str | None = None,
    only_decision_id: str | None = None,
) -> list[dict[str, Any]]:
    """List recorded decisions ordered by decided_at DESC (only `tenant_id`'s when given)."""
    return list_decisions_page(
        conn, applicant_id=applicant_id, limit=limit, tenant_id=tenant_id, only_decision_id=only_decision_id
    )[0]


def get_decision_detail(
    conn: psycopg.Connection[Any],
    decision_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Fetch full decision record and preserved snapshots by decision_id."""
    rec = reconstruct_decision_from_snapshots(conn, decision_id)
    return rec


def get_decision(
    conn: psycopg.Connection[Any],
    decision_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Fetch full decision record (including original outcome and any override) by decision_id."""
    return get_decision_detail(conn, decision_id)


def verify_decision(
    conn: psycopg.Connection[Any],
    decision_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Verify hash-chain integrity and evidence availability for decision_id."""
    rec = reconstruct_decision_from_snapshots(conn, decision_id)
    return {
        "decision_id": rec["decision_id"],
        "ok": rec["chain_valid"],
        "valid": rec["chain_valid"],
        "chain_valid": rec["chain_valid"],
        "status": rec["status"],
        "first_broken_seq": rec["first_broken_seq"],
        "reason": rec["broken_reason"],
        "tamper_reasons": rec["tamper_reasons"],
        "chain_length": rec["chain_length"],
        "snapshot_count": len(rec["snapshots"]),
        "chain_head": rec["chain_head"],
        "evidence_statuses": rec["evidence_statuses"],
        "corrections_count": len(rec["corrections"]),
        "corrections_valid": rec["corrections_valid"],
    }


def reconstruct_decision_from_snapshots(
    conn: psycopg.Connection[Any],
    decision_id: uuid.UUID | str,
) -> dict[str, Any]:
    """Reconstruct and verify a decision using ONLY preserved decision-time snapshots."""
    dec_uuid = uuid.UUID(str(decision_id))
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
        dec_row = cur.fetchone()
        if dec_row is None:
            raise LookupError(f"Decision {dec_uuid} not found")

        cur.execute(
            """
            SELECT seq, evidence_type, evidence_ref, source_id, source_timestamp,
                   payload, payload_sha256, prev_hash, entry_hash, captured_at
            FROM decision_snapshots
            WHERE decision_id = %s
            ORDER BY seq ASC;
            """,
            (dec_uuid,),
        )
        snap_rows = cur.fetchall()

        cur.execute(
            """
            SELECT override_id, new_outcome, reason, actor_role, created_at, kind
            FROM overrides
            WHERE decision_id = %s
            ORDER BY created_at DESC, override_id DESC;
            """,
            (dec_uuid,),
        )
        override_rows = cur.fetchall()

        cur.execute(
            """
            SELECT DISTINCT ON (evidence_ref) evidence_ref, status, changed_at
            FROM evidence_status
            WHERE decision_id = %s
            ORDER BY evidence_ref, changed_at DESC;
            """,
            (dec_uuid,),
        )
        status_rows = cur.fetchall()

    (
        _,
        application_id,
        decided_at,
        initial_outcome,
        model_version,
        policy_version,
        score_val,
        expected_chain_length,
        expected_chain_head,
    ) = dec_row

    from backend.crypto import ERASED_PAYLOAD, UNAVAILABLE_PAYLOAD, Reveal, is_encrypted

    reveal = Reveal(conn)
    chain_dicts = [
        {
            "decision_id": str(dec_uuid),
            "seq": int(r[0]),
            "evidence_type": str(r[1]),
            "evidence_ref": str(r[2]),
            "source_id": str(r[3]),
            "source_timestamp": (
                r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4])
            ),
            "payload": r[5],
            "payload_sha256": str(r[6]),
            "prev_hash": str(r[7]),
            "entry_hash": str(r[8]),
            "captured_at": (
                r[9].isoformat() if hasattr(r[9], "isoformat") else str(r[9])
            ),
        }
        for r in snap_rows
    ]
    protected = bool(conn.execute("SELECT encrypted FROM decisions WHERE decision_id = %s", (dec_uuid,)).fetchone()[0])
    chain_valid, first_broken_seq, broken_reason = verify_chain(
        chain_dicts,
        int(expected_chain_length),
        str(expected_chain_head),
        payload_check=reveal.checker(),
        protected=protected,
    )
    # From here on, protected content is shown in the clear. If its key was destroyed on purpose it reads as removed;
    # if the key is missing or unreadable for any other reason it reads as unavailable (and the check above has failed).
    if protected:
        for d in chain_dicts:
            if is_encrypted(d["payload"]):
                opened = reveal.payload(d["decision_id"], d["evidence_ref"], d["payload"])
                if opened is None:
                    d["payload"] = ERASED_PAYLOAD if reveal.state(d["decision_id"]) == "erased" else UNAVAILABLE_PAYLOAD
                else:
                    d["payload"] = opened
    shown_by_seq = {int(d["seq"]): d["payload"] for d in chain_dicts}
    tamper_reasons: list[str] = [broken_reason] if (not chain_valid and broken_reason) else []

    with conn.cursor() as cur:
        cur.execute("SELECT recording_mode FROM decisions WHERE decision_id = %s;", (dec_uuid,))
        recording_mode = str(cur.fetchone()[0])
    from backend.quality import load_corrections  # deferred: keeps this module free of the quality imports

    corrections, corrections_valid, corrections_problem = load_corrections(conn, str(dec_uuid))

    evidence_statuses = {str(r[0]): str(r[1]) for r in status_rows}
    has_erased_evidence = any(
        st in ("erased_by_retention", "missing") for st in evidence_statuses.values()
    )

    if not chain_valid:
        verification_status = "tamper_detected"
    elif has_erased_evidence:
        verification_status = "evidence_unavailable"
    else:
        verification_status = "verified"

    by_type: dict[str, dict[str, Any]] = {}
    rule_checks: list[dict[str, Any]] = []
    for r in snap_rows:
        ev_type = str(r[1])
        shown = shown_by_seq.get(int(r[0]), r[5])
        payload_dict = dict(shown) if isinstance(shown, dict) else {}
        if ev_type == "rule_check":
            rule_checks.append(payload_dict)
        elif ev_type not in by_type:
            by_type[ev_type] = payload_dict

    app_sub = by_type.get("application") or by_type.get("application_submission", {})
    bureau_sub = by_type.get("bureau") or by_type.get("bureau_snapshot", {})
    payslip_sub = by_type.get("document") or by_type.get("payslip_document", {})
    policy_sub = by_type.get("policy") or by_type.get("policy_evaluation", {})
    model_sub = by_type.get("model") or by_type.get("model_evaluation", {})

    cited_sections = list(policy_sub.get("cited_sections", []))
    latest_override = None
    overrides_list: list[dict[str, Any]] = []
    effective_outcome = str(initial_outcome)

    review_requests: list[dict[str, Any]] = []
    for ov in override_rows:
        ov_id, ov_outcome, ov_reason, ov_role, ov_ts, ov_kind = ov
        if ov_kind == "review_request":  # a customer asking for a look: shown, but never the outcome
            review_requests.append({
                "reason": str(ov_reason),
                "created_at": ov_ts.isoformat() if hasattr(ov_ts, "isoformat") else str(ov_ts),
            })
            continue
        ov_entry = {
            "override_id": int(ov_id),
            "new_outcome": str(ov_outcome),
            "reason": str(ov_reason),
            "actor_role": str(ov_role),
            "created_at": ov_ts.isoformat() if hasattr(ov_ts, "isoformat") else str(ov_ts),
        }
        overrides_list.append(ov_entry)

    human_override = None
    if overrides_list:
        latest_override = overrides_list[0]
        effective_outcome = str(latest_override["new_outcome"])
        human_override = {
            "new_outcome": latest_override["new_outcome"],
            "reason": latest_override["reason"],
            "actor_role": latest_override["actor_role"],
            "created_at": latest_override["created_at"],
        }
        if "5.1 Manual review" not in cited_sections:
            cited_sections.append("5.1 Manual review")

    preserved_applicant_params = {
        "applicant_id": app_sub.get("applicant_id"),
        "application_id": app_sub.get("application_id"),
        "annual_income": app_sub.get("annual_income"),
        "monthly_income": app_sub.get("monthly_income"),
        "existing_debt_service": app_sub.get("existing_debt_service"),
        "dti_ratio": app_sub.get("dti_ratio"),
        "employment_months": app_sub.get("employment_months"),
        "requested_amount": app_sub.get("requested_amount"),
        "currency": app_sub.get("currency"),
        "address_proof_status": app_sub.get("address_proof_status"),
        "credit_score": bureau_sub.get("credit_score"),
        "delinquencies_24m": bureau_sub.get("delinquencies_24m"),
        "account_age_months": bureau_sub.get("account_age_months"),
        "payslip_status": payslip_sub.get("status"),
    }

    return {
        "decision_id": str(dec_uuid),
        "application_id": str(application_id),
        "applicant_id": str(app_sub.get("applicant_id") or application_id),
        "decided_at": decided_at.isoformat() if hasattr(decided_at, "isoformat") else str(decided_at),
        "outcome": str(initial_outcome),
        "original_outcome": str(initial_outcome),
        "initial_outcome": str(initial_outcome),
        "effective_outcome": effective_outcome,
        "policy_version": str(policy_version),
        "model_version": str(model_version),
        "score": float(score_val),
        "contributions": dict(model_sub.get("contributions", {})),
        "rule_checks": rule_checks,
        "cited_sections": cited_sections,
        "chain_length": int(expected_chain_length),
        "chain_head": str(expected_chain_head),
        "status": verification_status,
        "chain_valid": chain_valid,
        "first_broken_seq": first_broken_seq,
        "broken_reason": broken_reason,
        "tamper_reasons": tamper_reasons,
        "evidence_statuses": evidence_statuses,
        "override": latest_override,
        "human_override": human_override,
        "overrides": overrides_list,
        "review_requests": review_requests,
        "snapshots": chain_dicts,
        "preserved_applicant_params": preserved_applicant_params,
        "recording_mode": recording_mode,
        "corrections": corrections,
        "corrections_valid": corrections_valid,
        "corrections_problem": corrections_problem,
    }

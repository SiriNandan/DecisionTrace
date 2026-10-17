"""Pydantic v2 contract models for DecisionTrace reconstruction reports.

The `ReconstructionReport` schema defines the structured output returned when
reconstructing a loan decision from preserved decision-time snapshots. It
supports audit and explanation and is tamper-evident against a retained reference.

Any change to these contract models requires a dated entry in `docs/contract_changelog.md`.

JSON Example:
```json
{
  "decision_id": "2c8d45f2-5317-5e6c-9d93-957f8a1e72c1",
  "outcome": "DECLINE",
  "score": 0.78142,
  "chain_verified": true,
  "replay_verified": true,
  "replay_tolerance": 1e-09,
  "completeness": 1.0,
  "missing_evidence": [],
  "principal_reasons": [
    "Debt-to-income ratio (0.62) exceeds policy limit (0.40)",
    "Credit score (540) is below policy minimum (620)"
  ],
  "rule_checks": [
    {
      "rule_id": "dti_limit",
      "observed_value": 0.62,
      "operator": "<=",
      "threshold": 0.4,
      "passed": false,
      "section": "3.2 Debt-to-income limit",
      "evidence_ref": "rule:dti_limit"
    },
    {
      "rule_id": "min_credit_score",
      "observed_value": 540,
      "operator": ">=",
      "threshold": 620,
      "passed": false,
      "section": "3.3 Minimum credit score",
      "evidence_ref": "rule:min_credit_score"
    }
  ],
  "reasons": [
    {
      "claims": [
        {
          "text": "Applicant debt-to-income ratio at decision time was 0.62.",
          "citation": "application:LOAN-2025-0001",
          "field": "dti_ratio",
          "value": 0.62
        },
        {
          "text": "Policy LP-2025-v2 section 3.2 sets a maximum debt-to-income limit of 0.40.",
          "citation": "3.2 Debt-to-income limit",
          "field": "dti_limit",
          "value": 0.4
        }
      ]
    }
  ],
  "policy_note": "Evaluated under policy LP-2025-v2 (effective 2025-07-01 to 2025-12-31); record supports audit and explanation.",
  "human_override": {
    "new_outcome": "APPROVE",
    "reason": "Verified supplemental savings account statement covering 18 months of debt service; credit risk mitigated under section 5.1.",
    "actor_role": "senior_underwriter",
    "created_at": "2025-11-12T18:00:00+00:00"
  },
  "guardrail_rejections": 0,
  "status": "ok"
}
```
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Union
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator


ScalarValue = Union[float, int, str, bool, None]
DecisionOutcome = Literal["APPROVE", "DECLINE", "REFER"]
ReconstructionStatus = Literal["ok", "timeout", "error", "evidence_unavailable"]

CONTRACT_VERSION = "1.0.0"


class RuleCheck(BaseModel):
    """Recorded policy rule evaluation linking an observed value and threshold to a cited section and evidence reference."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str = Field(..., min_length=1, description="Identifier of the evaluated policy rule")
    observed_value: ScalarValue = Field(
        ..., description="Decision-time observed value evaluated by the rule"
    )
    operator: str = Field(
        ..., min_length=1, description="Comparison operator (e.g. '<=', '>=', '==', '<')"
    )
    threshold: ScalarValue = Field(
        ..., description="Policy threshold against which observed_value was compared"
    )
    passed: bool = Field(..., description="Whether the rule check passed at decision time")
    section: str = Field(..., min_length=1, description="Numbered policy section heading")
    evidence_ref: str = Field(
        ..., min_length=1, description="Preserved snapshot evidence_ref for this rule evaluation"
    )


class Claim(BaseModel):
    """A single factual assertion grounded in a preserved snapshot or policy section."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1, description="Human-readable claim statement")
    citation: str = Field(
        ...,
        min_length=1,
        description="Preserved snapshot evidence_ref or numbered policy section identifier",
    )
    field: str = Field(..., min_length=1, description="Field name in the cited item")
    value: ScalarValue = Field(..., description="Asserted value from the cited item")


class Reason(BaseModel):
    """An explanation reason composed of one or more grounded claims."""

    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(
        default_factory=list,
        description="List of verified factual claims supporting this reason",
    )


class Override(BaseModel):
    """Append-only human override record associated with a decision."""

    model_config = ConfigDict(extra="forbid")

    new_outcome: DecisionOutcome = Field(
        ..., description="Overridden decision outcome ('APPROVE', 'DECLINE', or 'REFER')"
    )
    reason: str = Field(
        ...,
        min_length=20,
        description="Documented justification for the override (at least 20 characters)",
    )
    actor_role: str = Field(
        ..., min_length=1, description="Role of the human actor who recorded the override"
    )
    created_at: str = Field(
        ..., min_length=1, description="ISO-8601 timestamp when the override was recorded"
    )

    @field_validator("created_at", mode="before")
    @classmethod
    def _coerce_datetime(cls, value: str | datetime) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)


class CorrectionNote(BaseModel):
    """A later correction to an input of this decision. The decision itself used the original value."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(..., min_length=1, description="Name of the corrected field")
    old_value: ScalarValue | None = Field(default=None, description="Value the decision was made with (None if absent)")
    new_value: ScalarValue = Field(..., description="Corrected value")
    corrected_at: str = Field(..., min_length=1, description="ISO-8601 timestamp when the correction was recorded")
    reason: str = Field(..., min_length=1, description="Why it was corrected, as written by the reviewer")


class ReconstructionReport(BaseModel):
    """Structured reconstruction report synthesized exclusively from preserved decision-time evidence.

    JSON Example:
    ```json
    {
      "decision_id": "2c8d45f2-5317-5e6c-9d93-957f8a1e72c1",
      "outcome": "DECLINE",
      "score": 0.78142,
      "chain_verified": true,
      "replay_verified": true,
      "replay_tolerance": 1e-09,
      "completeness": 1.0,
      "missing_evidence": [],
      "principal_reasons": [
        "Debt-to-income ratio (0.62) exceeds policy limit (0.40)"
      ],
      "rule_checks": [
        {
          "rule_id": "dti_limit",
          "observed_value": 0.62,
          "operator": "<=",
          "threshold": 0.4,
          "passed": false,
          "section": "3.2 Debt-to-income limit",
          "evidence_ref": "rule:dti_limit"
        }
      ],
      "reasons": [
        {
          "claims": [
            {
              "text": "Applicant debt-to-income ratio at decision time was 0.62.",
              "citation": "application:LOAN-2025-0001",
              "field": "dti_ratio",
              "value": 0.62
            }
          ]
        }
      ],
      "policy_note": "Evaluated under policy LP-2025-v2; record supports audit and explanation.",
      "human_override": null,
      "guardrail_rejections": 0,
      "status": "ok"
    }
    ```
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(..., min_length=1, description="UUID of the reconstructed decision")
    outcome: DecisionOutcome = Field(
        ..., description="Effective outcome ('APPROVE', 'DECLINE', or 'REFER')"
    )
    score: float = Field(..., description="Model default probability score recorded at decision time")
    chain_verified: bool = Field(
        ...,
        description="True when snapshot hash chain verified against retained chain_length and chain_head",
    )
    replay_verified: bool = Field(
        ...,
        description="True when re-scoring preserved features against model_registry matches recorded score within replay_tolerance",
    )
    replay_tolerance: float = Field(
        default=1e-9,
        ge=0.0,
        description="Numerical tolerance used when verifying model score replay",
    )
    completeness: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Fraction of required evidence items available and verified (0.0 to 1.0)",
    )
    missing_evidence: list[str] = Field(
        default_factory=list,
        description="Evidence references that are missing or marked erased_by_retention",
    )
    principal_reasons: list[str] = Field(
        default_factory=list,
        description="High-level summary strings describing primary decision drivers",
    )
    rule_checks: list[RuleCheck] = Field(
        default_factory=list,
        description="Structured rule evaluations captured at decision time",
    )
    reasons: list[Reason] = Field(
        default_factory=list,
        description="Detailed explanation reasons backed by grounded Claim objects",
    )
    policy_note: str = Field(
        ...,
        description="Summary note identifying the effective policy version and governance context",
    )
    human_override: Override | None = Field(
        default=None,
        description="Optional human override record if the initial outcome was overridden",
    )
    plain_language_summary: str | None = Field(
        default=None,
        description=(
            "Optional LLM-written plain-language explanation. Only present when every number and "
            "[citation] in it was found in this session's tool results (runtime guardrail); "
            "the structured fields remain the verified source of truth."
        ),
    )
    recording_mode: Literal["scored", "recorded"] = Field(
        default="scored",
        description=(
            "'scored' = DecisionTrace computed the decision, so replay_verified and rule_checks apply; "
            "'recorded' = the lender's own system decided and DecisionTrace preserved it, so replay_verified is "
            "False because there is nothing for DecisionTrace to re-score"
        ),
    )
    corrections: list[CorrectionNote] = Field(
        default_factory=list,
        description="Corrections made after the decision; the explanation above describes the original inputs",
    )
    guardrail_rejections: int = Field(
        default=0,
        ge=0,
        description="Count of candidate explanation claims rejected by citation/number guardrails",
    )
    guardrail_removed: list[str] = Field(
        default_factory=list,
        description="Plain-language reason for each removed claim or summary (never the removed text itself)",
    )
    status: ReconstructionStatus = Field(
        ...,
        description="Reconstruction execution status ('ok', 'timeout', 'error', or 'evidence_unavailable')",
    )

    @field_validator("decision_id", mode="before")
    @classmethod
    def _coerce_decision_id(cls, value: str | uuid.UUID) -> str:
        if isinstance(value, uuid.UUID):
            return str(value)
        return str(value)


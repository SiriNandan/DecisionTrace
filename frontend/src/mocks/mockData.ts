/**
 * Synthetic mock data used ONLY when VITE_USE_MOCKS=true (or in isolated component unit tests).
 */

import {
  DecisionDetailRecord,
  DecisionSummaryItem,
  OverrideContract,
  ReconstructionReportContract,
} from "../types";

export const MOCK_DECISION_SUMMARIES: DecisionSummaryItem[] = [
  {
    decision_id: "00000000-0000-0000-0000-000000000001",
    application_id: "LOAN-SCEN-0001",
    applicant_id: "APP-0011",
    decided_at: "2026-01-15T10:30:00Z",
    outcome: "DECLINE",
    initial_outcome: "DECLINE",
    effective_outcome: "DECLINE",
    model_version: "loan_lr_v1",
    policy_version: "LP-2026-v1",
    score: 0.8912,
    chain_length: 2,
    chain_head:
      "a1b2c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef0123",
  },
  {
    decision_id: "00000000-0000-0000-0000-000000000008",
    application_id: "LOAN-SCEN-0008",
    applicant_id: "APP-0018",
    decided_at: "2026-02-05T10:30:00Z",
    outcome: "APPROVE",
    initial_outcome: "APPROVE",
    effective_outcome: "APPROVE",
    model_version: "loan_lr_v1",
    policy_version: "LP-2026-v1",
    score: 0.1245,
    chain_length: 2,
    chain_head:
      "b2c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef012345",
  },
  {
    decision_id: "00000000-0000-0000-0000-000000000010",
    application_id: "LOAN-SCEN-0010",
    applicant_id: "APP-0020",
    decided_at: "2025-04-10T11:00:00Z",
    outcome: "APPROVE",
    initial_outcome: "APPROVE",
    effective_outcome: "APPROVE",
    model_version: "loan_lr_v1",
    policy_version: "LP-2025-v1",
    score: 0.0812,
    chain_length: 2,
    chain_head:
      "c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef01234567",
  },
];

export const MOCK_DECISIONS_LIST = MOCK_DECISION_SUMMARIES;

export const MOCK_DECISION_DETAILS: Record<string, DecisionDetailRecord> = {
  "00000000-0000-0000-0000-000000000001": {
    decision_id: "00000000-0000-0000-0000-000000000001",
    application_id: "LOAN-SCEN-0001",
    applicant_id: "APP-0011",
    decided_at: "2026-01-15T10:30:00Z",
    outcome: "DECLINE",
    original_outcome: "DECLINE",
    initial_outcome: "DECLINE",
    effective_outcome: "DECLINE",
    policy_version: "LP-2026-v1",
    model_version: "loan_lr_v1",
    score: 0.8912,
    contributions: {
      dti_ratio: 1.42,
      credit_score: -0.31,
    },
    rule_checks: [
      {
        rule_id: "dti_limit",
        observed_value: 0.62,
        operator: "<=",
        threshold: 0.4,
        result: "FAIL",
        policy_version: "LP-2026-v1",
        section: "3.2 Debt-to-income limit",
      },
    ],
    cited_sections: ["3.2 Debt-to-income limit"],
    chain_length: 2,
    chain_head:
      "a1b2c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef0123",
    status: "verified",
    chain_valid: true,
    first_broken_seq: null,
    broken_reason: null,
    tamper_reasons: [],
    evidence_statuses: {},
    override: null,
    human_override: null,
    overrides: [],
    snapshots: [
      {
        decision_id: "00000000-0000-0000-0000-000000000001",
        seq: 1,
        evidence_type: "application",
        evidence_ref: "application:APP-0011",
        source_id: "src:applicants_csv",
        source_timestamp: "2026-01-15T10:29:50Z",
        captured_at: "2026-01-15T10:30:00Z",
        payload_sha256:
          "1111111111111111111111111111111111111111111111111111111111111111",
        prev_hash:
          "0000000000000000000000000000000000000000000000000000000000000000",
        entry_hash:
          "2222222222222222222222222222222222222222222222222222222222222222",
        payload: {
          applicant_id: "APP-0011",
          annual_income: 38000,
          dti_ratio: 0.62,
          requested_amount: 18000,
        },
      },
      {
        decision_id: "00000000-0000-0000-0000-000000000001",
        seq: 2,
        evidence_type: "rule_check",
        evidence_ref: "rule:dti_limit",
        source_id: "src:policy_engine:LP-2026-v1",
        source_timestamp: "2026-01-15T10:30:00Z",
        captured_at: "2026-01-15T10:30:00Z",
        payload_sha256:
          "3333333333333333333333333333333333333333333333333333333333333333",
        prev_hash:
          "2222222222222222222222222222222222222222222222222222222222222222",
        entry_hash:
          "a1b2c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef0123",
        payload: {
          rule_id: "dti_limit",
          observed_value: 0.62,
          operator: "<=",
          threshold: 0.4,
          result: "FAIL",
          policy_version: "LP-2026-v1",
          section: "3.2 Debt-to-income limit",
        },
      },
    ],
  },
  "00000000-0000-0000-0000-000000000008": {
    decision_id: "00000000-0000-0000-0000-000000000008",
    application_id: "LOAN-SCEN-0008",
    applicant_id: "APP-0018",
    decided_at: "2026-02-05T10:30:00Z",
    outcome: "APPROVE",
    original_outcome: "APPROVE",
    initial_outcome: "APPROVE",
    effective_outcome: "APPROVE",
    policy_version: "LP-2026-v1",
    model_version: "loan_lr_v1",
    score: 0.1245,
    contributions: { dti_ratio: -0.45 },
    rule_checks: [],
    cited_sections: ["3.2 Debt-to-income limit"],
    chain_length: 2,
    chain_head:
      "b2c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef012345",
    status: "tamper_detected",
    chain_valid: false,
    first_broken_seq: 1,
    broken_reason: "payload_sha256 mismatch at seq 1",
    tamper_reasons: ["payload_sha256 mismatch at seq 1"],
    evidence_statuses: {},
    override: null,
    human_override: null,
    overrides: [],
    snapshots: [],
  },
  "00000000-0000-0000-0000-000000000010": {
    decision_id: "00000000-0000-0000-0000-000000000010",
    application_id: "LOAN-SCEN-0010",
    applicant_id: "APP-0020",
    decided_at: "2025-04-10T11:00:00Z",
    outcome: "APPROVE",
    original_outcome: "APPROVE",
    initial_outcome: "APPROVE",
    effective_outcome: "APPROVE",
    policy_version: "LP-2025-v1",
    model_version: "loan_lr_v1",
    score: 0.0812,
    contributions: { dti_ratio: -0.52 },
    rule_checks: [],
    cited_sections: ["4.1 Required documents"],
    chain_length: 2,
    chain_head:
      "c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef01234567",
    status: "evidence_unavailable",
    chain_valid: true,
    first_broken_seq: null,
    broken_reason: null,
    tamper_reasons: [],
    evidence_statuses: {
      "doc:payslip": "erased_by_retention",
    },
    override: null,
    human_override: null,
    overrides: [],
    snapshots: [
      {
        decision_id: "00000000-0000-0000-0000-000000000010",
        seq: 1,
        evidence_type: "document",
        evidence_ref: "doc:payslip",
        source_id: "document_store_v1",
        source_timestamp: "2025-04-10T10:58:00Z",
        captured_at: "2025-04-10T11:00:00Z",
        payload_sha256:
          "4444444444444444444444444444444444444444444444444444444444444444",
        prev_hash:
          "0000000000000000000000000000000000000000000000000000000000000000",
        entry_hash:
          "c3d4e5f60123456789abcdef0123456789abcdef0123456789abcdef01234567",
        payload: {
          document_type: "payslip",
          status: "received",
        },
      },
    ],
  },
};

export const MOCK_DECISION_RECORDS = MOCK_DECISION_DETAILS;

export const MOCK_RECONSTRUCTION_REPORTS: Record<
  string,
  ReconstructionReportContract
> = {
  "00000000-0000-0000-0000-000000000001": {
    decision_id: "00000000-0000-0000-0000-000000000001",
    outcome: "DECLINE",
    score: 0.8912,
    chain_verified: true,
    replay_verified: true,
    replay_tolerance: 1e-9,
    completeness: 1.0,
    missing_evidence: [],
    principal_reasons: [
      "Rule dti_limit (3.2 Debt-to-income limit) failed: observed 0.62 <= 0.4.",
    ],
    rule_checks: [
      {
        rule_id: "dti_limit",
        observed_value: 0.62,
        operator: "<=",
        threshold: 0.4,
        passed: false,
        section: "3.2 Debt-to-income limit",
        evidence_ref: "rule:dti_limit",
      },
    ],
    reasons: [
      {
        claims: [
          {
            text: "Preserved application snapshot application:APP-0011 recorded dti_ratio of 0.62.",
            citation: "application:APP-0011",
            field: "dti_ratio",
            value: 0.62,
          },
          {
            text: "Rule check dti_limit observed 0.62 <= threshold 0.4 (passed=False).",
            citation: "rule:dti_limit",
            field: "observed_value",
            value: 0.62,
          },
          {
            text: "Evaluated under policy section 3.2 Debt-to-income limit.",
            citation: "3.2 Debt-to-income limit",
            field: "section",
            value: "3.2 Debt-to-income limit",
          },
        ],
      },
    ],
    policy_note:
      "Evaluated under policy LP-2026-v1; record supports audit and explanation and is tamper-evident against a retained reference.",
    human_override: null,
    guardrail_rejections: 0,
    status: "ok",
  },
  "00000000-0000-0000-0000-000000000008": {
    decision_id: "00000000-0000-0000-0000-000000000008",
    outcome: "APPROVE",
    score: 0.1245,
    chain_verified: false,
    replay_verified: false,
    replay_tolerance: 1e-9,
    completeness: 1.0,
    missing_evidence: [],
    principal_reasons: [
      "Hash-chain integrity verification failed at seq 1 (payload_sha256 mismatch).",
    ],
    rule_checks: [],
    reasons: [],
    policy_note:
      "Tamper detected against retained decision header reference under LP-2026-v1.",
    human_override: null,
    guardrail_rejections: 0,
    status: "ok",
  },
  "00000000-0000-0000-0000-000000000010": {
    decision_id: "00000000-0000-0000-0000-000000000010",
    outcome: "APPROVE",
    score: 0.0812,
    chain_verified: true,
    replay_verified: true,
    replay_tolerance: 1e-9,
    completeness: 0.8889,
    missing_evidence: ["doc:payslip"],
    principal_reasons: [
      "Required document evidence doc:payslip was erased under retention policy.",
    ],
    rule_checks: [],
    reasons: [],
    policy_note:
      "Partial reconstruction from remaining preserved snapshots under LP-2025-v1.",
    human_override: null,
    guardrail_rejections: 0,
    status: "evidence_unavailable",
  },
};

export function createMockOverride(
  decisionId: string,
  newOutcome: "APPROVE" | "DECLINE" | "REFER",
  reason: string,
  actorRole: string
): OverrideContract {
  const created: OverrideContract = {
    override_id: Date.now(),
    new_outcome: newOutcome,
    reason,
    actor_role: actorRole,
    created_at: new Date().toISOString(),
  };
  const detail = MOCK_DECISION_DETAILS[decisionId];
  if (detail) {
    detail.override = created;
    detail.human_override = created;
    detail.effective_outcome = newOutcome;
    detail.overrides = [created, ...detail.overrides];
  }
  return created;
}


/**
 * TypeScript contract definitions mirroring backend/contract.py and decision_api responses.
 */

export type DecisionOutcome = "APPROVE" | "DECLINE" | "REFER";

export type ReconstructionStatus =
  | "ok"
  | "timeout"
  | "error"
  | "evidence_unavailable";

export interface RuleCheckContract {
  rule_id: string;
  observed_value: number | string | boolean;
  operator: string;
  threshold: number | string | boolean;
  passed: boolean;
  section: string;
  evidence_ref: string;
}

export interface ClaimContract {
  text: string;
  citation: string;
  field: string;
  value: number | string | boolean;
}

export interface ReasonContract {
  claims: ClaimContract[];
}

export interface OverrideContract {
  override_id?: number;
  new_outcome: DecisionOutcome;
  reason: string;
  actor_role: string;
  created_at: string;
}

export interface ReconstructionReportContract {
  decision_id: string;
  outcome: DecisionOutcome;
  score: number;
  chain_verified: boolean;
  replay_verified: boolean;
  replay_tolerance: number;
  completeness: number;
  missing_evidence: string[];
  principal_reasons: string[];
  rule_checks: RuleCheckContract[];
  reasons: ReasonContract[];
  policy_note: string;
  human_override: OverrideContract | null;
  plain_language_summary?: string | null;
  guardrail_rejections: number;
  status: ReconstructionStatus;
}

export interface DecisionSummaryItem {
  decision_id: string;
  application_id: string;
  applicant_id: string;
  decided_at: string;
  outcome: DecisionOutcome;
  initial_outcome: DecisionOutcome;
  effective_outcome: DecisionOutcome;
  model_version: string;
  policy_version: string;
  score: number;
  chain_length: number;
  chain_head: string;
}

export interface DecisionSnapshotRow {
  decision_id: string;
  seq: number;
  evidence_type: string;
  evidence_ref: string;
  source_id: string;
  source_timestamp: string;
  captured_at: string;
  payload_sha256: string;
  prev_hash: string;
  entry_hash: string;
  payload: Record<string, unknown>;
}

export interface DecisionDetailRecord {
  decision_id: string;
  application_id: string;
  applicant_id: string;
  decided_at: string;
  outcome: DecisionOutcome;
  original_outcome?: DecisionOutcome;
  initial_outcome: DecisionOutcome;
  effective_outcome: DecisionOutcome;
  policy_version: string;
  model_version: string;
  score: number;
  contributions: Record<string, number>;
  rule_checks: Array<Record<string, unknown>>;
  cited_sections: string[];
  chain_length: number;
  chain_head: string;
  status: string;
  chain_valid: boolean;
  first_broken_seq: number | null;
  broken_reason: string | null;
  tamper_reasons: string[];
  evidence_statuses: Record<string, string>;
  override: OverrideContract | null;
  human_override?: OverrideContract | null;
  overrides: OverrideContract[];
  snapshots: DecisionSnapshotRow[];
  preserved_applicant_params?: Record<string, unknown>;
}

export interface CreateOverridePayload {
  decision_id: string;
  new_outcome: DecisionOutcome;
  reason: string;
  actor_role: string;
}


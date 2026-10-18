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
  recording_mode?: "scored" | "recorded";
  corrections?: Array<{ field: string; old_value: unknown; new_value: unknown; corrected_at: string; reason: string }>;
  guardrail_rejections: number;
  guardrail_removed?: string[];
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
  recording_mode?: "scored" | "recorded";
  corrections?: CorrectionItem[];
  corrections_valid?: boolean;
  signals?: SignalItem[];
  review_requests?: Array<{ reason: string; created_at: string }>;
}

export interface CreateOverridePayload {
  decision_id: string;
  new_outcome: DecisionOutcome;
  reason: string;
  actor_role: string;
}


/* ---- Connect your data (field matching and import) ---- */

export interface FieldRuleContract {
  column?: string | null;
  const?: string | number | null;
  transform: "text" | "number" | "percent_to_fraction" | "date" | "enum";
  date_formats: string[];
  value_map: Record<string, string>;
  confidence: number;
  note: string;
}

export interface MappingSpecContract {
  spec_version: 1;
  source_format: "csv" | "json";
  external_id: FieldRuleContract | null;
  subject_ref: FieldRuleContract | null;
  decided_at: FieldRuleContract | null;
  outcome: FieldRuleContract | null;
  score: FieldRuleContract | null;
  model_version: FieldRuleContract | null;
  policy_version: FieldRuleContract | null;
  reasons: { column: string; separator: string; code_text: Record<string, string>; confidence: number; note: string } | null;
  evidence: Array<{ evidence_type: string; source_id: string; fields: Record<string, FieldRuleContract> }>;
  ignored_columns: Record<string, string>;
}

export interface MappingPreview {
  rows_total: number;
  rows_checked: number;
  valid: number;
  invalid: number;
  problems: Array<{ row: number; problems: string[] }>;
}

export interface MappingRecord {
  mapping_id: number;
  name: string;
  version: number;
  spec: MappingSpecContract;
  spec_sha256: string;
  proposed_by: string;
  status: "proposed" | "approved" | "rejected";
  gaps: string[];
  preview?: MappingPreview;
}

export interface MappingProposal extends MappingRecord {
  issues: string[];
  columns: string[];
  outcome_values: string[];
}

export interface ImportSummary {
  mapping_id: number;
  version: number;
  dry_run: boolean;
  created: number;
  replayed: number;
  rejected: number;
  results: Array<{ row: number; status: string; problems?: string[] }>;
}

export type SourceFormat = "csv" | "json";
export interface SourceData {
  format: SourceFormat;
  content?: string;
  rows?: Array<Record<string, unknown>>;
}

/* ---- Data checks and corrections ---- */

export interface CorrectionItem {
  seq: number;
  evidence_ref: string;
  field: string;
  old_value: unknown;
  new_value: unknown;
  reason: string;
  source: "suggested" | "person";
  created_at: string;
}

export interface FindingItem {
  finding_id: number;
  decision_id: string;
  rule_id: string;
  kind: "missing" | "implausible" | "conflict" | "inconsistent" | "outlier";
  severity: "info" | "warning" | "error";
  evidence_ref: string;
  field: string;
  observed: unknown;
  message: string;
  suggested_fix: { evidence_ref: string; field: string; new_value: number | string; basis: string } | null;
  ai_note: string | null;
  found_by: string;
  created_at: string;
  status: "open" | "corrected" | "dismissed";
}

export interface QualityRunResult {
  decisions_checked: number;
  new_findings: number;
  open_total: number;
  by_severity: Record<string, number>;
  notes: string[];
  ai_used: boolean;
}

/* ---- Sign-in, customer links and risk signals ---- */

export interface Me {
  role: string;
  kind: "staff" | "customer" | "sample";
  tenant_id: string;
  tenant_name: string;
  label: string;
  email?: string;
  roles?: string[];
  scopes: string[];
  decision_id: string | null;
  expires_at: string | null;
}

export interface TeamRole { role: string; name: string; description: string; can_limit_to_portfolios: boolean }
export interface TeamMember {
  credential_id: number;
  label: string;
  role: string;
  portfolios: string[] | null;
  expires_at: string | null;
  withdrawn_at: string | null;
  created_at: string;
  email: string | null;
  roles: string[];
  state: "active" | "expired" | "withdrawn";
}
export interface TeamState { members: TeamMember[]; you: number | null; portfolios: Array<{ name: string; decisions: number }>; roles: TeamRole[] }
export interface NewTeamKey { credential_id: number; key: string; role: string; roles: string[]; label: string; portfolios: string[] | null; expires_at: string | null; email: string; email_status: "sent" | "not_configured" | "failed" }
export interface MappingListItem { mapping_id: number; name: string; version: number; status: "proposed" | "approved" | "rejected"; proposed_by: string; gaps: string[] }

export interface ServiceHealth {
  status: "ok" | "needs_upgrade" | "database_down" | "unreachable" | "unknown";
  database: string;
}

export interface ClaimLinkCreated {
  claim_id: number;
  decision_id: string;
  token: string;
  expires_at: string;
  path: string;
}

export interface SignalItem {
  signal_id: number;
  decision_id: string | null;
  scope: "decision" | "batch" | "access";
  signal_type: string;
  severity: "info" | "warning" | "high";
  message: string;
  details: Record<string, unknown>;
  why?: { text: string; source: "rule" | "ai" } | null;
  created_at: string;
  status: "open" | "acknowledged" | "cleared";
}

export interface SignalRunResult {
  decisions_checked: number;
  new_signals: number;
  open_total: number;
  by_severity: Record<string, number>;
}

/* ---- Seals, customer choices and paging ---- */

export interface SealProof {
  sealed: boolean;
  sealed_at?: string;
  leaf?: Record<string, unknown>;
  head?: { seq: number; tenant_id: string; root: string; signed_at: string; leaf_count: number };
  corrections?: { sealed: boolean; sealed_at?: string };
}

export interface SealBatch {
  seq: number;
  leaf_count: number;
  root: string;
  signed_at: string;
  published: boolean;
}

export interface SealCheck {
  ok: boolean;
  batches: number;
  records: number;
  checked_published_copy: boolean;
  not_yet_sealed: number;
  problems: Array<{ batch: number; problem: string }>;
}

export interface ConsentState {
  purpose: "ai_summary";
  policy: "ask" | "allow" | "never";
  choice: { granted: boolean; recorded_at: string; given_by: string } | null;
  ai_summary_on: boolean;
  history: Array<{ granted: boolean; recorded_at: string; given_by: string }>;
}

export interface DecisionsPage {
  items: DecisionSummaryItem[];
  next: string | null;
}

/**
 * Plain-language helpers: translate contract/database terms into customer-friendly copy.
 * Everything here is derived from the data returned by the API (no invented figures).
 */
import {
  DecisionOutcome,
  DecisionSnapshotRow,
  DecisionSummaryItem,
  RuleCheckContract,
} from "../types";

export type Tone = "good" | "bad" | "review" | "neutral";

export interface OutcomeCopy {
  label: string;
  headline: string;
  tone: Tone;
  icon: string;
}

export function outcomeCopy(outcome: DecisionOutcome | string): OutcomeCopy {
  switch (String(outcome).toUpperCase()) {
    case "APPROVE":
      return { label: "Approved", headline: "Your application was approved", tone: "good", icon: "✓" };
    case "DECLINE":
      return { label: "Not approved", headline: "Your application was not approved", tone: "bad", icon: "✕" };
    case "REFER":
      return { label: "In review", headline: "Your application needs a person to review it", tone: "review", icon: "◔" };
    default:
      return { label: String(outcome), headline: "Decision recorded", tone: "neutral", icon: "•" };
  }
}

const RULE_TITLES: Record<string, string> = {
  dti_limit: "Debt compared with income",
  min_credit_score: "Credit score",
  required_documents: "Supporting documents",
  delinquencies_manual_review: "Missed payments (last 2 years)",
};

export function ruleTitle(ruleId: string): string {
  return RULE_TITLES[ruleId] ?? prettify(ruleId);
}

export function ruleId(rc: { rule_id?: unknown; evidence_ref?: unknown }): string {
  return String(rc.rule_id ?? String(rc.evidence_ref ?? "").replace(/^rule:/, ""));
}

const pct = (v: number) => `${Math.round(v * 1000) / 10}%`;

export function formatRuleValue(id: string, v: unknown): string {
  if (typeof v === "number") {
    if (id === "dti_limit") return pct(v);
    return String(v);
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (v === "received") return "Received";
  if (v === "missing") return "Not received";
  return prettify(String(v));
}

const OPERATOR_WORDS: Record<string, string> = {
  "<=": "at most",
  "<": "below",
  ">=": "at least",
  ">": "above",
  "==": "must be",
};

export function ruleRequirement(id: string, operator: string, threshold: unknown): string {
  if (id === "required_documents") return "Required documents must be received";
  const word = OPERATOR_WORDS[operator] ?? operator;
  return `Needs to be ${word} ${formatRuleValue(id, threshold)}`;
}

/** Concrete, data-derived next step for a rule that was not met. */
export function ruleNextStep(rc: RuleCheckContract | Record<string, unknown>): string | null {
  const id = ruleId(rc as RuleCheckContract);
  const obs = (rc as RuleCheckContract).observed_value;
  const thr = (rc as RuleCheckContract).threshold;
  if (id === "dti_limit" && typeof obs === "number" && typeof thr === "number") {
    return `Bring monthly debts down relative to income, or ask for a smaller amount. The ratio needs to be ${pct(thr)} or lower; it was ${pct(obs)}.`;
  }
  if (id === "min_credit_score" && typeof obs === "number" && typeof thr === "number") {
    return `A score of ${thr} or higher is needed. It was ${obs}, which is ${Math.max(thr - obs, 0)} points short.`;
  }
  if (id === "required_documents") {
    return "Provide the missing supporting document (for example a recent payslip) so the application can be reassessed.";
  }
  if (id === "delinquencies_manual_review" && typeof obs === "number") {
    return `${obs} missed payments were found in the last 2 years, which triggers a manual review by a person.`;
  }
  return null;
}

const FEATURE_LABELS: Record<string, string> = {
  dti_ratio: "Debt compared with income",
  credit_score: "Credit score",
  income_annual: "Annual income",
  requested_amount: "Amount requested",
  delinquencies_24m: "Missed payments (2 years)",
  employment_months: "Time in current job",
  account_age_months: "Age of oldest account",
  annual_income: "Annual income",
  monthly_income: "Monthly income",
  payslip: "Payslip",
};

export function featureLabel(key: string): string {
  return FEATURE_LABELS[key] ?? prettify(key);
}

export function formatFieldValue(key: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") {
    if (key === "dti_ratio") return pct(v);
    if (/income|amount|salary|balance/i.test(key)) return `£${v.toLocaleString("en-GB")}`;
    if (/months/.test(key)) return `${v} months`;
    return String(Number.isInteger(v) ? v : Math.round(v * 10000) / 10000);
  }
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v)) return formatDate(v);
  return prettify(String(v));
}

export function prettify(s: string): string {
  const t = s.replace(/[_-]+/g, " ").trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function formatDate(iso: string, withTime = false): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export interface EvidenceKind {
  label: string;
  blurb: string;
}

const EVIDENCE_KINDS: Record<string, EvidenceKind> = {
  application: { label: "Application form", blurb: "What was submitted when applying" },
  bureau: { label: "Credit report", blurb: "Information from the credit reference agency" },
  account_history: { label: "Account history", blurb: "How existing accounts have been managed" },
  document: { label: "Supporting document", blurb: "Documents provided to confirm income" },
  consent: { label: "Your consent", blurb: "Permissions that were given" },
  analyst_note: { label: "Reviewer note", blurb: "Notes added during assessment" },
  policy: { label: "Lending policy", blurb: "The rules in force on the decision date" },
  model: { label: "Risk assessment", blurb: "The calculation behind the risk estimate" },
  rule_check: { label: "Policy rule result", blurb: "How one lending rule was applied" },
};

export function evidenceKind(type: string): EvidenceKind {
  return EVIDENCE_KINDS[type] ?? { label: prettify(type), blurb: "Preserved record" };
}

const HIDDEN_FIELD = /sha256|hash|^seq$|version$|_id$|^id$|^source|^policy_family$|^text|^effective_|^preprocessing|^threshold$|^intercept$|^log_odds$|^operator$|^rule_id$/i;

/** Customer-readable key facts from a preserved record (scalars only; hashes/ids hidden). */
export function evidenceFacts(row: DecisionSnapshotRow): Array<{ label: string; value: string }> {
  const out: Array<{ label: string; value: string }> = [];
  if (row.evidence_type === "rule_check") {
    const p = row.payload as Record<string, unknown>;
    const id = String(p.rule_id ?? row.evidence_ref.replace("rule:", ""));
    return [
      { label: "Outcome", value: String(p.result).toUpperCase() === "PASS" ? "Met" : "Not met" },
      { label: "Recorded value", value: formatRuleValue(id, p.observed_value) },
      { label: "Requirement", value: ruleRequirement(id, String(p.operator ?? ""), p.threshold).replace(/^Needs to be /, "") },
    ];
  }
  for (const [k, v] of Object.entries(row.payload ?? {})) {
    if (HIDDEN_FIELD.test(k)) continue;
    if (v !== null && typeof v === "object") continue;
    if (row.evidence_type === "rule_check" && k === "section") continue;
    out.push({ label: k === "result" ? "Result" : featureLabel(k), value: formatFieldValue(k, v) });
  }
  return out;
}

export interface TrustCheck {
  key: string;
  ok: boolean | null;
  title: string;
  detail: string;
}

export interface Recent {
  id: string;
  label: string;
}

export function isDemoFixture(item: DecisionSummaryItem): boolean {
  return /^APP-TEST/i.test(item.applicant_id) || /^APP-TEST/i.test(item.application_id);
}

export function riskLabel(score: number): string {
  if (score > 0 && score < 0.001) return "<0.1%";
  return `${Math.round(score * 1000) / 10}%`;
}

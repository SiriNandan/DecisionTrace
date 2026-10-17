/**
 * API client for DecisionTrace frontend.
 * Uses VITE_API_URL by default; consults src/mocks ONLY when VITE_USE_MOCKS=true.
 */

import { getApiKey, getApiUrl, getClaimToken, isUseMocksEnabled } from "./config";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  FindingItem,
  ClaimLinkCreated,
  ImportSummary,
  MappingProposal,
  MappingRecord,
  MappingSpecContract,
  Me,
  OverrideContract,
  QualityRunResult,
  ServiceHealth,
  SignalItem,
  SignalRunResult,
  ReconstructionReportContract,
  SourceData,
} from "./types";

/** Plain-language fallbacks for when the server gives no readable reason. */
const STATUS_MESSAGES: Record<number, string> = {
  401: "Sign-in is needed to see this.",
  403: "This sign-in isn't allowed to do that.",
  404: "We couldn't find that decision.",
  429: "That's a lot of requests at once. Please wait a moment and try again.",
};

/** Every request carries the sign-in key (when one is configured); reads need it as well as writes. */
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const key = getApiKey();
  const claim = getClaimToken();
  return { ...extra, ...(key ? { "X-API-Key": key } : {}), ...(claim ? { "X-Claim-Token": claim } : {}) };
}

/** fetch that turns "the browser couldn't connect" into a plain message the screens can recognise (status 0). */
async function send(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new ApiError("We couldn’t reach the service.", 0);
  }
}

/** An API failure that keeps the HTTP status so screens can tell "sign in" from "not found". */
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

export type ProblemKind = "signin" | "unreachable" | "upgrade" | "other";

/** What kind of trouble this is, so a screen can offer the right next step. */
export function problemKind(e: unknown): ProblemKind {
  if (!(e instanceof ApiError)) return "other";
  if (e.status === 0) return "unreachable";
  if (e.status === 401 || e.status === 403) return "signin";
  if (e.code === "needs_upgrade") return "upgrade";
  return "other";
}

export const needsSignIn = (e: unknown): boolean =>
  e instanceof ApiError && (e.status === 401 || e.status === 403);

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorDetail = STATUS_MESSAGES[response.status] ?? `HTTP ${response.status}`;
    let errorCode: string | undefined;
    try {
      const errBody = await response.json();
      if (errBody && typeof errBody.code === "string") errorCode = errBody.code;
      if (errBody && typeof errBody.detail === "string") {
        errorDetail = errBody.detail;
      } else if (errBody && Array.isArray(errBody.detail)) {
        errorDetail = errBody.detail
          .map((item: { msg?: string }) => item.msg || JSON.stringify(item))
          .join("; ");
      }
    } catch {
      // ignore json parse error
    }
    throw new ApiError(errorDetail, response.status, errorCode);
  }
  return (await response.json()) as T;
}

export async function fetchDecisionsList(
  applicantId?: string
): Promise<DecisionSummaryItem[]> {
  if (isUseMocksEnabled()) {
    const { MOCK_DECISION_SUMMARIES } = await import("./mocks/mockData");
    if (applicantId) {
      return MOCK_DECISION_SUMMARIES.filter(
        (d) => d.applicant_id === applicantId
      );
    }
    return MOCK_DECISION_SUMMARIES;
  }

  const baseUrl = getApiUrl();
  const query = applicantId
    ? `?applicant_id=${encodeURIComponent(applicantId)}`
    : "";
  const response = await send(`${baseUrl}/decisions${query}`, {
    headers: authHeaders(),
  });
  return parseJsonOrThrow<DecisionSummaryItem[]>(response);
}

export async function fetchDecisionRecord(
  decisionId: string
): Promise<DecisionDetailRecord> {
  if (isUseMocksEnabled()) {
    const { MOCK_DECISION_DETAILS } = await import("./mocks/mockData");
    const found = MOCK_DECISION_DETAILS[decisionId];
    if (!found) {
      throw new Error(`Mock decision ${decisionId} not found`);
    }
    return found;
  }

  const baseUrl = getApiUrl();
  const response = await send(
    `${baseUrl}/decisions/${encodeURIComponent(decisionId)}`,
    { headers: authHeaders() }
  );
  return parseJsonOrThrow<DecisionDetailRecord>(response);
}

export async function submitDecisionOverride(
  payload: CreateOverridePayload
): Promise<OverrideContract> {
  if (payload.reason.trim().length < 20) {
    throw new Error("Override reason must be at least 20 characters.");
  }

  if (isUseMocksEnabled()) {
    const { createMockOverride } = await import("./mocks/mockData");
    return createMockOverride(
      payload.decision_id,
      payload.new_outcome,
      payload.reason.trim(),
      payload.actor_role.trim()
    );
  }

  const baseUrl = getApiUrl();
  const response = await send(`${baseUrl}/overrides`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({
      decision_id: payload.decision_id,
      new_outcome: payload.new_outcome,
      reason: payload.reason.trim(),
      actor_role: payload.actor_role.trim(),
    }),
  });
  return parseJsonOrThrow<OverrideContract>(response);
}

export async function fetchReconstructionReport(
  decisionId: string
): Promise<ReconstructionReportContract> {
  if (isUseMocksEnabled()) {
    const { MOCK_RECONSTRUCTION_REPORTS } = await import("./mocks/mockData");
    const found = MOCK_RECONSTRUCTION_REPORTS[decisionId];
    if (!found) {
      throw new Error(`Mock reconstruction report ${decisionId} not found`);
    }
    return found;
  }

  const baseUrl = getApiUrl();
  const response = await send(
    `${baseUrl}/reconstruct/${encodeURIComponent(decisionId)}`,
    {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
    }
  );
  return parseJsonOrThrow<ReconstructionReportContract>(response);
}


/* ---- Connect your data ---- */

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await send(`${getApiUrl()}${path}`, {
    method: "POST",
    headers: authHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(body),
  });
  return parseJsonOrThrow<T>(response);
}

export async function proposeMapping(name: string, source: SourceData): Promise<MappingProposal> {
  return postJson<MappingProposal>("/v1/mappings/propose", { name, ...source });
}

export async function saveMapping(name: string, spec: MappingSpecContract, source?: SourceData): Promise<MappingRecord> {
  return postJson<MappingRecord>("/v1/mappings", { name, spec, source: source ?? null });
}

export async function approveMapping(mappingId: number, note = ""): Promise<MappingRecord> {
  return postJson<MappingRecord>(`/v1/mappings/${mappingId}/approve`, { note });
}

export async function runImport(mappingId: number, source: SourceData, dryRun = false): Promise<ImportSummary> {
  return postJson<ImportSummary>("/v1/imports", { mapping_id: mappingId, dry_run: dryRun, ...source });
}

/* ---- Data checks ---- */

export async function runQualityChecks(recent = 200): Promise<QualityRunResult> {
  return postJson<QualityRunResult>("/v1/quality/run", { recent });
}

export async function fetchFindings(status: FindingItem["status"] | "all" = "open"): Promise<FindingItem[]> {
  const response = await send(`${getApiUrl()}/v1/quality/findings?status=${status}`, { headers: authHeaders() });
  return parseJsonOrThrow<FindingItem[]>(response);
}

export async function resolveFinding(
  findingId: number,
  action: "correct" | "dismiss",
  note: string,
  newValue?: number | string
): Promise<{ finding_id: number; status: string }> {
  return postJson(`/v1/quality/findings/${findingId}/resolve`, { action, note, ...(newValue !== undefined ? { new_value: newValue } : {}) });
}

/* ---- Sign-in, health, customer links and risk signals ---- */

async function getJson<T>(path: string): Promise<T> {
  return parseJsonOrThrow<T>(await send(`${getApiUrl()}${path}`, { headers: authHeaders() }));
}

export async function fetchMe(): Promise<Me> {
  if (isUseMocksEnabled()) {
    return { role: "sample", kind: "sample", tenant_id: "sample", tenant_name: "Sample decisions", label: "", scopes: [], decision_id: null, expires_at: null };
  }
  return getJson<Me>("/v1/me");
}

/** Check a key or link code WITHOUT storing it, so a mistyped one can't sign out someone who is already signed in. */
export async function checkCredential(cred: { key?: string; claim?: string }): Promise<Me> {
  const headers: Record<string, string> = {};
  if (cred.key) headers["X-API-Key"] = cred.key;
  if (cred.claim) headers["X-Claim-Token"] = cred.claim;
  return parseJsonOrThrow<Me>(await send(`${getApiUrl()}/v1/me`, { headers }));
}

export async function fetchHealth(): Promise<ServiceHealth> {
  try {
    const response = await fetch(`${getApiUrl()}/health`);
    if (!response.ok) return { status: "unknown", database: "" };
    return (await response.json()) as ServiceHealth;
  } catch {
    return { status: "unreachable", database: "" };
  }
}

export async function createClaimLink(decisionId: string, days = 7, label = ""): Promise<ClaimLinkCreated> {
  return postJson<ClaimLinkCreated>(`/v1/decisions/${encodeURIComponent(decisionId)}/claim-links`, { days, label });
}

export async function runSignalChecks(): Promise<SignalRunResult> {
  return postJson<SignalRunResult>("/v1/signals/run", {});
}

export async function fetchSignals(status: SignalItem["status"] | "all" = "open"): Promise<SignalItem[]> {
  return getJson<SignalItem[]>(`/v1/signals?status=${status}`);
}

export async function reviewSignal(signalId: number, action: "acknowledge" | "clear", note: string): Promise<{ signal_id: number; status: string }> {
  return postJson(`/v1/signals/${signalId}/review`, { action, note });
}

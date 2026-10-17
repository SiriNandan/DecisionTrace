/**
 * API client for DecisionTrace frontend.
 * Uses VITE_API_URL by default; consults src/mocks ONLY when VITE_USE_MOCKS=true.
 */

import { getApiKey, getApiUrl, isUseMocksEnabled } from "./config";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  ImportSummary,
  MappingProposal,
  MappingRecord,
  MappingSpecContract,
  OverrideContract,
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
  return { ...extra, ...(key ? { "X-API-Key": key } : {}) };
}

/** An API failure that keeps the HTTP status so screens can tell "sign in" from "not found". */
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export const needsSignIn = (e: unknown): boolean =>
  e instanceof ApiError && (e.status === 401 || e.status === 403);

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorDetail = STATUS_MESSAGES[response.status] ?? `HTTP ${response.status}`;
    try {
      const errBody = await response.json();
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
    throw new ApiError(errorDetail, response.status);
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
  const response = await fetch(`${baseUrl}/decisions${query}`, {
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
  const response = await fetch(
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
  const response = await fetch(`${baseUrl}/overrides`, {
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
  const response = await fetch(
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
  const response = await fetch(`${getApiUrl()}${path}`, {
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

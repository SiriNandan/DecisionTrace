/**
 * API client for DecisionTrace frontend.
 * Uses VITE_API_URL by default; consults src/mocks ONLY when VITE_USE_MOCKS=true.
 */

import { getApiKey, getApiUrl, isUseMocksEnabled } from "./config";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  OverrideContract,
  ReconstructionReportContract,
} from "./types";

async function parseJsonOrThrow<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorDetail = `HTTP ${response.status}`;
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
    throw new Error(errorDetail);
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
  const response = await fetch(`${baseUrl}/decisions${query}`);
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
    `${baseUrl}/decisions/${encodeURIComponent(decisionId)}`
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
    headers: {
      "Content-Type": "application/json",
      ...(getApiKey() ? { "X-API-Key": getApiKey() } : {}),
    },
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
      headers: {
        "Content-Type": "application/json",
        ...(getApiKey() ? { "X-API-Key": getApiKey() } : {}),
      },
    }
  );
  return parseJsonOrThrow<ReconstructionReportContract>(response);
}


import React, { useMemo, useState } from "react";
import { DecisionSummaryItem } from "../types";
import { renderOutcomeBadge } from "./SyntheticBanner";

export interface CaseListProps {
  cases: DecisionSummaryItem[];
  selectedDecisionId: string | null;
  onSelectCase: (decisionId: string) => void;
  loading?: boolean;
  error?: string | null;
  onRefresh?: () => void;
}

export const CaseList: React.FC<CaseListProps> = ({
  cases,
  selectedDecisionId,
  onSelectCase,
  loading = false,
  error = null,
  onRefresh,
}) => {
  const [searchQuery, setSearchQuery] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState<string>("ALL");

  const filteredCases = useMemo(() => {
    return cases.filter((item) => {
      const matchesOutcome =
        outcomeFilter === "ALL" ||
        item.effective_outcome === outcomeFilter ||
        item.initial_outcome === outcomeFilter;
      if (!matchesOutcome) {
        return false;
      }
      if (!searchQuery.trim()) {
        return true;
      }
      const q = searchQuery.trim().toLowerCase();
      return (
        item.decision_id.toLowerCase().includes(q) ||
        item.applicant_id.toLowerCase().includes(q) ||
        item.application_id.toLowerCase().includes(q) ||
        item.policy_version.toLowerCase().includes(q)
      );
    });
  }, [cases, outcomeFilter, searchQuery]);

  return (
    <section
      className="dt-panel dt-caselist-panel"
      aria-labelledby="caselist-heading"
      data-testid="case-list"
    >
      <div className="dt-panel-header">
        <h2 id="caselist-heading" className="dt-panel-title">
          <span aria-hidden="true">🗂 </span>Recorded Decisions ({filteredCases.length})
        </h2>
        {onRefresh && (
          <button
            type="button"
            className="dt-btn dt-btn-secondary"
            onClick={onRefresh}
            aria-label="Refresh decision cases list"
          >
            ↻ Refresh
          </button>
        )}
      </div>

      <div className="dt-caselist-controls">
        <div className="dt-field-group">
          <label htmlFor="case-search-input" className="dt-label">
            Filter by Applicant, Loan ID, or Policy
          </label>
          <input
            id="case-search-input"
            type="search"
            className="dt-input"
            placeholder="e.g. APP-0011, LOAN-SCEN, LP-2026-v1..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>

        <div className="dt-field-group">
          <label htmlFor="case-outcome-filter" className="dt-label">
            Outcome Filter
          </label>
          <select
            id="case-outcome-filter"
            className="dt-select"
            value={outcomeFilter}
            onChange={(e) => setOutcomeFilter(e.target.value)}
          >
            <option value="ALL">All Outcomes</option>
            <option value="APPROVE">✓ APPROVE (Approved)</option>
            <option value="DECLINE">✗ DECLINE (Declined)</option>
            <option value="REFER">⚑ REFER (Manual Review)</option>
          </select>
        </div>
      </div>

      {loading && (
        <div className="dt-status-box dt-status-info" role="status">
          <span aria-hidden="true">⏳ </span>Loading recorded decisions...
        </div>
      )}

      {error && (
        <div className="dt-status-box dt-status-error" role="alert">
          <span aria-hidden="true">⛔ [ERROR] </span>
          <span>Failed to load cases: {error}</span>
        </div>
      )}

      {!loading && !error && filteredCases.length === 0 && (
        <div className="dt-status-box dt-status-neutral" role="status">
          <span aria-hidden="true">ℹ [EMPTY] </span>
          <span>No matching decision records found.</span>
        </div>
      )}

      <ul
        className="dt-case-items"
        aria-label="Decision Cases"
        data-testid="case-list-items"
      >
        {filteredCases.map((item) => {
          const isSelected = item.decision_id === selectedDecisionId;
          const hasOverride = item.effective_outcome !== item.initial_outcome;
          return (
            <li key={item.decision_id} className="dt-case-list-li">
              <button
                type="button"
                className={`dt-case-card ${isSelected ? "dt-case-card-selected" : ""}`}
                onClick={() => onSelectCase(item.decision_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectCase(item.decision_id);
                  }
                }}
                aria-pressed={isSelected}
                data-testid={`case-item-${item.decision_id}`}
              >
                <div className="dt-case-card-top">
                  <span className="dt-case-applicant">
                    <strong>{item.applicant_id}</strong>{" "}
                    <span className="dt-case-appid">({item.application_id})</span>
                  </span>
                  {renderOutcomeBadge(item.effective_outcome)}
                </div>

                <div className="dt-case-card-meta">
                  <span>
                    <strong>Policy:</strong> {item.policy_version}
                  </span>
                  <span>
                    <strong>Score:</strong> {item.score.toFixed(4)}
                  </span>
                  <span>
                    <strong>Chain:</strong> {item.chain_length} items
                  </span>
                </div>

                {hasOverride && (
                  <div className="dt-case-override-tag">
                    <span aria-hidden="true">✎ [OVERRIDDEN] </span>
                    Original: {item.initial_outcome} → Override:{" "}
                    {item.effective_outcome}
                  </div>
                )}

                <div className="dt-case-card-footer">
                  <code className="dt-mono-small">{item.decision_id}</code>
                  <time className="dt-timestamp" dateTime={item.decided_at}>
                    {item.decided_at}
                  </time>
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
};

export default CaseList;

import React from "react";
import { ReconstructionReportContract } from "../types";
import {
  renderOutcomeBadge,
  scrollToEvidenceCitation,
} from "./SyntheticBanner";

export interface ReconstructionReportProps {
  report: ReconstructionReportContract;
  onCitationClick?: (citation: string) => void;
}

export const ReconstructionReport: React.FC<ReconstructionReportProps> = ({
  report,
  onCitationClick,
}) => {
  const completenessPct = Math.round(report.completeness * 100);
  const guardrailCount = Number(report.guardrail_rejections ?? 0);

  const handleChipActivate = (citation: string) => {
    scrollToEvidenceCitation(citation, onCitationClick);
  };

  return (
    <section
      className="dt-panel dt-reconstruction-report"
      aria-labelledby="recon-report-heading"
      data-testid="reconstruction-report"
    >
      <div className="dt-panel-header">
        <div>
          <h2 id="recon-report-heading" className="dt-panel-title">
            <span aria-hidden="true">🔎 </span>Cited Reconstruction Report
          </h2>
          <p className="dt-subtitle">
            Decision ID: <code>{report.decision_id}</code> | Status:{" "}
            <strong data-testid="report-status-value">{report.status}</strong>
          </p>
        </div>
      </div>

      {/* Status State Banners: timeout, error, evidence_unavailable */}
      {report.status === "timeout" && (
        <div
          className="dt-alert dt-alert-error"
          role="alert"
          data-testid="report-state-timeout"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">⏱ [TIMEOUT] </span>
            <strong>
              Reconstruction Timed Out (status: timeout) — Exceeded Wall-Clock
              Limit
            </strong>
          </div>
          <p>
            The reconstruction agent exceeded the 30-second execution limit and
            returned a fallback report.
          </p>
        </div>
      )}

      {report.status === "error" && (
        <div
          className="dt-alert dt-alert-error"
          role="alert"
          data-testid="report-state-error"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">⛔ [ERROR] </span>
            <strong>
              Reconstruction Failed Validation or Tool Limit (status: error)
            </strong>
          </div>
          <p>
            The reconstruction agent encountered an error after retry or
            exceeded the 8-tool-call limit.
          </p>
        </div>
      )}

      {report.status === "evidence_unavailable" && (
        <div
          className="dt-alert dt-alert-warning"
          role="alert"
          data-testid="report-state-evidence-unavailable"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">⚠ [EVIDENCE UNAVAILABLE] </span>
            <strong>
              Preserved Evidence Incomplete or Erased by Retention (status:
              evidence_unavailable)
            </strong>
          </div>
          <p>
            Required decision-time evidence was marked{" "}
            <code>erased_by_retention</code> or <code>missing</code>. The
            explanation below is reconstructed strictly from the remaining
            preserved snapshots.
          </p>
          {report.missing_evidence.length > 0 && (
            <p>
              <strong>Unavailable Evidence Refs:</strong>{" "}
              {report.missing_evidence.map((ref) => (
                <code key={ref} className="dt-missing-ref-pill">
                  {ref}
                </code>
              ))}
            </p>
          )}
        </div>
      )}

      {/* Top Metrics & Verification Badges */}
      <div className="dt-summary-grid">
        <div className="dt-summary-card">
          <span className="dt-summary-label">Reconstructed Outcome</span>
          <div data-testid="report-outcome">
            {renderOutcomeBadge(report.outcome)}
          </div>
        </div>

        <div className="dt-summary-card">
          <span className="dt-summary-label">Model Score</span>
          <div data-testid="report-score">
            <strong>{report.score.toFixed(4)}</strong>
          </div>
        </div>

        {/* Chain badge: green ONLY when chain_verified, else red warning */}
        <div className="dt-summary-card">
          <span className="dt-summary-label">Hash-Chain Integrity</span>
          <div>
            {report.chain_verified ? (
              <span
                className="dt-badge dt-badge-chain-ok"
                data-testid="chain-badge"
                data-chain-verified="true"
              >
                <span aria-hidden="true">✓ </span>
                <span>Chain Verified</span>
              </span>
            ) : (
              <span
                className="dt-badge dt-badge-chain-error"
                role="alert"
                data-testid="chain-badge"
                data-chain-verified="false"
              >
                <span aria-hidden="true">⚠ </span>
                <span>Chain Warning: Tamper Detected / Unverified</span>
              </span>
            )}
          </div>
        </div>

        {/* Replay Verification Status */}
        <div className="dt-summary-card">
          <span className="dt-summary-label">Score Replay Status</span>
          <div>
            {report.replay_verified ? (
              <span
                className="dt-badge dt-badge-chain-ok"
                data-testid="replay-badge"
              >
                <span aria-hidden="true">✓ </span>
                <span>
                  Replay Verified (±{report.replay_tolerance.toExponential(0)})
                </span>
              </span>
            ) : (
              <span
                className="dt-badge dt-badge-chain-error"
                role="alert"
                data-testid="replay-badge"
              >
                <span aria-hidden="true">⚠ </span>
                <span>
                  Replay Mismatch (tol ±
                  {report.replay_tolerance.toExponential(0)})
                </span>
              </span>
            )}
          </div>
        </div>

        {/* Completeness Percentage */}
        <div className="dt-summary-card">
          <span className="dt-summary-label">Evidence Completeness</span>
          <div data-testid="report-completeness">
            <strong>{completenessPct}%</strong>{" "}
            <span>
              ({report.completeness === 1 ? "Complete" : "Partial Record"})
            </span>
          </div>
        </div>
      </div>

      {/* Guardrail Rejections Notice */}
      <div
        className={`dt-guardrail-banner ${
          guardrailCount > 0
            ? "dt-guardrail-banner-active"
            : "dt-guardrail-banner-clean"
        }`}
        role="status"
        data-testid="guardrail-line"
      >
        <span aria-hidden="true">
          {guardrailCount > 0 ? "🛡 [GUARDRAIL ACTIVE] " : "✓ [GUARDRAIL CHECK] "}
        </span>
        <strong>
          {guardrailCount} unsupported claims were removed by guardrails
        </strong>
      </div>

      {/* Missing Evidence List if any */}
      {report.missing_evidence.length > 0 && (
        <div
          className="dt-subsection"
          data-testid="report-missing-evidence-section"
        >
          <h3 className="dt-subsection-title">
            <span aria-hidden="true">⚠ </span>Missing or Erased Evidence (
            {report.missing_evidence.length})
          </h3>
          <ul className="dt-inline-list">
            {report.missing_evidence.map((evRef) => (
              <li key={evRef}>
                <code className="dt-missing-ref-pill">⚠ {evRef}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Human Override Details if present */}
      {report.human_override && (
        <div
          className="dt-alert dt-alert-override"
          role="region"
          aria-label="Human Override in Reconstruction Report"
          data-testid="report-human-override"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">✎ [HUMAN OVERRIDE] </span>
            <strong>Overridden to {report.human_override.new_outcome}</strong>
          </div>
          <p>
            <strong>Actor Role:</strong> {report.human_override.actor_role} |{" "}
            <strong>Recorded At:</strong> {report.human_override.created_at}
          </p>
          <p>
            <strong>Reason:</strong> {report.human_override.reason}
          </p>
        </div>
      )}

      {/* Principal Reasons */}
      <div className="dt-subsection" data-testid="report-principal-reasons">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">📌 </span>Principal Reasons (
          {report.principal_reasons.length})
        </h3>
        <ul className="dt-principal-reasons-list">
          {report.principal_reasons.map((pr, idx) => (
            <li key={`${idx}-${pr}`}>{pr}</li>
          ))}
        </ul>
      </div>

      {/* Rule Checks Table */}
      <div className="dt-subsection" data-testid="report-rule-checks">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">📏 </span>Recorded Policy Rule Checks (
          {report.rule_checks.length})
        </h3>
        <div className="dt-table-scroll">
          <table
            className="dt-table dt-rulechecks-table"
            aria-label="Recorded Policy Rule Checks"
          >
            <thead>
              <tr>
                <th scope="col">Rule ID</th>
                <th scope="col">Observed Value</th>
                <th scope="col">Operator</th>
                <th scope="col">Threshold</th>
                <th scope="col">Result</th>
                <th scope="col">Policy Section</th>
                <th scope="col">Evidence Citation</th>
              </tr>
            </thead>
            <tbody>
              {report.rule_checks.map((rc) => (
                <tr key={`${rc.rule_id}-${rc.evidence_ref}`}>
                  <td>
                    <code>{rc.rule_id}</code>
                  </td>
                  <td>
                    <code>{String(rc.observed_value)}</code>
                  </td>
                  <td>
                    <code>{rc.operator}</code>
                  </td>
                  <td>
                    <code>{String(rc.threshold)}</code>
                  </td>
                  <td>
                    {rc.passed ? (
                      <span className="dt-badge dt-badge-chain-ok">
                        <span aria-hidden="true">✓ </span>Passed
                      </span>
                    ) : (
                      <span className="dt-badge dt-badge-chain-error">
                        <span aria-hidden="true">✗ </span>Failed
                      </span>
                    )}
                  </td>
                  <td>{rc.section}</td>
                  <td>
                    <button
                      type="button"
                      className="dt-citation-chip"
                      onClick={() => handleChipActivate(rc.evidence_ref)}
                      aria-label={`Jump to evidence row ${rc.evidence_ref}`}
                      data-testid={`rule-citation-chip-${rc.evidence_ref}`}
                    >
                      <span aria-hidden="true">🔗 </span>
                      <span>{rc.evidence_ref}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Grounded Claims with Citation Chips */}
      <div className="dt-subsection" data-testid="report-claims-section">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">📑 </span>Grounded Explanation Claims &amp;
          Citations
        </h3>
        {report.reasons.length === 0 ? (
          <p className="dt-helper-text">
            No structured claims available for this report status.
          </p>
        ) : (
          <div className="dt-reasons-container">
            {report.reasons.map((reasonGroup, rIdx) => (
              <div
                key={`reason-group-${rIdx}`}
                className="dt-reason-group"
                data-testid={`reason-group-${rIdx}`}
              >
                <h4 className="dt-reason-group-heading">
                  Reason Group #{rIdx + 1}
                </h4>
                <ul className="dt-claims-list">
                  {reasonGroup.claims.map((claim, cIdx) => (
                    <li
                      key={`claim-${rIdx}-${cIdx}-${claim.citation}`}
                      className="dt-claim-item"
                      data-testid={`claim-item-${rIdx}-${cIdx}`}
                    >
                      <div className="dt-claim-text">{claim.text}</div>
                      <div className="dt-claim-meta">
                        <span className="dt-claim-field">
                          Field: <code>{claim.field}</code> ={" "}
                          <code>{String(claim.value)}</code>
                        </span>
                        <button
                          type="button"
                          className="dt-citation-chip"
                          onClick={() => handleChipActivate(claim.citation)}
                          aria-label={`Scroll to cited evidence ${claim.citation}`}
                          data-testid={`citation-chip-${claim.citation}`}
                          data-citation={claim.citation}
                        >
                          <span aria-hidden="true">🔗 [Cite] </span>
                          <span>{claim.citation}</span>
                        </button>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Policy & Governance Note */}
      <div className="dt-subsection" data-testid="report-policy-note">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">📜 </span>Policy &amp; Governance Note
        </h3>
        <p className="dt-policy-note-box">{report.policy_note}</p>
      </div>
    </section>
  );
};

export default ReconstructionReport;


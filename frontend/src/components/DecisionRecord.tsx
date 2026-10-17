import React, { useMemo, useState } from "react";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionOutcome,
} from "../types";
import {
  getEvidenceRowDomId,
  renderOutcomeBadge,
} from "./SyntheticBanner";

export interface DecisionRecordProps {
  record: DecisionDetailRecord;
  highlightedEvidenceRef?: string | null;
  onSubmitOverride?: (payload: CreateOverridePayload) => Promise<void> | void;
  onTriggerReconstruct?: (decisionId: string) => void;
  reconstructing?: boolean;
}

interface TimelineEventItem {
  id: string;
  timestamp: string;
  label: string;
  detail: string;
  sourceId?: string;
  kind: "source" | "decision" | "override" | "retention";
}

export const DecisionRecord: React.FC<DecisionRecordProps> = ({
  record,
  highlightedEvidenceRef = null,
  onSubmitOverride,
  onTriggerReconstruct,
  reconstructing = false,
}) => {
  const [newOutcome, setNewOutcome] = useState<DecisionOutcome>("APPROVE");
  const [actorRole, setActorRole] = useState<string>(
    "senior_credit_underwriter"
  );
  const [reason, setReason] = useState<string>("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);

  const trimmedReasonLen = reason.trim().length;
  const isReasonValid = trimmedReasonLen >= 20;

  const missingOrErasedItems = useMemo(() => {
    const warnings: Array<{ ref: string; status: string; detail: string }> = [];
    const statusMap = record.evidence_statuses || {};

    for (const [ref, st] of Object.entries(statusMap)) {
      if (st === "erased_by_retention" || st === "missing") {
        warnings.push({
          ref,
          status: st,
          detail:
            st === "erased_by_retention"
              ? "Erased under data retention policy (status: erased_by_retention)"
              : "Marked missing in evidence_status",
        });
      }
    }

    for (const snap of record.snapshots || []) {
      const payloadStatus =
        snap.payload && typeof snap.payload.status === "string"
          ? snap.payload.status
          : null;
      if (
        (payloadStatus === "missing" || payloadStatus === "processing") &&
        !warnings.some((w) => w.ref === snap.evidence_ref)
      ) {
        warnings.push({
          ref: snap.evidence_ref,
          status: payloadStatus,
          detail: `Document snapshot recorded with status='${payloadStatus}' at decision time`,
        });
      }
    }

    return warnings;
  }, [record.evidence_statuses, record.snapshots]);

  const timelineEvents = useMemo<TimelineEventItem[]>(() => {
    const events: TimelineEventItem[] = [];
    for (const snap of record.snapshots || []) {
      events.push({
        id: `snap-${snap.seq}`,
        timestamp: snap.source_timestamp || snap.captured_at,
        label: `[Seq #${snap.seq}] ${snap.evidence_type} (${snap.evidence_ref})`,
        detail: `Captured at ${snap.captured_at} | SHA-256: ${snap.payload_sha256.slice(0, 16)}…`,
        sourceId: snap.source_id,
        kind: "source",
      });
    }
    events.push({
      id: `decision-${record.decision_id}`,
      timestamp: record.decided_at,
      label: `Decision Recorded: ${record.initial_outcome}`,
      detail: `Policy ${record.policy_version}, Model ${record.model_version}, Score ${record.score.toFixed(4)}`,
      kind: "decision",
    });

    const activeOverride = record.override || record.human_override || null;
    if (activeOverride) {
      events.push({
        id: `override-${record.decision_id}`,
        timestamp: activeOverride.created_at,
        label: `Human Override Recorded: ${record.initial_outcome} → ${activeOverride.new_outcome}`,
        detail: `Actor: ${activeOverride.actor_role} — Reason: ${activeOverride.reason}`,
        kind: "override",
      });
    }
    return events;
  }, [record]);

  const handleOverrideSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitSuccess(null);

    if (trimmedReasonLen < 20) {
      setValidationError(
        `Override reason must be at least 20 characters (currently ${trimmedReasonLen} characters).`
      );
      return;
    }
    if (!actorRole.trim()) {
      setValidationError("Actor role is required.");
      return;
    }

    setValidationError(null);
    if (!onSubmitOverride) {
      return;
    }

    try {
      setSubmitting(true);
      await onSubmitOverride({
        decision_id: record.decision_id,
        new_outcome: newOutcome,
        reason: reason.trim(),
        actor_role: actorRole.trim(),
      });
      setReason("");
      setSubmitSuccess(
        `✓ Override recorded (${newOutcome}). Original decision and snapshots remain byte-identical.`
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setValidationError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const activeOverride = record.override || record.human_override || null;

  return (
    <section
      className="dt-panel dt-decision-record"
      aria-labelledby="decision-record-heading"
      data-testid="decision-record"
    >
      <div className="dt-panel-header">
        <div>
          <h2 id="decision-record-heading" className="dt-panel-title">
            <span aria-hidden="true">📋 </span>Preserved Decision Record
          </h2>
          <p className="dt-subtitle">
            Decision ID: <code>{record.decision_id}</code> | Applicant:{" "}
            <strong>{record.applicant_id}</strong> ({record.application_id})
          </p>
        </div>
        {onTriggerReconstruct && (
          <button
            type="button"
            className="dt-btn dt-btn-primary"
            onClick={() => onTriggerReconstruct(record.decision_id)}
            disabled={reconstructing}
            data-testid="reconstruct-trigger-btn"
          >
            {reconstructing
              ? "⏳ Reconstructing Explanation..."
              : "🔍 Reconstruct Cited Explanation"}
          </button>
        )}
      </div>

      {/* Summary Grid */}
      <div className="dt-summary-grid">
        <div className="dt-summary-card">
          <span className="dt-summary-label">Original Outcome</span>
          <div data-testid="decision-original-outcome">
            {renderOutcomeBadge(record.initial_outcome || record.outcome)}
          </div>
        </div>
        <div className="dt-summary-card">
          <span className="dt-summary-label">Effective Outcome</span>
          <div data-testid="decision-effective-outcome">
            {renderOutcomeBadge(record.effective_outcome)}
          </div>
        </div>
        <div className="dt-summary-card">
          <span className="dt-summary-label">Policy &amp; Model</span>
          <div>
            <strong>{record.policy_version}</strong> /{" "}
            <span>{record.model_version}</span>
          </div>
        </div>
        <div className="dt-summary-card">
          <span className="dt-summary-label">Model Risk Score</span>
          <div>
            <strong>{record.score.toFixed(4)}</strong>
          </div>
        </div>
        <div className="dt-summary-card">
          <span className="dt-summary-label">Hash Chain Verification</span>
          <div>
            {record.chain_valid ? (
              <span className="dt-badge dt-badge-chain-ok">
                <span aria-hidden="true">✓ </span>Chain Valid (
                {record.chain_length} snapshots)
              </span>
            ) : (
              <span className="dt-badge dt-badge-chain-error" role="alert">
                <span aria-hidden="true">⚠ </span>Chain Tamper Warning
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Missing or Erased Evidence Warnings */}
      {missingOrErasedItems.length > 0 && (
        <div
          className="dt-alert dt-alert-warning"
          role="alert"
          data-testid="missing-evidence-warnings"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">⚠ [MISSING / ERASED EVIDENCE WARNING] </span>
            <strong>
              {missingOrErasedItems.length} Evidence Item(s) Unavailable or
              Incomplete
            </strong>
          </div>
          <ul className="dt-alert-list">
            {missingOrErasedItems.map((item) => (
              <li key={item.ref}>
                <code>{item.ref}</code> — <strong>[{item.status}]</strong>:{" "}
                {item.detail}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Chain Tamper Warnings */}
      {!record.chain_valid && (
        <div
          className="dt-alert dt-alert-error"
          role="alert"
          data-testid="record-chain-tamper-warning"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">⛔ [TAMPER WARNING] </span>
            <strong>Hash-Chain Integrity Verification Failed</strong>
          </div>
          <p>
            {record.broken_reason ||
              record.tamper_reasons?.join("; ") ||
              "Snapshot chain hash mismatch detected against retained decision header."}
          </p>
        </div>
      )}

      {/* Existing Human Override Banner */}
      {activeOverride && (
        <div
          className="dt-alert dt-alert-override"
          role="region"
          aria-label="Recorded Human Override"
          data-testid="recorded-override-banner"
        >
          <div className="dt-alert-title">
            <span aria-hidden="true">✎ [HUMAN OVERRIDE] </span>
            <strong>
              Original Outcome ({record.initial_outcome}) Overridden to{" "}
              {activeOverride.new_outcome}
            </strong>
          </div>
          <p>
            <strong>Reviewer Role:</strong> {activeOverride.actor_role} |{" "}
            <strong>Timestamp:</strong> {activeOverride.created_at}
          </p>
          <p>
            <strong>Justification:</strong> {activeOverride.reason}
          </p>
        </div>
      )}

      {/* Timeline Section */}
      <div className="dt-subsection" data-testid="decision-timeline">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">🕒 </span>Decision &amp; Evidence Capture
          Timeline
        </h3>
        <ol className="dt-timeline-list" aria-label="Decision Capture Timeline">
          {timelineEvents.map((ev) => (
            <li
              key={ev.id}
              className={`dt-timeline-item dt-timeline-${ev.kind}`}
            >
              <div className="dt-timeline-header">
                <span className="dt-timeline-badge">
                  {ev.kind === "source" && "📥 [SOURCE]"}
                  {ev.kind === "decision" && "⚖ [DECISION]"}
                  {ev.kind === "override" && "✎ [OVERRIDE]"}
                </span>{" "}
                <strong>{ev.label}</strong>
                <time className="dt-timestamp" dateTime={ev.timestamp}>
                  {ev.timestamp}
                </time>
              </div>
              <div className="dt-timeline-detail">
                {ev.sourceId && (
                  <span className="dt-source-tag">
                    source_id: <code>{ev.sourceId}</code>
                  </span>
                )}{" "}
                <span>{ev.detail}</span>
              </div>
            </li>
          ))}
        </ol>
      </div>

      {/* Preserved Evidence Snapshots Panel */}
      <div className="dt-subsection" data-testid="evidence-panel">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">🔒 </span>Preserved Evidence Snapshots (
          {record.snapshots?.length || 0} items)
        </h3>
        <div className="dt-table-scroll">
          <table
            className="dt-table dt-evidence-table"
            aria-label="Preserved Decision Snapshots"
          >
            <thead>
              <tr>
                <th scope="col">Seq</th>
                <th scope="col">Evidence Ref &amp; Type</th>
                <th scope="col">Source ID</th>
                <th scope="col">Source &amp; Captured Timestamps</th>
                <th scope="col">Retention &amp; Hash</th>
                <th scope="col">Snapshot Payload</th>
              </tr>
            </thead>
            <tbody>
              {(record.snapshots || []).map((snap) => {
                const rowDomId = getEvidenceRowDomId(snap.evidence_ref);
                const retentionStatus =
                  record.evidence_statuses?.[snap.evidence_ref] || "available";
                const isErased = retentionStatus === "erased_by_retention";
                const isHighlighted =
                  highlightedEvidenceRef === snap.evidence_ref;

                const sectionList: string[] = [];
                if (snap.payload && typeof snap.payload.section === "string") {
                  sectionList.push(snap.payload.section);
                }
                if (
                  snap.payload &&
                  Array.isArray(snap.payload.cited_sections)
                ) {
                  for (const s of snap.payload.cited_sections) {
                    if (typeof s === "string") {
                      sectionList.push(s);
                    }
                  }
                }

                return (
                  <tr
                    key={`${snap.seq}-${snap.evidence_ref}`}
                    id={rowDomId}
                    data-testid={`evidence-row-${snap.evidence_ref}`}
                    data-evidence-ref={snap.evidence_ref}
                    data-evidence-type={snap.evidence_type}
                    data-evidence-sections={sectionList.join("|")}
                    tabIndex={-1}
                    className={`dt-evidence-row ${
                      isHighlighted ? "dt-evidence-row-highlighted" : ""
                    } ${isErased ? "dt-evidence-row-erased" : ""}`}
                  >
                    <td>
                      <strong>#{snap.seq}</strong>
                    </td>
                    <td>
                      <div>
                        <code className="dt-evidence-ref">
                          {snap.evidence_ref}
                        </code>
                      </div>
                      <span className="dt-evidence-type-tag">
                        {snap.evidence_type}
                      </span>
                    </td>
                    <td>
                      <code data-testid={`source-id-${snap.evidence_ref}`}>
                        {snap.source_id}
                      </code>
                    </td>
                    <td>
                      <div>
                        <strong>Source:</strong>{" "}
                        <time dateTime={snap.source_timestamp}>
                          {snap.source_timestamp}
                        </time>
                      </div>
                      <div>
                        <strong>Captured:</strong>{" "}
                        <time dateTime={snap.captured_at}>
                          {snap.captured_at}
                        </time>
                      </div>
                    </td>
                    <td>
                      {isErased ? (
                        <span className="dt-badge dt-badge-chain-error">
                          <span aria-hidden="true">⚠ </span>Erased by Retention
                        </span>
                      ) : (
                        <span className="dt-badge dt-badge-chain-ok">
                          <span aria-hidden="true">✓ </span>Preserved
                        </span>
                      )}
                      <div className="dt-hash-preview">
                        <small>
                          entry: <code>{snap.entry_hash.slice(0, 12)}…</code>
                        </small>
                      </div>
                    </td>
                    <td>
                      {isErased ? (
                        <em className="dt-erased-note">
                          [Payload redacted — marked erased_by_retention]
                        </em>
                      ) : (
                        <pre className="dt-payload-pre">
                          {JSON.stringify(snap.payload, null, 2)}
                        </pre>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Human Override Form (20-char validation) */}
      <div className="dt-subsection" data-testid="override-form-section">
        <h3 className="dt-subsection-title">
          <span aria-hidden="true">✍️ </span>Record Human Override
          (Append-Only)
        </h3>
        <p className="dt-helper-text">
          Recording an override inserts into the <code>overrides</code> table
          only. The original <code>decisions</code> row and{" "}
          <code>decision_snapshots</code> remain byte-identical.
        </p>

        <form
          onSubmit={handleOverrideSubmit}
          className="dt-override-form"
          noValidate
          data-testid="override-form"
        >
          <div className="dt-form-row">
            <div className="dt-field-group">
              <label htmlFor="override-new-outcome" className="dt-label">
                New Outcome
              </label>
              <select
                id="override-new-outcome"
                className="dt-select"
                value={newOutcome}
                onChange={(e) =>
                  setNewOutcome(e.target.value as DecisionOutcome)
                }
              >
                <option value="APPROVE">✓ APPROVE</option>
                <option value="DECLINE">✗ DECLINE</option>
                <option value="REFER">⚑ REFER</option>
              </select>
            </div>

            <div className="dt-field-group">
              <label htmlFor="override-actor-role" className="dt-label">
                Reviewer Role (actor_role)
              </label>
              <input
                id="override-actor-role"
                type="text"
                className="dt-input"
                value={actorRole}
                onChange={(e) => setActorRole(e.target.value)}
                required
              />
            </div>
          </div>

          <div className="dt-field-group">
            <label htmlFor="override-reason-input" className="dt-label">
              Override Justification (Minimum 20 characters required){" "}
              <span
                className={`dt-char-counter ${
                  isReasonValid ? "dt-char-valid" : "dt-char-invalid"
                }`}
              >
                [{trimmedReasonLen}/20 chars{" "}
                {isReasonValid ? "✓ Valid" : "⚠ Minimum 20 required"}]
              </span>
            </label>
            <textarea
              id="override-reason-input"
              className="dt-textarea"
              rows={3}
              placeholder="Provide a detailed audit justification of at least 20 characters..."
              value={reason}
              onChange={(e) => {
                setReason(e.target.value);
                if (validationError && e.target.value.trim().length >= 20) {
                  setValidationError(null);
                }
              }}
              aria-invalid={!isReasonValid && reason.length > 0}
              aria-describedby="override-reason-help"
            />
            <small id="override-reason-help" className="dt-helper-text">
              Enter at least 20 non-whitespace characters explaining the policy
              basis for this override.
            </small>
          </div>

          {validationError && (
            <div
              className="dt-alert dt-alert-error"
              role="alert"
              data-testid="override-validation-error"
            >
              <span aria-hidden="true">⛔ [VALIDATION ERROR] </span>
              <span>{validationError}</span>
            </div>
          )}

          {submitSuccess && (
            <div
              className="dt-alert dt-alert-success"
              role="status"
              data-testid="override-submit-success"
            >
              <span>{submitSuccess}</span>
            </div>
          )}

          <button
            type="submit"
            className="dt-btn dt-btn-primary"
            disabled={submitting}
            data-testid="override-submit-btn"
          >
            {submitting ? "⏳ Recording Override..." : "✓ Submit Override"}
          </button>
        </form>
      </div>
    </section>
  );
};

export default DecisionRecord;


import React, { useState } from "react";
import { CreateOverridePayload, DecisionOutcome } from "../types";
import { outcomeCopy } from "../lib/plain";

const MIN = 20;

export const ReviewForm: React.FC<{
  decisionId: string;
  currentOutcome: DecisionOutcome;
  onSubmit?: (p: CreateOverridePayload) => Promise<void>;
  /** A customer can only ask for a person to look again; they can't pick a result or a job title. */
  customer?: boolean;
}> = ({ decisionId, currentOutcome, onSubmit, customer = false }) => {
  const [outcome, setOutcome] = useState<DecisionOutcome>(currentOutcome === "APPROVE" ? "REFER" : "APPROVE");
  const [role, setRole] = useState("senior_credit_underwriter");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const len = reason.trim().length;
  const tooShort = len < MIN;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    setError(null);
    if (tooShort || !onSubmit) return;
    setBusy(true);
    try {
      await onSubmit({
        decision_id: decisionId,
        new_outcome: customer ? "REFER" : outcome,
        reason: reason.trim(),
        actor_role: customer ? "customer" : role.trim(),
      });
      setDone(true);
      setReason("");
      setTouched(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="cl-form" onSubmit={submit} noValidate data-testid="review-form">
      <p className="cl-muted">
        {customer
          ? "If you think something is wrong, you can ask for a person at the lender to look at this decision again. Your request is added to the record. The original decision is never overwritten, and only a person can change the result."
          : "A person can ask for this decision to be looked at again. Your request is added to the record. The original decision is never overwritten."}
      </p>
      {!customer && (
      <div className="cl-form-row">
        <label className="cl-field">
          <span>Requested result</span>
          <select value={outcome} onChange={(e) => setOutcome(e.target.value as DecisionOutcome)}>
            {(["APPROVE", "DECLINE", "REFER"] as DecisionOutcome[]).map((o) => (
              <option key={o} value={o}>{outcomeCopy(o).label}</option>
            ))}
          </select>
        </label>
        <label className="cl-field">
          <span>Your role</span>
          <input value={role} onChange={(e) => setRole(e.target.value)} required />
        </label>
      </div>
      )}
      <label className="cl-field">
        <span>{customer ? "What do you think is wrong?" : "Why should this be reviewed?"} <em>(at least {MIN} characters)</em></span>
        <textarea
          rows={4}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          onBlur={() => setTouched(true)}
          aria-invalid={touched && tooShort}
          aria-describedby="review-help"
          placeholder="For example: new payslip received confirming higher income."
        />
      </label>
      <p id="review-help" className={`cl-help ${touched && tooShort ? "is-error" : ""}`} data-testid="review-help">
        {touched && tooShort
          ? `Please write at least ${MIN} characters so reviewers have enough to go on (${len}/${MIN}).`
          : `${len}/${MIN} characters`}
      </p>
      {error && <p className="cl-note cl-note-bad" role="alert">{error}</p>}
      {done && <p className="cl-note cl-note-good" role="status">Thanks. Your review request has been added to this record.</p>}
      <button type="submit" className="cl-btn cl-btn-primary" disabled={busy} data-testid="review-submit">
        {busy ? "Sending…" : "Request a review"}
      </button>
    </form>
  );
};

export default ReviewForm;

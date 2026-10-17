import React, { useState } from "react";
import { formatDate } from "../lib/plain";
import type { ConsentState } from "../types";

/** The customer's choice about AI-written summaries. Staff see the same panel to record a choice the customer gave them. */
export const ChoicesPanel: React.FC<{
  state: ConsentState;
  onChoose: (granted: boolean) => Promise<void>;
  onBehalf?: boolean;
}> = ({ state, onChoose, onBehalf = false }) => {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choose = async (granted: boolean) => {
    setBusy(true);
    setError(null);
    try { await onChoose(granted); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong."); } finally { setBusy(false); }
  };
  const who = (given: string) => (given === "customer" ? "the customer" : "your lender, for the customer");

  return (
    <div className="cl-card-rail" data-testid="choices-panel">
      <h2 className="cl-h3">{onBehalf ? "The customer’s choices" : "Your choices"}</h2>
      {state.policy === "never" ? (
        <p className="cl-muted" data-testid="choices-never">
          {onBehalf ? "This lender doesn’t use AI to write summaries, so there is nothing to choose." : "Your lender doesn’t use AI to write summaries, so there is nothing for you to choose."}
        </p>
      ) : (
        <>
          <h3 className="cl-h4">AI-written summary</h3>
          <p className="cl-muted">
            An AI model can write a short summary of the explanation in plain words. It is only given the facts the explanation is built on,
            such as figures and reasons, and not a name or contact details. Every figure it uses is checked against the record, and
            anything it can’t back up is left out.
          </p>
          <p data-testid="choice-now" role="status">
            <strong>{state.ai_summary_on ? "On" : "Off"}</strong>
            {state.choice
              ? ` — chosen by ${who(state.choice.given_by)} on ${formatDate(state.choice.recorded_at, true)}.`
              : state.policy === "allow" ? " — the lender’s default. You can switch it off." : " — it stays off unless you allow it."}
          </p>
          <div className="cl-actions">
            <button type="button" className="cl-btn cl-btn-ghost" disabled={busy || state.ai_summary_on} onClick={() => choose(true)}>
              {onBehalf ? "Record: allowed" : "Allow AI summaries"}
            </button>
            <button type="button" className="cl-btn cl-btn-ghost" disabled={busy || (!state.ai_summary_on && state.choice !== null)} onClick={() => choose(false)}>
              {onBehalf ? "Record: not allowed" : "Don’t allow"}
            </button>
          </div>
          {error && <p className="cl-note cl-note-bad" role="alert">{error}</p>}
          {state.history.length > 0 && (
            <details className="cl-details">
              <summary>History of this choice</summary>
              <ul data-testid="choice-history">
                {state.history.map((h) => (
                  <li key={h.recorded_at + String(h.granted)}>{h.granted ? "Allowed" : "Not allowed"} by {who(h.given_by)} on {formatDate(h.recorded_at, true)}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
};

export default ChoicesPanel;

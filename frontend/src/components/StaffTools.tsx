import React, { useState } from "react";
import { createClaimLink, needsSignIn } from "../api";
import { formatDate } from "../lib/plain";
import { Link } from "../lib/router";
import type { ClaimLinkCreated, DecisionDetailRecord, Me } from "../types";

const SEVERITY = { high: "Look soon", warning: "Worth a look", info: "For information" } as const;

/** Lender-only panel on a decision: risk signals to look at, and a way to give the customer their own link. */
export const StaffTools: React.FC<{ record: DecisionDetailRecord; me: Me }> = ({ record, me }) => {
  const [days, setDays] = useState(7);
  const [link, setLink] = useState<ClaimLinkCreated | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const open = (record.signals ?? []).filter((s) => s.status === "open");
  const canLink = me.scopes.includes("claims:issue");

  const make = async () => {
    setBusy(true); setError(null); setCopied(false);
    try { setLink(await createClaimLink(record.decision_id, days, "Created on the decision page")); }
    catch (e) { setError(needsSignIn(e) ? "This sign-in isn’t allowed to create links." : e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); }
  };
  const url = link ? `${window.location.origin}${window.location.pathname}${link.path}` : "";
  const [copyNote, setCopyNote] = useState("");
  const copy = async () => {
    try { await navigator.clipboard.writeText(url); setCopied(true); setCopyNote("Link copied."); }
    catch { setCopied(false); setCopyNote("Couldn’t copy it automatically. Select the link above and copy it by hand."); }
  };

  return (
    <div className="cl-card-rail" data-testid="staff-tools">
      <h2 className="cl-h3">For your team</h2>

      <h3 className="cl-h4">Things to look at</h3>
      {open.length === 0 ? (
        <p className="cl-muted" data-testid="no-flags">Nothing flagged for this decision.</p>
      ) : (
        <ul className="cl-flags" data-testid="flags">
          {open.map((s) => (
            <li key={s.signal_id}><strong>{SEVERITY[s.severity]}.</strong> {s.message}</li>
          ))}
        </ul>
      )}
      <p className="cl-fine">These are prompts for a person to look at. They are not conclusions, and the customer doesn’t see them. <Link to={{ page: "signals" }}>All signals</Link></p>

      {canLink && (
        <>
          <h3 className="cl-h4">Give the customer their link</h3>
          <label className="cl-field"><span>It works for</span>
            <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
              <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
            </select>
          </label>
          <button type="button" className="cl-btn cl-btn-ghost" onClick={make} disabled={busy}>Create a link</button>
          {error && <p className="cl-note cl-note-bad" role="alert">{error}</p>}
          {link && (
            <div data-testid="claim-link">
              <label className="cl-field"><span>Send this only to the customer</span>
                <input type="text" readOnly value={url} onFocus={(e) => e.currentTarget.select()} />
              </label>
              <button type="button" className="cl-btn cl-btn-ghost" onClick={copy}>{copied ? "Copied" : "Copy the link"}</button>
              <p className="cl-fine" role="status" data-testid="copy-note">{copyNote}</p>
              <p className="cl-fine">It opens only this decision, until {formatDate(link.expires_at)}. Anyone who has it can see the decision, so share it privately.</p>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export default StaffTools;

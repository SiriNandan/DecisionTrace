import React, { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCustomers, problemKind } from "../api";
import OutcomePill from "../components/OutcomePill";
import ServiceProblem from "../components/ServiceProblem";
import { SEVERITY_TEXT, signalLabel } from "../lib/customerSignals";
import { Link } from "../lib/router";
import type { CustomerRow } from "../types";

const DOT: Record<string, string> = { APPROVE: "✓", DECLINE: "✕", REFER: "◔" };
const date = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

export const HistoryStrip: React.FC<{ history: string[] }> = ({ history }) => (
  <span className="cl-strip" role="img" aria-label={`Outcomes in order: ${history.map((h) => DOT[h] ?? "•").join(" ")}`}>
    {history.map((h, i) => <span key={i} className={`cl-dot cl-dot-${h.toLowerCase()}`} aria-hidden="true">{DOT[h] ?? "•"}</span>)}
  </span>
);

export const CustomersPage: React.FC = () => {
  const [rows, setRows] = useState<CustomerRow[] | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemKind> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const [repeatOnly, setRepeatOnly] = useState(false);
  const [q, setQ] = useState("");

  const load = useCallback(async () => {
    setError(null); setProblem(null);
    try { setRows(await fetchCustomers()); } catch (e) { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => (rows ?? []).filter((r) =>
    (!flaggedOnly || r.flag_level) && (!repeatOnly || r.applications > 1) && (!q.trim() || r.subject_ref.toLowerCase().includes(q.trim().toLowerCase()))), [rows, flaggedOnly, repeatOnly, q]);
  const flagged = (rows ?? []).filter((r) => r.flag_level).length;

  return (
    <div className="cl-page cl-quality">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Customers</h1>
      <p className="cl-page-lede">
        Every application grouped by customer, so repeat applicants stand out. Patterns such as many applications in a short time,
        approval after a run of declines, several loans approved close together, or an amount lowered until it was approved are marked for a
        person to look at. They are prompts, not conclusions: nothing is blocked or changed.
      </p>
      {problem && <ServiceProblem kind={problem} what="page" onRetry={() => void load()} offerSample={false} />}
      {error && !problem && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}
      {rows && (
        <p className="cl-muted" data-testid="customers-summary">
          {rows.length} customers · {rows.filter((r) => r.applications > 1).length} with more than one application · {flagged} with a pattern to look at
        </p>
      )}
      <div className="cl-chips" role="group" aria-label="Filter">
        <button type="button" aria-pressed={flaggedOnly} className={`cl-chip ${flaggedOnly ? "is-on" : ""}`} onClick={() => setFlaggedOnly(!flaggedOnly)}>Patterns to look at</button>
        <button type="button" aria-pressed={repeatOnly} className={`cl-chip ${repeatOnly ? "is-on" : ""}`} onClick={() => setRepeatOnly(!repeatOnly)}>Repeat applicants</button>
        <label className="cl-field cl-cust-search"><span className="cl-sr">Find a customer reference</span>
          <input type="text" value={q} placeholder="Find a customer reference" onChange={(e) => setQ(e.target.value)} />
        </label>
      </div>
      {rows && shown.length === 0 && <p className="cl-muted" data-testid="none">No customers match.</p>}
      <ul className="cl-findings" aria-label="Customers">
        {shown.map((r) => (
          <li key={r.subject_ref} className={`cl-finding is-${r.flag_level === "high" ? "error" : r.flag_level === "warning" ? "warning" : "info"}`} data-testid={`customer-${r.subject_ref}`}>
            <div className="cl-finding-head">
              <Link to={{ page: "customer", id: r.subject_ref }} className="cl-cust-ref"><strong>{r.subject_ref}</strong></Link>
              <span className="cl-muted">{r.applications} {r.applications === 1 ? "application" : "applications"} · {r.approved} approved · {r.declined} not approved · {r.referred} in review</span>
              <OutcomePill outcome={r.last_outcome} />
            </div>
            <p className="cl-cust-line"><HistoryStrip history={r.history} /> <span className="cl-muted">{date(r.first_at)} to {date(r.last_at)}</span></p>
            {r.flags.length > 0 && (
              <p className="cl-cust-flags">
                {r.flags.map((f) => <span key={f.signal_type} className={`cl-tag cl-tag-${f.severity}`}>{signalLabel(f.signal_type)}{f.count > 1 ? ` ×${f.count}` : ""} · {SEVERITY_TEXT[f.severity]}</span>)}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
};

export default CustomersPage;

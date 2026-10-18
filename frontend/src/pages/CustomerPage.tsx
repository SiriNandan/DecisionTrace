import React, { useCallback, useEffect, useState } from "react";
import { fetchCustomer, problemKind } from "../api";
import OutcomePill from "../components/OutcomePill";
import ServiceProblem from "../components/ServiceProblem";
import { SEVERITY_TEXT, signalLabel } from "../lib/customerSignals";
import { Link } from "../lib/router";
import type { CustomerDetail } from "../types";
import { HistoryStrip } from "./CustomersPage";

const money = (n: number | null) => (n == null ? "—" : n.toLocaleString(undefined, { maximumFractionDigits: 0 }));
const when = (iso: string) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

export const CustomerPage: React.FC<{ subjectRef: string | null }> = ({ subjectRef }) => {
  const [c, setC] = useState<CustomerDetail | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemKind> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!subjectRef) return;
    setError(null); setProblem(null);
    try { setC(await fetchCustomer(subjectRef)); } catch (e) { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); }
  }, [subjectRef]);
  useEffect(() => { void load(); }, [load]);

  return (
    <div className="cl-page cl-quality">
      <p className="cl-eyebrow"><Link to={{ page: "customers" }}>← All customers</Link></p>
      <h1 className="cl-page-title" tabIndex={-1}>{subjectRef ?? "Customer"}</h1>
      {problem && <ServiceProblem kind={problem} what="page" onRetry={() => void load()} offerSample={false} />}
      {error && !problem && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}
      {c && (
        <>
          <p className="cl-muted" data-testid="customer-summary">
            {c.applications} {c.applications === 1 ? "application" : "applications"} · {c.approved} approved · {c.declined} not approved · {c.referred} in review
          </p>
          <p className="cl-cust-line"><HistoryStrip history={c.timeline.map((t) => t.outcome)} /></p>
          {c.signals.length > 0 && (
            <>
              <h2 className="cl-h2">Patterns to look at</h2>
              <ul className="cl-findings" aria-label="Patterns">
                {c.signals.map((s, i) => (
                  <li key={i} className={`cl-finding is-${s.severity === "high" ? "error" : s.severity === "warning" ? "warning" : "info"}`}>
                    <div className="cl-finding-head"><span className="cl-sure cl-sure-lo">{SEVERITY_TEXT[s.severity]}</span><strong>{signalLabel(s.signal_type)}</strong></div>
                    <p className="cl-finding-msg">{s.message}</p>
                    <p className="cl-muted"><Link to={{ page: "decision", id: s.decision_id }}>Open the decision it points to</Link></p>
                  </li>
                ))}
              </ul>
            </>
          )}
          <h2 className="cl-h2">Applications, oldest first</h2>
          <ol className="cl-timeline" aria-label="Applications">
            {c.timeline.map((t, i) => (
              <li key={t.decision_id} className="cl-timeline-item" data-testid={`timeline-${i}`}>
                <span className="cl-timeline-when">{when(t.decided_at)}</span>
                <OutcomePill outcome={t.outcome} />
                <span className="cl-muted">Requested {money(t.requested_amount)}{t.score != null ? ` · risk score ${t.score.toFixed(2)}` : ""}{t.reasons.length ? ` · ${t.reasons.join(", ")}` : ""}</span>
                {t.flags.map((f) => <span key={f} className="cl-tag cl-tag-warning">{signalLabel(f)}</span>)}
                <Link to={{ page: "decision", id: t.decision_id }}>Open</Link>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
};

export default CustomerPage;

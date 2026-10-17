import React, { useMemo, useState } from "react";
import { DecisionSummaryItem } from "../types";
import { computeStats } from "../lib/stats";
import { outcomeCopy, riskLabel } from "../lib/plain";
import { Link } from "../lib/router";
import { Activity, resetActivity } from "../lib/usage";

const OUTCOMES = ["APPROVE", "DECLINE", "REFER"] as const;
const pct = (n: number) => `${Math.round(n * 1000) / 10}%`;

const ChartCard: React.FC<{ title: string; note: string; table: React.ReactNode; children: React.ReactNode }> = ({ title, note, table, children }) => {
  const [asTable, setAsTable] = useState(false);
  return (
    <section className="cl-chart" aria-label={title}>
      <div className="cl-chart-head">
        <div>
          <h3 className="cl-h3">{title}</h3>
          <p className="cl-fine" style={{ marginTop: 0 }}>{note}</p>
        </div>
        <button type="button" className="cl-link" aria-pressed={asTable} onClick={() => setAsTable((v) => !v)}>
          {asTable ? "View chart" : "View as table"}
        </button>
      </div>
      {asTable ? table : children}
    </section>
  );
};

export interface InsightsPageProps {
  cases: DecisionSummaryItem[];
  loading: boolean;
  error: string | null;
  activity: Activity;
  onActivityReset: () => void;
}

export const InsightsPage: React.FC<InsightsPageProps> = ({ cases, loading, error, activity, onActivityReset }) => {
  const s = useMemo(() => computeStats(cases), [cases]);
  const maxMonth = Math.max(...s.months.map((m) => m.count), 1);
  const byId = useMemo(() => new Map(cases.map((c) => [c.decision_id, c])), [cases]);
  const recent = [...activity.decisions].reverse().map((id) => byId.get(id)).filter(Boolean).slice(0, 5) as DecisionSummaryItem[];

  return (
    <div className="cl-page cl-page-wide" data-testid="insights-page">
      <p className="cl-eyebrow">Insights</p>
      <h1 className="cl-page-title" tabIndex={-1}>The big picture</h1>
      <p className="cl-page-lede">How decisions are being made across all recorded applications, and how you’ve used Clearly.</p>

      {error && <div className="cl-note cl-note-bad" role="alert"><strong>We couldn’t load the numbers.</strong><span>Check your connection and refresh the page.</span></div>}
      {loading && <div className="cl-skeleton cl-skel-hero" role="status" aria-label="Loading insights" />}

      {!loading && !error && s.total === 0 && (
        <div className="cl-empty"><div className="cl-empty-art" aria-hidden="true">◌</div><h2>No decisions to summarise yet</h2><p>Once decisions are recorded, trends will appear here.</p></div>
      )}

      {!loading && !error && s.total > 0 && (
        <>
          <section className="cl-kpis" aria-label="Key numbers">
            <div className="cl-kpi"><span className="cl-kpi-n">{s.total}</span><span className="cl-kpi-l">Decisions recorded</span></div>
            <div className="cl-kpi"><span className="cl-kpi-n" style={{ color: "var(--good)" }}>{pct(s.approvalRate)}</span><span className="cl-kpi-l">Approved</span></div>
            <div className="cl-kpi"><span className="cl-kpi-n">{s.counts.REFER}</span><span className="cl-kpi-l">Waiting for a person</span></div>
            <div className="cl-kpi"><span className="cl-kpi-n">{s.changedByPerson}</span><span className="cl-kpi-l">Changed after human review</span></div>
          </section>

          <div className="cl-charts">
            <ChartCard
              title="Results at a glance"
              note="Share of all decisions by result."
              table={
                <table className="cl-table"><thead><tr><th>Result</th><th>Decisions</th><th>Share</th></tr></thead>
                  <tbody>{OUTCOMES.map((o) => <tr key={o}><td>{outcomeCopy(o).label}</td><td>{s.counts[o]}</td><td>{pct(s.counts[o] / s.total)}</td></tr>)}</tbody></table>
              }
            >
              <div className="cl-stack" role="img" aria-label={OUTCOMES.map((o) => `${outcomeCopy(o).label} ${s.counts[o]}`).join(", ")}>
                {OUTCOMES.filter((o) => s.counts[o] > 0).map((o) => (
                  <span key={o} className={`cl-stack-seg cl-seg-${outcomeCopy(o).tone}`} style={{ flexGrow: s.counts[o] }} title={`${outcomeCopy(o).label}: ${s.counts[o]} (${pct(s.counts[o] / s.total)})`} />
                ))}
              </div>
              <ul className="cl-legend">
                {OUTCOMES.map((o) => (
                  <li key={o}><span className={`cl-swatch cl-seg-${outcomeCopy(o).tone}`} aria-hidden="true" /><span aria-hidden="true">{outcomeCopy(o).icon}</span> {outcomeCopy(o).label} <strong>{s.counts[o]}</strong> <span className="cl-muted">({pct(s.counts[o] / s.total)})</span></li>
                ))}
              </ul>
            </ChartCard>

            <ChartCard
              title="Decisions per month"
              note="How many applications were decided each month."
              table={
                <table className="cl-table"><thead><tr><th>Month</th><th>Decisions</th></tr></thead>
                  <tbody>{s.months.map((m) => <tr key={m.key}><td>{m.label}</td><td>{m.count}</td></tr>)}</tbody></table>
              }
            >
              <ul className="cl-bars" aria-label="Decisions per month">
                {s.months.map((m) => (
                  <li key={m.key} tabIndex={0} title={`${m.label}: ${m.count} decisions`} aria-label={`${m.label}: ${m.count} decisions`}>
                    <span className="cl-bar-v">{m.count}</span>
                    <span className="cl-bar" style={{ height: `${(m.count / maxMonth) * 100}%` }} />
                    <span className="cl-bar-l">{m.label}</span>
                  </li>
                ))}
              </ul>
            </ChartCard>

            <ChartCard
              title="Average risk estimate by result"
              note="Higher means the model saw more risk. It is one input, not the decision."
              table={
                <table className="cl-table"><thead><tr><th>Result</th><th>Average risk estimate</th></tr></thead>
                  <tbody>{OUTCOMES.map((o) => <tr key={o}><td>{outcomeCopy(o).label}</td><td>{s.avgRisk[o] === null ? "—" : riskLabel(s.avgRisk[o] as number)}</td></tr>)}</tbody></table>
              }
            >
              <ul className="cl-hbars">
                {OUTCOMES.map((o) => {
                  const v = s.avgRisk[o];
                  return (
                    <li key={o} title={v === null ? "No decisions" : `${outcomeCopy(o).label}: ${riskLabel(v)}`}>
                      <span className="cl-hbar-l"><span aria-hidden="true">{outcomeCopy(o).icon}</span> {outcomeCopy(o).label}</span>
                      <span className="cl-hbar-track"><span className={`cl-hbar cl-seg-${outcomeCopy(o).tone}`} style={{ width: `${Math.min((v ?? 0) * 100, 100)}%` }} /></span>
                      <strong>{v === null ? "—" : riskLabel(v)}</strong>
                    </li>
                  );
                })}
              </ul>
            </ChartCard>
          </div>
        </>
      )}

      <section className="cl-activity" aria-labelledby="act-h" data-testid="activity">
        <h2 id="act-h" className="cl-h2">Your activity</h2>
        <p className="cl-muted">Stored only in this browser. It is never sent anywhere, and you can clear it at any time.</p>
        <div className="cl-kpis">
          <div className="cl-kpi"><span className="cl-kpi-n">{activity.decisions.length}</span><span className="cl-kpi-l">Decisions explored</span></div>
          <div className="cl-kpi"><span className="cl-kpi-n">{activity.views}</span><span className="cl-kpi-l">Pages opened</span></div>
          <div className="cl-kpi"><span className="cl-kpi-n">{activity.printed + activity.linksCopied}</span><span className="cl-kpi-l">Saved or shared</span></div>
          <div className="cl-kpi"><span className="cl-kpi-n">{activity.reviewsRequested}</span><span className="cl-kpi-l">Reviews requested</span></div>
        </div>
        {recent.length > 0 ? (
          <>
            <h3 className="cl-h3" style={{ marginTop: 20 }}>Recently viewed</h3>
            <ul className="cl-recent">
              {recent.map((d) => (
                <li key={d.decision_id}>
                  <Link to={{ page: "decision", id: d.decision_id }}>{d.applicant_id} · {d.application_id}</Link>
                  <span className="cl-muted">{outcomeCopy(d.effective_outcome).label}</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="cl-muted" style={{ marginTop: 14 }}>Nothing yet. <Link to={{ page: "home" }}>Open a decision</Link> to get started.</p>
        )}
        <button type="button" className="cl-btn cl-btn-ghost" style={{ marginTop: 16 }} onClick={() => { resetActivity(); onActivityReset(); }}>Clear my activity</button>
      </section>
    </div>
  );
};

export default InsightsPage;

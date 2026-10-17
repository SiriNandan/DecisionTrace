import React, { useCallback, useMemo, useState } from "react";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSnapshotRow,
  ReconstructionReportContract,
} from "../types";
import {
  evidenceFacts,
  evidenceKind,
  featureLabel,
  formatDate,
  formatRuleValue,
  outcomeCopy,
  riskLabel,
  ruleId,
  ruleNextStep,
  ruleRequirement,
  ruleTitle,
} from "../lib/plain";
import OutcomePill from "./OutcomePill";
import ReviewForm from "./ReviewForm";

export interface DecisionViewProps {
  record: DecisionDetailRecord;
  report: ReconstructionReportContract | null;
  reportLoading: boolean;
  reportError: string | null;
  onRetryReport: () => void;
  onBack: () => void;
  onSubmitReview?: (p: CreateOverridePayload) => Promise<void>;
  onTrack?: (e: "print" | "copy") => void;
}

interface RuleRow {
  id: string;
  title: string;
  observed: unknown;
  operator: string;
  threshold: unknown;
  passed: boolean;
  section: string;
  ref: string;
}

function normaliseRules(record: DecisionDetailRecord, report: ReconstructionReportContract | null): RuleRow[] {
  if (report && report.rule_checks.length > 0) {
    return report.rule_checks.map((r) => ({
      id: ruleId(r), title: ruleTitle(ruleId(r)), observed: r.observed_value, operator: r.operator,
      threshold: r.threshold, passed: r.passed, section: r.section, ref: r.evidence_ref,
    }));
  }
  return (record.rule_checks ?? []).map((r) => {
    const id = ruleId(r as { rule_id?: unknown });
    return {
      id, title: ruleTitle(id), observed: r.observed_value, operator: String(r.operator ?? ""),
      threshold: r.threshold, passed: String(r.result).toUpperCase() === "PASS",
      section: String(r.section ?? ""), ref: `rule:${id}`,
    };
  });
}

function composeSummary(outcome: string, rules: RuleRow[]): string {
  const c = outcomeCopy(outcome);
  const failed = rules.filter((r) => !r.passed);
  if (failed.length === 0) {
    return rules.length
      ? `${c.headline}. All ${rules.length} lending rules that were checked were met.`
      : `${c.headline}.`;
  }
  const parts = failed.map((r) =>
    r.id === "required_documents"
      ? "a required document was not received"
      : `${r.title.toLowerCase()} was ${formatRuleValue(r.id, r.observed)}, which ${ruleRequirement(r.id, r.operator, r.threshold).replace("Needs to be", "needs to be")}`
  );
  const because = c.tone === "good" ? "Even so," : "This is because";
  return `${c.headline}. ${because} ${parts.join(", and ")}.`;
}

function Meter({ row }: { row: RuleRow }) {
  const obs = Number(row.observed);
  const thr = Number(row.threshold);
  if (!Number.isFinite(obs) || !Number.isFinite(thr) || thr === 0 || row.id === "required_documents") return null;
  const max = Math.max(obs, thr) * 1.25;
  return (
    <div className="cl-meter" aria-hidden="true">
      <div className={`cl-meter-fill ${row.passed ? "is-ok" : "is-bad"}`} style={{ width: `${Math.min((obs / max) * 100, 100)}%` }} />
      <div className="cl-meter-mark" style={{ left: `${Math.min((thr / max) * 100, 100)}%` }}><span>limit</span></div>
    </div>
  );
}

function RiskRing({ score, tone }: { score: number; tone: string }) {
  const r = 52, c = 2 * Math.PI * r;
  return (
    <figure className="cl-ring" aria-label={`Risk estimate ${riskLabel(score)}`}>
      <svg viewBox="0 0 130 130" aria-hidden="true">
        <circle cx="65" cy="65" r={r} className="cl-ring-bg" />
        <circle cx="65" cy="65" r={r} className={`cl-ring-fg cl-ring-${tone}`} strokeDasharray={`${Math.min(score, 1) * c} ${c}`} transform="rotate(-90 65 65)" />
      </svg>
      <figcaption><strong>{riskLabel(score)}</strong><span>risk estimate</span></figcaption>
    </figure>
  );
}

export const DecisionView: React.FC<DecisionViewProps> = ({
  record, report, reportLoading, reportError, onRetryReport, onBack, onSubmitReview, onTrack,
}) => {
  const [openRefs, setOpenRefs] = useState<Set<string>>(new Set());
  const [highlight, setHighlight] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [showAllFacts, setShowAllFacts] = useState(false);

  const effective = report?.outcome ?? record.effective_outcome;
  const copy = outcomeCopy(effective);
  const rules = useMemo(() => normaliseRules(record, report), [record, report]);
  const failed = rules.filter((r) => !r.passed);

  const removed = useMemo(() => {
    const s = new Set<string>(report?.missing_evidence ?? []);
    for (const [ref, st] of Object.entries(record.evidence_statuses ?? {})) if (st !== "available") s.add(ref);
    return s;
  }, [record, report]);

  const snapshotRefs = useMemo(() => new Set(record.snapshots.map((s) => s.evidence_ref)), [record]);

  const openEvidence = useCallback(
    (cite: string) => {
      let ref = cite;
      if (!snapshotRefs.has(ref)) {
        const bySection = rules.find((r) => r.section === cite);
        ref = bySection?.ref ?? record.snapshots.find((s) => s.evidence_type === "policy")?.evidence_ref ?? cite;
      }
      setOpenRefs((p) => new Set(p).add(ref));
      setHighlight(ref);
      requestAnimationFrame(() => {
        const el = Array.from(document.querySelectorAll<HTMLElement>("[data-evidence-ref]")).find(
          (n) => n.getAttribute("data-evidence-ref") === ref
        );
        el?.scrollIntoView?.({ behavior: "smooth", block: "center" });
        el?.focus?.({ preventScroll: true });
      });
    },
    [rules, snapshotRefs, record.snapshots]
  );

  const toggle = (ref: string) =>
    setOpenRefs((p) => {
      const n = new Set(p);
      n.has(ref) ? n.delete(ref) : n.add(ref);
      return n;
    });

  const chainOk = record.chain_valid && (report ? report.chain_verified : true);
  const replayOk = report?.replay_verified ?? null;
  const completeness = report ? Math.round(report.completeness * 100) : null;

  const summary = report?.plain_language_summary || composeSummary(effective, rules);
  const aiWritten = Boolean(report?.plain_language_summary);

  const factors = useMemo(
    () => Object.entries(record.contributions ?? {}).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).slice(0, 5),
    [record]
  );
  const maxAbs = Math.max(...factors.map(([, v]) => Math.abs(v)), 0.0001);

  const facts = useMemo(() => {
    const seen = new Set<string>();
    const out: Array<{ label: string; value: string; cite: string; source: string }> = [];
    for (const g of report?.reasons ?? [])
      for (const c of g.claims) {
        const key = `${c.citation}|${c.field}`;
        if (seen.has(key)) continue;
        seen.add(key);
        const snap = record.snapshots.find((s) => s.evidence_ref === c.citation);
        if (c.field === "section_id") continue;
        const rid = c.citation.startsWith("rule:") ? c.citation.slice(5) : null;
        let label = featureLabel(c.field);
        let value = formatRuleValueFor(c.field, c.value);
        if (rid && (c.field === "observed_value" || c.field === "threshold")) {
          label = `${ruleTitle(rid)}${c.field === "threshold" ? " (limit)" : ""}`;
          value = formatRuleValue(rid, c.value);
        } else if (c.field === "score") {
          label = "Risk estimate";
          value = riskLabel(Number(c.value));
        }
        out.push({
          label, value, cite: c.citation,
          source: snap ? evidenceKind(snap.evidence_type).label : "Lending policy",
        });
      }
    return out;
  }, [report, record.snapshots]);

  const overrides = [...(record.overrides ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const lastOverride = record.override ?? record.human_override ?? overrides[overrides.length - 1] ?? null;

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      onTrack?.("copy");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable: ignore */
    }
  };

  const evidenceRows: Array<DecisionSnapshotRow | { removedRef: string }> = [
    ...record.snapshots.filter((s) => !removed.has(s.evidence_ref)),
    ...[...removed].map((removedRef) => ({ removedRef })),
  ];

  return (
    <article className="cl-decision" data-testid="decision-record">
      <button type="button" className="cl-back" onClick={onBack}>← All decisions</button>

      {!chainOk && (
        <div className="cl-note cl-note-bad cl-note-big" role="alert" data-testid="integrity-warning">
          <strong>We can’t confirm this record is unchanged.</strong>
          <span>
            Something in the saved evidence no longer matches how it was first recorded. Treat the
            explanation below with caution and ask an auditor to look into it.
          </span>
        </div>
      )}

      <header className={`cl-outcome cl-outcome-${copy.tone}`}>
        <div className="cl-outcome-main">
          <OutcomePill outcome={effective} size="lg" />
          <h1 className="cl-outcome-title" tabIndex={-1}>{copy.headline}</h1>
          <p className="cl-outcome-meta">
            <span>Applicant <strong>{record.applicant_id}</strong></span>
            <span>Application <strong>{record.application_id}</strong></span>
            <span>Decided <strong>{formatDate(record.decided_at)}</strong></span>
          </p>
        </div>
        <RiskRing score={record.score} tone={copy.tone} />
      </header>

      {lastOverride && (
        <div className="cl-note cl-note-review" data-testid="recorded-override-banner">
          <strong>A person changed this result to “{outcomeCopy(lastOverride.new_outcome).label}”.</strong>
          <span>
            {lastOverride.actor_role.replace(/_/g, " ")}, {formatDate(lastOverride.created_at, true)}:
            {" "}“{lastOverride.reason}”
          </span>
        </div>
      )}

      <div className="cl-layout">
        <div className="cl-col-main">
          <section id="why" className="cl-section" aria-labelledby="why-h" data-testid="reconstruction-report">
            <h2 id="why-h" className="cl-h2">Why this decision</h2>
            {reportLoading && !report ? (
              <div className="cl-skeleton cl-skel-text" role="status" aria-label="Preparing your explanation" />
            ) : (
              <p className="cl-lede" data-testid="plain-summary">{summary}</p>
            )}
            {aiWritten && (
              <p className="cl-ai-tag">✦ Written by AI, and checked against the saved evidence. Every figure in it has a source.</p>
            )}
            {reportError && (
              <div className="cl-note cl-note-bad" role="alert">
                <strong>The detailed explanation isn’t available right now.</strong>
                <span>You can still see the recorded rules and evidence below.</span>
                <button type="button" className="cl-btn cl-btn-ghost" onClick={onRetryReport}>Try again</button>
              </div>
            )}
            {report?.status === "evidence_unavailable" && (
              <div className="cl-note cl-note-review" data-testid="evidence-removed-note">
                <strong>Some evidence is no longer held.</strong>
                <span>It was removed under data-retention rules, so this explanation is partial. It covers {completeness}% of the original evidence.</span>
              </div>
            )}
            {(report?.status === "timeout" || report?.status === "error") && (
              <div className="cl-note cl-note-bad" role="alert">
                <strong>We couldn’t finish building the explanation.</strong>
                <button type="button" className="cl-btn cl-btn-ghost" onClick={onRetryReport}>Try again</button>
              </div>
            )}
          </section>

          <section id="rules" className="cl-section" aria-labelledby="rules-h">
            <h2 id="rules-h" className="cl-h2">How each lending rule applied</h2>
            {rules.length === 0 ? (
              <p className="cl-muted">No rule results were recorded for this decision.</p>
            ) : (
              <ul className="cl-rules">
                {rules.map((r) => (
                  <li key={r.id} className={`cl-rule ${r.passed ? "is-ok" : "is-bad"}`} data-testid={`rule-${r.id}`}>
                    <div className="cl-rule-head">
                      <h3>{r.title}</h3>
                      <span className={`cl-status ${r.passed ? "is-ok" : "is-bad"}`}>
                        <span aria-hidden="true">{r.passed ? "✓" : "✕"}</span> {r.passed ? "Met" : "Not met"}
                      </span>
                    </div>
                    <p className="cl-rule-vals">
                      <strong>{formatRuleValue(r.id, r.observed)}</strong>
                      <span>{ruleRequirement(r.id, r.operator, r.threshold)}</span>
                    </p>
                    <Meter row={r} />
                    <button type="button" className="cl-link" onClick={() => openEvidence(r.ref)} data-testid={`rule-citation-chip-${r.ref}`}>
                      See the evidence →
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section id="next" className="cl-section" aria-labelledby="next-h">
            <h2 id="next-h" className="cl-h2">What could change the outcome</h2>
            {failed.length === 0 ? (
              <p className="cl-muted">
                {effective === "APPROVE"
                  ? "Nothing further is needed. Every rule that was checked was met."
                  : "No single rule was missed. A person will look at the overall picture."}
              </p>
            ) : (
              <ol className="cl-steps">
                {failed.map((r) => {
                  const step = ruleNextStep({ rule_id: r.id, observed_value: r.observed, threshold: r.threshold } as never);
                  return (
                    <li key={r.id}>
                      <strong>{r.title}</strong>
                      <span>{step ?? "This rule needs to be met before the application can be approved."}</span>
                    </li>
                  );
                })}
              </ol>
            )}
            <p className="cl-fine">Based on the rules in force on the decision date. A person always confirms eligibility.</p>
          </section>

          {factors.length > 0 && (
            <section id="factors" className="cl-section" aria-labelledby="factors-h">
              <h2 id="factors-h" className="cl-h2">What shaped the risk estimate</h2>
              <ul className="cl-factors">
                {factors.map(([k, v]) => (
                  <li key={k}>
                    <span className="cl-factor-name">{featureLabel(k)}</span>
                    <span className="cl-factor-bar" aria-hidden="true">
                      <span className={v > 0 ? "up" : "down"} style={{ width: `${(Math.abs(v) / maxAbs) * 100}%` }} />
                    </span>
                    <span className={`cl-factor-dir ${v > 0 ? "up" : "down"}`}>{v > 0 ? "raised" : "lowered"}</span>
                  </li>
                ))}
              </ul>
              <p className="cl-fine">The five factors with the biggest effect on the {riskLabel(record.score)} estimate.</p>
            </section>
          )}

          {facts.length > 0 && (
            <section id="facts" className="cl-section" aria-labelledby="facts-h">
              <h2 id="facts-h" className="cl-h2">The facts this explanation relies on</h2>
              <ul className="cl-facts">
                {(showAllFacts ? facts : facts.slice(0, 6)).map((f) => (
                  <li key={`${f.cite}${f.label}`}>
                    <span>{f.label}</span>
                    <strong>{f.value}</strong>
                    <button type="button" className="cl-chip cl-chip-src" onClick={() => openEvidence(f.cite)} data-testid={`citation-chip-${f.cite}`}>
                      {f.source}
                    </button>
                  </li>
                ))}
              </ul>
              {facts.length > 6 && (
                <button type="button" className="cl-link" onClick={() => setShowAllFacts((v) => !v)}>
                  {showAllFacts ? "Show fewer" : `Show all ${facts.length}`}
                </button>
              )}
            </section>
          )}

          <section id="evidence" className="cl-section" aria-labelledby="evidence-h">
            <h2 id="evidence-h" className="cl-h2">The evidence on file</h2>
            <ul className="cl-evidence" data-testid="decision-timeline">
              {evidenceRows.map((row) => {
                if ("removedRef" in row) {
                  return (
                    <li key={row.removedRef} className="cl-ev is-removed" data-testid="missing-evidence-warnings">
                      <div className="cl-ev-head"><strong>{row.removedRef.split(":")[0].replace(/^./, (c) => c.toUpperCase())}</strong>
                        <span className="cl-tag cl-tag-review">No longer held</span></div>
                      <p className="cl-muted">Removed under data-retention rules. It can’t be shown here.</p>
                    </li>
                  );
                }
                const kind = evidenceKind(row.evidence_type);
                const title = row.evidence_type === "rule_check" ? `Rule: ${ruleTitle(String(row.payload.rule_id ?? row.evidence_ref.replace("rule:", "")))}` : kind.label;
                const open = openRefs.has(row.evidence_ref);
                const facts = evidenceFacts(row);
                return (
                  <li
                    key={row.evidence_ref}
                    id={`ev-${row.seq}`}
                    tabIndex={-1}
                    data-evidence-ref={row.evidence_ref}
                    data-testid={`evidence-row-${row.evidence_ref}`}
                    className={`cl-ev ${highlight === row.evidence_ref ? "is-hl" : ""}`}
                  >
                    <button type="button" className="cl-ev-head" aria-expanded={open} onClick={() => toggle(row.evidence_ref)}>
                      <span><strong>{title}</strong><small>{kind.blurb}</small></span>
                      <span className="cl-ev-when">{formatDate(row.captured_at)}<i aria-hidden="true">{open ? "−" : "+"}</i></span>
                    </button>
                    {open && (
                      <dl className="cl-kv">
                        {facts.map((f) => (<div key={f.label}><dt>{f.label}</dt><dd>{f.value}</dd></div>))}
                        {facts.length === 0 && <p className="cl-muted">No further details to show.</p>}
                      </dl>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>

          <section id="review" className="cl-section" aria-labelledby="review-h">
            <h2 id="review-h" className="cl-h2">Think this is wrong?</h2>
            <ReviewForm decisionId={record.decision_id} currentOutcome={record.effective_outcome} onSubmit={onSubmitReview} />
            {overrides.length > 0 && (
              <>
                <h3 className="cl-h3">Review history</h3>
                <ul className="cl-history">
                  {overrides.map((o, i) => (
                    <li key={i}>
                      <span className="cl-dot" aria-hidden="true" />
                      <div>
                        <strong>Changed to {outcomeCopy(o.new_outcome).label}</strong>
                        <small>{formatDate(o.created_at, true)} · {o.actor_role.replace(/_/g, " ")}</small>
                        <p>{o.reason}</p>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </section>

          <details className="cl-tech">
            <summary>Technical details for auditors</summary>
            <dl className="cl-kv">
              <div><dt>Decision reference</dt><dd><code>{record.decision_id}</code></dd></div>
              <div><dt>Lending policy</dt><dd>{record.policy_version}</dd></div>
              <div><dt>Risk model</dt><dd>{record.model_version}</dd></div>
              <div><dt>Records in the tamper-evident chain</dt><dd>{record.chain_length}</dd></div>
              <div><dt>Chain fingerprint</dt><dd><code className="cl-wrap">{record.chain_head}</code></dd></div>
            </dl>
          </details>
        </div>

        <aside className="cl-col-rail" aria-label="Trust and actions">
          <div className="cl-card-rail" data-testid="trust-panel">
            <h2 className="cl-h3">Can you trust this record?</h2>
            <ul className="cl-trust">
              <li className={chainOk ? "is-ok" : "is-bad"} data-testid="chain-badge" data-chain-verified={String(chainOk)}>
                <span aria-hidden="true">{chainOk ? "✓" : "!"}</span>
                <div><strong>{chainOk ? "Unchanged since the decision" : "May have been altered"}</strong>
                  <small>{chainOk ? "Saved evidence still matches how it was first recorded." : "Saved evidence no longer matches the original."}</small></div>
              </li>
              <li className={replayOk === null ? "is-wait" : replayOk ? "is-ok" : "is-bad"}>
                <span aria-hidden="true">{replayOk === null ? "…" : replayOk ? "✓" : "!"}</span>
                <div><strong>{replayOk === null ? "Re-checking the score" : replayOk ? "Score re-calculated and matches" : "Score does not match"}</strong>
                  <small>The risk estimate was re-worked from the saved inputs.</small></div>
              </li>
              <li className={completeness === null ? "is-wait" : completeness === 100 ? "is-ok" : "is-review"}>
                <span aria-hidden="true">{completeness === null ? "…" : completeness === 100 ? "✓" : "◐"}</span>
                <div><strong>{completeness === null ? "Checking the evidence" : `${completeness}% of evidence available`}</strong>
                  <small>{completeness === 100 ? "Nothing is missing." : "Some items were removed or are missing."}</small></div>
              </li>
            </ul>
            {report && (
              <p className="cl-fine" data-testid="guardrail-line">
                Every statement in the explanation was checked against the evidence
                {report.guardrail_rejections > 0 ? `; ${report.guardrail_rejections} unsupported ${report.guardrail_rejections === 1 ? "statement was" : "statements were"} left out.` : "."}
              </p>
            )}
          </div>

          <div className="cl-card-rail">
            <h2 className="cl-h3">Take it with you</h2>
            <div className="cl-actions">
              <button type="button" className="cl-btn cl-btn-primary" onClick={() => { onTrack?.("print"); window.print(); }}>Save as PDF / print</button>
              <button type="button" className="cl-btn cl-btn-ghost" onClick={() => void copyLink()}>{copied ? "Link copied ✓" : "Copy link"}</button>
              <a className="cl-btn cl-btn-ghost" href="#review">Request a review</a>
            </div>
          </div>

          <nav className="cl-card-rail cl-jump" aria-label="On this page">
            <h2 className="cl-h3">On this page</h2>
            {[["why", "Why this decision"], ["rules", "Lending rules"], ["next", "What could change it"], ["evidence", "Evidence on file"], ["review", "Request a review"]].map(([id, label]) => (
              <a key={id} href={`#${id}`}>{label}</a>
            ))}
          </nav>
        </aside>
      </div>
    </article>
  );
};

function formatRuleValueFor(field: string, v: unknown): string {
  if (field === "dti_ratio" && typeof v === "number") return `${Math.round(v * 1000) / 10}%`;
  if (typeof v === "number" && /income|amount/.test(field)) return `£${v.toLocaleString("en-GB")}`;
  if (typeof v === "boolean") return v ? "Yes" : "No";
  return String(v);
}

export default DecisionView;

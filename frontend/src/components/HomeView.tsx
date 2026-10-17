import React, { useEffect, useMemo, useRef, useState } from "react";
import { ProblemKind } from "../api";
import ServiceProblem from "./ServiceProblem";
import { DecisionSummaryItem } from "../types";
import { formatDate, outcomeCopy, riskLabel } from "../lib/plain";
import OutcomePill from "./OutcomePill";

type Filter = "ALL" | "APPROVE" | "DECLINE" | "REFER" | "REVIEWED";

const PAGE = 12;

const FILTERS: Array<{ key: Filter; label: string }> = [
  { key: "ALL", label: "All" },
  { key: "APPROVE", label: "Approved" },
  { key: "DECLINE", label: "Not approved" },
  { key: "REFER", label: "In review" },
  { key: "REVIEWED", label: "Changed by a person" },
];

export interface HomeViewProps {
  cases: DecisionSummaryItem[];
  loading: boolean;
  error: string | null;
  signInNeeded?: boolean;
  problem?: ProblemKind;
  hasMore?: boolean;
  moreError?: string | null;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  onRetry: () => void;
  onOpen: (id: string) => void;
  recent: { id: string; label: string } | null;
  onSearch?: () => void;
}

export const HomeView: React.FC<HomeViewProps> = ({ cases, loading, error, signInNeeded, problem, hasMore, moreError, loadingMore, onLoadMore, onRetry, onOpen, recent, onSearch }) => {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("ALL");
  const [sort, setSort] = useState<"new" | "risk-high" | "risk-low">("new");
  const [limit, setLimit] = useState(PAGE);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (e.key === "/" && t && !/input|textarea|select/i.test(t.tagName)) {
        e.preventDefault();
        searchRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const counts = useMemo(() => {
    const c = { total: cases.length, APPROVE: 0, DECLINE: 0, REFER: 0, REVIEWED: 0 };
    for (const d of cases) {
      c[d.effective_outcome] += 1;
      if (d.effective_outcome !== d.initial_outcome) c.REVIEWED += 1;
    }
    return c;
  }, [cases]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return cases.filter((d) => {
      if (filter === "REVIEWED" ? d.effective_outcome === d.initial_outcome : filter !== "ALL" && d.effective_outcome !== filter) return false;
      return !q || [d.applicant_id, d.application_id, d.decision_id, outcomeCopy(d.effective_outcome).label].some((s) => s.toLowerCase().includes(q));
    });
  }, [cases, query, filter]);

  const sorted = useMemo(() => {
    const a = [...shown];
    if (sort === "new") a.sort((x, y) => y.decided_at.localeCompare(x.decided_at));
    else a.sort((x, y) => (sort === "risk-high" ? y.score - x.score : x.score - y.score));
    return a;
  }, [shown, sort]);

  const examples = useMemo(() => {
    const pick = (f: (d: DecisionSummaryItem) => boolean, label: string) => {
      const d = cases.find(f);
      return d ? { id: d.decision_id, label } : null;
    };
    return [
      pick((d) => d.effective_outcome === "DECLINE" && d.initial_outcome === "DECLINE", "A declined application"),
      pick((d) => d.effective_outcome === "APPROVE" && d.initial_outcome === "APPROVE", "An approved application"),
      pick((d) => d.effective_outcome === "REFER" && d.initial_outcome === "REFER", "One sent for review"),
      pick((d) => d.effective_outcome !== d.initial_outcome, "One a person changed"),
    ].filter(Boolean) as Array<{ id: string; label: string }>;
  }, [cases]);

  return (
    <div className="cl-home">
      <section className="cl-hero" aria-labelledby="hero-title">
        <svg className="cl-hero-thread" viewBox="0 0 600 220" aria-hidden="true" focusable="false">
          <path d="M10 170 C 120 170, 120 60, 230 60 S 360 150, 470 110 S 560 40, 592 40" />
          {[[10, 170], [230, 60], [470, 110], [592, 40]].map(([x, y]) => (
            <circle key={x} cx={x} cy={y} r="7" />
          ))}
        </svg>
        <p className="cl-eyebrow">Loan decisions, explained</p>
        <h1 id="hero-title" className="cl-hero-title">
          Know exactly <em>why</em> a loan decision was made.
        </h1>
        <p className="cl-hero-sub">
          Every decision comes with the evidence behind it, a plain-language explanation, and proof
          that the record hasn’t been changed since.
        </p>

        <div className="cl-search">
          <label htmlFor="case-search" className="cl-sr">Find a decision</label>
          <span className="cl-search-icon" aria-hidden="true">⌕</span>
          <input
            id="case-search"
            ref={searchRef}
            type="search"
            className="cl-search-input"
            placeholder="Search, e.g. APP-0011"
            value={query}
            onChange={(e) => { setQuery(e.target.value); setLimit(PAGE); }}
            onBlur={() => query.trim() && onSearch?.()}
            autoComplete="off"
          />
          <kbd className="cl-kbd" aria-hidden="true">/</kbd>
        </div>

        {recent && (
          <button type="button" className="cl-resume" onClick={() => onOpen(recent.id)}>
            <span aria-hidden="true">↺</span> Pick up where you left off: <strong>{recent.label}</strong>
          </button>
        )}
        {examples.length > 0 && (
          <div className="cl-examples">
            <span>New here? Try an example:</span>
            {examples.map((x) => (
              <button key={x.id} type="button" className="cl-chip" onClick={() => onOpen(x.id)}>{x.label}</button>
            ))}
          </div>
        )}
      </section>

      <ol className="cl-start" aria-label="How to get started">
        <li><span aria-hidden="true">1</span><div><strong>Find</strong><small>Search or pick an example</small></div></li>
        <li><span aria-hidden="true">2</span><div><strong>Understand</strong><small>Read why, rule by rule</small></div></li>
        <li><span aria-hidden="true">3</span><div><strong>Act</strong><small>Save a copy or ask for a review</small></div></li>
      </ol>

      <section className="cl-stats" aria-label="Summary of decisions">
        {[
          [hasMore ? "Decisions loaded" : "Decisions", counts.total, "neutral"],
          ["Approved", counts.APPROVE, "good"],
          ["Not approved", counts.DECLINE, "bad"],
          ["In review", counts.REFER, "review"],
        ].map(([label, n, tone]) => (
          <div key={String(label)} className={`cl-stat cl-stat-${tone}`}>
            <span className="cl-stat-n">{n}</span>
            <span className="cl-stat-l">{label}</span>
          </div>
        ))}
      </section>

      <section aria-labelledby="list-title" data-testid="case-list">
        <div className="cl-list-head">
          <h2 id="list-title" className="cl-h2">Browse decisions</h2>
          <div className="cl-chips" role="group" aria-label="Filter by result">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`cl-chip ${filter === f.key ? "is-on" : ""}`}
                aria-pressed={filter === f.key}
                onClick={() => { setFilter(f.key); setLimit(PAGE); }}
              >
                {f.label}
              </button>
            ))}
          </div>
        </div>
        <div className="cl-toolbar">
          <p className="cl-muted" role="status" aria-live="polite">
            {loading ? "Loading…" : `Showing ${Math.min(limit, sorted.length)} of ${sorted.length} decision${sorted.length === 1 ? "" : "s"}`}
            {!loading && hasMore && " loaded so far. There are older ones."}
          </p>
          <label className="cl-sort">
            <span>Sort by</span>
            <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
              <option value="new">Newest first</option>
              <option value="risk-high">Highest risk first</option>
              <option value="risk-low">Lowest risk first</option>
            </select>
          </label>
        </div>

        {loading && (
          <div className="cl-grid" role="status" aria-label="Loading decisions">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="cl-card cl-skeleton" />
            ))}
          </div>
        )}

        {error && <ServiceProblem kind={problem ?? (signInNeeded ? "signin" : "other")} what="decisions" onRetry={onRetry} />}

        {!loading && !error && shown.length === 0 && (
          <div className="cl-empty" role="status">
            <div className="cl-empty-art" aria-hidden="true">◌</div>
            <h3>{cases.length === 0 ? "No decisions yet" : "Nothing matches that search"}</h3>
            <p>
              {cases.length === 0
                ? "Once a decision has been recorded it will appear here."
                : "Try a different reference or clear the filter."}
            </p>
            {cases.length > 0 && (
              <button type="button" className="cl-btn cl-btn-ghost" onClick={() => { setQuery(""); setFilter("ALL"); }}>
                Clear search
              </button>
            )}
          </div>
        )}

        <ul className="cl-grid" data-testid="case-list-items">
          {sorted.slice(0, limit).map((d) => {
            const c = outcomeCopy(d.effective_outcome);
            const changed = d.effective_outcome !== d.initial_outcome;
            return (
              <li key={d.decision_id}>
                <button
                  type="button"
                  className={`cl-card cl-card-${c.tone}`}
                  onClick={() => onOpen(d.decision_id)}
                  data-testid={`case-item-${d.decision_id}`}
                >
                  <span className="cl-card-top">
                    <OutcomePill outcome={d.effective_outcome} />
                    <time dateTime={d.decided_at} className="cl-card-date">{formatDate(d.decided_at)}</time>
                  </span>
                  <span className="cl-card-title">{d.applicant_id}</span>
                  <span className="cl-card-sub">Application {d.application_id} · Ref {d.decision_id.slice(0, 8)}</span>
                  <span className="cl-card-foot">
                    <span>Risk estimate <strong>{riskLabel(d.score)}</strong></span>
                    {changed && <span className="cl-tag">Changed by a person</span>}
                    <span className="cl-card-go" aria-hidden="true">→</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {sorted.length > limit && (
          <div className="cl-more">
            <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setLimit((n) => n + PAGE)}>
              Show {Math.min(PAGE, sorted.length - limit)} more
            </button>
          </div>
        )}
        {sorted.length <= limit && hasMore && onLoadMore && !loading && !error && (
          <div className="cl-more">
            <button type="button" className="cl-btn cl-btn-ghost" onClick={onLoadMore} disabled={loadingMore} data-testid="load-more">
              {loadingMore ? "Loading…" : moreError ? "Try again" : "Load older decisions"}
            </button>
            {moreError && <p className="cl-note cl-note-bad" role="alert">{moreError}</p>}
          </div>
        )}
      </section>
    </div>
  );
};

export default HomeView;

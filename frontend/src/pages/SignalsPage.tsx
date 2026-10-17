import React, { useCallback, useEffect, useRef, useState } from "react";
import { fetchSignals, needsSignIn, problemKind, reviewSignal, runSignalChecks } from "../api";
import ServiceProblem from "../components/ServiceProblem";
import { Link } from "../lib/router";
import type { SignalItem, SignalRunResult } from "../types";

type Tab = "open" | "acknowledged" | "cleared";
const TABS: Array<[Tab, string]> = [["open", "To look at"], ["acknowledged", "Looked at"], ["cleared", "Nothing to worry about"]];
const SEVERITY: Record<SignalItem["severity"], string> = { high: "Look soon", warning: "Worth a look", info: "For information" };
const WHERE: Record<SignalItem["scope"], string> = { decision: "One decision", batch: "Across your decisions", access: "Who is looking" };

const SignalCard: React.FC<{ s: SignalItem; onDone: () => void; onError: (m: string) => void }> = ({ s, onDone, onError }) => {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const act = async (action: "acknowledge" | "clear") => {
    setBusy(true);
    try { await reviewSignal(s.signal_id, action, note.trim()); onDone(); }
    catch (e) { onError(needsSignIn(e) ? "This sign-in isn’t allowed to do this." : e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); }
  };
  return (
    <li className={`cl-finding is-${s.severity === "high" ? "error" : s.severity === "warning" ? "warning" : "info"}`} data-testid={`signal-${s.signal_id}`}>
      <div className="cl-finding-head">
        <span className="cl-sure cl-sure-lo">{SEVERITY[s.severity]}</span>
        <span className="cl-muted">{WHERE[s.scope]}</span>
      </div>
      <p className="cl-finding-msg">{s.message}</p>
      {s.decision_id && <p className="cl-muted"><Link to={{ page: "decision", id: s.decision_id }}>Open the decision</Link></p>}
      {s.status === "open" && (
        <div className="cl-finding-actions">
          <label className="cl-field"><span>Your note (at least 10 characters)</span>
            <input type="text" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </label>
          <div className="cl-actions">
            <button type="button" className="cl-btn cl-btn-primary" disabled={busy || note.trim().length < 10} onClick={() => act("acknowledge")}>I’ve looked at this</button>
            <button type="button" className="cl-btn cl-btn-ghost" disabled={busy || note.trim().length < 10} onClick={() => act("clear")}>Nothing to worry about</button>
          </div>
        </div>
      )}
    </li>
  );
};

export const SignalsPage: React.FC = () => {
  const listRef = useRef<HTMLUListElement>(null);
  const [tab, setTab] = useState<Tab>("open");
  const [items, setItems] = useState<SignalItem[] | null>(null);
  const [run, setRun] = useState<SignalRunResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemKind> | null>(null);

  const load = useCallback(async (t: Tab) => {
    setError(null); setProblem(null);
    try { setItems(await fetchSignals(t)); } catch (e) { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); }
  }, []);
  useEffect(() => { void load(tab); }, [tab, load]);

  const check = async () => {
    setBusy(true); setError(null);
    try { setRun(await runSignalChecks()); await load(tab); }
    catch (e) { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); }
    finally { setBusy(false); }
  };

  return (
    <div className="cl-page cl-quality">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Risk signals</h1>
      <p className="cl-page-lede">
        Patterns worth a person’s attention: the same customer reference applying again and again, identical details across different
        customers, a sudden shift in outcomes, or someone looking up lots of decisions that don’t exist. They are prompts to look,
        not conclusions. Nothing is blocked or changed, and customers never see them.
      </p>
      <button type="button" className="cl-btn cl-btn-primary" onClick={check} disabled={busy}>Check for signals</button>
      {busy && <p role="status" className="cl-muted">Looking…</p>}
      {problem && <ServiceProblem kind={problem} what="page" onRetry={() => void load(tab)} offerSample={false} />}
      {error && !problem && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}
      {run && (
        <div className="cl-note cl-note-good" role="status" data-testid="run-summary">
          <strong>Looked through {run.decisions_checked} decisions. {run.new_signals} new {run.new_signals === 1 ? "item" : "items"} to look at.</strong>
          <span>{run.open_total} in total are waiting.</span>
        </div>
      )}
      <div className="cl-chips" role="group" aria-label="Show">
        {TABS.map(([t, name]) => (
          <button key={t} type="button" aria-pressed={tab === t} className={`cl-chip ${tab === t ? "is-on" : ""}`} onClick={() => setTab(t)}>{name}</button>
        ))}
      </div>
      {items && items.length === 0 && <p className="cl-muted" data-testid="none">{tab === "open" ? "Nothing needs a look right now." : "Nothing here yet."}</p>}
      <ul className="cl-findings" ref={listRef} tabIndex={-1} aria-label="Items">
        {(items ?? []).map((s) => <SignalCard key={s.signal_id} s={s} onDone={() => load(tab).then(() => listRef.current?.focus())} onError={setError} />)}
      </ul>
    </div>
  );
};

export default SignalsPage;

import React, { useCallback, useEffect, useRef, useState } from "react";
import { fetchFindings, needsSignIn, resolveFinding, runQualityChecks } from "../api";
import { Link } from "../lib/router";
import type { FindingItem, QualityRunResult } from "../types";

type Tab = "open" | "corrected" | "dismissed";
const TABS: Array<[Tab, string]> = [["open", "To look at"], ["corrected", "Corrected"], ["dismissed", "Left as it is"]];
const SEVERITY: Record<FindingItem["severity"], string> = { error: "Likely a mistake", warning: "Worth checking", info: "For your information" };
const KIND: Record<FindingItem["kind"], string> = {
  missing: "Something is missing", implausible: "Unlikely value", conflict: "Sources disagree",
  inconsistent: "Doesn’t add up", outlier: "Unusual for you",
};

const show = (v: unknown) => (v === null || v === undefined ? "nothing" : typeof v === "number" ? v.toLocaleString("en-GB") : String(v));
const label = (key: string) => key.replace(/_/g, " ");

const FindingCard: React.FC<{ f: FindingItem; onDone: () => void; onError: (m: string) => void }> = ({ f, onDone, onError }) => {
  const [note, setNote] = useState("");
  const [value, setValue] = useState(f.suggested_fix ? String(f.suggested_fix.new_value) : "");
  const [busy, setBusy] = useState(false);
  const canAct = f.status === "open";
  const noteOk = note.trim().length >= 10;

  const act = async (action: "correct" | "dismiss") => {
    setBusy(true);
    try {
      const typed = value.trim();
      const asNumber = typed !== "" && !Number.isNaN(Number(typed)) ? Number(typed) : typed;
      await resolveFinding(f.finding_id, action, note.trim(), action === "correct" && typed !== "" && String(asNumber) !== String(f.suggested_fix?.new_value) ? asNumber : undefined);
      onDone();
    } catch (e) {
      onError(needsSignIn(e) ? "Your sign-in isn’t allowed to do this." : e instanceof Error ? e.message : "Something went wrong.");
    } finally { setBusy(false); }
  };

  return (
    <li className={`cl-finding is-${f.severity}`} data-testid={`finding-${f.finding_id}`}>
      <div className="cl-finding-head">
        <span className="cl-sure cl-sure-lo">{SEVERITY[f.severity]}</span>
        <span className="cl-muted">{KIND[f.kind]}</span>
      </div>
      <p className="cl-finding-msg">{f.message}</p>
      <p className="cl-muted">
        In <Link to={{ page: "decision", id: f.decision_id }}>this decision</Link>
        {f.field ? <>, about “{label(f.field)}”</> : null}.
      </p>
      {f.ai_note && <p className="cl-ai-tag">✦ AI’s guess: {f.ai_note}</p>}
      {f.suggested_fix && (
        <p data-testid="suggested">
          Suggested change: <strong>{label(f.suggested_fix.field)}</strong> from <strong>{show(f.observed)}</strong> to{" "}
          <strong>{show(f.suggested_fix.new_value)}</strong>. <span className="cl-muted">{f.suggested_fix.basis}</span>
        </p>
      )}
      {canAct && (
        <div className="cl-finding-actions">
          <label className="cl-field"><span>Why? (at least 10 characters)</span>
            <input type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
          </label>
          {(f.suggested_fix || f.kind !== "missing") && (
            <label className="cl-field"><span>Corrected value</span>
              <input type="text" value={value} onChange={(e) => setValue(e.target.value)} />
            </label>
          )}
          <div className="cl-actions">
            <button type="button" className="cl-btn cl-btn-primary" disabled={busy || !noteOk || value.trim() === ""} onClick={() => act("correct")}>
              Apply this correction
            </button>
            <button type="button" className="cl-btn cl-btn-ghost" disabled={busy || !noteOk} onClick={() => act("dismiss")}>
              It’s right as it is
            </button>
          </div>
          <p className="cl-fine">The original record is kept as it was. A correction is added next to it.</p>
        </div>
      )}
    </li>
  );
};

export const QualityPage: React.FC = () => {
  const listRef = useRef<HTMLUListElement>(null);
  const [tab, setTab] = useState<Tab>("open");
  const [items, setItems] = useState<FindingItem[] | null>(null);
  const [run, setRun] = useState<QualityRunResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) => setError(needsSignIn(e) ? "Sign-in is needed to see this." : e instanceof Error ? e.message : "Something went wrong.");

  const load = useCallback(async (t: Tab) => {
    setError(null);
    try { setItems(await fetchFindings(t)); } catch (e) { fail(e); }
  }, []);

  useEffect(() => { void load(tab); }, [tab, load]);

  const check = async () => {
    setBusy("Checking your decisions…"); setError(null);
    try { setRun(await runQualityChecks()); await load(tab); } catch (e) { fail(e); } finally { setBusy(null); }
  };

  return (
    <div className="cl-page cl-quality">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Data checks</h1>
      <p className="cl-page-lede">
        We look through your saved decisions for values that are missing, unlikely, or don’t agree with each other.
        You decide what is right. Nothing is ever overwritten: a correction is added alongside the original.
      </p>

      <button type="button" className="cl-btn cl-btn-primary" onClick={check} disabled={busy !== null}>Check my decisions</button>
      {busy && <p role="status" className="cl-muted">{busy}</p>}
      {error && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}
      {run && (
        <div className="cl-note cl-note-good" role="status" data-testid="run-summary">
          <strong>Checked {run.decisions_checked} decisions. {run.new_findings} new {run.new_findings === 1 ? "item" : "items"} to look at.</strong>
          <span>{run.open_total} in total are waiting.{run.ai_used ? " An AI model helped explain them." : ""}</span>
          {run.notes.map((n) => <span key={n}>{n}</span>)}
        </div>
      )}

      <div className="cl-chips" role="group" aria-label="Show">
        {TABS.map(([t, name]) => (
          <button key={t} type="button" aria-pressed={tab === t} className={`cl-chip ${tab === t ? "is-on" : ""}`} onClick={() => setTab(t)}>{name}</button>
        ))}
      </div>

      {items && items.length === 0 && (
        <p className="cl-muted" data-testid="none">{tab === "open" ? "Nothing needs a look right now." : "Nothing here yet."}</p>
      )}
      <ul className="cl-findings" ref={listRef} tabIndex={-1} aria-label="Items">
        {(items ?? []).map((f) => (
          <FindingCard key={f.finding_id} f={f} onDone={() => load(tab).then(() => listRef.current?.focus())} onError={setError} />
        ))}
      </ul>
    </div>
  );
};

export default QualityPage;

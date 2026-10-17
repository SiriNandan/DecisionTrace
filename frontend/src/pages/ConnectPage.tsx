import React, { useMemo, useState } from "react";
import { approveMapping, needsSignIn, proposeMapping, runImport, saveMapping } from "../api";
import { chunk, parseCsv, readFileText } from "../lib/csv";
import { Link } from "../lib/router";
import type { FieldRuleContract, ImportSummary, MappingPreview, MappingProposal, MappingSpecContract, SourceData } from "../types";

type CoreField = "external_id" | "subject_ref" | "decided_at" | "outcome" | "score" | "model_version" | "policy_version";

const FIELDS: Array<[CoreField, string, string]> = [
  ["external_id", "Application reference", "Your own reference for the application."],
  ["subject_ref", "Customer reference", "A reference number, never a name or contact detail."],
  ["decided_at", "Decision date", "When the decision was made."],
  ["outcome", "The decision", "Approved, declined or referred."],
  ["score", "Risk score", "The risk estimate your system gave, from 0 to 1 or as a percentage."],
  ["model_version", "Model version", "Which model produced the score."],
  ["policy_version", "Policy version", "Which lending policy was in force."],
];

const PLACEHOLDER = "not provided in file";
const FIXED = "__fixed__";
const OUTCOMES: Array<[string, string]> = [["APPROVE", "Approved"], ["DECLINE", "Declined"], ["REFER", "Referred"], ["", "Not set"]];
const CHUNK = 200;

const sure = (c: number) => (c >= 0.85 ? "High" : c >= 0.5 ? "Medium" : "Please check");

function gapsOf(spec: MappingSpecContract): string[] {
  const out: string[] = [];
  for (const [f, label] of FIELDS) {
    const r = spec[f];
    if (!r || (!r.column && (r.const == null || r.const === PLACEHOLDER))) out.push(label);
  }
  return out;
}

function withColumn(spec: MappingSpecContract, field: CoreField, value: string): MappingSpecContract {
  const prev = spec[field];
  let next: FieldRuleContract | null;
  if (value === "") next = null;
  else if (value === FIXED) next = { const: prev?.const && prev.const !== PLACEHOLDER ? prev.const : "", transform: "text", date_formats: [], value_map: {}, confidence: 1, note: "" };
  else {
    const transform = field === "decided_at" ? "date" : field === "outcome" ? "enum" : field === "score" ? (prev?.transform === "percent_to_fraction" ? "percent_to_fraction" : "number") : "text";
    next = {
      column: value, transform, date_formats: prev?.column === value ? prev.date_formats : [],
      value_map: prev?.value_map ?? {}, confidence: 1, note: "Chosen by you.",
    };
  }
  return { ...spec, [field]: next };
}

export const ConnectPage: React.FC = () => {
  const [name, setName] = useState("");
  const [source, setSource] = useState<{ file: string; format: "csv" | "json"; content?: string; rows: Array<Record<string, unknown>> } | null>(null);
  const [proposal, setProposal] = useState<MappingProposal | null>(null);
  const [spec, setSpec] = useState<MappingSpecContract | null>(null);
  const [mappingId, setMappingId] = useState<number | null>(null);
  const [approved, setApproved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<MappingPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ created: number; replayed: number; rejected: number; problems: ImportSummary["results"] } | null>(null);

  const gaps = useMemo(() => (spec ? gapsOf(spec) : []), [spec]);
  const fail = (e: unknown) => setError(needsSignIn(e) ? "Your sign-in is missing or isn’t allowed to do this. Please sign in again." : e instanceof Error ? e.message : "Something went wrong.");

  const sendable = (): SourceData | null =>
    source ? (source.format === "csv" ? { format: "csv", content: source.content } : { format: "json", rows: source.rows }) : null;

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null); setProposal(null); setSpec(null); setSummary(null); setApproved(false); setMappingId(null); setPreview(null);
    try {
      const text = await readFileText(file);
      const isJson = /\.json$/i.test(file.name) || text.trimStart().startsWith("[");
      if (isJson) {
        const parsed = JSON.parse(text);
        if (!Array.isArray(parsed)) throw new Error("A JSON file should hold a list of decisions.");
        setSource({ file: file.name, format: "json", rows: parsed });
      } else {
        setSource({ file: file.name, format: "csv", content: text, rows: parseCsv(text) });
      }
      setName(file.name.replace(/\.[^.]+$/, "").slice(0, 100));
    } catch (err) {
      setSource(null);
      setError(err instanceof SyntaxError ? "That file isn’t valid JSON." : err instanceof Error ? err.message : "We couldn’t read that file.");
    }
  };

  const check = async () => {
    const src = sendable();
    if (!src) return;
    setBusy("Reading your file…"); setError(null);
    try {
      const p = await proposeMapping(name || "my-file", src);
      setProposal(p); setSpec(p.spec); setMappingId(p.mapping_id); setPreview(p.preview ?? null); setDirty(false); setApproved(false);
    } catch (err) { fail(err); } finally { setBusy(null); }
  };

  const edit = (next: MappingSpecContract) => { setSpec(next); setDirty(true); setApproved(false); };

  const saveEdits = async (): Promise<number | null> => {
    const src = sendable();
    if (!spec || !src) return null;
    setBusy("Saving your changes…"); setError(null);
    try {
      const rec = await saveMapping(name || "my-file", spec, src);
      setMappingId(rec.mapping_id); setPreview(rec.preview ?? null); setDirty(false);
      return rec.mapping_id;
    } catch (err) { fail(err); return null; } finally { setBusy(null); }
  };

  const approve = async () => {
    let id = mappingId;
    if (dirty) id = await saveEdits();
    if (id == null) return;
    setBusy("Approving…"); setError(null);
    try { await approveMapping(id, "Approved in the Connect page"); setApproved(true); }
    catch (err) { fail(err); } finally { setBusy(null); }
  };

  const importAll = async () => {
    if (!source || mappingId == null) return;
    setError(null);
    const parts = chunk(source.rows, CHUNK);
    const total = { created: 0, replayed: 0, rejected: 0, problems: [] as ImportSummary["results"] };
    try {
      for (let i = 0; i < parts.length; i++) {
        setBusy(`Importing part ${i + 1} of ${parts.length}…`);
        setProgress(`${Math.min((i + 1) * CHUNK, source.rows.length)} of ${source.rows.length} rows`);
        const r = await runImport(mappingId, { format: "json", rows: parts[i] });
        total.created += r.created; total.replayed += r.replayed; total.rejected += r.rejected;
        total.problems.push(...r.results.filter((x) => x.status === "rejected").map((x) => ({ ...x, row: x.row + i * CHUNK })));
      }
      setSummary(total);
    } catch (err) { fail(err); setSummary(total); } finally { setBusy(null); setProgress(null); }
  };

  return (
    <div className="cl-page cl-connect">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Connect your data</h1>
      <p className="cl-page-lede">
        Choose a file of your decisions, in any layout. We suggest how your columns match what Clearly needs.
        You check it, approve it, and only then is anything saved. Names and contact details are never stored.
      </p>

      {error && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}

      <section className="cl-section" aria-labelledby="c1">
        <h2 id="c1" className="cl-h2">1. Choose your file</h2>
        <label className="cl-field">
          <span>A CSV or JSON file of decisions</span>
          <input type="file" accept=".csv,.json,text/csv,application/json" onChange={onFile} data-testid="file-input" />
        </label>
        {source && (
          <>
            <p className="cl-muted" data-testid="file-summary">{source.file}: {source.rows.length} rows.</p>
            <label className="cl-field"><span>Name for this layout</span>
              <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            </label>
            <button type="button" className="cl-btn cl-btn-primary" onClick={check} disabled={busy !== null || source.rows.length === 0}>
              Check my file
            </button>
          </>
        )}
      </section>

      {busy && <p role="status" className="cl-muted">{busy}{progress ? ` (${progress})` : ""}</p>}

      {proposal && spec && (
        <section className="cl-section" aria-labelledby="c2">
          <h2 id="c2" className="cl-h2">2. Check how we matched your columns</h2>
          <p className="cl-muted">
            {proposal.proposed_by.startsWith("ai:")
              ? "✦ Suggested by AI, then checked against your real rows. "
              : "Suggested by built-in rules, then checked against your real rows. "}
            Nothing is saved until you approve.
          </p>

          {proposal.issues.length > 0 && (
            <div className="cl-note cl-note-review" data-testid="issues">
              <strong>Please look at these</strong>
              <ul>{proposal.issues.map((i) => <li key={i}>{i}</li>)}</ul>
            </div>
          )}

          <table className="cl-table cl-match" data-testid="match-table">
            <caption className="cl-sr">How your columns match</caption>
            <thead><tr><th scope="col">What we need</th><th scope="col">Where we found it</th><th scope="col">How sure we are</th></tr></thead>
            <tbody>
              {FIELDS.map(([f, label, help]) => {
                const r = spec[f];
                const mode = !r ? "" : r.column ? r.column : FIXED;
                return (
                  <tr key={f} data-testid={`match-${f}`}>
                    <th scope="row">{label}<small className="cl-muted">{help}</small></th>
                    <td>
                      <select aria-label={`Column for ${label}`} value={mode} onChange={(e) => edit(withColumn(spec, f, e.target.value))}>
                        <option value="">Not found</option>
                        {proposal.columns.map((c) => <option key={c} value={c}>{c}</option>)}
                        <option value={FIXED}>A fixed value…</option>
                      </select>
                      {r && !r.column && (
                        <input type="text" aria-label={`Fixed value for ${label}`} value={r.const && r.const !== PLACEHOLDER ? String(r.const) : ""}
                          placeholder="Type the value" onChange={(e) => edit({ ...spec, [f]: { ...r, const: e.target.value } })} />
                      )}
                    </td>
                    <td>{r ? <span className={`cl-sure cl-sure-${sure(r.confidence) === "High" ? "hi" : "lo"}`}>{sure(r.confidence)}</span> : <span className="cl-sure cl-sure-lo">Needed</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {spec.outcome?.column && proposal.outcome_values.length > 0 && (
            <fieldset className="cl-fieldset">
              <legend>What each value in “{spec.outcome.column}” means</legend>
              {proposal.outcome_values.map((v) => {
                const cur = Object.entries(spec.outcome!.value_map).find(([k]) => k.toLowerCase() === v.toLowerCase())?.[1] ?? "";
                return (
                  <label key={v} className="cl-field cl-inline"><span>“{v}”</span>
                    <select value={cur} onChange={(e) => {
                      const vm = { ...spec.outcome!.value_map };
                      for (const k of Object.keys(vm)) if (k.toLowerCase() === v.toLowerCase()) delete vm[k];
                      if (e.target.value) vm[v] = e.target.value;
                      edit({ ...spec, outcome: { ...spec.outcome!, value_map: vm } });
                    }}>
                      {OUTCOMES.map(([val, lab]) => <option key={lab} value={val}>{lab}</option>)}
                    </select>
                  </label>
                );
              })}
            </fieldset>
          )}

          <details className="cl-details">
            <summary>Other columns we use or leave out</summary>
            <p><strong>Used as supporting evidence:</strong> {spec.evidence.flatMap((e) => Object.values(e.fields).map((f) => f.column)).filter(Boolean).join(", ") || "none"}.</p>
            {spec.reasons && <p><strong>Reasons for the decision:</strong> {spec.reasons.column}.</p>}
            <p><strong>Left out:</strong></p>
            <ul>{Object.entries(spec.ignored_columns).map(([c, why]) => <li key={c}>{c}: {why}</li>)}</ul>
          </details>

          {preview && (
            <div className={`cl-note ${preview.invalid === 0 ? "cl-note-good" : "cl-note-review"}`} data-testid="preview">
              <strong>{preview.valid} of {preview.rows_checked} rows read correctly{preview.invalid ? `, ${preview.invalid} can’t be read yet` : ""}.</strong>
              {preview.problems.slice(0, 3).map((p) => <span key={p.row}>Row {p.row}: {p.problems.join("; ")}</span>)}
            </div>
          )}
          {dirty && <p className="cl-muted" role="status">You’ve made changes. Approving will save them first.</p>}
          {gaps.length > 0 && <p className="cl-muted" data-testid="gaps">Still needed: {gaps.join(", ")}.</p>}

          <div className="cl-actions">
            <button type="button" className="cl-btn cl-btn-primary" disabled={busy !== null || gaps.length > 0 || approved} onClick={approve}>
              {approved ? "Approved" : "Approve this match"}
            </button>
          </div>
        </section>
      )}

      {approved && source && (
        <section className="cl-section" aria-labelledby="c3">
          <h2 id="c3" className="cl-h2">3. Import</h2>
          <p className="cl-muted">This saves {source.rows.length} decisions with their evidence. Running it again won’t create duplicates.</p>
          <button type="button" className="cl-btn cl-btn-primary" disabled={busy !== null || summary !== null} onClick={importAll}>
            Import {source.rows.length} decisions
          </button>
        </section>
      )}

      {summary && (
        <section className="cl-section" aria-live="polite" data-testid="import-summary">
          <h2 className="cl-h2">Done</h2>
          <p><strong>{summary.created}</strong> saved, <strong>{summary.replayed}</strong> already there, <strong>{summary.rejected}</strong> couldn’t be saved.</p>
          {summary.problems.length > 0 && (
            <ul>{summary.problems.slice(0, 8).map((p) => <li key={p.row}>Row {p.row}: {(p.problems ?? []).join("; ")}</li>)}</ul>
          )}
          <Link to={{ page: "home" }} className="cl-btn cl-btn-ghost">See the decisions</Link>
        </section>
      )}
    </div>
  );
};

export default ConnectPage;

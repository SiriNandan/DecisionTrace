import React, { useCallback, useEffect, useMemo, useState } from "react";
import { approveMapping, draftSqlQuery, fetchMapping, fetchMappings, needsSignIn, proposeMapping, runImport, saveMapping } from "../api";
import { chunk, parseCsv, readFileText } from "../lib/csv";
import { Link } from "../lib/router";
import type { DataSourceSpec, FieldRuleContract, ImportSummary, MappingListItem, MappingPreview, MappingProposal, MappingRecord, MappingSpecContract, Me, SourceData } from "../types";

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

export const ConnectPage: React.FC<{ me?: Me | null }> = ({ me = null }) => {
  // With no sign-in details given (as in the sample), everything is offered; otherwise each step needs its own permission.
  const can = (scope: string) => !me || me.scopes.includes(scope);
  const canPropose = can("mappings:write"), canApprove = can("mappings:approve"), canImport = can("ingest:write");
  const [name, setName] = useState("");
  const [source, setSource] = useState<{ file: string; format: "csv" | "json"; content?: string; rows: Array<Record<string, unknown>> } | null>(null);
  // Data fetched by the server from a URL, a storage bucket or a database, instead of a file chosen here.
  const [remote, setRemote] = useState<{ format: "csv" | "json"; spec: DataSourceSpec; label: string } | null>(null);
  const [remoteKind, setRemoteKind] = useState<"url" | "gcs" | "sql">("url");
  const [remoteFormat, setRemoteFormat] = useState<"csv" | "json">("csv");
  const [rUrl, setRUrl] = useState(""), [rToken, setRToken] = useState(""), [rUri, setRUri] = useState("");
  const [rDsn, setRDsn] = useState(""), [rQuery, setRQuery] = useState("");
  const [proposal, setProposal] = useState<MappingProposal | null>(null);
  const [spec, setSpec] = useState<MappingSpecContract | null>(null);
  const [mappingId, setMappingId] = useState<number | null>(null);
  const [approved, setApproved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<MappingPreview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<string | null>(null);
  const [portfolio, setPortfolio] = useState("");
  const [layouts, setLayouts] = useState<MappingListItem[] | null>(null);
  const [looking, setLooking] = useState<MappingRecord | null>(null);
  const [summary, setSummary] = useState<{ created: number; replayed: number; rejected: number; problems: ImportSummary["results"] } | null>(null);

  const loadLayouts = useCallback(async () => {
    if (!me || !me.scopes.includes("mappings:read")) return;
    try { setLayouts(await fetchMappings()); } catch { setLayouts([]); }
  }, [me]);
  useEffect(() => { void loadLayouts(); }, [loadLayouts]);

  const gaps = useMemo(() => (spec ? gapsOf(spec) : []), [spec]);
  const fail = (e: unknown) => setError(needsSignIn(e) ? "Your sign-in is missing or isn’t allowed to do this. Please sign in again." : e instanceof Error ? e.message : "Something went wrong.");

  const sendable = (): SourceData | null =>
    remote ? { format: remote.format, source: remote.spec } :
    source ? (source.format === "csv" ? { format: "csv", content: source.content } : { format: "json", rows: source.rows }) : null;

  const resetChecks = () => { setError(null); setProposal(null); setSpec(null); setSummary(null); setApproved(false); setMappingId(null); setPreview(null); };

  const [drafted, setDrafted] = useState<{ query: string; tables: Record<string, number>; model: string } | null>(null);
  const useRemote = async () => {
    resetChecks(); setSource(null); setDrafted(null);
    let query = "";
    if (remoteKind === "sql") {
      setBusy("Reading the database structure and writing the query…");
      try { const d = await draftSqlQuery(rDsn.trim()); query = d.query; setDrafted(d); }
      catch (err) { setBusy(null); fail(err); return; }
      setBusy(null);
    }
    const spec: DataSourceSpec =
      remoteKind === "url" ? { type: "url", url: rUrl.trim(), ...(rToken ? { bearer_token: rToken } : {}) }
      : remoteKind === "gcs" ? { type: "gcs", uri: rUri.trim() }
      : { type: "sql", dsn: rDsn.trim(), query };
    let label: string;
    try {
      label = remoteKind === "url" ? `web address ${new URL(rUrl.trim()).hostname}`
        : remoteKind === "gcs" ? `storage ${rUri.trim().split("/").slice(0, 3).join("/")}`
        : `database ${new URL(rDsn.trim()).hostname}`;
    } catch { setError("That address doesn’t look right. Check it and try again."); return; }
    setRemote({ format: remoteKind === "sql" ? "json" : remoteFormat, spec, label });
    setName(`${remoteKind}-source`);
  };
  const remoteReady = remoteKind === "url" ? rUrl.trim() !== "" : remoteKind === "gcs" ? /^gs:\/\/[^/]+\/.+/.test(rUri.trim()) : rDsn.trim() !== "";

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setRemote(null);
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
    setBusy(remote ? "Fetching and reading your data…" : "Reading your file…"); setError(null);
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
    if (mappingId == null) return;
    setError(null);
    if (remote) {  // the server fetches the data itself and loads it one page (up to 500 rows) at a time
      const total = { created: 0, replayed: 0, rejected: 0, problems: [] as ImportSummary["results"] };
      let offset: number | null = 0;
      try {
        while (offset !== null) {
          setBusy("Fetching and importing…");
          const r: ImportSummary = await runImport(mappingId, { format: remote.format, source: remote.spec }, false, portfolio.trim() || undefined, offset);
          total.created += r.created; total.replayed += r.replayed; total.rejected += r.rejected;
          total.problems.push(...r.results.filter((x) => x.status === "rejected"));
          if (r.total_rows) setProgress(`${Math.min(r.total_rows, (r.next_offset ?? r.total_rows))} of ${r.total_rows} rows`);
          offset = r.next_offset ?? null;
        }
        setSummary(total);
      } catch (err) { fail(err); setSummary(total); } finally { setBusy(null); setProgress(null); }
      return;
    }
    if (!source) return;
    const parts = chunk(source.rows, CHUNK);
    const total = { created: 0, replayed: 0, rejected: 0, problems: [] as ImportSummary["results"] };
    try {
      for (let i = 0; i < parts.length; i++) {
        setBusy(`Importing part ${i + 1} of ${parts.length}…`);
        setProgress(`${Math.min((i + 1) * CHUNK, source.rows.length)} of ${source.rows.length} rows`);
        const r = await runImport(mappingId, { format: "json", rows: parts[i] }, false, portfolio.trim() || undefined);
        total.created += r.created; total.replayed += r.replayed; total.rejected += r.rejected;
        total.problems.push(...r.results.filter((x) => x.status === "rejected").map((x) => ({ ...x, row: x.row + i * CHUNK })));
      }
      setSummary(total);
    } catch (err) { fail(err); setSummary(total); } finally { setBusy(null); setProgress(null); }
  };

  const reviewLayout = async (id: number) => {
    setError(null);
    try { setLooking(await fetchMapping(id)); } catch (err) { fail(err); }
  };
  const approveLayout = async (id: number) => {
    setBusy("Approving…"); setError(null);
    try { await approveMapping(id, "Approved in the Connect page"); setLooking(null); await loadLayouts(); }
    catch (err) { fail(err); } finally { setBusy(null); }
  };
  const useLayout = (l: MappingListItem) => { setMappingId(l.mapping_id); setApproved(true); setProposal(null); setSpec(null); setSummary(null); };

  return (
    <div className="cl-page cl-connect">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Connect your data</h1>
      <p className="cl-page-lede">
        Choose a file of your decisions, in any layout. We suggest how your columns match what DecisionTrace needs.
        You check it, approve it, and only then is anything saved. Names and contact details are never stored.
      </p>

      {error && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}

      {me && (
        <section className="cl-section" aria-labelledby="c0" data-testid="who-does-what">
          <h2 id="c0" className="cl-h2">Who does what</h2>
          <ol>
            <li>The <strong>data officer</strong> chooses a file and checks how its columns were read.</li>
            <li>An <strong>analyst</strong> reviews that match and approves it.</li>
            <li>The <strong>data officer</strong> loads the file. Nobody else can load data.</li>
          </ol>
          <p className="cl-muted" data-testid="your-part">
            {canImport ? "You’re the data officer: you choose the file and load it once an analyst has approved the match."
              : canApprove ? "You’re an analyst: review the layouts waiting for you below. Loading data is done by your data officer."
              : "You can see layouts here, but loading data and approving them is done by others on your team."}
          </p>
        </section>
      )}

      {layouts && layouts.length > 0 && (
        <section className="cl-section" aria-labelledby="c-l" data-testid="layouts">
          <h2 id="c-l" className="cl-h2">Layouts your team has set up</h2>
          <ul className="cl-findings">
            {layouts.map((l) => (
              <li key={l.mapping_id} className="cl-finding is-info" data-testid={`layout-${l.mapping_id}`}>
                <p className="cl-finding-msg"><strong>{l.name}</strong> <span className="cl-muted">version {l.version}</span></p>
                <p className="cl-muted">{l.status === "approved" ? "Approved and ready to use" : l.status === "proposed" ? "Waiting for an analyst to approve it" : "Turned down"}</p>
                {l.status === "proposed" && canApprove && (
                  <button type="button" className="cl-btn cl-btn-ghost" onClick={() => void reviewLayout(l.mapping_id)} aria-label={`Review ${l.name}`}>Review</button>
                )}
                {l.status === "approved" && canImport && (source || remote) && !summary && (
                  <button type="button" className="cl-btn cl-btn-ghost" onClick={() => useLayout(l)} aria-label={`Use ${l.name} for this file`}>Use for this file</button>
                )}
              </li>
            ))}
          </ul>
          {looking && (
            <div className="cl-note cl-note-review" data-testid="looking">
              <strong>{looking.name}: how the columns were matched</strong>
              <ul>
                {FIELDS.map(([f, label]) => {
                  const r = looking.spec[f];
                  return <li key={f}>{label}: {r?.column ? `column “${r.column}”` : r?.const != null && r.const !== PLACEHOLDER ? `fixed value “${String(r.const)}”` : "not found"}</li>;
                })}
              </ul>
              {looking.gaps.length > 0 && <span>Still needed: {looking.gaps.join(", ")}.</span>}
              <div className="cl-actions">
                <button type="button" className="cl-btn cl-btn-primary" disabled={busy !== null || looking.gaps.length > 0} onClick={() => void approveLayout(looking.mapping_id)}>Approve this match</button>
                <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setLooking(null)}>Close</button>
              </div>
            </div>
          )}
        </section>
      )}

      {!canPropose && !canImport ? (
        <p className="cl-muted" data-testid="no-loading">Choosing and loading files isn’t part of this role.</p>
      ) : (
      <section className="cl-section" aria-labelledby="c1">
        <h2 id="c1" className="cl-h2">1. Choose your file</h2>
        <label className="cl-field">
          <span>A CSV or JSON file of decisions</span>
          <input type="file" accept=".csv,.json,text/csv,application/json" onChange={onFile} data-testid="file-input" />
        </label>
        <details className="cl-field" data-testid="remote-source">
          <summary>Or fetch it from a data source</summary>
          <label className="cl-field"><span>Where is it?</span>
            <select value={remoteKind} onChange={(e) => setRemoteKind(e.target.value as "url" | "gcs" | "sql")} data-testid="remote-kind">
              <option value="url">A web address (https)</option>
              <option value="gcs">A Google Cloud Storage file</option>
              <option value="sql">A database (read-only query)</option>
            </select>
          </label>
          {remoteKind !== "sql" && (
            <label className="cl-field"><span>What kind of file is it?</span>
              <select value={remoteFormat} onChange={(e) => setRemoteFormat(e.target.value as "csv" | "json")}>
                <option value="csv">CSV</option>
                <option value="json">JSON (a list of decisions)</option>
              </select>
            </label>
          )}
          {remoteKind === "url" && (
            <>
              <label className="cl-field"><span>Web address</span>
                <input type="url" value={rUrl} onChange={(e) => setRUrl(e.target.value)} placeholder="https://files.yourbank.example/decisions.csv" />
              </label>
              <label className="cl-field"><span>Access token (optional)</span>
                <input type="password" autoComplete="off" value={rToken} onChange={(e) => setRToken(e.target.value)} />
                <small className="cl-muted">Used for this request only and never saved.</small>
              </label>
            </>
          )}
          {remoteKind === "gcs" && (
            <label className="cl-field"><span>Storage path</span>
              <input type="text" value={rUri} onChange={(e) => setRUri(e.target.value)} placeholder="gs://your-bucket/exports/decisions.csv" />
            </label>
          )}
          {remoteKind === "sql" && (
            <>
              <label className="cl-field"><span>Database address</span>
                <input type="password" autoComplete="off" value={rDsn} onChange={(e) => setRDsn(e.target.value)} placeholder="postgresql://user:password@host:5432/database" />
                <small className="cl-muted">Use an account that can only read. It is used for this request only and never saved.</small>
              </label>
              <p className="cl-muted">No query needed. The AI reads the table names, columns and links (never the data) and writes a read-only query that gives one row per loan application.</p>
            </>
          )}
          <p className="cl-muted">Your service administrator must allow each web host, storage bucket or database host first.</p>
          <button type="button" className="cl-btn cl-btn-ghost" disabled={!remoteReady || busy !== null} onClick={useRemote}>Use this data source</button>
        </details>
        {remote && (
          <>
            <p className="cl-muted" data-testid="remote-summary">Data source: {remote.label}.</p>
            {drafted && (
              <details className="cl-field" data-testid="drafted-query">
                <summary>The AI read {Object.keys(drafted.tables).length} tables. Show the query it wrote</summary>
                <pre className="cl-fine" style={{ whiteSpace: "pre-wrap" }}>{drafted.query}</pre>
              </details>
            )}
            <label className="cl-field"><span>Name for this layout</span>
              <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            </label>
            {canPropose && (
              <button type="button" className="cl-btn cl-btn-primary" onClick={check} disabled={busy !== null}>Check my data</button>
            )}
          </>
        )}
        {source && (
          <>
            <p className="cl-muted" data-testid="file-summary">{source.file}: {source.rows.length} rows.</p>
            <label className="cl-field"><span>Name for this layout</span>
              <input type="text" value={name} maxLength={100} onChange={(e) => setName(e.target.value)} />
            </label>
            {canPropose && (
              <button type="button" className="cl-btn cl-btn-primary" onClick={check} disabled={busy !== null || source.rows.length === 0}>
                Check my file
              </button>
            )}
          </>
        )}
      </section>
      )}

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

          {canApprove ? (
            <div className="cl-actions">
              <button type="button" className="cl-btn cl-btn-primary" disabled={busy !== null || gaps.length > 0 || approved} onClick={approve}>
                {approved ? "Approved" : "Approve this match"}
              </button>
            </div>
          ) : (
            <p className="cl-note cl-note-review" role="status" data-testid="needs-analyst">
              An analyst needs to approve this match before anything is loaded. It is saved as “{name || "my-file"}”{mappingId != null ? ` (number ${mappingId})` : ""}. Come back once they have, and choose it under “Layouts your team has set up”.
            </p>
          )}
        </section>
      )}

      {approved && (source || remote) && !canImport && (
        <p className="cl-note cl-note-review" data-testid="officer-loads">Approved. Your data officer loads the file; this sign-in can’t.</p>
      )}
      {approved && (source || remote) && canImport && (
        <section className="cl-section" aria-labelledby="c3">
          <h2 id="c3" className="cl-h2">3. Import</h2>
          <p className="cl-muted">{remote ? `This fetches your data from the ${remote.label} and saves it (in pages of 500) with its evidence.` : `This saves ${source!.rows.length} decisions with their evidence.`} Running it again won’t create duplicates.</p>
          <label className="cl-field"><span>Group these decisions (optional)</span>
            <input type="text" value={portfolio} maxLength={64} onChange={(e) => setPortfolio(e.target.value)} placeholder="For example: Leeds branch" />
            <small className="cl-muted">Your team administrator can limit a person to some groups.</small>
          </label>
          <button type="button" className="cl-btn cl-btn-primary" disabled={busy !== null || summary !== null} onClick={importAll}>
            {remote ? "Fetch and import" : `Import ${source!.rows.length} decisions`}
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

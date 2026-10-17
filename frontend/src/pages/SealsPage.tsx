import React, { useCallback, useEffect, useState } from "react";
import { checkSeals, fetchSeals, problemKind, runSeal } from "../api";
import ServiceProblem from "../components/ServiceProblem";
import { formatDate } from "../lib/plain";
import type { SealBatch, SealCheck } from "../types";

export const SealsPage: React.FC = () => {
  const [batches, setBatches] = useState<SealBatch[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [result, setResult] = useState<SealCheck | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemKind> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) => { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); };
  const load = useCallback(async () => {
    setProblem(null); setError(null);
    try { setBatches(await fetchSeals()); } catch (e) { fail(e); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const seal = async () => {
    setBusy("Sealing…"); setMessage(null);
    try { const r = await runSeal(); setMessage(r.sealed ? `Sealed ${r.batch ? `${r.batch.leaf_count} records` : ""}.` : r.message); await load(); }
    catch (e) { fail(e); } finally { setBusy(null); }
  };
  const check = async () => {
    setBusy("Checking every seal…"); setResult(null);
    try { setResult(await checkSeals()); } catch (e) { fail(e); } finally { setBusy(null); }
  };

  return (
    <div className="cl-page cl-quality">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Record seals</h1>
      <p className="cl-page-lede">
        Sealing records a signed fingerprint of every record at a moment in time. If someone later changes a record, or removes
        a seal, checking the seals will say so. How well that holds depends on keeping the signing key and a copy of the seals
        away from whoever runs the database. Customers and auditors can ask for a proof for any one decision.
      </p>
      <div className="cl-actions">
        <button type="button" className="cl-btn cl-btn-primary" onClick={seal} disabled={busy !== null}>Seal new records</button>
        <button type="button" className="cl-btn cl-btn-ghost" onClick={check} disabled={busy !== null}>Check all seals</button>
      </div>
      {busy && <p role="status" className="cl-muted">{busy}</p>}
      {problem && <ServiceProblem kind={problem} what="page" onRetry={() => void load()} offerSample={false} />}
      {error && !problem && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}
      {message && <div className="cl-note cl-note-good" role="status" data-testid="seal-message"><strong>{message}</strong></div>}
      {result && (
        <div className={`cl-note ${result.ok ? "cl-note-good" : "cl-note-bad"}`} role="status" data-testid="seal-check">
          <strong>{result.ok ? `No changes found: ${result.records} records in ${result.batches} seals match.` : "Something doesn’t match."}</strong>
          {result.not_yet_sealed > 0 && <span>{result.not_yet_sealed} newer {result.not_yet_sealed === 1 ? "record isn’t" : "records aren’t"} sealed yet, so they aren’t covered by this check.</span>}
          {!result.checked_published_copy && <span>No outside copy of the seals is set up, so this only compares the database with itself and the signature. It can’t show a change made by someone who controls both.</span>}
          {result.problems.length > 0 && <ul>{result.problems.map((p, i) => <li key={i}>Seal {p.batch}: {p.problem}</li>)}</ul>}
        </div>
      )}
      {batches && batches.length === 0 && <p className="cl-muted" data-testid="no-seals">Nothing has been sealed yet.</p>}
      {batches && batches.length > 0 && (
        <table className="cl-table" data-testid="seal-table">
          <caption className="cl-sr">Seals, newest first</caption>
          <thead><tr><th scope="col">Seal</th><th scope="col">When</th><th scope="col">Records</th><th scope="col">Copy kept outside the database</th></tr></thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.seq}><th scope="row">#{b.seq}</th><td>{formatDate(b.signed_at, true)}</td><td>{b.leaf_count}</td><td>{b.published ? "Yes" : "No"}</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export default SealsPage;

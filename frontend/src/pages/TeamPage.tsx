import React, { useCallback, useEffect, useState } from "react";
import { addTeamMember, fetchTeam, needsSignIn, problemKind, withdrawTeamMember } from "../api";
import ServiceProblem from "../components/ServiceProblem";
import type { NewTeamKey, TeamMember, TeamState } from "../types";

const EXPIRY: Array<[string, string]> = [["", "Never"], ["30", "30 days"], ["90", "90 days"], ["365", "1 year"]];
const STATE_TEXT: Record<TeamMember["state"], string> = { active: "Active", expired: "Expired", withdrawn: "Withdrawn" };
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("en-GB") : "Never");

export const TeamPage: React.FC = () => {
  const [team, setTeam] = useState<TeamState | null>(null);
  const [problem, setProblem] = useState<ReturnType<typeof problemKind> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [email, setEmail] = useState("");
  const [roles, setRoles] = useState<string[]>([]);
  const [days, setDays] = useState("");
  const [chosen, setChosen] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [made, setMade] = useState<NewTeamKey | null>(null);
  const [copied, setCopied] = useState(false);
  const [confirm, setConfirm] = useState<number | null>(null);

  const load = useCallback(async () => {
    setError(null); setProblem(null);
    try { setTeam(await fetchTeam()); } catch (e) { setProblem(problemKind(e)); setError(e instanceof Error ? e.message : "Something went wrong."); }
  }, []);
  useEffect(() => { void load(); }, [load]);

  const fail = (e: unknown) => setError(needsSignIn(e) ? "This sign-in isn’t allowed to manage the team." : e instanceof Error ? e.message : "Something went wrong.");
  const picked = (team?.roles ?? []).filter((r) => roles.includes(r.role));
  const canLimit = picked.length > 0 && picked.every((r) => r.can_limit_to_portfolios);
  const roleNames = (list: string[]) => list.map((x) => team?.roles.find((r) => r.role === x)?.name ?? x).join(" + ");
  const limits = canLimit ? [...chosen, ...typed.split(",")].map((s) => s.trim()).filter(Boolean) : [];

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null); setMade(null); setCopied(false);
    if (label.trim().length < 2) { setError("Please enter the person’s name."); return; }
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) { setError("Please enter a valid email address."); return; }
    if (!roles.length) { setError("Please choose what this person does (one or more)."); return; }
    setBusy(true);
    try {
      setMade(await addTeamMember({ label: label.trim(), email: email.trim(), roles, ...(days ? { expires_days: Number(days) } : {}), ...(limits.length ? { portfolios: [...new Set(limits)] } : {}) }));
      setLabel(""); setEmail(""); setRoles([]); setDays(""); setChosen([]); setTyped("");
      await load();
    } catch (err) { fail(err); } finally { setBusy(false); }
  };

  const withdraw = async (id: number) => {
    setBusy(true); setError(null);
    try { await withdrawTeamMember(id); setConfirm(null); await load(); } catch (err) { fail(err); } finally { setBusy(false); }
  };

  const copy = async () => {
    if (!made) return;
    try { await navigator.clipboard.writeText(made.key); setCopied(true); } catch { setCopied(false); }
  };

  return (
    <div className="cl-page cl-team">
      <p className="cl-eyebrow">For lenders</p>
      <h1 className="cl-page-title" tabIndex={-1}>Your team</h1>
      <p className="cl-page-lede">
        Give each person their own access key, with only the part of DecisionTrace their job needs. Withdraw a key and it stops working at once.
        Everything you change here is written to the access log.
      </p>

      {problem && <ServiceProblem kind={problem} what="page" onRetry={() => void load()} offerSample={false} />}
      {error && !problem && <div className="cl-note cl-note-bad" role="alert"><strong>{error}</strong></div>}

      {made && (
        <div className="cl-note cl-note-good" role="status" data-testid="new-key">
          <strong>{made.email_status === "sent" ? `Invitation sent to ${made.email}` : `Key created for ${made.label}`}</strong>
          {made.email_status === "sent"
            ? <span>The email contains their key. It is also shown here once, in case you need it.</span>
            : <span>{made.email_status === "failed" ? "We couldn’t send the email." : "Email isn’t set up on this server, so nothing was sent."} Copy the key now and send it to {made.email} privately. It is shown only once and can’t be found again.</span>}
          <code className="cl-code" data-testid="new-key-value">{made.key}</code>
          <div className="cl-actions">
            <button type="button" className="cl-btn cl-btn-ghost" onClick={() => void copy()}>{copied ? "Copied ✓" : "Copy the key"}</button>
            <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setMade(null)}>I’ve saved it</button>
          </div>
        </div>
      )}

      {team && (
        <>
          <section className="cl-section" aria-labelledby="t-add">
            <h2 id="t-add" className="cl-h2">Invite someone</h2>
            <p className="cl-muted">They’ll get an email with their own access key and how to sign in.</p>
            <form onSubmit={add} noValidate>
              <div className="cl-grid-2">
                <label className="cl-field"><span>Name</span>
                  <input type="text" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} placeholder="Priya Shah" autoComplete="off" />
                </label>
                <label className="cl-field"><span>Email</span>
                  <input type="email" value={email} maxLength={200} onChange={(e) => setEmail(e.target.value)} placeholder="priya@yourbank.com" autoComplete="off" />
                </label>
              </div>
              <fieldset className="cl-fieldset">
                <legend>What do they do? <small className="cl-muted">Choose one or more</small></legend>
                <div className="cl-roles">
                {team.roles.map((r) => (
                  <label key={r.role} className={`cl-role${roles.includes(r.role) ? " is-on" : ""}`}>
                    <input type="checkbox" name="role" value={r.role} checked={roles.includes(r.role)}
                      onChange={(e) => setRoles(e.target.checked ? [...roles, r.role] : roles.filter((x) => x !== r.role))} />
                    <span><strong>{r.name}</strong><small>{r.description}</small></span>
                  </label>
                ))}
                </div>
              </fieldset>
              {canLimit && (
                <fieldset className="cl-fieldset">
                  <legend>Which decisions can they see? (leave empty for all)</legend>
                  {team.portfolios.map((p) => (
                    <label key={p.name} className="cl-check">
                      <input type="checkbox" checked={chosen.includes(p.name)}
                        onChange={(e) => setChosen(e.target.checked ? [...chosen, p.name] : chosen.filter((c) => c !== p.name))} />
                      <span>{p.name} <small className="cl-muted">({p.decisions} decisions)</small></span>
                    </label>
                  ))}
                  <label className="cl-field"><span>Or type group names, separated by commas</span>
                    <input type="text" value={typed} onChange={(e) => setTyped(e.target.value)} placeholder="Leeds, York" />
                  </label>
                  <p className="cl-fine">Someone limited to some groups can’t see the whole-lender pages (risk signals, data checks, record seals, the access log).</p>
                </fieldset>
              )}
              {picked.length > 1 && !canLimit && <p className="cl-fine">Because one of these roles covers the whole lender, this person can’t be limited to some groups.</p>}
              <label className="cl-field"><span>Key lasts</span>
                <select value={days} onChange={(e) => setDays(e.target.value)}>
                  {EXPIRY.map(([v, n]) => <option key={n} value={v}>{n}</option>)}
                </select>
              </label>
              <button type="submit" className="cl-btn cl-btn-primary" disabled={busy}>{busy ? "Sending…" : "Send invite"}</button>
            </form>
          </section>

          <section className="cl-section" aria-labelledby="t-list">
            <h2 id="t-list" className="cl-h2">People with a key</h2>
            <div className="cl-table-wrap cl-team-table">
              <table className="cl-table" data-testid="team-table">
                <caption className="cl-sr">People with a key and what each can do</caption>
                <thead><tr><th scope="col">Person</th><th scope="col">Does</th><th scope="col">Can see</th><th scope="col">Expires</th><th scope="col">Status</th><th scope="col"><span className="cl-sr">Action</span></th></tr></thead>
                <tbody>
                  {team.members.map((m) => (
                    <tr key={m.credential_id} data-testid={`member-${m.credential_id}`}>
                      <th scope="row">{m.label || `Key ${m.credential_id}`}{m.credential_id === team.you && <small className="cl-muted"> (you)</small>}{m.email && <small className="cl-muted cl-block">{m.email}</small>}</th>
                      <td>{roleNames(m.roles?.length ? m.roles : [m.role])}</td>
                      <td>{m.portfolios ? m.portfolios.join(", ") : "Everything their job allows"}</td>
                      <td>{when(m.expires_at)}</td>
                      <td><span className={`cl-state cl-state-${m.state}`}>{STATE_TEXT[m.state]}</span></td>
                      <td>
                        {m.state === "active" && m.credential_id !== team.you && (
                          confirm === m.credential_id ? (
                            <span className="cl-actions">
                              <button type="button" className="cl-btn cl-btn-primary" disabled={busy} onClick={() => void withdraw(m.credential_id)}>Yes, withdraw {m.label || `key ${m.credential_id}`}</button>
                              <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setConfirm(null)}>Keep</button>
                            </span>
                          ) : (
                            <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setConfirm(m.credential_id)} aria-label={`Withdraw key for ${m.label || m.credential_id}`}>Withdraw</button>
                          )
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
};

export default TeamPage;

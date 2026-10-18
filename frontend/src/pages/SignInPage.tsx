import React, { useEffect, useState } from "react";
import { ApiError, checkCredential, fetchHealth } from "../api";
import { getApiUrl, isDevBuild } from "../config";
import { navigate } from "../lib/router";
import { extractClaimToken, extractDecisionId, signInCustomer, signInStaff, signOut, startSample } from "../lib/session";
import type { Me, ServiceHealth } from "../types";

export const ROLE_LABEL: Record<string, string> = {
  lender_ingest: "Data officer", lender_analyst: "Analyst", lender_support: "Support", lender_admin: "Team administrator", auditor: "Auditor", admin: "Administrator",
  consumer: "Customer", sample: "Sample",
};

const HEALTH_TEXT: Record<ServiceHealth["status"], string> = {
  ok: "The service is running.",
  needs_upgrade: "The service is running, but its database needs an update. Restart the service to update it.",
  database_down: "The service is running, but it can’t reach its database.",
  unreachable: "We can’t reach the service{where}. It may not be running.",
  unknown: "We couldn’t tell whether the service is running.",
};

export const SignInPage: React.FC<{ me: Me | null; onChanged: () => void }> = ({ me, onChanged }) => {
  const [health, setHealth] = useState<ServiceHealth | null>(null);
  const [link, setLink] = useState("");
  const [key, setKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState<"customer" | "staff" | null>(null);
  const [error, setError] = useState<{ where: "customer" | "staff"; text: string } | null>(null);

  useEffect(() => { void fetchHealth().then(setHealth); }, []);

  const failWith = (where: "customer" | "staff", e: unknown) => {
    const text =
      e instanceof ApiError && e.status === 401
        ? where === "staff" ? "That access key wasn’t recognised. Check it and try again." : e.message
        : e instanceof ApiError && e.status === 0 ? "We couldn’t reach the service. Check that it’s running."
        : e instanceof ApiError && e.status === 429 ? e.message
        : e instanceof Error ? e.message : "Something went wrong.";
    setError({ where, text });
  };

  const openCustomer = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError(null);
    const token = extractClaimToken(link);
    if (!token) {
      setError({ where: "customer", text: "That doesn’t look like a link from your lender. Paste the whole link, or the code that came with it." });
      return;
    }
    setBusy("customer");
    try {
      const who = await checkCredential({ claim: token });  // only stored once it is known to work
      signInCustomer(token);
      onChanged();
      navigate({ page: "decision", id: who.decision_id ?? extractDecisionId(link) });
    } catch (e) { failWith("customer", e); } finally { setBusy(null); }
  };

  const signInLender = async (ev: React.FormEvent) => {
    ev.preventDefault();
    setError(null);
    if (!key.trim()) { setError({ where: "staff", text: "Enter your access key." }); return; }
    setBusy("staff");
    try {
      await checkCredential({ key: key.trim() });  // only stored once it is known to work
      signInStaff(key);
      setKey("");
      onChanged();
      navigate({ page: "home" });
    } catch (e) { failWith("staff", e); } finally { setBusy(null); }
  };

  const explore = () => { startSample(); onChanged(); navigate({ page: "home" }); };
  const leave = () => { signOut(); onChanged(); };

  return (
    <div className="cl-page cl-signin">
      <p className="cl-eyebrow">Sign in</p>
      <h1 className="cl-page-title" tabIndex={-1}>Welcome to DecisionTrace</h1>
      <p className="cl-page-lede">Customers open their own decision with the link their lender sent. Lenders sign in with an access key. Or look around with fictional sample decisions.</p>

      {health && (
        <p className={`cl-muted cl-health is-${health.status}`} role="status" data-testid="health">
          {HEALTH_TEXT[health.status].replace("{where}", isDevBuild() ? ` at ${getApiUrl()}` : "")}
        </p>
      )}

      {me && me.kind !== "sample" && (
        <div className="cl-note cl-note-good" data-testid="signed-in">
          <strong>You’re signed in as {me.tenant_name} ({ROLE_LABEL[me.role] ?? me.role}).</strong>
          {me.kind === "customer" && me.expires_at && <span>Your link works until {new Date(me.expires_at).toLocaleDateString("en-GB")}.</span>}
          <div className="cl-actions"><button type="button" className="cl-btn cl-btn-ghost" onClick={leave}>Sign out</button></div>
        </div>
      )}

      <section className="cl-section" aria-labelledby="si-cust">
        <h2 id="si-cust" className="cl-h2">I’m a customer</h2>
        <form onSubmit={openCustomer} noValidate>
          <label className="cl-field"><span>The link or code from your lender</span>
            <input type="text" value={link} onChange={(e) => setLink(e.target.value)} autoComplete="off" spellCheck={false} placeholder="Paste it here" />
          </label>
          {error?.where === "customer" && <p className="cl-note cl-note-bad" role="alert">{error.text}</p>}
          <button type="submit" className="cl-btn cl-btn-primary" disabled={busy !== null}>Open my decision</button>
        </form>
        <p className="cl-fine">Anyone with your link can see your decision until it expires, so please keep it private.</p>
      </section>

      <section className="cl-section" aria-labelledby="si-staff">
        <h2 id="si-staff" className="cl-h2">I work for a lender</h2>
        <form onSubmit={signInLender} noValidate>
          <label className="cl-field"><span>Access key</span>
            <input type={showKey ? "text" : "password"} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" spellCheck={false} />
          </label>
          <label className="cl-check"><input type="checkbox" checked={showKey} onChange={(e) => setShowKey(e.target.checked)} /> Show the key</label>
          {error?.where === "staff" && <p className="cl-note cl-note-bad" role="alert">{error.text}</p>}
          <button type="submit" className="cl-btn cl-btn-primary" disabled={busy !== null}>Sign in</button>
        </form>
        <p className="cl-fine">
          Your administrator gives you a key. It is kept only for this browser tab.
          {isDevBuild() && " Running the demo yourself? Use the DT_API_KEY value from your .env file."}
        </p>
      </section>

      <section className="cl-section" aria-labelledby="si-sample">
        <h2 id="si-sample" className="cl-h2">Just looking around</h2>
        <p className="cl-muted">See how DecisionTrace explains a decision, using fictional people and decisions. Nothing is saved.</p>
        <button type="button" className="cl-btn cl-btn-ghost" onClick={explore}>Try the sample decisions</button>
      </section>
    </div>
  );
};

export default SignInPage;

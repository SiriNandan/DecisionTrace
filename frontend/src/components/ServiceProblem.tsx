import React from "react";
import { ProblemKind } from "../api";
import { getApiUrl, isDevBuild } from "../config";
import { Link } from "../lib/router";
import { getSession, startSample } from "../lib/session";

type What = "decisions" | "decision" | "page";

const NOUN: Record<What, { these: string; load: string }> = {
  decisions: { these: "these decisions", load: "the decisions" },
  decision: { these: "this decision", load: "this decision" },
  page: { these: "this page", load: "this page" },
};

/** What went wrong and what to do next, in plain words. Used wherever a request can fail. */
export const ServiceProblem: React.FC<{ kind: ProblemKind; what?: What; onRetry?: () => void; offerSample?: boolean }> = ({
  kind, what = "decisions", onRetry, offerSample = true,
}) => {
  const n = NOUN[what];
  const customer = getSession().mode === "customer";
  const where = isDevBuild() && !customer ? ` (${getApiUrl()})` : "";
  const copy: Record<ProblemKind, [string, string]> = {
    signin: [`Sign-in is needed to see ${n.these}.`, "You’re not signed in, or your sign-in has expired. Please sign in again."],
    unreachable: [
      "We couldn’t reach the service.",
      customer
        ? "Please try again in a little while, or contact your lender."
        : `It may not be running, or its address may be wrong${where}. You can still look around with the sample decisions.`,
    ],
    upgrade: customer
      ? ["We can’t show this right now.", "Please try again in a little while, or contact your lender."]
      : ["The service needs an update.", "Its database is out of date. Restart the service so it can update itself, then try again."],
    other: [`We couldn’t load ${n.load}.`, "Check your connection and try again."],
  };
  const [title, body] = copy[kind];
  return (
    <div className="cl-note cl-note-bad" role="alert" data-testid="service-problem" data-kind={kind}>
      <strong>{title}</strong>
      <span>{body}</span>
      <div className="cl-actions">
        {kind === "signin" && <Link to={{ page: "signin" }} className="cl-btn cl-btn-primary">Sign in</Link>}
        {onRetry && kind !== "signin" && <button type="button" className="cl-btn cl-btn-ghost" onClick={onRetry}>Try again</button>}
        {offerSample && kind !== "other" && !customer && (
          <button type="button" className="cl-btn cl-btn-ghost" onClick={startSample}>Try the sample decisions</button>
        )}
        {kind === "signin" && onRetry && <button type="button" className="cl-btn cl-btn-ghost" onClick={onRetry}>Try again</button>}
      </div>
    </div>
  );
};

export default ServiceProblem;

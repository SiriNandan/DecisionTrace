import React from "react";
import { Link } from "../lib/router";

const STEPS: Array<[string, string]> = [
  ["Choose where the data lives", "Upload a file (CSV or JSON), or fetch it from a web address, a Google Cloud Storage file, or a read-only database query. Remote sources work only for hosts, buckets and databases your operator has allowed."],
  ["Let AI propose the match", "The service suggests how your columns map to a decision: the reference, the customer, the date, the outcome, the score, the reasons and the evidence. The model sees only column names and a few masked sample values. Names, contact details and account numbers are withheld, and every column it names must really exist."],
  ["Check the preview", "Each rule is re-run on your real rows, so you can see how many are valid and which are not. Fill any gaps by hand."],
  ["A person approves it", "An analyst signs off the mapping. You can propose and import, but not sign off, which keeps loading and reviewing separate."],
  ["Import", "Rows load 500 at a time, one row at a time. A bad row is refused with a reason and the rest go through. Loading the same file again replays instead of duplicating, and each decision records which mapping version made it."],
];

export const IngestGuidePage: React.FC = () => (
  <div className="cl-page cl-quality">
    <p className="cl-eyebrow">For data officers</p>
    <h1 className="cl-page-title" tabIndex={-1}>How to ingest</h1>
    <p className="cl-page-lede">Bring decisions in from your own systems in five steps. Everything you load becomes a tamper-evident record. You can load data but you can’t read decisions back, which keeps loading and reviewing separate.</p>
    <ol className="cl-findings" aria-label="Steps">
      {STEPS.map(([title, text], i) => (
        <li key={title} className="cl-finding is-info">
          <p className="cl-finding-msg">{i + 1}. {title}</p>
          <p className="cl-muted">{text}</p>
        </li>
      ))}
    </ol>
    <h2 className="cl-h2">Security</h2>
    <ul className="cl-findings" aria-label="Security">
      <li className="cl-finding is-info"><p className="cl-muted">Database logins are read-only and used for one request. Passwords and tokens are never stored or logged.</p></li>
      <li className="cl-finding is-info"><p className="cl-muted">Hosts, buckets and databases must be on the operator’s allow-list. Private network addresses, redirects and non-SELECT queries are refused.</p></li>
    </ul>
    <p><Link to={{ page: "connect" }} className="cl-btn cl-btn-primary">Go to Connect your data</Link></p>
  </div>
);

export default IngestGuidePage;

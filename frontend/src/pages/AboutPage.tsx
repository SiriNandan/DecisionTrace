import React from "react";
import { Link } from "../lib/router";

const STEPS = [
  ["1", "We keep the evidence", "When a decision is made, the application, credit report, documents and the lending rules in force that day are saved exactly as they were."],
  ["2", "We seal the record", "Each saved item is linked to the one before it, so any later change to the record can be detected."],
  ["3", "We explain it, and check our work", "Every explanation is built from the saved evidence. Anything we can’t back up with evidence is left out."],
];

const PRINCIPLES = [
  ["Evidence first", "Explanations come from what was recorded at the time, not from what we’d guess today."],
  ["Honest about gaps", "If evidence was removed under retention rules, we say so and show how much is missing."],
  ["People stay in charge", "Clearly never makes or changes a decision. A person can ask for a review, and the original is never overwritten."],
  ["Plain language", "No jargon where you don’t need it. Technical details are there for auditors, one click away."],
];

export const AboutPage: React.FC = () => (
  <div className="cl-page">
    <p className="cl-eyebrow">About Clearly</p>
    <h1 className="cl-page-title" tabIndex={-1}>Decisions you can understand, and check.</h1>
    <p className="cl-page-lede">
      A loan decision can change someone’s plans. Clearly exists so that anyone affected can see
      what was decided, why, and what evidence sits behind it, without needing to be an expert.
    </p>

    <h2 className="cl-h2">How it works</h2>
    <ol className="cl-how">
      {STEPS.map(([n, t, d]) => (
        <li key={n}><span className="cl-how-n" aria-hidden="true">{n}</span><h3>{t}</h3><p>{d}</p></li>
      ))}
    </ol>

    <h2 className="cl-h2">What we stand for</h2>
    <ul className="cl-principles">
      {PRINCIPLES.map(([t, d]) => (<li key={t}><h3>{t}</h3><p>{d}</p></li>))}
    </ul>

    <div className="cl-note cl-note-review cl-note-big">
      <strong>This is a demonstration.</strong>
      <span>Every applicant, credit report and decision you see is fictional, created to show how the experience works. Nothing here is financial advice or a real lending decision.</span>
    </div>

    <div className="cl-cta">
      <Link className="cl-btn cl-btn-primary" to={{ page: "home" }}>Browse decisions</Link>
      <Link className="cl-btn cl-btn-ghost" to={{ page: "help" }}>Read the help guide</Link>
    </div>
  </div>
);

export default AboutPage;

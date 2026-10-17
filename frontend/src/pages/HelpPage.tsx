import React, { useMemo, useState } from "react";
import { Link } from "../lib/router";

const FAQ: Array<[string, string]> = [
  ["How do I find my decision?", "Go to Decisions and type your applicant or application reference into the search box (press / to jump straight to it). You can also use the filters to narrow by result."],
  ["What does “risk estimate” mean?", "It’s the model’s estimate of how likely a loan would be to run into trouble, shown as a percentage. It’s one input. The lending rules and any human review also shape the final result."],
  ["Why was my application not approved?", "Open the decision and read “Why this decision”. Each lending rule is listed as met or not met, with your value next to the requirement. “What could change the outcome” suggests practical next steps."],
  ["What does “In review” mean?", "A lending rule sent the application to a person for a closer look. It hasn’t been approved or declined yet."],
  ["What does “Unchanged since the decision” mean?", "The evidence saved at decision time still matches how it was first recorded. If we ever see a mismatch, you’ll see a warning at the top of the page."],
  ["Why is some evidence “no longer held”?", "Personal data can only be kept for a limited time. When an item has been removed under those rules, we tell you, and show how much of the original evidence is still available."],
  ["How do I ask for a decision to be reviewed?", "At the bottom of a decision, use “Think this is wrong?”. Say why in at least 20 characters. Your request is added to the record, and the original decision is kept as it was."],
  ["Can I keep a copy?", "Yes. Use “Save as PDF / print” on a decision, or “Copy link” to share the page."],
  ["Does Clearly collect my data?", "No accounts, no cookies and no tracking. A few preferences stay in your browser only. See the Privacy page for details."],
  ["Is this real?", "No. This is a demonstration with fictional people and decisions."],
];

const GLOSSARY: Array<[string, string]> = [
  ["Debt compared with income", "Your monthly debt payments as a share of your income. Lower is better for lenders."],
  ["Lending rule", "A fixed requirement in the lender’s policy, such as a minimum credit score."],
  ["Risk estimate", "A percentage showing how risky the model judged the application to be."],
  ["Evidence on file", "The records saved when the decision was made."],
  ["Human review", "A person looking at the decision again and recording their view."],
];

const KEYS: Array<[string, string]> = [["/", "Jump to search"], ["Tab", "Move between controls"], ["Enter", "Open a decision or expand evidence"], ["Esc", "Close the welcome tour"]];

export const HelpPage: React.FC<{ onReplayTour: () => void }> = ({ onReplayTour }) => {
  const [q, setQ] = useState("");
  const shown = useMemo(() => {
    const n = q.trim().toLowerCase();
    return n ? FAQ.filter(([a, b]) => `${a} ${b}`.toLowerCase().includes(n)) : FAQ;
  }, [q]);

  return (
    <div className="cl-page" data-testid="help-page">
      <p className="cl-eyebrow">Help</p>
      <h1 className="cl-page-title" tabIndex={-1}>How can we help?</h1>
      <p className="cl-page-lede">Quick answers about reading a decision. Not finding it? Try different words.</p>

      <div className="cl-search" style={{ maxWidth: 560 }}>
        <label htmlFor="faq-search" className="cl-sr">Search help</label>
        <span className="cl-search-icon" aria-hidden="true">⌕</span>
        <input id="faq-search" type="search" className="cl-search-input" placeholder="Search help, e.g. “review”" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="off" />
      </div>

      <section aria-labelledby="faq-h" style={{ marginTop: 32 }}>
        <h2 id="faq-h" className="cl-h2">Common questions</h2>
        {shown.length === 0 ? (
          <p className="cl-muted" role="status">No answers match “{q}”. Try a shorter word, or browse the glossary below.</p>
        ) : (
          <div className="cl-faq">
            {shown.map(([a, b]) => (<details key={a}><summary>{a}</summary><p>{b}</p></details>))}
          </div>
        )}
      </section>

      <div className="cl-two">
        <section aria-labelledby="gl-h">
          <h2 id="gl-h" className="cl-h2">Plain-English glossary</h2>
          <dl className="cl-glossary">{GLOSSARY.map(([t, d]) => (<div key={t}><dt>{t}</dt><dd>{d}</dd></div>))}</dl>
        </section>
        <section aria-labelledby="kb-h">
          <h2 id="kb-h" className="cl-h2">Shortcuts</h2>
          <dl className="cl-glossary">{KEYS.map(([k, d]) => (<div key={k}><dt><kbd className="cl-kbd">{k}</kbd></dt><dd>{d}</dd></div>))}</dl>
        </section>
      </div>

      <section className="cl-card-rail" style={{ marginTop: 32 }}>
        <h2 className="cl-h3">New here?</h2>
        <p className="cl-muted">Take the 30-second tour of how to read a decision.</p>
        <button type="button" className="cl-btn cl-btn-primary" style={{ marginTop: 12 }} onClick={onReplayTour}>Replay the welcome tour</button>
      </section>

      <section className="cl-card-rail" style={{ marginTop: 16 }}>
        <h2 className="cl-h3">Still stuck?</h2>
        <p className="cl-muted">This is a demonstration, so there’s no live support team. In a live service, contact options would appear here. You can also read <Link to={{ page: "about" }}>how Clearly works</Link>.</p>
      </section>
    </div>
  );
};

export default HelpPage;

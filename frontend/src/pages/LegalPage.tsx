import React from "react";

type Kind = "privacy" | "terms" | "accessibility";

const CONTENT: Record<Kind, { title: string; lede: string; sections: Array<[string, string]> }> = {
  privacy: {
    title: "Privacy",
    lede: "Short version: DecisionTrace doesn’t track you.",
    sections: [
      ["What we collect", "Nothing about you. There are no accounts, no cookies and no analytics. The people and decisions shown are fictional."],
      ["What stays on your device", "Your theme choice, whether you’ve seen the welcome tour, the last decision you opened, and the private activity counts on the Insights page. They live in your browser’s local storage, are never sent anywhere, and “Clear my activity” removes the counts."],
      ["Third parties", "Page fonts are loaded from Google Fonts, which means your browser contacts Google to fetch them."],
      ["Review requests", "If you submit a review request, the text you write is added to that decision’s record and can’t be edited or deleted. Don’t include personal details you wouldn’t want kept."],
    ],
  },
  terms: {
    title: "Terms of use",
    lede: "Please read before using this demonstration.",
    sections: [
      ["Demonstration only", "DecisionTrace is a demonstration. All data is fictional. Nothing here is a real decision, financial advice, or an offer of credit."],
      ["No guarantee", "Explanations are generated from saved evidence and checked automatically, but they can still contain mistakes. Always rely on the lender’s official communication for real decisions."],
      ["Fair use", "Please don’t try to disrupt the service or submit unlawful, abusive or personal information in review requests."],
      ["Changes", "This demonstration may change or be withdrawn at any time."],
    ],
  },
  accessibility: {
    title: "Accessibility",
    lede: "DecisionTrace is built so that everyone can use it.",
    sections: [
      ["What we’ve built in", "Keyboard access to every control, visible focus, a skip link, labelled forms, colour that is never the only signal, text and layouts that adapt to small screens, light and dark themes, and reduced motion when your device asks for it."],
      ["What we haven’t verified", "We haven’t had the site audited against WCAG by a specialist or tested it with every assistive technology. Treat this as a good-faith effort, not a certification."],
      ["Tell us about a barrier", "This is a demonstration without a support team. In a live service, a contact route for accessibility problems would be listed here."],
    ],
  },
};

export const LegalPage: React.FC<{ kind: Kind }> = ({ kind }) => {
  const c = CONTENT[kind];
  return (
    <div className="cl-page cl-prose">
      <p className="cl-eyebrow">{c.title}</p>
      <h1 className="cl-page-title" tabIndex={-1}>{c.title}</h1>
      <p className="cl-page-lede">{c.lede}</p>
      {c.sections.map(([h, p]) => (<section key={h}><h2 className="cl-h3">{h}</h2><p>{p}</p></section>))}
    </div>
  );
};

export default LegalPage;

import React, { useEffect, useRef, useState } from "react";

const STEPS = [
  { icon: "🔎", title: "Find a decision", body: "Search by applicant or application reference, or try one of the examples on the home page. Press / any time to jump to search." },
  { icon: "💬", title: "Understand why", body: "Every decision opens with a plain-language explanation, then shows each lending rule as met or not met, with the value and the requirement side by side." },
  { icon: "🛡", title: "Trust the record", body: "A trust panel tells you whether the evidence is unchanged and complete. Something wrong? You can ask for a review. The original is always kept." },
];

export const WelcomeDialog: React.FC<{ onClose: () => void }> = ({ onClose }) => {
  const [i, setI] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const last = i === STEPS.length - 1;

  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>("button")?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !ref.current) return;
      const f = Array.from(ref.current.querySelectorAll<HTMLElement>("button:not([disabled])"));
      if (f.length === 0) return;
      const first = f[0], end = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); end.focus(); }
      else if (!e.shiftKey && document.activeElement === end) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);

  const s = STEPS[i];
  return (
    <div className="cl-modal-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cl-modal" role="dialog" aria-modal="true" aria-labelledby="tour-h" ref={ref} data-testid="welcome-dialog">
        <p className="cl-eyebrow">Welcome to Clearly · {i + 1} of {STEPS.length}</p>
        <div className="cl-modal-icon" aria-hidden="true">{s.icon}</div>
        <h2 id="tour-h" className="cl-h2">{s.title}</h2>
        <p className="cl-lede" style={{ fontSize: 17 }}>{s.body}</p>
        <div className="cl-dots" aria-hidden="true">{STEPS.map((_, n) => <span key={n} className={n === i ? "is-on" : ""} />)}</div>
        <div className="cl-modal-actions">
          <button type="button" className="cl-btn cl-btn-ghost" onClick={onClose}>Skip</button>
          <span style={{ flex: 1 }} />
          {i > 0 && <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setI(i - 1)}>Back</button>}
          <button type="button" className="cl-btn cl-btn-primary" onClick={() => (last ? onClose() : setI(i + 1))}>{last ? "Get started" : "Next"}</button>
        </div>
      </div>
    </div>
  );
};

export default WelcomeDialog;

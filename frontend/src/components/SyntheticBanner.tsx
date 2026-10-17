import React from "react";

export const SyntheticBanner: React.FC = () => (
  <div
    className="cl-demo-bar"
    role="region"
    aria-label="Demo data notice"
    data-testid="synthetic-data-banner"
  >
    <span aria-hidden="true">⚠</span>
    <strong>SYNTHETIC DATA - FOR DEMONSTRATION</strong>
    <span className="cl-demo-sub">Every person, credit report and decision here is fictional.</span>
  </div>
);

export default SyntheticBanner;

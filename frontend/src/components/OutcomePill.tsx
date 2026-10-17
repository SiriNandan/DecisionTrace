import React from "react";
import { outcomeCopy } from "../lib/plain";

export const OutcomePill: React.FC<{ outcome: string; size?: "sm" | "lg" }> = ({
  outcome,
  size = "sm",
}) => {
  const c = outcomeCopy(outcome);
  return (
    <span className={`cl-pill cl-pill-${c.tone} cl-pill-${size}`} data-outcome={outcome}>
      <span aria-hidden="true">{c.icon}</span> {c.label}
    </span>
  );
};

export default OutcomePill;

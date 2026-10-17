import React from "react";
import { DecisionOutcome } from "../types";

export function sanitizeDomId(raw: string): string {
  return raw.replace(/[^a-zA-Z0-9_-]/g, "_");
}

export function getEvidenceRowDomId(evidenceRef: string): string {
  return `evidence-row-${sanitizeDomId(evidenceRef)}`;
}

export function scrollToEvidenceCitation(
  citation: string,
  onHighlight?: (evidenceRef: string) => void
): HTMLElement | null {
  if (typeof document === "undefined") {
    return null;
  }

  const directId = getEvidenceRowDomId(citation);
  let target = document.getElementById(directId);

  if (!target) {
    const allRows = Array.from(
      document.querySelectorAll<HTMLElement>("[data-evidence-ref]")
    );
    target =
      allRows.find((el) => el.getAttribute("data-evidence-ref") === citation) ||
      allRows.find((el) => {
        const sections = el.getAttribute("data-evidence-sections") || "";
        return sections.includes(citation);
      }) ||
      allRows.find(
        (el) => el.getAttribute("data-evidence-type") === "policy"
      ) ||
      null;
  }

  if (target) {
    if (typeof target.scrollIntoView === "function") {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    target.focus({ preventScroll: true });
    const refAttr = target.getAttribute("data-evidence-ref") || citation;
    if (onHighlight) {
      onHighlight(refAttr);
    }
  } else if (onHighlight) {
    onHighlight(citation);
  }

  return target;
}

export function renderOutcomeBadge(
  outcome: DecisionOutcome | string,
  labelPrefix?: string
): React.ReactElement {
  const normalized = String(outcome).toUpperCase();
  let icon = "●";
  let badgeClass = "dt-badge dt-badge-neutral";
  let statusWord = normalized;

  if (normalized === "APPROVE") {
    icon = "✓";
    badgeClass = "dt-badge dt-badge-approve";
    statusWord = "APPROVE (Approved)";
  } else if (normalized === "DECLINE") {
    icon = "✗";
    badgeClass = "dt-badge dt-badge-decline";
    statusWord = "DECLINE (Declined)";
  } else if (normalized === "REFER") {
    icon = "⚑";
    badgeClass = "dt-badge dt-badge-refer";
    statusWord = "REFER (Manual Review)";
  }

  return (
    <span className={badgeClass} role="status">
      <span className="dt-badge-icon" aria-hidden="true">
        {icon}
      </span>{" "}
      <span>
        {labelPrefix ? `${labelPrefix}: ` : ""}
        {statusWord}
      </span>
    </span>
  );
}

export const SyntheticBanner: React.FC = () => {
  return (
    <div
      className="dt-synthetic-banner"
      role="region"
      aria-label="Synthetic Data Notice"
      data-testid="synthetic-data-banner"
    >
      <span className="dt-banner-icon" aria-hidden="true">
        ⚠ [NOTICE]
      </span>{" "}
      <strong className="dt-banner-text">
        SYNTHETIC DATA - FOR DEMONSTRATION
      </strong>{" "}
      <span className="dt-banner-subtext">
        — All applicant profiles, bureau snapshots, and loan decisions are
        synthetic records that support audit and explanation and are
        tamper-evident against a retained reference.
      </span>
    </div>
  );
};

export default SyntheticBanner;


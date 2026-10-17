import React from "react";
import { render, screen } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import DecisionView from "../components/DecisionView";
import { MOCK_DECISION_RECORDS, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";
import type { DecisionDetailRecord, ReconstructionReportContract } from "../types";

expect.extend(toHaveNoViolations);

const BASE = "00000000-0000-0000-0000-000000000001";
const noop = () => undefined;

function recordedPair(reasons: Array<{ code: string; text: string }>) {
  const record: DecisionDetailRecord = {
    ...MOCK_DECISION_RECORDS[BASE],
    recording_mode: "recorded",
    effective_outcome: "DECLINE",
    outcome: "DECLINE",
    rule_checks: [],
    contributions: {},
    snapshots: [
      {
        ...MOCK_DECISION_RECORDS[BASE].snapshots[0],
        seq: 1,
        evidence_type: "lender_decision",
        evidence_ref: "lender_decision:L-1",
        payload: { outcome: "DECLINE", score: 0.4, reasons },
      },
    ],
  };
  const report: ReconstructionReportContract = {
    ...MOCK_RECONSTRUCTION_REPORTS[BASE],
    outcome: "DECLINE",
    recording_mode: "recorded",
    replay_verified: false,
    rule_checks: [],
    principal_reasons: reasons.map((r) => r.text),
    reasons: [],
    plain_language_summary: null,
  };
  return { record, report };
}

const show = (reasons: Array<{ code: string; text: string }>) => {
  const { record, report } = recordedPair(reasons);
  return render(
    <DecisionView record={record} report={report} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />
  );
};

describe("a decision the lender made in its own system", () => {
  test("shows the lender's reasons instead of lending rules, and no re-worked score", () => {
    show([{ code: "DTI_HIGH", text: "Debt is high compared with income." }]);
    expect(screen.getByTestId("lender-reasons")).toHaveTextContent("Debt is high compared with income.");
    expect(screen.getByRole("heading", { name: "The reasons your lender gave" })).toBeInTheDocument();
    expect(screen.queryByText("How each lending rule applied")).toBeNull();
    expect(screen.getByTestId("score-as-recorded")).toHaveTextContent("Score kept as your lender sent it");
    expect(screen.queryByText(/Score does not match/)).toBeNull();
    expect(screen.getByTestId("plain-summary")).toHaveTextContent("Debt is high compared with income.");
  });

  test("says plainly when the lender gave no reason, without inventing one", () => {
    show([]);
    expect(screen.queryByTestId("lender-reasons")).toBeNull();
    expect(screen.getAllByText(/didn’t give a reason/).length).toBeGreaterThan(0);
  });

  test("a checked, model-written summary is shown with the AI note; without one the lender's own words are used", () => {
    const { record, report } = recordedPair([{ code: "DTI_HIGH", text: "Debt is high compared with income." }]);
    const written = "Your lender declined the application [lender_decision:L-1].";
    const view = (r: typeof report) =>
      render(<DecisionView record={record} report={r} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
    const a = view({ ...report, plain_language_summary: written });
    expect(screen.getByTestId("plain-summary")).toHaveTextContent("Your lender declined the application");
    expect(screen.getByText(/Written by AI, and checked against the saved evidence/)).toBeInTheDocument();
    a.unmount();
    view(report);
    expect(screen.queryByText(/Written by AI/)).toBeNull();
    expect(screen.getByTestId("plain-summary")).toHaveTextContent("Debt is high compared with income.");
  });

  test("uses no internal terms and has no accessibility violations", async () => {
    const { container } = show([{ code: "X", text: "A reason." }]);
    const clone = container.cloneNode(true) as HTMLElement;
    clone.querySelectorAll("details").forEach((d) => d.remove());
    expect(clone.textContent).not.toMatch(/guardrail|snapshot|hash|replay|lender_decision/i);
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

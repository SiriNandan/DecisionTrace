import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import DecisionView from "../components/DecisionView";
import { MOCK_DECISION_RECORDS, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";
import type { ReconstructionReportContract } from "../types";

expect.extend(toHaveNoViolations);

const ID = "00000000-0000-0000-0000-000000000001";
const noop = () => undefined;
const show = (patch: Partial<ReconstructionReportContract>) => {
  const report = { ...MOCK_RECONSTRUCTION_REPORTS[ID], ...patch };
  const kept = report.reasons.reduce((n, r) => n + r.claims.length, 0);
  const view = render(<DecisionView record={MOCK_DECISION_RECORDS[ID]} report={report} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
  return { ...view, kept };
};

beforeEach(() => { window.localStorage.setItem("clearly:tour-done", "1"); window.HTMLElement.prototype.scrollIntoView = jest.fn(); window.scrollTo = jest.fn() as never; });

describe("the check against the record", () => {
  test("says how many claims were kept and removed, and lists the reasons", async () => {
    const { kept } = show({ guardrail_rejections: 3, guardrail_removed: ["It relied on evidence that has since been removed.", "It contained a figure that does not appear in the preserved evidence.", "It relied on evidence that has since been removed."] });
    const panel = screen.getByTestId("guardrail-line");
    expect(within(panel).getByRole("heading", { name: "Checked against the record" })).toBeInTheDocument();
    expect(panel).toHaveTextContent(`${kept} claims kept, 3 removed because they were not in the preserved evidence.`);
    await userEvent.click(within(panel).getByText("Why they were removed"));
    expect(within(panel).getAllByRole("listitem")).toHaveLength(2);  // repeated reasons are listed once
    expect(await axe(panel)).toHaveNoViolations();
  });

  test("with nothing removed there is no list, and an older report without reasons still renders", () => {
    show({ guardrail_rejections: 0 });
    const panel = screen.getByTestId("guardrail-line");
    expect(panel).toHaveTextContent("0 removed");
    expect(panel.querySelector("details")).toBeNull();
    expect(panel.textContent).not.toMatch(/guardrail|_/i);
  });

  test("a single removal reads naturally", () => {
    show({ guardrail_rejections: 1, guardrail_removed: [] });
    expect(screen.getByTestId("guardrail-line")).toHaveTextContent("1 removed because it was not in the preserved evidence.");
    expect(screen.getByTestId("guardrail-line").querySelector("details")).toBeNull();
  });
});

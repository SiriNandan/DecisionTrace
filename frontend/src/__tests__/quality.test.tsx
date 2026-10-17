import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import DecisionView from "../components/DecisionView";
import QualityPage from "../pages/QualityPage";
import { MOCK_DECISION_RECORDS, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";
import type { FindingItem } from "../types";

expect.extend(toHaveNoViolations);

const FINDING: FindingItem = {
  finding_id: 5, decision_id: "11111111-1111-4111-8111-111111111111", rule_id: "income_unit_mismatch", kind: "conflict",
  severity: "warning", evidence_ref: "application:A1", field: "income_annual", observed: 2500,
  message: "The yearly income (2,500) matches the monthly figure from document:A1 (2,500), so it may be a monthly amount entered as a yearly one.",
  suggested_fix: { evidence_ref: "application:A1", field: "income_annual", new_value: 30000, basis: "Monthly income of 2,500 from document:A1 times 12." },
  ai_note: "Looks like a monthly figure.", found_by: "rules+ai", created_at: "2026-10-01T10:00:00Z", status: "open",
};

type Call = { url: string; method: string; body: any };
function installFetch(open: FindingItem[]) {
  const calls: Call[] = [];
  let list = [...open];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
    const method = init.method ?? "GET";
    const body = init.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method, body });
    const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
    if (url.includes("/v1/quality/findings?status=")) return ok(list);
    if (url.endsWith("/v1/quality/run")) return ok({ decisions_checked: 40, new_findings: 1, open_total: 1, by_severity: {}, notes: [], ai_used: true });
    if (url.includes("/resolve")) { list = []; return ok({ finding_id: 5, status: "corrected" }); }
    throw new Error(`unexpected ${url}`);
  });
  return calls;
}

describe("Data checks", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "k";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => {
    (globalThis as unknown as { fetch: unknown }).fetch = realFetch;
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = undefined;
  });

  test("lists what needs a look, explains it, and applies a correction only with a reason", async () => {
    const calls = installFetch([FINDING]);
    const { container } = render(<QualityPage />);
    const card = await screen.findByTestId("finding-5");
    expect(card).toHaveTextContent("Worth checking");
    expect(card).toHaveTextContent("Sources disagree");
    expect(within(card).getByTestId("suggested")).toHaveTextContent("income annual from 2,500 to 30,000");
    expect(card).toHaveTextContent("AI’s guess: Looks like a monthly figure.");
    expect(card).toHaveTextContent("The original record is kept as it was");

    const apply = within(card).getByRole("button", { name: "Apply this correction" });
    expect(apply).toBeDisabled();
    await userEvent.type(within(card).getByLabelText(/Why\?/), "Payslip says 2,500 a month.");
    expect(apply).toBeEnabled();
    fireEvent.click(apply);
    await waitFor(() => expect(screen.getByTestId("none")).toBeInTheDocument());

    const post = calls.find((c) => c.url.includes("/resolve"))!;
    expect(post.url).toBe("http://api.test/v1/quality/findings/5/resolve");
    expect(post.body).toEqual({ action: "correct", note: "Payslip says 2,500 a month." });  // the suggested value is used as is
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });

  test("a person's own value is sent", async () => {
    const calls = installFetch([FINDING]);
    render(<QualityPage />);
    const card = await screen.findByTestId("finding-5");
    await userEvent.type(within(card).getByLabelText(/Why\?/), "Confirmed by phone call.");
    const value = within(card).getByLabelText("Corrected value");
    await userEvent.clear(value);
    await userEvent.type(value, "31000");
    fireEvent.click(within(card).getByRole("button", { name: "Apply this correction" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/resolve"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/resolve"))!.body).toMatchObject({ action: "correct", new_value: 31000 });
  });

  test("'it's right as it is' dismisses without a correction", async () => {
    const calls = installFetch([FINDING]);
    render(<QualityPage />);
    const card = await screen.findByTestId("finding-5");
    await userEvent.type(within(card).getByLabelText(/Why\?/), "It really is correct.");
    fireEvent.click(within(card).getByRole("button", { name: "It’s right as it is" }));
    await waitFor(() => expect(calls.some((c) => c.url.includes("/resolve"))).toBe(true));
    expect(calls.find((c) => c.url.includes("/resolve"))!.body).toEqual({ action: "dismiss", note: "It really is correct." });
  });

  test("running the checks reports what was found, and a missing sign-in is explained", async () => {
    installFetch([]);
    render(<QualityPage />);
    expect(await screen.findByTestId("none")).toHaveTextContent("Nothing needs a look right now.");
    fireEvent.click(screen.getByRole("button", { name: "Check my decisions" }));
    expect(await screen.findByTestId("run-summary")).toHaveTextContent("Checked 40 decisions. 1 new item to look at.");

    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async () => ({ ok: false, status: 401, json: async () => ({}) }));
    render(<QualityPage />);
    await waitFor(() => expect(screen.getAllByRole("alert")[0]).toHaveTextContent("Sign-in is needed to see this."));
  });

  test("uses no internal terms", async () => {
    installFetch([FINDING]);
    const { container } = render(<QualityPage />);
    await screen.findByTestId("finding-5");
    expect(container.textContent).not.toMatch(/schema|hash|snapshot|envelope|guardrail|idempot|chain|rule_id|evidence_ref/i);
  });
});

describe("corrections on a decision page", () => {
  const ID = "00000000-0000-0000-0000-000000000001";
  const withCorrections = (valid: boolean) =>
    render(
      <DecisionView
        record={{
          ...MOCK_DECISION_RECORDS[ID],
          corrections: [{ seq: 1, evidence_ref: "application:A1", field: "income_annual", old_value: 2500, new_value: 30000,
            reason: "Payslip shows 2,500 a month.", source: "suggested", created_at: "2026-10-02T09:00:00Z" }],
          corrections_valid: valid,
        }}
        report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false} reportError={null} onRetryReport={() => undefined} onBack={() => undefined}
      />
    );

  test("says what was corrected, that the decision used the original, and flags a tampered list", async () => {
    const { container, unmount } = withCorrections(true);
    const note = screen.getByTestId("corrections-note");
    expect(note).toHaveTextContent("Some details were corrected after this decision was made.");
    expect(note).toHaveTextContent("The decision was based on the original details");
    expect(note).toHaveTextContent("£2,500");
    expect(note).toHaveTextContent("£30,000");
    expect(note).not.toHaveTextContent("may have been changed");
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
    unmount();
    withCorrections(false);
    expect(screen.getByTestId("corrections-note")).toHaveTextContent("may have been changed");
  });

  test("shows nothing extra when there are no corrections", () => {
    render(<DecisionView record={MOCK_DECISION_RECORDS[ID]} report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false}
      reportError={null} onRetryReport={() => undefined} onBack={() => undefined} />);
    expect(screen.queryByTestId("corrections-note")).toBeNull();
  });
});

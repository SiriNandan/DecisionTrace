import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import SignalsPage from "../pages/SignalsPage";
import { signOut } from "../lib/session";
import type { SignalItem } from "../types";

expect.extend(toHaveNoViolations);

const SIGNAL: SignalItem = {
  signal_id: 9, decision_id: "11111111-1111-4111-8111-111111111111", scope: "decision", signal_type: "shared_details", severity: "warning",
  message: "The same income, loan amount and credit score appear for 3 different customer references. That can happen by chance, but it may be worth a look.",
  details: {}, created_at: "2026-10-01T00:00:00Z", status: "open",
};
const ACCESS: SignalItem = { ...SIGNAL, signal_id: 10, decision_id: null, scope: "access", signal_type: "id_enumeration", severity: "high",
  message: "Credential 7 asked for 12 decisions that don't exist in 10 minutes. That can be a script guessing reference numbers." };

type Call = { url: string; method: string; body: any };
function install(open: SignalItem[], status = 200) {
  const calls: Call[] = [];
  let list = [...open];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, method: init.method ?? "GET", body });
    const ok = (data: unknown) => ({ ok: status < 300, status, json: async () => data });
    if (url.includes("/v1/signals?status=")) return ok(status < 300 ? list : { detail: "This sign-in isn't allowed to do that." });
    if (url.endsWith("/v1/signals/run")) return ok({ decisions_checked: 120, new_signals: 2, open_total: 2, by_severity: {} });
    if (url.includes("/review")) { list = []; return ok({ signal_id: 9, status: "cleared" }); }
    throw new Error(`unexpected ${url}`);
  });
  return calls;
}

describe("Risk signals", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    signOut();
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "k";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; delete process.env.VITE_API_KEY; globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test("explains that signals are prompts, lists them by seriousness, and links to the decision", async () => {
    install([ACCESS, SIGNAL]);
    const { container } = render(<SignalsPage />);
    const high = await screen.findByTestId("signal-10");
    expect(high).toHaveTextContent("Look soon");
    expect(high).toHaveTextContent("Who is looking");
    expect(within(high).queryByRole("link", { name: "Open the decision" })).toBeNull();  // not about one decision
    const warn = screen.getByTestId("signal-9");
    expect(within(warn).getByRole("link", { name: "Open the decision" })).toHaveAttribute("href", `?d=${SIGNAL.decision_id}`);
    expect(document.body.textContent).toMatch(/prompts to look,\s+not conclusions/);
    expect(document.body.textContent).toMatch(/customers never see them/);
    expect(container.textContent).not.toMatch(/fraud|criminal|guilty|cheat/i);
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });

  test("a note of at least ten characters is needed before a signal is marked as looked at", async () => {
    const calls = install([SIGNAL]);
    render(<SignalsPage />);
    const card = await screen.findByTestId("signal-9");
    const clear = within(card).getByRole("button", { name: "Nothing to worry about" });
    expect(clear).toBeDisabled();
    await userEvent.type(within(card).getByLabelText(/Your note/), "Known repeat applicant, checked.");
    fireEvent.click(clear);
    await waitFor(() => expect(screen.getByTestId("none")).toBeInTheDocument());
    const post = calls.find((c) => c.url.includes("/review"))!;
    expect(post.url).toBe("http://api.test/v1/signals/9/review");
    expect(post.body).toEqual({ action: "clear", note: "Known repeat applicant, checked." });
  });

  test("checking reports what was found; a sign-in problem is explained", async () => {
    install([]);
    render(<SignalsPage />);
    expect(await screen.findByTestId("none")).toHaveTextContent("Nothing needs a look right now.");
    fireEvent.click(screen.getByRole("button", { name: "Check for signals" }));
    expect(await screen.findByTestId("run-summary")).toHaveTextContent("Looked through 120 decisions. 2 new items to look at.");

    install([], 403);
    render(<SignalsPage />);
    await waitFor(() => expect(screen.getAllByTestId("service-problem")[0]).toHaveAttribute("data-kind", "signin"));
  });
});

describe("why a signal fired", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    signOut();
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "k";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; delete process.env.VITE_API_KEY; globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test("each signal is labelled as a heuristic rule, with the rule's sentence or the AI-worded one marked as such", async () => {
    const rule = { ...SIGNAL, why: { text: "This rule fired because the same customer reference has 3 decisions within 7 days, and it looks for 3 or more.", source: "rule" as const } };
    const ai = { ...SIGNAL, signal_id: 11, why: { text: "This customer reference has 3 decisions within 7 days, so it is worth a look.", source: "ai" as const } };
    install([rule, ai, ACCESS]);  // ACCESS has no sentence at all (an older record)
    const { container } = render(<SignalsPage />);
    const a = await screen.findByTestId("signal-9");
    expect(within(a).getByTestId("why-9")).toHaveTextContent("Signal rule (heuristic) · This rule fired because");
    expect(within(a).getByTestId("why-9")).not.toHaveTextContent("Worded by AI");
    expect(screen.getByTestId("why-11")).toHaveTextContent("Worded by AI from the figures in this rule only, and checked against them.");
    expect(screen.getByTestId("why-10")).toHaveTextContent(/^Signal rule \(heuristic\)$/);
    expect(document.body.textContent).toMatch(/prompts to look,\s+not conclusions/);
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

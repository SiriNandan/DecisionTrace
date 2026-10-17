import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../App";
import DecisionView from "../components/DecisionView";
import HomeView from "../components/HomeView";
import SyntheticBanner from "../components/SyntheticBanner";
import InsightsPage from "../pages/InsightsPage";
import HelpPage from "../pages/HelpPage";
import { computeStats } from "../lib/stats";
import { readActivity, track } from "../lib/usage";
import { fetchDecisionRecord, fetchDecisionsList, fetchReconstructionReport } from "../api";
import { outcomeCopy, ruleNextStep } from "../lib/plain";
import { MOCK_DECISION_RECORDS, MOCK_DECISIONS_LIST, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";

const OK_ID = "00000000-0000-0000-0000-000000000001";
const TAMPER_ID = "00000000-0000-0000-0000-000000000008";
const ERASED_ID = "00000000-0000-0000-0000-000000000010";

const noop = () => undefined;
const view = (id: string, extra: Partial<React.ComponentProps<typeof DecisionView>> = {}) =>
  render(
    <DecisionView
      record={MOCK_DECISION_RECORDS[id]}
      report={MOCK_RECONSTRUCTION_REPORTS[id]}
      reportLoading={false}
      reportError={null}
      onRetryReport={noop}
      onBack={noop}
      {...extra}
    />
  );

/** Visible text outside the auditor-only disclosure. */
const customerText = (container: HTMLElement) => {
  const clone = container.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("details").forEach((d) => d.remove());
  return clone.textContent ?? "";
};

describe("Clearly customer experience", () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  test("shows the synthetic-data notice", () => {
    render(<SyntheticBanner />);
    expect(screen.getByTestId("synthetic-data-banner")).toHaveTextContent("SYNTHETIC DATA - FOR DEMONSTRATION");
  });

  test("outcomes are described in plain language", () => {
    expect(outcomeCopy("DECLINE").label).toBe("Not approved");
    expect(outcomeCopy("REFER").label).toBe("In review");
    expect(ruleNextStep({ rule_id: "min_credit_score", observed_value: 600, threshold: 640 } as never)).toMatch(/40 points short/);
  });

  test("decision page leaks no internal terminology to customers", () => {
    for (const id of [OK_ID, TAMPER_ID, ERASED_ID]) {
      const { container, unmount } = view(id);
      const text = customerText(container);
      for (const banned of [/hash/i, /guardrail/i, /snapshot/i, /evidence_unavailable/i, /payload/i, /sha-?256/i, /erased_by_retention/i, /api\b/i, /reconstruct/i, /\bseq\b/i, /_/]) {
        expect(text).not.toMatch(banned);
      }
      unmount();
    }
  });

  test("explains the decision, each rule, and next steps", () => {
    view(OK_ID);
    expect(screen.getByTestId("plain-summary")).toBeInTheDocument();
    expect(screen.getByTestId("rule-dti_limit")).toHaveTextContent("Debt compared with income");
    expect(screen.getByTestId("trust-panel")).toHaveTextContent("Unchanged since the decision");
    expect(screen.getByText("What could change the outcome")).toBeInTheDocument();
  });

  test("citation chips open and scroll to the matching evidence", async () => {
    view(OK_ID);
    fireEvent.click(screen.getByTestId("rule-citation-chip-rule:dti_limit"));
    await waitFor(() => expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled());
    const row = screen.getByTestId("evidence-row-rule:dti_limit");
    expect(within(row).getByRole("button")).toHaveAttribute("aria-expanded", "true");
  });

  test("tampered record shows a clear, non-technical warning", () => {
    view(TAMPER_ID);
    expect(screen.getByTestId("integrity-warning")).toHaveTextContent("We can’t confirm this record is unchanged");
    expect(screen.getByTestId("chain-badge")).toHaveAttribute("data-chain-verified", "false");
  });

  test("removed evidence is explained and the report is marked partial", () => {
    view(ERASED_ID);
    expect(screen.getByTestId("evidence-removed-note")).toHaveTextContent("no longer held");
    expect(screen.getByTestId("missing-evidence-warnings")).toHaveTextContent("No longer held");
  });

  test("falls back gracefully when the explanation can't load", () => {
    const onRetry = jest.fn();
    view(OK_ID, { report: null, reportError: "boom", onRetryReport: onRetry });
    expect(screen.getByText(/detailed explanation isn’t available/i)).toBeInTheDocument();
    expect(screen.getByTestId("rule-dti_limit")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  test("review request needs 20+ characters, then submits", async () => {
    const onSubmit = jest.fn().mockResolvedValue(undefined);
    view(ERASED_ID, { onSubmitReview: onSubmit });
    const box = screen.getByLabelText(/Why should this be reviewed/i);
    fireEvent.change(box, { target: { value: "too short" } });
    fireEvent.click(screen.getByTestId("review-submit"));
    expect(screen.getByTestId("review-help")).toHaveTextContent(/at least 20 characters/i);
    expect(onSubmit).not.toHaveBeenCalled();

    const reason = "Verified secondary audited tax return confirming sufficient net income.";
    fireEvent.change(box, { target: { value: reason } });
    fireEvent.click(screen.getByTestId("review-submit"));
    await waitFor(() =>
      expect(onSubmit).toHaveBeenCalledWith({
        decision_id: MOCK_DECISION_RECORDS[ERASED_ID].decision_id,
        new_outcome: "REFER",
        reason,
        actor_role: "senior_credit_underwriter",
      })
    );
    expect(await screen.findByText(/review request has been added/i)).toBeInTheDocument();
  });

  test("home: search, filters, empty state, keyboard shortcut and resume link", () => {
    const onOpen = jest.fn();
    render(<HomeView cases={MOCK_DECISIONS_LIST} loading={false} error={null} onRetry={noop} onOpen={onOpen} recent={{ id: "x", label: "APP-1 · LOAN-1" }} />);
    const first = MOCK_DECISIONS_LIST[0];
    fireEvent.click(screen.getByTestId(`case-item-${first.decision_id}`));
    expect(onOpen).toHaveBeenCalledWith(first.decision_id);

    fireEvent.click(screen.getByText(/Pick up where you left off/));
    expect(onOpen).toHaveBeenLastCalledWith("x");

    fireEvent.keyDown(window, { key: "/" });
    expect(screen.getByLabelText("Find a decision")).toHaveFocus();

    fireEvent.change(screen.getByLabelText("Find a decision"), { target: { value: "zzz-no-match" } });
    expect(screen.getByText("Nothing matches that search")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Clear search"));
    expect(screen.getByTestId("case-list-items").children.length).toBe(MOCK_DECISIONS_LIST.length);

    fireEvent.click(screen.getByRole("button", { name: "Not approved" }));
    for (const li of Array.from(screen.getByTestId("case-list-items").children)) {
      expect(li).toHaveTextContent("Not approved");
    }
  });

  test("home shows a retry on load failure", () => {
    const onRetry = jest.fn();
    render(<HomeView cases={[]} loading={false} error="x" onRetry={onRetry} onOpen={noop} recent={null} />);
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });
});

describe("Site pages, onboarding and usage", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem("clearly:tour-done", "1");
    document.documentElement.removeAttribute("data-theme");
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
    window.scrollTo = jest.fn() as never;
  });

  const go = (search: string) => window.history.replaceState({}, "", `/${search}`);

  test("first-time visitors get a skippable, keyboard-friendly welcome tour (shown once)", () => {
    window.localStorage.removeItem("clearly:tour-done");
    go("");
    const { unmount } = render(<App />);
    const dialog = screen.getByTestId("welcome-dialog");
    expect(dialog).toHaveTextContent("Find a decision");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(dialog).toHaveTextContent("Understand why");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("welcome-dialog")).not.toBeInTheDocument();
    expect(window.localStorage.getItem("clearly:tour-done")).toBe("1");
    unmount();
    render(<App />);
    expect(screen.queryByTestId("welcome-dialog")).not.toBeInTheDocument();
  });

  test("footer offers About, Help, Privacy, Terms and Accessibility pages that navigate", async () => {
    go("");
    render(<App />);
    const footer = screen.getByRole("contentinfo");
    for (const [name, heading] of [["How it works", /Decisions you can understand/], ["Privacy", "Privacy"], ["Terms", "Terms of use"], ["Accessibility", "Accessibility"]] as const) {
      fireEvent.click(within(footer).getByRole("link", { name }));
      expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
    }
    expect(document.title).toMatch(/Accessibility · Clearly/);
  });

  test("unknown pages show a helpful 404", () => {
    go("?p=nope");
    render(<App />);
    expect(screen.getByRole("heading", { name: "We can’t find that page" })).toBeInTheDocument();
  });

  test("theme toggle cycles Auto → Light → Dark and is remembered", () => {
    go("?p=about");
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: /Colour theme: Auto/ }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    fireEvent.click(screen.getByRole("button", { name: /Colour theme: Light/ }));
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(window.localStorage.getItem("clearly:theme")).toBe("dark");
  });

  test("help search filters answers and offers to replay the tour", () => {
    const replay = jest.fn();
    render(<HelpPage onReplayTour={replay} />);
    fireEvent.change(screen.getByLabelText("Search help"), { target: { value: "cookies" } });
    expect(document.querySelectorAll(".cl-faq details").length).toBe(1);
    fireEvent.change(screen.getByLabelText("Search help"), { target: { value: "zzzz" } });
    expect(screen.getByRole("status")).toHaveTextContent(/No answers match/);
    fireEvent.click(screen.getByRole("button", { name: "Replay the welcome tour" }));
    expect(replay).toHaveBeenCalled();
  });

  test("insights are computed from the decisions, with a table alternative", () => {
    const stats = computeStats(MOCK_DECISIONS_LIST);
    expect(stats.total).toBe(MOCK_DECISIONS_LIST.length);
    expect(stats.counts.APPROVE + stats.counts.DECLINE + stats.counts.REFER).toBe(stats.total);
    render(<InsightsPage cases={MOCK_DECISIONS_LIST} loading={false} error={null} activity={readActivity()} onActivityReset={noop} />);
    expect(screen.getByText("Decisions recorded").previousSibling).toHaveTextContent(String(stats.total));
    fireEvent.click(screen.getAllByRole("button", { name: "View as table" })[0]);
    expect(screen.getAllByRole("table").length).toBe(1);
  });

  test("your activity is private, counted on this device, and clearable", () => {
    track("view", "a"); track("view", "a"); track("view", "b"); track("copy", "b");
    const act = readActivity();
    expect(act.views).toBe(3);
    expect(act.decisions).toEqual(["a", "b"]);
    const onReset = jest.fn();
    render(<InsightsPage cases={MOCK_DECISIONS_LIST} loading={false} error={null} activity={act} onActivityReset={onReset} />);
    expect(screen.getByTestId("activity")).toHaveTextContent("Stored only in this browser");
    fireEvent.click(screen.getByRole("button", { name: "Clear my activity" }));
    expect(onReset).toHaveBeenCalled();
    expect(readActivity().views).toBe(0);
  });

  test("insights page handles loading failure gracefully", () => {
    render(<InsightsPage cases={[]} loading={false} error="x" activity={readActivity()} onActivityReset={noop} />);
    expect(screen.getByRole("alert")).toHaveTextContent("couldn’t load this page");
  });

  test("insights tells a signed-out visitor to sign in, not to check their connection", () => {
    render(<InsightsPage cases={[]} loading={false} error="x" problem="signin" activity={readActivity()} onActivityReset={noop} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in is needed to see this page.");
    expect(screen.getByRole("alert")).not.toHaveTextContent(/connection/i);
  });
});

describe("Live API integration (VITE_USE_MOCKS=false; needs the APIs running)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";

  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: apiUrl, VITE_USE_MOCKS: "false" };
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  const find = (list: Awaited<ReturnType<typeof fetchDecisionsList>>, app: string) => list.find((d) => d.application_id === app)!;

  test("seeded scenarios render with the right story for customers", async () => {
    const list = await fetchDecisionsList();
    expect(list.length).toBeGreaterThanOrEqual(10);

    // Clear decline: verified, rule not met, next step offered
    const dec = find(list, "LOAN-SCEN-0001");
    const decRecord = await fetchDecisionRecord(dec.decision_id);
    const decReport = await fetchReconstructionReport(dec.decision_id);
    expect(decReport.chain_verified && decReport.replay_verified).toBe(true);
    const a = render(<DecisionView record={decRecord} report={decReport} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
    expect(screen.getByTestId("rule-dti_limit")).toHaveTextContent("Not met");
    expect(screen.getByTestId("chain-badge")).toHaveAttribute("data-chain-verified", "true");
    expect(customerText(a.container)).not.toMatch(/guardrail|snapshot|hash/i);
    a.unmount();

    // Tampered record
    const tam = find(list, "LOAN-SCEN-0008");
    const tamReport = await fetchReconstructionReport(tam.decision_id);
    const b = render(<DecisionView record={await fetchDecisionRecord(tam.decision_id)} report={tamReport} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
    expect(screen.getByTestId("integrity-warning")).toBeInTheDocument();
    b.unmount();

    // Removed evidence
    const era = find(list, "LOAN-SCEN-0010");
    const eraReport = await fetchReconstructionReport(era.decision_id);
    expect(eraReport.status).toBe("evidence_unavailable");
    const c = render(<DecisionView record={await fetchDecisionRecord(era.decision_id)} report={eraReport} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
    expect(screen.getByTestId("evidence-removed-note")).toBeInTheDocument();
    c.unmount();

    // Reviewed by a person
    const ov = find(list, "LOAN-SCEN-0007");
    const d = render(<DecisionView record={await fetchDecisionRecord(ov.decision_id)} report={await fetchReconstructionReport(ov.decision_id)} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />);
    expect(screen.getByTestId("recorded-override-banner")).toHaveTextContent("A person changed this result to “Approved”");
    d.unmount();
  });

  test("full app: home → open a decision → explanation and trust panel appear", async () => {
    window.localStorage.setItem("clearly:tour-done", "1");
    window.scrollTo = jest.fn() as never;
    window.history.replaceState({}, "", "/");
    render(<App />);
    expect(screen.getByTestId("synthetic-data-banner")).toBeInTheDocument();
    const items = await screen.findByTestId("case-list-items", {}, { timeout: 10000 });
    await waitFor(() => expect(items.children.length).toBeGreaterThan(0), { timeout: 10000 });
    fireEvent.click(items.querySelector("button")!);
    await waitFor(
      () => {
        expect(screen.getByTestId("decision-record")).toBeInTheDocument();
        expect(screen.getByTestId("reconstruction-report")).toBeInTheDocument();
        expect(screen.getByTestId("trust-panel")).toBeInTheDocument();
      },
      { timeout: 10000 }
    );
    expect(window.location.search).toMatch(/^\?d=/);
    fireEvent.click(screen.getByText("← All decisions"));
    expect(window.location.search).toBe("");
    expect(await screen.findByTestId("case-list")).toBeInTheDocument();
  });
});

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import App from "../App";
import ChoicesPanel from "../components/ChoicesPanel";
import DecisionView from "../components/DecisionView";
import SealsPage from "../pages/SealsPage";
import { getSession, signInCustomer, signInStaff, signOut } from "../lib/session";
import { MOCK_DECISION_RECORDS, MOCK_DECISIONS_LIST, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";
import type { ConsentState, Me, SealBatch, SealCheck } from "../types";

expect.extend(toHaveNoViolations);

const ID = "00000000-0000-0000-0000-000000000001";
const TOKEN = "dtc_" + "d".repeat(43);
const noop = () => undefined;
const axeOpts = { rules: { "color-contrast": { enabled: false } } };

const STATE = (over: Partial<ConsentState> = {}): ConsentState => ({
  purpose: "ai_summary", policy: "ask", choice: null, ai_summary_on: false, history: [], ...over,
});

describe("the customer's choices", () => {
  test("by default nothing is on, and the words say what the AI is and isn't given", async () => {
    const onChoose = jest.fn().mockResolvedValue(undefined);
    const { container } = render(<ChoicesPanel state={STATE()} onChoose={onChoose} />);
    const panel = screen.getByTestId("choices-panel");
    expect(panel).toHaveTextContent("Your choices");
    expect(panel).toHaveTextContent("only given the facts the explanation is built on");
    expect(panel).toHaveTextContent("not a name or contact details");
    expect(screen.getByTestId("choice-now")).toHaveTextContent("Off — it stays off unless you allow it.");
    expect(screen.getByRole("button", { name: "Don’t allow" })).toBeEnabled();  // no choice yet: "no" can be recorded explicitly
    fireEvent.click(screen.getByRole("button", { name: "Allow AI summaries" }));
    await waitFor(() => expect(onChoose).toHaveBeenCalledWith(true));
    expect(container.textContent).not.toMatch(/consent|ledger|policy|guardrail|hash|token/i);
    expect(await axe(container, axeOpts)).toHaveNoViolations();
  });

  test("shows who chose and when, keeps the history, and only offers the change that makes sense", () => {
    const state = STATE({
      choice: { granted: true, recorded_at: "2026-10-02T09:00:00Z", given_by: "customer" }, ai_summary_on: true,
      history: [{ granted: true, recorded_at: "2026-10-02T09:00:00Z", given_by: "customer" }, { granted: false, recorded_at: "2026-10-01T09:00:00Z", given_by: "staff_for_customer" }],
    });
    render(<ChoicesPanel state={state} onChoose={noop as never} />);
    expect(screen.getByTestId("choice-now")).toHaveTextContent("On — chosen by the customer on");
    expect(screen.getByRole("button", { name: "Allow AI summaries" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Don’t allow" })).toBeEnabled();
    const items = within(screen.getByTestId("choice-history")).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Allowed by the customer");
    expect(items[1]).toHaveTextContent("Not allowed by your lender, for the customer");
  });

  test("when the lender's default is on, the person is told they can switch it off", () => {
    render(<ChoicesPanel state={STATE({ policy: "allow", ai_summary_on: true })} onChoose={noop as never} />);
    expect(screen.getByTestId("choice-now")).toHaveTextContent("the lender’s default. You can switch it off.");
  });

  test("a lender that doesn't use AI summaries says so and offers no controls", () => {
    render(<ChoicesPanel state={STATE({ policy: "never" })} onChoose={noop as never} />);
    expect(screen.getByTestId("choices-never")).toHaveTextContent("nothing for you to choose");
    expect(screen.queryByRole("button")).toBeNull();
  });

  test("staff record a choice on the customer's behalf, in words that say so; a failure is explained", async () => {
    const onChoose = jest.fn().mockRejectedValue(new Error("Your lender doesn't use AI-written summaries, so there is nothing to choose."));
    render(<ChoicesPanel state={STATE()} onChoose={onChoose} onBehalf />);
    expect(screen.getByRole("heading", { name: "The customer’s choices" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Record: allowed" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("nothing to choose");
    expect(screen.getByRole("button", { name: "Record: not allowed" })).toBeInTheDocument();
  });
});

describe("the seal on a record", () => {
  const view = (seal: Parameters<typeof DecisionView>[0]["seal"]) =>
    render(<DecisionView record={MOCK_DECISION_RECORDS[ID]} report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false} reportError={null}
      onRetryReport={noop} onBack={noop} seal={seal} />);

  test("sealed records say when, and unsealed ones say nothing vouches for them yet", () => {
    const a = view({ sealed: true, sealed_at: "2026-10-05T10:00:00Z" });
    expect(screen.getByTestId("seal-badge")).toHaveAttribute("data-sealed", "true");
    expect(screen.getByTestId("seal-badge")).toHaveTextContent("Sealed on 5 Oct 2026");
    expect(screen.getByTestId("seal-badge")).toHaveTextContent("If it is changed afterwards, a check will show it.");
    expect(screen.getByTestId("seal-badge")).not.toHaveTextContent(/fingerprint|signed|log/i);
    a.unmount();
    view({ sealed: false });
    expect(screen.getByTestId("seal-badge")).toHaveAttribute("data-sealed", "false");
    expect(screen.getByTestId("seal-badge")).toHaveTextContent("Not sealed yet");
  });

  test("nothing is shown when the seal isn't known (for example the built-in sample decisions)", () => {
    view(undefined);
    expect(screen.queryByTestId("seal-badge")).toBeNull();
  });
});

describe("the seals page for lenders", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    signOut();
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "k";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; delete process.env.VITE_API_KEY; globalThis.__DECISIONTRACE_ENV__ = undefined; });

  const BATCH: SealBatch = { seq: 2, leaf_count: 40, root: "a".repeat(64), signed_at: "2026-10-05T10:00:00Z", published: false };
  function install(opts: { check?: SealCheck; sealed?: boolean } = {}) {
    const calls: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
      calls.push(`${init.method ?? "GET"} ${url.replace("http://api.test", "")}`);
      const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
      if (url.endsWith("/v1/seals/run")) return ok(opts.sealed === false ? { sealed: false, batch: null, message: "Everything was already sealed." } : { sealed: true, batch: BATCH, message: "Sealed." });
      if (url.endsWith("/v1/seals/verify")) return ok(opts.check ?? { ok: true, batches: 2, records: 80, checked_published_copy: false, not_yet_sealed: 0, problems: [] });
      if (url.endsWith("/v1/seals")) return ok(calls.some((c) => c.includes("run")) ? [BATCH] : []);
      throw new Error(`unexpected ${url}`);
    });
    return calls;
  }

  test("lists seals, seals what's new, and says plainly when there is nothing to seal", async () => {
    const calls = install();
    const { container } = render(<SealsPage />);
    expect(await screen.findByTestId("no-seals")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Seal new records" }));
    expect(await screen.findByTestId("seal-message")).toHaveTextContent("Sealed 40 records.");
    expect(await screen.findByTestId("seal-table")).toHaveTextContent("#2");
    expect(calls).toContain("POST /v1/seals/run");
    expect(await axe(container, axeOpts)).toHaveNoViolations();
  });

  test("an 'already sealed' answer is shown as is", async () => {
    install({ sealed: false });
    render(<SealsPage />);
    await screen.findByTestId("no-seals");
    fireEvent.click(screen.getByRole("button", { name: "Seal new records" }));
    expect(await screen.findByTestId("seal-message")).toHaveTextContent("Everything was already sealed.");
  });

  test("checking says everything matches, with a caveat when no outside copy exists", async () => {
    install();
    render(<SealsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check all seals" }));
    const box = await screen.findByTestId("seal-check");
    expect(box).toHaveTextContent("No changes found: 80 records in 2 seals match.");
    expect(box).toHaveTextContent("No outside copy of the seals is set up");
    expect(box).toHaveTextContent("can’t show a change made by someone who controls both");
  });

  test("records that are not sealed yet are said to be outside the check", async () => {
    install({ check: { ok: true, batches: 2, records: 80, checked_published_copy: true, not_yet_sealed: 3, problems: [] } });
    render(<SealsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check all seals" }));
    expect(await screen.findByTestId("seal-check")).toHaveTextContent("3 newer records aren’t sealed yet, so they aren’t covered by this check.");
  });

  test("a problem is reported in plain words, with the seal it is about", async () => {
    install({ check: { ok: false, batches: 2, records: 80, checked_published_copy: true, not_yet_sealed: 0, problems: [{ batch: 1, problem: "Decision 123 changed after it was sealed." }] } });
    render(<SealsPage />);
    fireEvent.click(screen.getByRole("button", { name: "Check all seals" }));
    const box = await screen.findByTestId("seal-check");
    expect(box).toHaveTextContent("Something doesn’t match.");
    expect(box).toHaveTextContent("Seal 1: Decision 123 changed after it was sealed.");
    expect(box).not.toHaveTextContent("No outside copy");
  });
});

describe("in the app", () => {
  const realFetch = globalThis.fetch;
  const ME_CUSTOMER: Me = { role: "consumer", kind: "customer", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: ["decisions:read", "reconstruct", "review:request", "consent:write"], decision_id: ID, expires_at: null };
  const ME_STAFF: Me = { ...ME_CUSTOMER, role: "lender_analyst", kind: "staff", scopes: ["decisions:read", "consent:write", "claims:issue", "signals:read"], decision_id: null };

  beforeEach(() => {
    signOut();
    window.localStorage.clear();
    window.localStorage.setItem("clearly:tour-done", "1");
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
    window.scrollTo = jest.fn() as never;
    process.env.VITE_USE_MOCKS = "false";
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
    window.history.replaceState({}, "", "/");
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; signOut(); globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test("a customer sees whether their record is sealed, and can allow AI summaries; the explanation is asked for again", async () => {
    window.history.replaceState({}, "", `/?d=${ID}&t=${TOKEN}`);
    let state = STATE();
    const calls: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
      calls.push(`${init.method ?? "GET"} ${url.replace("http://api.test", "")}`);
      const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_CUSTOMER);
      if (url.includes("/consents/ai_summary")) {
        state = STATE({ choice: { granted: true, recorded_at: "2026-10-03T09:00:00Z", given_by: "customer" }, ai_summary_on: true, history: [{ granted: true, recorded_at: "2026-10-03T09:00:00Z", given_by: "customer" }] });
        return ok(state);
      }
      if (url.endsWith("/consents")) return ok(state);
      if (url.endsWith("/seal")) return ok({ sealed: true, sealed_at: "2026-10-05T10:00:00Z" });
      if (url.includes("/reconstruct/")) return ok(MOCK_RECONSTRUCTION_REPORTS[ID]);
      if (url.endsWith(`/decisions/${ID}`)) return ok(MOCK_DECISION_RECORDS[ID]);
      return ok([MOCK_DECISIONS_LIST[0]]);
    });
    render(<App />);
    expect(await screen.findByTestId("seal-badge")).toHaveTextContent("Sealed on 5 Oct 2026");
    const panel = await screen.findByTestId("choices-panel");
    expect(panel).toHaveTextContent("Your choices");
    const reconstructsBefore = calls.filter((c) => c.includes("/reconstruct/")).length;
    await userEvent.click(within(panel).getByRole("button", { name: "Allow AI summaries" }));
    await waitFor(() => expect(screen.getByTestId("choice-now")).toHaveTextContent("On — chosen by the customer"));
    await waitFor(() => expect(calls.filter((c) => c.includes("/reconstruct/")).length).toBeGreaterThan(reconstructsBefore));
    expect(calls).toContain(`POST /v1/decisions/${ID}/consents/ai_summary`);
  });

  test("staff see the same panel worded for recording on the customer's behalf; the sample decisions show neither seal nor choices", async () => {
    signInStaff("k");
    window.history.replaceState({}, "", `/?d=${ID}`);
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      if (url.endsWith("/consents")) return ok(STATE());
      if (url.endsWith("/seal")) return ok({ sealed: false });
      if (url.includes("/reconstruct/")) return ok(MOCK_RECONSTRUCTION_REPORTS[ID]);
      if (url.endsWith(`/decisions/${ID}`)) return ok(MOCK_DECISION_RECORDS[ID]);
      return ok([]);
    });
    const { unmount } = render(<App />);
    expect(await screen.findByRole("heading", { name: "The customer’s choices" })).toBeInTheDocument();
    expect(screen.getByTestId("seal-badge")).toHaveAttribute("data-sealed", "false");
    unmount();

    signOut();
    process.env.VITE_USE_MOCKS = "true";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "true" };
    window.history.replaceState({}, "", `/?d=${ID}`);
    render(<App />);
    await screen.findByTestId("decision-record");
    expect(screen.queryByTestId("seal-badge")).toBeNull();
    expect(screen.queryByTestId("choices-panel")).toBeNull();
    process.env.VITE_USE_MOCKS = "false";
  });

  test("a long list loads older decisions on request, without repeating any", async () => {
    signInStaff("k");
    const mk = (n: number) => ({ ...MOCK_DECISIONS_LIST[0], decision_id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, application_id: `LOAN-X-${n}`, applicant_id: `APP-X-${n}` });
    const first = Array.from({ length: 3 }, (_, i) => mk(i + 1));
    const second = [mk(3), mk(4), mk(5)];  // one overlaps, as can happen when the list changes while someone reads
    const seen: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      seen.push(url.replace("http://api.test", ""));
      const ok = (b: unknown, next?: string) => ({ ok: true, status: 200, headers: { get: (h: string) => (h === "X-Next-Cursor" ? next ?? null : null) }, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      if (url.includes("cursor=NEXT1")) return ok(second);
      if (url.includes("/decisions?")) return ok(first, "NEXT1");
      return ok({});
    });
    render(<App />);
    const more = await screen.findByTestId("load-more");
    expect(screen.getAllByText(/LOAN-X-\d/).length).toBe(3);
    fireEvent.click(more);
    await waitFor(() => expect(screen.getAllByText(/LOAN-X-\d/).length).toBe(5));
    expect(seen.some((u) => u.includes("cursor=NEXT1"))).toBe(true);
    await waitFor(() => expect(screen.queryByTestId("load-more")).toBeNull());  // that was the last page
  });
});


describe("things the independent review found", () => {
  const realFetch = globalThis.fetch;
  const ME_STAFF: Me = { role: "lender_analyst", kind: "staff", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: ["decisions:read", "consent:write", "claims:issue", "signals:read"], decision_id: null, expires_at: null };
  const mk = (n: number) => ({ ...MOCK_DECISIONS_LIST[0], decision_id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, application_id: `LOAN-Y-${n}`, applicant_id: `APP-Y-${n}` });

  beforeEach(() => {
    signOut();
    window.localStorage.clear();
    window.localStorage.setItem("clearly:tour-done", "1");
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
    window.scrollTo = jest.fn() as never;
    process.env.VITE_USE_MOCKS = "false";
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
    window.history.replaceState({}, "", "/");
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; signOut(); globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test("a failed 'load older' keeps what is shown, says so beside the button, and retries the same page", async () => {
    signInStaff("k");
    let failing = true;
    const urls: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      urls.push(url);
      const ok = (b: unknown, next?: string) => ({ ok: true, status: 200, headers: { get: (h: string) => (h === "X-Next-Cursor" ? next ?? null : null) }, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      if (url.includes("cursor=NEXT1")) return failing ? { ok: false, status: 500, json: async () => ({}) } : ok([mk(4)]);
      if (url.includes("/decisions?")) return ok([mk(1), mk(2), mk(3)], "NEXT1");
      return ok({});
    });
    render(<App />);
    fireEvent.click(await screen.findByTestId("load-more"));
    expect(await screen.findByText(/We couldn’t load older decisions/)).toBeInTheDocument();
    expect(screen.getAllByText(/LOAN-Y-\d/).length).toBe(3);            // nothing already shown was thrown away
    expect(screen.queryByTestId("service-problem")).toBeNull();           // and the page isn't replaced by a full error
    failing = false;
    fireEvent.click(screen.getByTestId("load-more"));
    await waitFor(() => expect(screen.getAllByText(/LOAN-Y-\d/).length).toBe(4));
    expect(urls.filter((u) => u.includes("cursor=NEXT1")).length).toBe(2);  // the same marker, not a restart from page one
  });

  test("the list says it is only what has been loaded so far when there are older ones", async () => {
    signInStaff("k");
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      const ok = (b: unknown, next?: string) => ({ ok: true, status: 200, headers: { get: (h: string) => (h === "X-Next-Cursor" ? next ?? null : null) }, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      return url.includes("/decisions?") ? ok([mk(1), mk(2)], "NEXT1") : ok({});
    });
    render(<App />);
    await screen.findByTestId("load-more");
    expect(screen.getByText("Decisions loaded")).toBeInTheDocument();
    expect(screen.getByText(/loaded so far. There are older ones./)).toBeInTheDocument();
  });

  test("a page marker from one person's session is never used for the next person", async () => {
    signInStaff("lender-a");
    const keysUsed: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
      if (url.includes("cursor=NEXT-A")) keysUsed.push(String((init.headers as Record<string, string>)["X-API-Key"]));
      const ok = (b: unknown, next?: string) => ({ ok: true, status: 200, headers: { get: (h: string) => (h === "X-Next-Cursor" ? next ?? null : null) }, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      if (url.includes("/decisions?")) return (init.headers as Record<string, string>)["X-API-Key"] === "lender-a" ? ok([mk(1)], "NEXT-A") : { ok: false, status: 500, json: async () => ({}) };
      return ok({});
    });
    render(<App />);
    await screen.findByTestId("load-more");
    act(() => { signOut(); signInStaff("lender-b"); });   // someone else signs in; their first load fails
    await waitFor(() => expect(screen.getByTestId("service-problem")).toBeInTheDocument());
    expect(screen.queryByTestId("load-more")).toBeNull();
    expect(keysUsed).toEqual([]);                          // lender A's marker was never sent
  });

  test("when the customer's choices can't be loaded they are told, and can try again", async () => {
    window.history.replaceState({}, "", `/?d=${ID}&t=${TOKEN}`);
    let consentsOk = false;
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
      if (url.endsWith("/v1/me")) return ok({ ...ME_STAFF, role: "consumer", kind: "customer", decision_id: ID, scopes: ["decisions:read", "reconstruct", "review:request", "consent:write"] });
      if (url.endsWith("/consents")) return consentsOk ? ok(STATE()) : { ok: false, status: 500, json: async () => ({}) };
      if (url.endsWith("/seal")) return ok({ sealed: false });
      if (url.includes("/reconstruct/")) return ok(MOCK_RECONSTRUCTION_REPORTS[ID]);
      if (url.endsWith(`/decisions/${ID}`)) return ok(MOCK_DECISION_RECORDS[ID]);
      return ok([MOCK_DECISIONS_LIST[0]]);
    });
    render(<App />);
    const failed = await screen.findByTestId("choices-failed");
    expect(failed).toHaveTextContent("We couldn’t load the choices just now.");
    consentsOk = true;
    fireEvent.click(within(failed).getByRole("button", { name: "Try again" }));
    expect(await screen.findByTestId("choices-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("choices-failed")).toBeNull();
  });

  test("a choice that finishes saving after the person has gone to another decision does not change what they are reading", async () => {
    signInStaff("k");
    const other = "00000000-0000-0000-0000-000000000008";
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    const reconstructs: string[] = [];
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
      const ok = (b: unknown) => ({ ok: true, status: 200, json: async () => b });
      if (url.endsWith("/v1/me")) return ok(ME_STAFF);
      if (url.includes("/consents/ai_summary")) { await gate; return ok(STATE({ ai_summary_on: true, choice: { granted: true, recorded_at: "2026-10-03T09:00:00Z", given_by: "staff_for_customer" } })); }
      if (url.endsWith("/consents")) return ok(STATE());
      if (url.endsWith("/seal")) return ok({ sealed: false });
      if (url.includes("/reconstruct/")) { reconstructs.push(url); return ok(url.includes(other) ? MOCK_RECONSTRUCTION_REPORTS[other] : MOCK_RECONSTRUCTION_REPORTS[ID]); }
      if (url.endsWith(`/decisions/${ID}`)) return ok(MOCK_DECISION_RECORDS[ID]);
      if (url.endsWith(`/decisions/${other}`)) return ok(MOCK_DECISION_RECORDS[other]);
      return ok([]);
    });
    window.history.replaceState({}, "", `/?d=${ID}`);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Record: allowed" }));
    act(() => { window.history.pushState({}, "", `/?d=${other}`); window.dispatchEvent(new PopStateEvent("popstate")); });
    await screen.findByTestId("decision-record");
    await waitFor(() => expect(reconstructs.some((u) => u.includes(other))).toBe(true));
    const before = reconstructs.length;
    await act(async () => { release(); await Promise.resolve(); });
    await new Promise((r) => setTimeout(r, 40));
    expect(reconstructs.length).toBe(before);  // the late answer for the first decision did not reload anything
  });
});

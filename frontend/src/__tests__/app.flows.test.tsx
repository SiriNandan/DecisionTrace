import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "../App";
import StaffTools from "../components/StaffTools";
import DecisionView from "../components/DecisionView";
import { getSession, signInStaff, signOut } from "../lib/session";
import { MOCK_DECISION_RECORDS, MOCK_DECISIONS_LIST, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";
import type { Me, SignalItem } from "../types";

const ID = "00000000-0000-0000-0000-000000000001";
const TOKEN = "dtc_" + "c".repeat(43);
const CUSTOMER: Me = { role: "consumer", kind: "customer", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: ["decisions:read", "reconstruct", "review:request"], decision_id: ID, expires_at: "2026-12-01T00:00:00Z" };
const STAFF: Me = { role: "lender_analyst", kind: "staff", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: ["decisions:read", "claims:issue", "signals:read"], decision_id: null, expires_at: null };

type Seen = { url: string; method: string; headers: Record<string, string>; body: any };
function install(routes: Record<string, (seen: Seen) => { status: number; body: unknown } | "network">) {
  const seen: Seen[] = [];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
    const call: Seen = { url, method: init.method ?? "GET", headers: (init.headers ?? {}) as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : null };
    seen.push(call);
    const key = Object.keys(routes).find((k) => url.includes(k));
    if (!key) throw new Error(`unexpected ${url}`);
    const r = routes[key](call);
    if (r === "network") throw new TypeError("Failed to fetch");
    return { ok: r.status < 300, status: r.status, json: async () => r.body };
  });
  return seen;
}

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
afterEach(() => { signOut(); globalThis.__DECISIONTRACE_ENV__ = undefined; });

describe("first visit and trouble", () => {
  test("with nothing to sign in with, it says so at once instead of failing a request", async () => {
    const seen = install({});
    render(<App />);
    const box = await screen.findByTestId("service-problem");
    expect(box).toHaveAttribute("data-kind", "signin");
    expect(within(box).getByRole("link", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByTestId("who")).toHaveTextContent("Sign in");  // and the way in is always in the header
    expect(seen).toEqual([]);  // no request was made
  });

  test("'Try the sample decisions' works without a service or a key", async () => {
    install({});
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "Try the sample decisions" }));
    await waitFor(() => expect(screen.getAllByText(/Approved|Declined|Referred|Under review/i).length).toBeGreaterThan(0));
    expect(getSession().mode).toBe("sample");
    expect(screen.getByTestId("who")).toHaveTextContent("Sample decisions");
    expect(screen.queryByTestId("service-problem")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Leave samples" }));
    expect(getSession().mode).toBeNull();
  });

  test("an unreachable service is named as such, and the next steps are offered", async () => {
    signInStaff("k");
    install({ "/v1/me": () => "network", "/decisions": () => "network" });
    render(<App />);
    const box = await screen.findByTestId("service-problem");
    expect(box).toHaveAttribute("data-kind", "unreachable");
    expect(box).toHaveTextContent("We couldn’t reach the service.");
    expect(within(box).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(box).getByRole("button", { name: "Try the sample decisions" })).toBeInTheDocument();
  });

  test("an out-of-date database is explained, not shown as a connection problem", async () => {
    signInStaff("k");
    install({ "/v1/me": () => ({ status: 503, body: { detail: "x", code: "needs_upgrade" } }), "/decisions": () => ({ status: 503, body: { detail: "x", code: "needs_upgrade" } }) });
    render(<App />);
    const box = await screen.findByTestId("service-problem");
    expect(box).toHaveAttribute("data-kind", "upgrade");
    expect(box).toHaveTextContent("Restart the service so it can update itself");
  });

  test("a lender who signs in sees their own name and role, the decisions, and the lender links", async () => {
    signInStaff("dtk_good");
    const seen = install({
      "/v1/me": () => ({ status: 200, body: STAFF }),
      "/decisions": () => ({ status: 200, body: MOCK_DECISIONS_LIST }),
    });
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("Acme Bank · Analyst"));
    expect(seen.find((s) => s.url.endsWith("/decisions"))!.headers["X-API-Key"]).toBe("dtk_good");
    expect(screen.getByRole("link", { name: "Connect your data" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Risk signals" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(getSession().mode).toBeNull();
  });
});

describe("a customer opening their own link", () => {
  function openLink() {
    window.history.replaceState({}, "", `/?d=${ID}&t=${TOKEN}`);
    return install({
      "/v1/me": () => ({ status: 200, body: CUSTOMER }),
      "/overrides": () => ({ status: 200, body: { override_id: 1, decision_id: ID, new_outcome: "REFER", reason: "x", actor_role: "customer", created_at: "2026-10-01T00:00:00Z" } }),
      "/reconstruct/": () => ({ status: 200, body: MOCK_RECONSTRUCTION_REPORTS[ID] }),
      [`/decisions/${ID}`]: () => ({ status: 200, body: MOCK_DECISION_RECORDS[ID] }),
      "/decisions": () => ({ status: 200, body: [MOCK_DECISIONS_LIST[0]] }),
    });
  }

  test("the secret is kept for this tab and taken out of the address bar; only the link's code is sent", async () => {
    const seen = openLink();
    render(<App />);
    expect(await screen.findByTestId("decision-record")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("Your decision"));  // let every request finish
    await waitFor(() => expect(screen.getByTestId("guardrail-line")).toBeInTheDocument());
    expect(window.location.search).toBe(`?d=${ID}`);
    expect(window.location.href).not.toContain(TOKEN);
    expect(getSession()).toEqual({ mode: "customer", claim: TOKEN });
    for (const call of seen) {
      expect(call.headers["X-Claim-Token"]).toBe(TOKEN);
      expect(call.headers["X-API-Key"]).toBeUndefined();
    }
  });

  test("they see their decision with no lender tools, no way to browse, and a simple review request", async () => {
    const seen = openLink();
    render(<App />);
    await screen.findByTestId("decision-record");
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("Your decision"));
    expect(screen.queryByTestId("staff-tools")).toBeNull();
    expect(screen.queryByRole("button", { name: /All decisions/ })).toBeNull();
    expect(screen.queryByRole("link", { name: "Insights" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Connect your data" })).toBeNull();
    await new Promise((r) => setTimeout(r, 120));  // the page moves focus to its heading just after loading; let that settle
    const form = screen.getByTestId("review-form");
    expect(within(form).queryByText("Requested result")).toBeNull();
    expect(within(form).queryByText("Your role")).toBeNull();

    await userEvent.type(within(form).getByRole("textbox"), "My income was recorded wrongly on this application.");
    fireEvent.click(screen.getByTestId("review-submit"));
    await waitFor(() => expect(seen.some((s) => s.url.endsWith("/overrides"))).toBe(true));
    expect(seen.find((s) => s.url.endsWith("/overrides"))!.body).toMatchObject({ decision_id: ID, new_outcome: "REFER", actor_role: "customer" });
  });

  test("an expired link says so and offers a way forward", async () => {
    window.history.replaceState({}, "", `/?d=${ID}&t=${TOKEN}`);
    install({
      "/v1/me": () => ({ status: 401, body: { detail: "This link has expired or was withdrawn. Please ask your lender for a new one." } }),
      "/reconstruct/": () => ({ status: 401, body: { detail: "expired" } }),
      "/decisions": () => ({ status: 401, body: { detail: "This link has expired or was withdrawn. Please ask your lender for a new one." } }),
    });
    render(<App />);
    const box = await screen.findByTestId("service-problem");
    expect(box).toHaveAttribute("data-kind", "signin");
    expect(box).toHaveTextContent("Sign-in is needed to see this decision.");
  });
});

describe("lender tools on a decision", () => {
  const signal = (over: Partial<SignalItem> = {}): SignalItem => ({
    signal_id: 1, decision_id: ID, scope: "decision", signal_type: "applicant_velocity", severity: "warning",
    message: "This customer reference has 3 decisions within 7 days, which is more than usual.", details: {},
    created_at: "2026-10-01T00:00:00Z", status: "open", ...over,
  });

  test("open signals are shown with cautious wording, cleared ones are not", () => {
    const record = { ...MOCK_DECISION_RECORDS[ID], signals: [signal(), signal({ signal_id: 2, message: "Old one", status: "cleared" })] };
    render(<StaffTools record={record} me={STAFF} />);
    expect(screen.getByTestId("flags")).toHaveTextContent("3 decisions within 7 days");
    expect(screen.getByTestId("flags")).not.toHaveTextContent("Old one");
    expect(screen.getByTestId("staff-tools")).toHaveTextContent("not conclusions");
    render(<StaffTools record={{ ...MOCK_DECISION_RECORDS[ID], signals: [] }} me={STAFF} />);
    expect(screen.getByTestId("no-flags")).toBeInTheDocument();
  });

  test("a customer link is created, shown once, and can be copied", async () => {
    signInStaff("k");
    const seen = install({
      "/claim-links": () => ({ status: 200, body: { claim_id: 4, decision_id: ID, token: TOKEN, expires_at: "2026-10-24T00:00:00Z", path: `?d=${ID}&t=${TOKEN}` } }),
    });
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<StaffTools record={MOCK_DECISION_RECORDS[ID]} me={STAFF} />);
    await userEvent.selectOptions(screen.getByLabelText("It works for"), "30 days");
    fireEvent.click(screen.getByRole("button", { name: "Create a link" }));
    const box = await screen.findByTestId("claim-link");
    expect(seen[0].body).toMatchObject({ days: 30 });
    expect(seen[0].url).toBe(`http://api.test/v1/decisions/${ID}/claim-links`);
    const field = within(box).getByLabelText("Send this only to the customer") as HTMLInputElement;
    expect(field.value).toContain(`?d=${ID}&t=${TOKEN}`);
    expect(box).toHaveTextContent("Anyone who has it can see the decision");
    fireEvent.click(within(box).getByRole("button", { name: "Copy the link" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(field.value));
  });

  test("no link maker without permission to issue links", () => {
    render(<StaffTools record={MOCK_DECISION_RECORDS[ID]} me={{ ...STAFF, scopes: ["signals:read"] }} />);
    expect(screen.queryByRole("button", { name: "Create a link" })).toBeNull();
  });

  test("the customer view of a decision never contains the lender panel", () => {
    render(<DecisionView record={MOCK_DECISION_RECORDS[ID]} report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false} reportError={null}
      onRetryReport={() => undefined} onBack={() => undefined} customer />);
    expect(screen.queryByTestId("staff-tools")).toBeNull();
    expect(document.body.textContent).not.toMatch(/Things to look at|risk signal/i);
  });
});


describe("things the independent review found", () => {
  test("a mistyped key does not sign out someone who is already signed in", async () => {
    const SignInPage = (await import("../pages/SignInPage")).default;
    signInStaff("dtk_good");
    install({
      "/health": () => ({ status: 200, body: { status: "ok", database: "ready" } }),
      "/v1/me": (c) => (c.headers["X-API-Key"] === "dtk_good" ? { status: 200, body: STAFF } : { status: 401, body: { detail: "x" } }),
    });
    render(<SignInPage me={STAFF} onChanged={() => undefined} />);
    await screen.findByTestId("health");
    await userEvent.type(screen.getByLabelText("Access key"), "dtk_typo");
    fireEvent.click(screen.getAllByRole("button", { name: "Sign in" })[0]);
    expect(await screen.findByText(/wasn’t recognised/)).toBeInTheDocument();
    expect(getSession()).toEqual({ mode: "staff", key: "dtk_good" });  // still signed in
  });

  test("a customer's decision is not remembered on the computer, and a sign-out leaves nothing behind", async () => {
    window.localStorage.setItem("clearly:last-decision", JSON.stringify({ id: "x", label: "APP-0099 · LOAN-0099" }));
    window.history.replaceState({}, "", `/?d=${ID}&t=${TOKEN}`);
    install({
      "/v1/me": () => ({ status: 200, body: CUSTOMER }),
      "/reconstruct/": () => ({ status: 200, body: MOCK_RECONSTRUCTION_REPORTS[ID] }),
      [`/decisions/${ID}`]: () => ({ status: 200, body: MOCK_DECISION_RECORDS[ID] }),
      "/decisions": () => ({ status: 200, body: [MOCK_DECISIONS_LIST[0]] }),
    });
    render(<App />);
    await screen.findByTestId("decision-record");
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("Your decision"));
    expect(window.localStorage.getItem("clearly:last-decision")).toBeNull();  // the earlier person's note was cleared on sign-in
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(window.localStorage.getItem("clearly:last-decision")).toBeNull();
    expect(getSession().mode).toBeNull();
  });

  test("a slow answer for an earlier decision cannot overwrite the one now shown", async () => {
    signInStaff("k");
    const other = "00000000-0000-0000-0000-000000000008";
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string) => {
      const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body });
      if (url.endsWith("/v1/me")) return ok(STAFF);
      if (url.includes("/reconstruct/")) return ok(MOCK_RECONSTRUCTION_REPORTS[ID]);
      if (url.endsWith(`/decisions/${ID}`)) { await gate; return ok(MOCK_DECISION_RECORDS[ID]); }   // slow
      if (url.endsWith(`/decisions/${other}`)) return ok(MOCK_DECISION_RECORDS[other]);
      return ok(MOCK_DECISIONS_LIST);
    });
    window.history.replaceState({}, "", `/?d=${ID}`);
    render(<App />);
    await waitFor(() => expect(screen.getByTestId("who")).toHaveTextContent("Acme Bank"));
    act(() => { window.history.pushState({}, "", `/?d=${other}`); window.dispatchEvent(new PopStateEvent("popstate")); });
    const shown = await screen.findByTestId("decision-record");
    const label = shown.textContent;
    await act(async () => { release(); await Promise.resolve(); });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.getByTestId("decision-record").textContent).toBe(label);  // still the second decision
    expect(label).toContain(MOCK_DECISION_RECORDS[other].application_id);
  });

  test("a review request is shown to the customer and to staff, and stops a second one", async () => {
    const withRequest = { ...MOCK_DECISION_RECORDS[ID], review_requests: [{ reason: "My income was wrong.", created_at: "2026-10-02T09:00:00Z" }] };
    const { unmount } = render(<DecisionView record={withRequest} report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false} reportError={null}
      onRetryReport={() => undefined} onBack={() => undefined} customer />);
    expect(screen.getByTestId("review-requested")).toHaveTextContent("My income was wrong.");
    expect(screen.getByTestId("review-requested")).toHaveTextContent("stays as it is");
    expect(screen.getByTestId("review-already")).toBeInTheDocument();
    expect(screen.queryByTestId("review-form")).toBeNull();
    unmount();
    render(<DecisionView record={withRequest} report={MOCK_RECONSTRUCTION_REPORTS[ID]} reportLoading={false} reportError={null}
      onRetryReport={() => undefined} onBack={() => undefined} />);
    expect(screen.getByTestId("review-requested")).toBeInTheDocument();  // staff see it too
  });

  test("customers get plain words and no addresses when the service is down, even on a development build", () => {
    const { ServiceProblem } = jest.requireActual("../components/ServiceProblem");
    signInCustomerForTest();
    render(<ServiceProblem kind="unreachable" what="decision" />);
    const box = screen.getByTestId("service-problem");
    expect(box).toHaveTextContent("Please try again in a little while, or contact your lender.");
    expect(box).not.toHaveTextContent(/http|running|address/i);
    expect(within(box).queryByRole("button", { name: "Try the sample decisions" })).toBeNull();
  });

  test("a failed copy says so", async () => {
    Object.assign(navigator, { clipboard: { writeText: jest.fn().mockRejectedValue(new Error("no")) } });
    signInStaff("k");
    install({ "/claim-links": () => ({ status: 200, body: { claim_id: 4, decision_id: ID, token: TOKEN, expires_at: "2026-10-24T00:00:00Z", path: `?d=${ID}&t=${TOKEN}` } }) });
    render(<StaffTools record={MOCK_DECISION_RECORDS[ID]} me={STAFF} />);
    fireEvent.click(screen.getByRole("button", { name: "Create a link" }));
    fireEvent.click(await screen.findByRole("button", { name: "Copy the link" }));
    await waitFor(() => expect(screen.getByTestId("copy-note")).toHaveTextContent("Couldn’t copy it automatically"));
  });
});

function signInCustomerForTest() {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  require("../lib/session").signInCustomer(TOKEN);
}

describe("lender-only pages", () => {
  test.each(["connect", "quality", "signals"])("%s asks anyone who isn't a signed-in lender to sign in", async (page) => {
    window.history.replaceState({}, "", `/?p=${page}`);
    install({});
    render(<App />);
    const box = await screen.findByTestId("service-problem");
    expect(box).toHaveAttribute("data-kind", "signin");
    expect(box).toHaveTextContent("Sign-in is needed to see this page.");
    expect(screen.queryByRole("button", { name: /Check my (file|decisions)|Check for signals/ })).toBeNull();
  });

  test("a customer cannot open them either", async () => {
    window.history.replaceState({}, "", `/?p=signals&t=${TOKEN}`);
    install({ "/v1/me": () => ({ status: 200, body: CUSTOMER }), "/decisions": () => ({ status: 200, body: [MOCK_DECISIONS_LIST[0]] }) });
    render(<App />);
    expect(await screen.findByTestId("service-problem")).toHaveAttribute("data-kind", "signin");
    expect(screen.queryByText("Risk signals", { selector: "h1" })).toBeNull();
  });

  test("a signed-in lender gets the page", async () => {
    signInStaff("k");
    window.history.replaceState({}, "", "/?p=signals");
    install({ "/v1/me": () => ({ status: 200, body: STAFF }), "/v1/signals": () => ({ status: 200, body: [] }), "/decisions": () => ({ status: 200, body: [] }) });
    render(<App />);
    expect(await screen.findByRole("heading", { name: "Risk signals" })).toBeInTheDocument();
  });
});

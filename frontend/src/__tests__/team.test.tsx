import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import ConnectPage from "../pages/ConnectPage";
import StaffHome from "../components/StaffHome";
import { SiteFooter } from "../components/SiteChrome";
import TeamPage from "../pages/TeamPage";
import type { Me, TeamState } from "../types";

expect.extend(toHaveNoViolations);
const run = (c: HTMLElement) => axe(c, { rules: { "color-contrast": { enabled: false } } });

const me = (role: string, scopes: string[]): Me => ({ role, kind: "staff", tenant_id: "acme", tenant_name: "Acme Bank", label: "Asha Rao", email: "asha@acme.test", scopes, decision_id: null, expires_at: null });
const OFFICER = me("lender_ingest", ["ingest:write", "decide:write", "mappings:read", "mappings:write", "quality:read"]);
const ANALYST = me("lender_analyst", ["decisions:read", "mappings:read", "mappings:write", "mappings:approve", "signals:read", "quality:read", "seals:read"]);
const ADMIN = me("lender_admin", ["team:manage", "audit:read"]);
const AUDITOR = me("auditor", ["decisions:read", "mappings:read", "audit:read"]);

const TEAM: TeamState = {
  you: 1,
  members: [
    { credential_id: 1, label: "Asha, administrator", role: "lender_admin", roles: ["lender_admin"], portfolios: null, expires_at: null, withdrawn_at: null, created_at: "2026-10-01T00:00:00Z", email: "asha@acme.test", state: "active" },
    { credential_id: 2, label: "Priya, Leeds branch", role: "lender_analyst", roles: ["lender_analyst", "lender_support"], portfolios: ["Leeds"], expires_at: "2027-01-01T00:00:00Z", withdrawn_at: null, created_at: "2026-10-02T00:00:00Z", email: "priya@acme.test", state: "active" },
    { credential_id: 3, label: "Old contractor", role: "auditor", roles: ["auditor"], portfolios: null, expires_at: null, withdrawn_at: "2026-10-03T00:00:00Z", created_at: "2026-09-01T00:00:00Z", email: null, state: "withdrawn" },
  ],
  portfolios: [{ name: "Leeds", decisions: 12 }, { name: "York", decisions: 4 }],
  roles: [
    { role: "lender_admin", name: "Team administrator", description: "Adds and removes people.", can_limit_to_portfolios: false },
    { role: "lender_ingest", name: "Data officer", description: "The only role that loads decision data.", can_limit_to_portfolios: false },
    { role: "lender_analyst", name: "Analyst", description: "Reads decisions and records reviews.", can_limit_to_portfolios: true },
    { role: "lender_support", name: "Support", description: "Helps a customer.", can_limit_to_portfolios: true },
    { role: "auditor", name: "Auditor", description: "Reads everything.", can_limit_to_portfolios: true },
  ],
};

type Call = { url: string; method: string; body: any };
function install(extra: (url: string, method: string, body: any) => unknown | undefined = () => undefined) {
  const calls: Call[] = [];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
    const body = init.body ? JSON.parse(String(init.body)) : null;
    const method = init.method ?? "GET";
    calls.push({ url, method, body });
    const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
    const mine = extra(url, method, body);
    if (mine !== undefined) return ok(mine);
    if (url.endsWith("/v1/team") && method === "GET") return ok(TEAM);
    if (url.endsWith("/v1/team/keys")) return ok({ credential_id: 9, key: "dtk_secret_value_123", role: body.roles[0], roles: body.roles, label: body.label, email: body.email, email_status: "not_configured", portfolios: body.portfolios ?? null, expires_at: null });
    if (url.includes("/revoke")) return ok({ credential_id: 2, state: "withdrawn" });
    throw new Error(`unexpected ${url}`);
  });
  return calls;
}

beforeEach(() => {
  process.env.VITE_USE_MOCKS = "false";
  process.env.VITE_API_KEY = "k";
  globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
});
afterEach(() => { delete process.env.VITE_API_KEY; globalThis.__DECISIONTRACE_ENV__ = undefined; });

describe("Your team", () => {
  test("lists people with what they do and can see, and is accessible", async () => {
    install();
    const { container } = render(<TeamPage />);
    const table = await screen.findByTestId("team-table");
    expect(within(table).getByTestId("member-2")).toHaveTextContent("Priya, Leeds branch");
    expect(within(table).getByTestId("member-2")).toHaveTextContent("Analyst");
    expect(within(table).getByTestId("member-2")).toHaveTextContent("Leeds");
    expect(within(table).getByTestId("member-1")).toHaveTextContent("(you)");
    expect(within(table).getByTestId("member-1")).not.toHaveTextContent("Withdraw");  // you can't withdraw yourself
    expect(within(table).getByTestId("member-3")).toHaveTextContent("Withdrawn");
    expect(within(table).getByTestId("member-3")).not.toHaveTextContent("Withdraw key");
    expect(await run(container)).toHaveNoViolations();
  });

  test("adding a person limited to a portfolio shows their key once", async () => {
    const calls = install();
    render(<TeamPage />);
    await screen.findByTestId("team-table");
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("person’s name");
    await userEvent.type(screen.getByLabelText("Name"), "Sam Jones");
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("valid email");
    await userEvent.type(screen.getByLabelText("Email"), "sam@acme.test");
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("what this person does");

    expect(screen.queryByText(/Which decisions can they see/)).toBeNull();  // only roles that can be limited offer it
    await userEvent.click(screen.getByRole("checkbox", { name: /Support/ }));
    await userEvent.click(screen.getByRole("checkbox", { name: /York/ }));
    await userEvent.selectOptions(screen.getByLabelText("Key lasts"), "90");
    fireEvent.click(screen.getByRole("button", { name: "Send invite" }));

    const note = await screen.findByTestId("new-key");
    expect(within(note).getByTestId("new-key-value")).toHaveTextContent("dtk_secret_value_123");
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.body).toEqual({ label: "Sam Jones", email: "sam@acme.test", roles: ["lender_support"], expires_days: 90, portfolios: ["York"] });
    await userEvent.click(within(note).getByRole("button", { name: "I’ve saved it" }));
    expect(screen.queryByTestId("new-key")).toBeNull();
  });

  test("a person with two roles is listed with both, and a whole-lender role hides the group limit", async () => {
    install();
    render(<TeamPage />);
    const table = await screen.findByTestId("team-table");
    expect(within(table).getByTestId("member-2")).toHaveTextContent("Analyst + Support");
    await userEvent.click(screen.getByRole("checkbox", { name: /Analyst/ }));
    expect(screen.getByText(/Which decisions can they see/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("checkbox", { name: /Data officer/ }));
    expect(screen.queryByText(/Which decisions can they see/)).toBeNull();
    expect(screen.getByText(/can’t be limited to some groups/)).toBeInTheDocument();
  });

  test("withdrawing asks first, then withdraws only that person", async () => {
    const calls = install();
    render(<TeamPage />);
    const row = await screen.findByTestId("member-2");
    fireEvent.click(within(row).getByRole("button", { name: "Withdraw key for Priya, Leeds branch" }));
    expect(calls.some((c) => c.url.includes("/revoke"))).toBe(false);
    fireEvent.click(within(row).getByRole("button", { name: "Keep" }));
    fireEvent.click(within(row).getByRole("button", { name: /Withdraw key for/ }));
    fireEvent.click(within(row).getByRole("button", { name: /Yes, withdraw/ }));
    await waitFor(() => expect(calls.some((c) => c.url === "http://api.test/v1/team/keys/2/revoke" && c.method === "POST")).toBe(true));
  });
});

describe("Who sees what", () => {
  test("each role lands on what it can do, and the footer offers only that", () => {
    const { rerender } = render(<StaffHome me={OFFICER} />);
    expect(screen.getByRole("heading", { name: "Welcome, Data officer" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Load decision data" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Manage your team" })).toBeNull();
    expect(document.body.textContent).toMatch(/can’t read the decisions themselves/);
    rerender(<StaffHome me={ADMIN} />);
    expect(screen.getByRole("link", { name: "Manage your team" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Load decision data" })).toBeNull();

    const footer = render(<SiteFooter onTour={() => undefined} staff scopes={ADMIN.scopes} />);
    const lender = within(footer.container).getByRole("navigation", { name: "For lenders" });
    expect(within(lender).getByRole("link", { name: "Your team" })).toBeInTheDocument();
    expect(within(lender).queryByRole("link", { name: "Connect your data" })).toBeNull();
  });
});

describe("Loading data, by role", () => {
  const CSV = "ref,cust,when,result,pd,model,policy\nA1,c1,2026-01-01,Yes,0.1,m1,P1\n";
  const SPEC = { spec_version: 1, source_format: "csv", external_id: { column: "ref", transform: "text", date_formats: [], value_map: {}, confidence: 1, note: "" }, subject_ref: null, decided_at: null, outcome: null, score: null, model_version: null, policy_version: null, reasons: null, evidence: [], ignored_columns: {} };
  const LAYOUTS = [
    { mapping_id: 4, name: "uk-bank", version: 1, status: "approved", proposed_by: "rules", gaps: [] },
    { mapping_id: 5, name: "us-lender", version: 1, status: "proposed", proposed_by: "rules", gaps: [] },
  ];
  const PROPOSAL = { mapping_id: 6, name: "new-file", version: 1, spec: SPEC, spec_sha256: "x", proposed_by: "rules", status: "proposed", gaps: [], issues: [], columns: ["ref"], outcome_values: [] };
  const route = (url: string, method: string, body: any) => {
    if (url.endsWith("/v1/mappings") && method === "GET") return LAYOUTS;
    if (url.endsWith("/v1/mappings/5")) return { ...LAYOUTS[1], spec: SPEC };
    if (url.endsWith("/v1/mappings/propose")) return PROPOSAL;
    if (url.endsWith("/approve")) return { ...LAYOUTS[1], status: "approved" };
    if (url.endsWith("/v1/imports")) return { mapping_id: 4, version: 1, dry_run: false, created: body.rows.length, replayed: 0, rejected: 0, results: [] };
    return undefined;
  };
  const pick = () => fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File([CSV], "uk.csv", { type: "text/csv" })] } });

  test("the data officer can check a file but an analyst has to approve it, then the officer loads it under a group", async () => {
    const calls = install(route);
    const { container } = render(<ConnectPage me={OFFICER} />);
    expect(await screen.findByTestId("your-part")).toHaveTextContent("You’re the data officer");
    expect(await screen.findByTestId("layout-5")).toHaveTextContent("Waiting for an analyst to approve it");
    expect(within(screen.getByTestId("layout-5")).queryByRole("button", { name: /Review/ })).toBeNull();

    pick();
    await screen.findByTestId("file-summary");
    fireEvent.click(screen.getByRole("button", { name: "Check my file" }));
    expect(await screen.findByTestId("needs-analyst")).toHaveTextContent("An analyst needs to approve this match");
    expect(screen.queryByRole("button", { name: "Approve this match" })).toBeNull();

    fireEvent.click(within(screen.getByTestId("layout-4")).getByRole("button", { name: "Use uk-bank for this file" }));
    await userEvent.type(await screen.findByLabelText(/Group these decisions/), "Leeds branch");
    fireEvent.click(screen.getByRole("button", { name: /Import 1 decisions/ }));
    await screen.findByTestId("import-summary");
    const post = calls.find((c) => c.url.endsWith("/v1/imports"))!;
    expect(post.body).toMatchObject({ mapping_id: 4, portfolio: "Leeds branch" });
    expect(await run(container)).toHaveNoViolations();
  });

  test("an analyst reviews and approves a waiting layout but is not offered the loading", async () => {
    const calls = install(route);
    render(<ConnectPage me={ANALYST} />);
    expect(await screen.findByTestId("your-part")).toHaveTextContent("You’re an analyst");
    fireEvent.click(await screen.findByRole("button", { name: "Review us-lender" }));
    expect(await screen.findByTestId("looking")).toHaveTextContent("Application reference: column “ref”");
    fireEvent.click(within(screen.getByTestId("looking")).getByRole("button", { name: "Approve this match" }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/v1/mappings/5/approve"))).toBe(true));
    pick();
    await screen.findByTestId("file-summary");
    expect(screen.queryByRole("button", { name: /Use .* for this file/ })).toBeNull();
    expect(calls.some((c) => c.url.endsWith("/v1/imports"))).toBe(false);
  });

  test("a read-only role is told this isn't part of its job", async () => {
    install(route);
    render(<ConnectPage me={AUDITOR} />);
    expect(await screen.findByTestId("no-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("file-input")).toBeNull();
  });
});

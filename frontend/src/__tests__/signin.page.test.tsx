import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import SignInPage from "../pages/SignInPage";
import ServiceProblem from "../components/ServiceProblem";
import { getSession, signOut } from "../lib/session";
import type { Me } from "../types";

expect.extend(toHaveNoViolations);

const TOKEN = "dtc_" + "b".repeat(43);
const DEC = "22222222-2222-4222-8222-222222222222";
const STAFF: Me = { role: "lender_analyst", kind: "staff", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: [], decision_id: null, expires_at: null };
const CUSTOMER: Me = { role: "consumer", kind: "customer", tenant_id: "acme", tenant_name: "Acme Bank", label: "", scopes: [], decision_id: DEC, expires_at: "2026-12-01T00:00:00Z" };

type Seen = { url: string; headers: Record<string, string> };
function installFetch(handlers: Record<string, () => { status: number; body: unknown } | "network">) {
  const seen: Seen[] = [];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit = {}) => {
    seen.push({ url, headers: (init.headers ?? {}) as Record<string, string> });
    const key = Object.keys(handlers).find((k) => url.endsWith(k));
    if (!key) throw new Error(`unexpected ${url}`);
    const r = handlers[key]();
    if (r === "network") throw new TypeError("Failed to fetch");
    return { ok: r.status < 300, status: r.status, json: async () => r.body };
  });
  return seen;
}

describe("Sign in", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    signOut();
    process.env.VITE_USE_MOCKS = "false";
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
    window.history.replaceState({}, "", "/");
  });
  afterEach(() => { (globalThis as unknown as { fetch: unknown }).fetch = realFetch; signOut(); globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test("shows whether the service is running, in plain words", async () => {
    installFetch({ "/health": () => ({ status: 200, body: { status: "needs_upgrade", database: "behind" } }) });
    render(<SignInPage me={null} onChanged={() => undefined} />);
    expect(await screen.findByTestId("health")).toHaveTextContent("its database needs an update");

    installFetch({ "/health": () => "network" });
    render(<SignInPage me={null} onChanged={() => undefined} />);
    await waitFor(() => expect(screen.getAllByTestId("health").some((h) => /can’t reach the service at http:\/\/api\.test/.test(h.textContent ?? ""))).toBe(true));
  });

  test("a lender signs in with a key; a wrong key is explained and not kept", async () => {
    let accepted = false;
    const seen = installFetch({
      "/health": () => ({ status: 200, body: { status: "ok", database: "ready" } }),
      "/v1/me": () => (accepted ? { status: 200, body: STAFF } : { status: 401, body: { detail: "Sign-in is needed to see this." } }),
    });
    const changed = jest.fn();
    const { container } = render(<SignInPage me={null} onChanged={changed} />);
    await screen.findByTestId("health");
    const input = screen.getByLabelText("Access key");
    expect(input).toHaveAttribute("type", "password");
    await userEvent.type(input, "wrong-key");
    fireEvent.click(screen.getAllByRole("button", { name: "Sign in" })[0]);
    expect(await screen.findByText("That access key wasn’t recognised. Check it and try again.")).toBeInTheDocument();
    expect(getSession().mode).toBeNull();
    expect(changed).not.toHaveBeenCalled();

    accepted = true;
    await userEvent.clear(input);
    await userEvent.type(input, "dtk_good");
    await userEvent.click(screen.getByLabelText("Show the key"));
    expect(input).toHaveAttribute("type", "text");
    fireEvent.click(screen.getAllByRole("button", { name: "Sign in" })[0]);
    await waitFor(() => expect(changed).toHaveBeenCalled());
    expect(getSession()).toEqual({ mode: "staff", key: "dtk_good" });
    expect(seen.filter((s) => s.url.endsWith("/v1/me")).pop()!.headers["X-API-Key"]).toBe("dtk_good");
    expect(window.location.search).toBe("");
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });

  test("a customer pastes their link and goes to their decision, sending only the link's code", async () => {
    const seen = installFetch({
      "/health": () => ({ status: 200, body: { status: "ok", database: "ready" } }),
      "/v1/me": () => ({ status: 200, body: CUSTOMER }),
    });
    render(<SignInPage me={null} onChanged={() => undefined} />);
    await userEvent.type(screen.getByLabelText("The link or code from your lender"), `https://x.example/?d=${DEC}&t=${TOKEN}`);
    fireEvent.click(screen.getByRole("button", { name: "Open my decision" }));
    await waitFor(() => expect(window.location.search).toBe(`?d=${DEC}`));
    const me = seen.filter((s) => s.url.endsWith("/v1/me")).pop()!;
    expect(me.headers["X-Claim-Token"]).toBe(TOKEN);
    expect(me.headers["X-API-Key"]).toBeUndefined();
    expect(getSession()).toEqual({ mode: "customer", claim: TOKEN });
  });

  test("a link that isn't a link, and a link that has expired, are explained kindly", async () => {
    installFetch({
      "/health": () => ({ status: 200, body: { status: "ok", database: "ready" } }),
      "/v1/me": () => ({ status: 401, body: { detail: "This link has expired or was withdrawn. Please ask your lender for a new one." } }),
    });
    render(<SignInPage me={null} onChanged={() => undefined} />);
    const field = screen.getByLabelText("The link or code from your lender");
    await userEvent.type(field, "hello");
    fireEvent.click(screen.getByRole("button", { name: "Open my decision" }));
    expect(await screen.findByText(/doesn’t look like a link from your lender/)).toBeInTheDocument();
    await userEvent.clear(field);
    await userEvent.type(field, TOKEN);
    fireEvent.click(screen.getByRole("button", { name: "Open my decision" }));
    expect(await screen.findByText(/expired or was withdrawn/)).toBeInTheDocument();
    expect(getSession().mode).toBeNull();
  });

  test("trying the samples needs no key, and a signed-in person can sign out", async () => {
    installFetch({ "/health": () => ({ status: 200, body: { status: "ok", database: "ready" } }) });
    const changed = jest.fn();
    const { unmount } = render(<SignInPage me={null} onChanged={changed} />);
    await screen.findByTestId("health");
    fireEvent.click(screen.getByRole("button", { name: "Try the sample decisions" }));
    expect(getSession().mode).toBe("sample");
    expect(changed).toHaveBeenCalled();
    unmount();

    signOut();
    const out = jest.fn();
    render(<SignInPage me={STAFF} onChanged={out} />);
    await screen.findByTestId("health");
    expect(screen.getByTestId("signed-in")).toHaveTextContent("You’re signed in as Acme Bank (Analyst).");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(out).toHaveBeenCalled();
  });
});

describe("what went wrong, and what to do next", () => {
  beforeEach(() => { globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" }; });
  afterEach(() => { signOut(); globalThis.__DECISIONTRACE_ENV__ = undefined; });

  test.each([
    ["signin", "Sign-in is needed to see these decisions.", ["Sign in", "Try the sample decisions"]],
    ["unreachable", "We couldn’t reach the service.", ["Try again", "Try the sample decisions"]],
    ["upgrade", "The service needs an update.", ["Try again", "Try the sample decisions"]],
    ["other", "We couldn’t load the decisions.", ["Try again"]],
  ] as const)("%s", async (kind, title, actions) => {
    const retry = jest.fn();
    const { container } = render(<ServiceProblem kind={kind} what="decisions" onRetry={retry} />);
    expect(screen.getByTestId("service-problem")).toHaveTextContent(title);
    for (const a of actions) expect(screen.getByRole(a === "Sign in" ? "link" : "button", { name: a })).toBeInTheDocument();
    if (kind === "unreachable") expect(screen.getByTestId("service-problem")).toHaveTextContent("http://api.test");
    if (kind !== "signin") { fireEvent.click(screen.getByRole("button", { name: "Try again" })); expect(retry).toHaveBeenCalled(); }
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });
});

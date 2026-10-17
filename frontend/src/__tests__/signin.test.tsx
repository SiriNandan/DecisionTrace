import React from "react";
import { render, screen } from "@testing-library/react";
import HomeView from "../components/HomeView";
import {
  ApiError,
  fetchDecisionRecord,
  fetchDecisionsList,
  fetchReconstructionReport,
  needsSignIn,
  submitDecisionOverride,
} from "../api";

const ID = "00000000-0000-0000-0000-000000000001";
const noop = () => undefined;

function mockFetch(status: number, body: unknown) {
  const fn = jest.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
  (globalThis as unknown as { fetch: unknown }).fetch = fn;
  return fn;
}

describe("sign-in key and plain-language errors", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "test-key";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => {
    (globalThis as unknown as { fetch: unknown }).fetch = realFetch;
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = undefined;
  });

  test("reads and writes all send the key", async () => {
    const fn = mockFetch(200, []);
    await fetchDecisionsList();
    await fetchDecisionRecord(ID).catch(noop);
    await fetchReconstructionReport(ID).catch(noop);
    await submitDecisionOverride({
      decision_id: ID,
      new_outcome: "APPROVE",
      reason: "A long enough reason for review.",
      actor_role: "customer",
    }).catch(noop);
    expect(fn).toHaveBeenCalledTimes(4);
    for (const call of fn.mock.calls) {
      expect((call[1] as RequestInit).headers).toMatchObject({ "X-API-Key": "test-key" });
    }
  });

  test("no key configured means no key header", async () => {
    delete process.env.VITE_API_KEY;
    const fn = mockFetch(200, []);
    await fetchDecisionsList();
    expect(((fn.mock.calls[0][1] as RequestInit).headers as Record<string, string>)["X-API-Key"]).toBeUndefined();
  });

  test.each([
    [401, "Sign-in is needed to see this."],
    [403, "This sign-in isn't allowed to do that."],
    [404, "We couldn't find that decision."],
    [429, "That's a lot of requests at once. Please wait a moment and try again."],
  ])("HTTP %i without a reason reads in plain language", async (status, message) => {
    mockFetch(status, {});
    await expect(fetchDecisionRecord(ID)).rejects.toThrow(message);
  });

  test("the server's own reason is preferred and the status is kept", async () => {
    mockFetch(401, { detail: "Sign-in is needed to see this." });
    const err = await fetchDecisionsList().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(needsSignIn(err)).toBe(true);
    expect(needsSignIn(new ApiError("x", 404))).toBe(false);
    expect(needsSignIn(new Error("x"))).toBe(false);
  });

  test("home explains a missing sign-in instead of blaming the connection", () => {
    render(
      <HomeView cases={[]} loading={false} error="Sign-in is needed to see this." signInNeeded onRetry={noop} onOpen={noop} recent={null} />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Sign-in is needed to see these decisions.");
    expect(screen.getByRole("alert")).not.toHaveTextContent(/connection/i);
  });
});

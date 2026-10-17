/** Needs the live API: staff sign-in, a customer link end to end, and the risk-signal screens' endpoints. */
import { createClaimLink, fetchDecisionRecord, fetchDecisionsList, fetchHealth, fetchMe, fetchSignals, runSignalChecks, submitDecisionOverride, ApiError } from "../api";
import { signInCustomer, signInStaff, signOut } from "../lib/session";

describe("Sign-in and customer links against the live API (VITE_USE_MOCKS=false)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";
  const staffKey = process.env.VITE_API_KEY || "";
  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: apiUrl, VITE_USE_MOCKS: "false" };
  });
  afterEach(() => signOut());

  test("the service reports that it is healthy and its database is current", async () => {
    expect(await fetchHealth()).toEqual({ status: "ok", database: "ready" });
  });

  test("a lender signs in, makes a link, and the customer sees only that decision", async () => {
    signInStaff(staffKey);
    const me = await fetchMe();
    expect(me.kind).toBe("staff");
    expect(me.scopes).toContain("claims:issue");
    const list = await fetchDecisionsList();
    const target = list.find((d) => d.application_id === "LOAN-SCEN-0001") ?? list[0];
    const full = await fetchDecisionRecord(target.decision_id);
    expect(Array.isArray(full.signals)).toBe(true);  // lenders get the signals list

    const link = await createClaimLink(target.decision_id, 1, "live test");
    expect(link.token.startsWith("dtc_")).toBe(true);
    expect(link.path).toBe(`?d=${target.decision_id}&t=${link.token}`);

    signOut();
    signInCustomer(link.token);
    const who = await fetchMe();
    expect(who).toMatchObject({ kind: "customer", decision_id: target.decision_id });
    const mine = await fetchDecisionsList();
    expect(mine.map((d) => d.decision_id)).toEqual([target.decision_id]);
    const record = await fetchDecisionRecord(target.decision_id);
    expect(record.signals).toBeUndefined();  // customers never see risk signals
    expect(record.snapshots.some((s) => s.evidence_type === "analyst_note")).toBe(false);
    const other = list.find((d) => d.decision_id !== target.decision_id)!;
    await expect(fetchDecisionRecord(other.decision_id)).rejects.toMatchObject({ status: 404 });
    await expect(fetchSignals()).rejects.toMatchObject({ status: 403 });
    await expect(
      submitDecisionOverride({ decision_id: target.decision_id, new_outcome: "APPROVE", reason: "Please change this to approved now.", actor_role: "customer" })
    ).rejects.toBeInstanceOf(ApiError);
  });

  test("a wrong key and a made-up link are refused with plain messages", async () => {
    signInStaff("dtk_definitely-wrong");
    await expect(fetchMe()).rejects.toMatchObject({ status: 401 });
    signOut();
    signInCustomer("dtc_" + "z".repeat(43));
    await expect(fetchMe()).rejects.toMatchObject({ status: 401 });
  });

  test("signal checks run and are repeatable", async () => {
    signInStaff(staffKey);
    const first = await runSignalChecks();
    expect(first.decisions_checked).toBeGreaterThanOrEqual(10);
    const again = await runSignalChecks();
    expect(again.new_signals).toBe(0);
    for (const s of await fetchSignals("all")) expect(s.message).not.toMatch(/fraud/i);
  });
});

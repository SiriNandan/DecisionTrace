/** Needs the live API: paging, seals and customer choices end to end. */
import {
  checkSeals, createClaimLink, fetchConsents, fetchDecisionSeal, fetchDecisionsPage, fetchSeals, runSeal, setAiSummaryChoice,
} from "../api";
import { signInCustomer, signInStaff, signOut } from "../lib/session";

describe("Paging, seals and choices against the live API (VITE_USE_MOCKS=false)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";
  const staffKey = process.env.VITE_API_KEY || "";
  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: apiUrl, VITE_USE_MOCKS: "false" };
  });
  afterEach(() => signOut());

  test("pages follow one another without repeating a decision", async () => {
    signInStaff(staffKey);
    const first = await fetchDecisionsPage(null, 4);
    expect(first.items.length).toBe(4);
    expect(first.next).toBeTruthy();
    const second = await fetchDecisionsPage(first.next, 4);
    const ids = [...first.items, ...second.items].map((d) => d.decision_id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(first.items[0].decided_at >= second.items[0].decided_at).toBe(true);
    const all = await fetchDecisionsPage(null, 1000);
    expect(all.next).toBeNull();
  });

  test("a lender seals its records, the seals check out, and a customer can see their record's seal", async () => {
    signInStaff(staffKey);
    await runSeal();
    const batches = await fetchSeals();
    expect(batches.length).toBeGreaterThan(0);
    const check = await checkSeals();
    expect(check).toMatchObject({ ok: true });
    const target = (await fetchDecisionsPage(null, 1)).items[0];
    expect((await fetchDecisionSeal(target.decision_id)).sealed).toBe(true);

    const link = await createClaimLink(target.decision_id, 1, "live test");
    signOut();
    signInCustomer(link.token);
    const mine = await fetchDecisionSeal(target.decision_id);
    expect(mine.sealed).toBe(true);
    await expect(runSeal()).rejects.toMatchObject({ status: 403 });
  });

  test("a customer allows and then withdraws AI summaries, and every change is kept", async () => {
    signInStaff(staffKey);
    const target = (await fetchDecisionsPage(null, 2)).items[1];
    const link = await createClaimLink(target.decision_id, 1, "live test");
    signOut();
    signInCustomer(link.token);
    const before = await fetchConsents(target.decision_id);
    const on = await setAiSummaryChoice(target.decision_id, true);
    expect(on.ai_summary_on).toBe(true);
    const off = await setAiSummaryChoice(target.decision_id, false);
    expect(off.ai_summary_on).toBe(false);
    expect(off.history.length).toBe(before.history.length + 2);
    expect(off.history.slice(0, 2).map((h) => h.granted)).toEqual([false, true]);
  });
});

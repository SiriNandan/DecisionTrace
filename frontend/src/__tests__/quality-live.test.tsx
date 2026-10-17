/** Needs the live API: the checks run over the demo lender's seeded decisions. */
import { fetchFindings, runQualityChecks } from "../api";

describe("Data checks against the live API (VITE_USE_MOCKS=false)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";
  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: apiUrl, VITE_USE_MOCKS: "false" };
  });

  test("checking is repeatable and findings read back in plain words", async () => {
    const first = await runQualityChecks();
    expect(first.decisions_checked).toBeGreaterThanOrEqual(10);
    const again = await runQualityChecks();
    expect(again.new_findings).toBe(0);
    const open = await fetchFindings("open");
    expect(open.length).toBeLessThanOrEqual(again.open_total);
    for (const f of open) {
      expect(f.message.length).toBeGreaterThan(10);
      expect(["error", "warning", "info"]).toContain(f.severity);
    }
  });
});

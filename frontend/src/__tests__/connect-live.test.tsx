/** Needs the live API (same as the "Live API integration" tests): a real file goes in, a real explained decision comes out. */
import fs from "fs";
import path from "path";
import { approveMapping, fetchDecisionRecord, fetchReconstructionReport, proposeMapping, runImport } from "../api";

const UK = fs.readFileSync(path.join(__dirname, "../../../fixtures/sources/uk_bank.csv"), "utf-8");

describe("Connect your data against the live API (VITE_USE_MOCKS=false)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";
  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: apiUrl, VITE_USE_MOCKS: "false" };
  });

  test("an unfamiliar bank file is matched, approved, imported and explained", async () => {
    const proposal = await proposeMapping("uk-bank-ui-live", { format: "csv", content: UK });
    expect(proposal.gaps).toEqual([]);
    expect(proposal.preview?.valid).toBe(40);
    expect(Object.keys(proposal.spec.ignored_columns)).toEqual(expect.arrayContaining(["Customer Name", "Email"]));

    await approveMapping(proposal.mapping_id, "live test");

    const imported = await runImport(proposal.mapping_id, { format: "csv", content: UK });
    expect(imported.created + imported.replayed).toBe(40);
    expect(imported.rejected).toBe(0);

    const first = imported.results[0] as unknown as { decision_id: string };
    const record = await fetchDecisionRecord(first.decision_id);
    expect(record.recording_mode).toBe("recorded");
    expect(record.chain_valid).toBe(true);
    const report = await fetchReconstructionReport(first.decision_id);
    expect(report.recording_mode).toBe("recorded");
    expect(report.principal_reasons.length).toBeGreaterThan(0);
  });
});

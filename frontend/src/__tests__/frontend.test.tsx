import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../App";
import CaseList from "../components/CaseList";
import DecisionRecord from "../components/DecisionRecord";
import ReconstructionReport from "../components/ReconstructionReport";
import SyntheticBanner from "../components/SyntheticBanner";
import {
  fetchDecisionRecord,
  fetchDecisionsList,
  fetchReconstructionReport,
} from "../api";
import {
  MOCK_DECISION_RECORDS,
  MOCK_DECISIONS_LIST,
  MOCK_RECONSTRUCTION_REPORTS,
} from "../mocks/mockData";
import { ReconstructionReportContract } from "../types";

describe("DecisionTrace Frontend Unit & Component Tests", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  test("renders SYNTHETIC DATA - FOR DEMONSTRATION banner with icon and text", () => {
    render(<SyntheticBanner />);
    const banner = screen.getByTestId("synthetic-data-banner");
    expect(banner).toBeInTheDocument();
    expect(banner).toHaveTextContent("SYNTHETIC DATA - FOR DEMONSTRATION");
    expect(banner).toHaveTextContent("⚠");
  });

  test("renders citation chips and scrolls to matching evidence row in DecisionRecord on click", () => {
    const record = MOCK_DECISION_RECORDS["00000000-0000-0000-0000-000000000001"];
    const report =
      MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000001"];
    const onCitationClick = jest.fn();

    render(
      <div>
        <ReconstructionReport
          report={report}
          onCitationClick={onCitationClick}
        />
        <DecisionRecord record={record} />
      </div>
    );

    // Verify claim citation chip renders
    const dtiChip = screen.getByTestId("citation-chip-rule:dti_limit");
    expect(dtiChip).toBeInTheDocument();
    expect(dtiChip).toHaveTextContent("rule:dti_limit");

    // Verify matching evidence row exists in DecisionRecord
    const targetRow = screen.getByTestId("evidence-row-rule:dti_limit");
    expect(targetRow).toBeInTheDocument();

    // Click citation chip and verify scrollIntoView is invoked on the target row
    fireEvent.click(dtiChip);
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    expect(onCitationClick).toHaveBeenCalledWith("rule:dti_limit");

    // Also test policy section citation chip ("3.2 Debt-to-income limit") scrolling to the matching policy/rule row ("rule:dti_limit")
    const sectionChip = screen.getByTestId(
      "citation-chip-3.2 Debt-to-income limit"
    );
    expect(sectionChip).toBeInTheDocument();
    fireEvent.click(sectionChip);
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalledTimes(2);
    expect(onCitationClick).toHaveBeenLastCalledWith("rule:dti_limit");
  });

  test("renders green chain badge ONLY when chain_verified is true, and red chain warning when false", () => {
    const validReport =
      MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000001"];
    const tamperedReport =
      MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000008"];

    const { rerender } = render(<ReconstructionReport report={validReport} />);
    const validBadge = screen.getByTestId("chain-badge");
    expect(validBadge).toHaveAttribute("data-chain-verified", "true");
    expect(validBadge).toHaveClass("dt-badge-chain-ok");
    expect(validBadge).not.toHaveClass("dt-badge-chain-error");
    expect(validBadge).toHaveTextContent("✓");
    expect(validBadge).toHaveTextContent("Chain Verified");

    rerender(<ReconstructionReport report={tamperedReport} />);
    const warningBadge = screen.getByTestId("chain-badge");
    expect(warningBadge).toHaveAttribute("data-chain-verified", "false");
    expect(warningBadge).toHaveClass("dt-badge-chain-error");
    expect(warningBadge).not.toHaveClass("dt-badge-chain-ok");
    expect(warningBadge).toHaveTextContent("⚠");
    expect(warningBadge).toHaveTextContent(
      "Chain Warning: Tamper Detected / Unverified"
    );
  });

  test("renders guardrail line: 'N unsupported claims were removed by guardrails'", () => {
    const reportWithRejections: ReconstructionReportContract = {
      ...MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000001"],
      guardrail_rejections: 3,
    };

    const { rerender } = render(
      <ReconstructionReport report={reportWithRejections} />
    );
    const guardrailLine = screen.getByTestId("guardrail-line");
    expect(guardrailLine).toHaveTextContent(
      "3 unsupported claims were removed by guardrails"
    );

    const reportZeroRejections: ReconstructionReportContract = {
      ...MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000001"],
      guardrail_rejections: 0,
    };
    rerender(<ReconstructionReport report={reportZeroRejections} />);
    expect(screen.getByTestId("guardrail-line")).toHaveTextContent(
      "0 unsupported claims were removed by guardrails"
    );
  });

  test("renders evidence_unavailable, timeout, and error states distinctly", () => {
    const erasedReport =
      MOCK_RECONSTRUCTION_REPORTS["00000000-0000-0000-0000-000000000010"];
    const { rerender } = render(<ReconstructionReport report={erasedReport} />);

    // evidence_unavailable state
    const unavailableAlert = screen.getByTestId(
      "report-state-evidence-unavailable"
    );
    expect(unavailableAlert).toBeInTheDocument();
    expect(unavailableAlert).toHaveTextContent("evidence_unavailable");
    expect(unavailableAlert).toHaveTextContent("doc:payslip");

    // timeout state
    const timeoutReport: ReconstructionReportContract = {
      ...erasedReport,
      status: "timeout",
    };
    rerender(<ReconstructionReport report={timeoutReport} />);
    const timeoutAlert = screen.getByTestId("report-state-timeout");
    expect(timeoutAlert).toBeInTheDocument();
    expect(timeoutAlert).toHaveTextContent("status: timeout");

    // error state
    const errorReport: ReconstructionReportContract = {
      ...erasedReport,
      status: "error",
    };
    rerender(<ReconstructionReport report={errorReport} />);
    const errorAlert = screen.getByTestId("report-state-error");
    expect(errorAlert).toBeInTheDocument();
    expect(errorAlert).toHaveTextContent("status: error");
  });

  test("DecisionRecord renders source_id, timestamps, timeline, missing-evidence warnings, and 20-char override validation", async () => {
    const erasedRecord =
      MOCK_DECISION_RECORDS["00000000-0000-0000-0000-000000000010"];
    const mockSubmitOverride = jest.fn().mockResolvedValue(undefined);

    render(
      <DecisionRecord
        record={erasedRecord}
        onSubmitOverride={mockSubmitOverride}
      />
    );

    // Missing/erased evidence warning
    const missingWarning = screen.getByTestId("missing-evidence-warnings");
    expect(missingWarning).toBeInTheDocument();
    expect(missingWarning).toHaveTextContent("doc:payslip");
    expect(missingWarning).toHaveTextContent("erased_by_retention");

    // Timeline & source_id
    expect(screen.getByTestId("decision-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("source-id-doc:payslip")).toHaveTextContent(
      "document_store_v1"
    );

    // Override form 20-char validation
    const textarea = screen.getByLabelText(/Override Justification/i);
    const submitBtn = screen.getByTestId("override-submit-btn");

    // Try submitting with fewer than 20 characters
    fireEvent.change(textarea, { target: { value: "Too short reason" } });
    fireEvent.click(submitBtn);

    expect(screen.getByTestId("override-validation-error")).toHaveTextContent(
      /at least 20 characters/i
    );
    expect(mockSubmitOverride).not.toHaveBeenCalled();

    // Now enter 20+ characters and submit
    const validReason =
      "Verified secondary audited tax return confirming sufficient net income.";
    fireEvent.change(textarea, { target: { value: validReason } });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockSubmitOverride).toHaveBeenCalledWith({
        decision_id: erasedRecord.decision_id,
        new_outcome: "APPROVE",
        reason: validReason,
        actor_role: "senior_credit_underwriter",
      });
    });
  });

  test("CaseList supports keyboard navigation and works at 360px width", () => {
    window.innerWidth = 360;
    window.dispatchEvent(new Event("resize"));

    const onSelectCase = jest.fn();
    render(
      <CaseList
        cases={MOCK_DECISIONS_LIST}
        selectedDecisionId={MOCK_DECISIONS_LIST[0].decision_id}
        onSelectCase={onSelectCase}
      />
    );

    const secondItem = screen.getByTestId(
      `case-item-${MOCK_DECISIONS_LIST[1].decision_id}`
    );
    fireEvent.keyDown(secondItem, { key: "Enter" });
    expect(onSelectCase).toHaveBeenCalledWith(
      MOCK_DECISIONS_LIST[1].decision_id
    );
  });
});

describe("DecisionTrace Real API Integration Tests (VITE_USE_MOCKS=false)", () => {
  const apiUrl = process.env.VITE_API_URL || "http://127.0.0.1:8000";

  beforeAll(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_URL = apiUrl;
    globalThis.__DECISIONTRACE_ENV__ = {
      VITE_API_URL: apiUrl,
      VITE_USE_MOCKS: "false",
    };
    window.HTMLElement.prototype.scrollIntoView = jest.fn();
  });

  test("fetches seeded decisions from live API and reconstructs clear_decline, tampered_payload, erased_evidence, and override_case", async () => {
    const decisions = await fetchDecisionsList();
    expect(Array.isArray(decisions)).toBe(true);
    expect(decisions.length).toBeGreaterThanOrEqual(10);

    // 1. clear_decline (APP-0011 / LOAN-SCEN-0001)
    const clearDeclineItem = decisions.find(
      (d) =>
        d.application_id === "LOAN-SCEN-0001" || d.applicant_id === "APP-0011"
    );
    expect(clearDeclineItem).toBeDefined();
    const clearDeclineRecord = await fetchDecisionRecord(
      clearDeclineItem!.decision_id
    );
    const clearDeclineReport = await fetchReconstructionReport(
      clearDeclineItem!.decision_id
    );

    expect(clearDeclineReport.outcome).toBe("DECLINE");
    expect(clearDeclineReport.chain_verified).toBe(true);
    expect(clearDeclineReport.replay_verified).toBe(true);

    const { unmount: unmountClear } = render(
      <div>
        <ReconstructionReport report={clearDeclineReport} />
        <DecisionRecord record={clearDeclineRecord} />
      </div>
    );

    expect(screen.getByTestId("chain-badge")).toHaveAttribute(
      "data-chain-verified",
      "true"
    );
    expect(screen.getByTestId("guardrail-line")).toHaveTextContent(
      /unsupported claims were removed by guardrails/i
    );

    // Click a citation chip from the live reconstruction report and verify it scrolls to the live DecisionRecord row
    const firstRuleRef = clearDeclineReport.rule_checks[0]?.evidence_ref;
    expect(firstRuleRef).toBeDefined();
    const ruleChip = screen.getByTestId(`rule-citation-chip-${firstRuleRef}`);
    fireEvent.click(ruleChip);
    expect(window.HTMLElement.prototype.scrollIntoView).toHaveBeenCalled();
    unmountClear();

    // 2. tampered_payload (APP-0018 / LOAN-SCEN-0008) -> chain_verified=false -> red warning badge
    const tamperedItem = decisions.find(
      (d) =>
        d.application_id === "LOAN-SCEN-0008" || d.applicant_id === "APP-0018"
    );
    expect(tamperedItem).toBeDefined();
    const tamperedReport = await fetchReconstructionReport(
      tamperedItem!.decision_id
    );
    expect(tamperedReport.chain_verified).toBe(false);

    const { unmount: unmountTampered } = render(
      <ReconstructionReport report={tamperedReport} />
    );
    const tamperedBadge = screen.getByTestId("chain-badge");
    expect(tamperedBadge).toHaveAttribute("data-chain-verified", "false");
    expect(tamperedBadge).toHaveClass("dt-badge-chain-error");
    expect(tamperedBadge).toHaveTextContent(
      "Chain Warning: Tamper Detected / Unverified"
    );
    unmountTampered();

    // 3. erased_evidence (APP-0020 / LOAN-SCEN-0010) -> status=evidence_unavailable
    const erasedItem = decisions.find(
      (d) =>
        d.application_id === "LOAN-SCEN-0010" || d.applicant_id === "APP-0020"
    );
    expect(erasedItem).toBeDefined();
    const erasedRecord = await fetchDecisionRecord(erasedItem!.decision_id);
    const erasedReport = await fetchReconstructionReport(
      erasedItem!.decision_id
    );
    expect(erasedReport.status).toBe("evidence_unavailable");
    expect(erasedReport.missing_evidence.length).toBeGreaterThan(0);

    const { unmount: unmountErased } = render(
      <div>
        <ReconstructionReport report={erasedReport} />
        <DecisionRecord record={erasedRecord} />
      </div>
    );
    expect(
      screen.getByTestId("report-state-evidence-unavailable")
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("missing-evidence-warnings")
    ).toBeInTheDocument();
    unmountErased();

    // 4. override_case (APP-0017 / LOAN-SCEN-0007) -> human_override present
    const overrideItem = decisions.find(
      (d) =>
        d.application_id === "LOAN-SCEN-0007" || d.applicant_id === "APP-0017"
    );
    expect(overrideItem).toBeDefined();
    const overrideRecord = await fetchDecisionRecord(overrideItem!.decision_id);
    const overrideReport = await fetchReconstructionReport(
      overrideItem!.decision_id
    );
    expect(overrideReport.human_override).not.toBeNull();
    expect(overrideReport.human_override?.new_outcome).toBe("APPROVE");

    const { unmount: unmountOverride } = render(
      <div>
        <ReconstructionReport report={overrideReport} />
        <DecisionRecord record={overrideRecord} />
      </div>
    );
    expect(screen.getByTestId("report-human-override")).toHaveTextContent(
      "Overridden to APPROVE"
    );
    expect(screen.getByTestId("recorded-override-banner")).toHaveTextContent(
      "Overridden to APPROVE"
    );
    unmountOverride();
  });

  test("renders full App end-to-end against live API", async () => {
    render(<App />);

    // Banner is always present
    expect(screen.getByTestId("synthetic-data-banner")).toHaveTextContent(
      "SYNTHETIC DATA - FOR DEMONSTRATION"
    );

    // Wait for CaseList, DecisionRecord, and ReconstructionReport to load from live API
    await waitFor(
      () => {
        expect(screen.getByTestId("case-list-items")).toBeInTheDocument();
        expect(screen.getByTestId("decision-record")).toBeInTheDocument();
        expect(screen.getByTestId("reconstruction-report")).toBeInTheDocument();
      },
      { timeout: 10000 }
    );
  });
});

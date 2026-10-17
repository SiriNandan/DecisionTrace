import React, { useCallback, useEffect, useState } from "react";
import {
  fetchDecisionRecord,
  fetchDecisionsList,
  fetchReconstructionReport,
  submitDecisionOverride,
} from "./api";
import CaseList from "./components/CaseList";
import DecisionRecord from "./components/DecisionRecord";
import ReconstructionReport from "./components/ReconstructionReport";
import SyntheticBanner from "./components/SyntheticBanner";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  ReconstructionReportContract,
} from "./types";

export const App: React.FC = () => {
  const [cases, setCases] = useState<DecisionSummaryItem[]>([]);
  const [loadingCases, setLoadingCases] = useState<boolean>(true);
  const [casesError, setCasesError] = useState<string | null>(null);

  const [selectedDecisionId, setSelectedDecisionId] = useState<string | null>(
    null
  );
  const [decisionRecord, setDecisionRecord] =
    useState<DecisionDetailRecord | null>(null);
  const [loadingRecord, setLoadingRecord] = useState<boolean>(false);
  const [recordError, setRecordError] = useState<string | null>(null);

  const [reconReport, setReconReport] =
    useState<ReconstructionReportContract | null>(null);
  const [loadingRecon, setLoadingRecon] = useState<boolean>(false);
  const [reconError, setReconError] = useState<string | null>(null);

  const [highlightedEvidenceRef, setHighlightedEvidenceRef] = useState<
    string | null
  >(null);

  const loadCases = useCallback(async () => {
    setLoadingCases(true);
    setCasesError(null);
    try {
      const list = await fetchDecisionsList();
      setCases(list);
      setSelectedDecisionId((prev) =>
        prev ? prev : list.length > 0 ? list[0].decision_id : null
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setCasesError(msg);
    } finally {
      setLoadingCases(false);
    }
  }, []);

  const loadSelectedCaseDetail = useCallback(async (decisionId: string) => {
    setLoadingRecord(true);
    setRecordError(null);
    setLoadingRecon(true);
    setReconError(null);
    setHighlightedEvidenceRef(null);

    try {
      const rec = await fetchDecisionRecord(decisionId);
      setDecisionRecord(rec);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setRecordError(msg);
      setDecisionRecord(null);
    } finally {
      setLoadingRecord(false);
    }

    try {
      const rep = await fetchReconstructionReport(decisionId);
      setReconReport(rep);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setReconError(msg);
      setReconReport(null);
    } finally {
      setLoadingRecon(false);
    }
  }, []);

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  useEffect(() => {
    if (selectedDecisionId) {
      void loadSelectedCaseDetail(selectedDecisionId);
    }
  }, [selectedDecisionId, loadSelectedCaseDetail]);

  const handleOverrideSubmit = async (payload: CreateOverridePayload) => {
    await submitDecisionOverride(payload);
    await loadCases();
    await loadSelectedCaseDetail(payload.decision_id);
  };

  const handleManualReconstruct = async (decisionId: string) => {
    setLoadingRecon(true);
    setReconError(null);
    try {
      const rep = await fetchReconstructionReport(decisionId);
      setReconReport(rep);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setReconError(msg);
    } finally {
      setLoadingRecon(false);
    }
  };

  return (
    <div className="dt-app-shell">
      <SyntheticBanner />

      <header className="dt-app-header">
        <div className="dt-header-inner">
          <h1 className="dt-app-title">
            DecisionTrace — Audit &amp; Explanation Workbench
          </h1>
          <p className="dt-app-tagline">
            Hash-chained decision-time evidence capture and cited explanation
            reconstruction that supports audit and explanation and is
            tamper-evident against a retained reference.
          </p>
        </div>
      </header>

      <main className="dt-main-layout">
        <aside className="dt-sidebar-col">
          <CaseList
            cases={cases}
            selectedDecisionId={selectedDecisionId}
            onSelectCase={(id) => setSelectedDecisionId(id)}
            loading={loadingCases}
            error={casesError}
            onRefresh={() => void loadCases()}
          />
        </aside>

        <div className="dt-content-col">
          {loadingRecord && (
            <div className="dt-status-box dt-status-info" role="status">
              <span aria-hidden="true">⏳ </span>Loading preserved decision
              record...
            </div>
          )}

          {recordError && (
            <div className="dt-status-box dt-status-error" role="alert">
              <span aria-hidden="true">⛔ [ERROR] </span>
              <span>Failed to load decision record: {recordError}</span>
            </div>
          )}

          {reconError && (
            <div className="dt-status-box dt-status-error" role="alert">
              <span aria-hidden="true">⛔ [RECONSTRUCTION ERROR] </span>
              <span>Failed to reconstruct explanation: {reconError}</span>
            </div>
          )}

          {reconReport && (
            <ReconstructionReport
              report={reconReport}
              onCitationClick={(ref) => setHighlightedEvidenceRef(ref)}
            />
          )}

          {decisionRecord && (
            <DecisionRecord
              record={decisionRecord}
              highlightedEvidenceRef={highlightedEvidenceRef}
              onSubmitOverride={handleOverrideSubmit}
              onTriggerReconstruct={(id) => void handleManualReconstruct(id)}
              reconstructing={loadingRecon}
            />
          )}
        </div>
      </main>
    </div>
  );
};

export default App;


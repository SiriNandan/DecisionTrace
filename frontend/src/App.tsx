import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDecisionRecord,
  fetchDecisionsList,
  fetchReconstructionReport,
  needsSignIn,
  submitDecisionOverride,
} from "./api";
import DecisionView from "./components/DecisionView";
import HomeView from "./components/HomeView";
import { ErrorBoundary, OfflineBar, SiteFooter, SiteNav } from "./components/SiteChrome";
import SyntheticBanner from "./components/SyntheticBanner";
import WelcomeDialog from "./components/WelcomeDialog";
import { isDemoFixture } from "./lib/plain";
import { navigate, readRoute, Route } from "./lib/router";
import { applyTheme, markTourSeen, readActivity, readTheme, Theme, tourSeen, track } from "./lib/usage";
import AboutPage from "./pages/AboutPage";
import ConnectPage from "./pages/ConnectPage";
import HelpPage from "./pages/HelpPage";
import InsightsPage from "./pages/InsightsPage";
import LegalPage from "./pages/LegalPage";
import NotFoundPage from "./pages/NotFoundPage";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  ReconstructionReportContract,
} from "./types";

const RECENT_KEY = "clearly:last-decision";

const readRecent = (): { id: string; label: string } | null => {
  try {
    const raw = window.localStorage.getItem(RECENT_KEY);
    return raw ? (JSON.parse(raw) as { id: string; label: string }) : null;
  } catch {
    return null;
  }
};

const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const TITLES: Record<string, string> = {
  home: "Loan decisions, explained",
  about: "How it works",
  help: "Help",
  insights: "Insights",
  privacy: "Privacy",
  terms: "Terms of use",
  accessibility: "Accessibility",
  notfound: "Page not found",
  decision: "Your decision, explained",
};

export const App: React.FC = () => {
  const [route, setRoute] = useState<Route>(() => readRoute());
  const [allCases, setAllCases] = useState<DecisionSummaryItem[]>([]);
  const [loadingCases, setLoadingCases] = useState(true);
  const [casesError, setCasesError] = useState<string | null>(null);

  const [record, setRecord] = useState<DecisionDetailRecord | null>(null);
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [signInNeeded, setSignInNeeded] = useState(false);
  const [report, setReport] = useState<ReconstructionReportContract | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const [recent, setRecent] = useState(readRecent);
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [tourOpen, setTourOpen] = useState(() => readRoute().page === "home" && !tourSeen());
  const [activity, setActivity] = useState(readActivity);
  const mainRef = useRef<HTMLElement>(null);

  const cases = useMemo(() => allCases.filter((c) => !isDemoFixture(c)), [allCases]);

  const loadCases = useCallback(async () => {
    setLoadingCases(true);
    setCasesError(null);
    setSignInNeeded(false);
    try {
      setAllCases(await fetchDecisionsList());
    } catch (e) {
      setCasesError(errMsg(e));
      setSignInNeeded(needsSignIn(e));
    } finally {
      setLoadingCases(false);
    }
  }, []);

  const loadReport = useCallback(async (id: string) => {
    setLoadingReport(true);
    setReportError(null);
    try {
      setReport(await fetchReconstructionReport(id));
    } catch (e) {
      setReport(null);
      setReportError(errMsg(e));
    } finally {
      setLoadingReport(false);
    }
  }, []);

  const loadDecision = useCallback(
    async (id: string) => {
      setLoadingRecord(true);
      setRecordError(null);
      setSignInNeeded(false);
      setRecord(null);
      setReport(null);
      void loadReport(id);
      try {
        setRecord(await fetchDecisionRecord(id));
      } catch (e) {
        setRecordError(errMsg(e));
        setSignInNeeded(needsSignIn(e));
      } finally {
        setLoadingRecord(false);
      }
    },
    [loadReport]
  );

  useEffect(() => {
    void loadCases();
  }, [loadCases]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const onPop = () => setRoute(readRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // Per-route: title, scroll to top, move focus to the page heading, load data, count the view.
  useEffect(() => {
    document.title = `${TITLES[route.page]} · Clearly`;
    window.scrollTo?.(0, 0);
    if (route.page === "decision" && route.id) {
      void loadDecision(route.id);
      setActivity(track("view", route.id));
    } else {
      setActivity(track("view"));
    }
    const t = window.setTimeout(() => mainRef.current?.querySelector<HTMLElement>("h1")?.focus({ preventScroll: true }), 50);
    return () => window.clearTimeout(t);
  }, [route.page, route.id, loadDecision]);

  useEffect(() => {
    if (!record) return;
    const next = { id: record.decision_id, label: `${record.applicant_id} · ${record.application_id}` };
    try {
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
    setRecent(next);
  }, [record]);

  const closeTour = useCallback(() => {
    markTourSeen();
    setTourOpen(false);
  }, []);

  const submitReview = async (payload: CreateOverridePayload) => {
    await submitDecisionOverride(payload);
    setActivity(track("review", payload.decision_id));
    await Promise.all([loadCases(), loadDecision(payload.decision_id)]);
  };

  const refreshActivity = () => setActivity(readActivity());

  return (
    <div className="cl-app">
      <a className="cl-skip" href="#main-content">Skip to content</a>
      <SyntheticBanner />
      <OfflineBar />
      <SiteNav page={route.page} theme={theme} onTheme={setTheme} />

      <main id="main-content" className="cl-main" tabIndex={-1} ref={mainRef}>
        <ErrorBoundary>
          {route.page === "home" && (
            <HomeView
              cases={cases}
              loading={loadingCases}
              error={casesError}
              signInNeeded={signInNeeded}
              onRetry={() => void loadCases()}
              onOpen={(id) => navigate({ page: "decision", id })}
              recent={recent}
              onSearch={() => setActivity(track("search"))}
            />
          )}

          {route.page === "decision" && loadingRecord && !record && (
            <div className="cl-loading" role="status" aria-label="Loading decision">
              <div className="cl-skeleton cl-skel-hero" />
              <div className="cl-skeleton cl-skel-text" />
              <div className="cl-skeleton cl-skel-text" />
            </div>
          )}

          {route.page === "decision" && recordError && (
            <div className="cl-empty" role="alert">
              <div className="cl-empty-art" aria-hidden="true">⌀</div>
              <h1 className="cl-h2" tabIndex={-1}>{signInNeeded ? "Sign-in is needed to see this" : "We couldn’t find that decision"}</h1>
              <p>{signInNeeded ? "Your sign-in is missing or has expired. Please sign in again, or ask your lender for a new link." : "It may have been removed, or the link may be wrong."}</p>
              <button type="button" className="cl-btn cl-btn-primary" onClick={() => navigate({ page: "home" })}>Browse all decisions</button>
            </div>
          )}

          {route.page === "decision" && record && (
            <DecisionView
              record={record}
              report={report}
              reportLoading={loadingReport}
              reportError={reportError}
              onRetryReport={() => void loadReport(record.decision_id)}
              onBack={() => navigate({ page: "home" })}
              onSubmitReview={submitReview}
              onTrack={(e) => setActivity(track(e, record.decision_id))}
            />
          )}

          {route.page === "insights" && (
            <InsightsPage cases={cases} loading={loadingCases} error={casesError} activity={activity} onActivityReset={refreshActivity} />
          )}
          {route.page === "about" && <AboutPage />}
          {route.page === "connect" && <ConnectPage />}
          {route.page === "help" && <HelpPage onReplayTour={() => setTourOpen(true)} />}
          {(route.page === "privacy" || route.page === "terms" || route.page === "accessibility") && <LegalPage kind={route.page} />}
          {route.page === "notfound" && <NotFoundPage />}
        </ErrorBoundary>
      </main>

      <SiteFooter onTour={() => setTourOpen(true)} />
      {tourOpen && <WelcomeDialog onClose={closeTour} />}
    </div>
  );
};

export default App;

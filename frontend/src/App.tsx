import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchDecisionRecord,
  fetchDecisionsList,
  fetchMe,
  fetchReconstructionReport,
  ProblemKind,
  problemKind,
  submitDecisionOverride,
} from "./api";
import { hasCredential } from "./config";
import { extractClaimToken, getSession, onSessionChange, signInCustomer, signOut } from "./lib/session";
import DecisionView from "./components/DecisionView";
import HomeView from "./components/HomeView";
import ServiceProblem from "./components/ServiceProblem";
import StaffTools from "./components/StaffTools";
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
import SignalsPage from "./pages/SignalsPage";
import SignInPage from "./pages/SignInPage";
import QualityPage from "./pages/QualityPage";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  DecisionSummaryItem,
  Me,
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

/** A customer's link looks like `?d=<decision>&t=<secret>`. Keep the secret for this tab and take it out of the address bar. */
function consumeLinkFromUrl(): void {
  try {
    const q = new URLSearchParams(window.location.search);
    const t = q.get("t");
    if (!t) return;
    const token = extractClaimToken(`?t=${t}`);
    if (token) {
      signInCustomer(token);
      window.localStorage.removeItem(RECENT_KEY);  // nothing a previous person looked at is shown to the next
    }
    q.delete("t");
    const rest = q.toString();
    window.history.replaceState({}, "", `${window.location.pathname}${rest ? `?${rest}` : ""}`);
  } catch {
    /* no window or history: nothing to do */
  }
}

const TITLES: Record<string, string> = {
  home: "Loan decisions, explained",
  about: "How it works",
  help: "Help",
  insights: "Insights",
  signin: "Sign in",
  connect: "Connect your data",
  quality: "Data checks",
  signals: "Risk signals",
  privacy: "Privacy",
  terms: "Terms of use",
  accessibility: "Accessibility",
  notfound: "Page not found",
  decision: "Your decision, explained",
};

export const App: React.FC = () => {
  const [route, setRoute] = useState<Route>(() => {
    consumeLinkFromUrl();
    return readRoute();
  });
  const [me, setMe] = useState<Me | null>(null);
  const [meReady, setMeReady] = useState(false);
  const [casesProblem, setCasesProblem] = useState<ProblemKind>("other");
  const [recordProblem, setRecordProblem] = useState<ProblemKind>("other");
  const [allCases, setAllCases] = useState<DecisionSummaryItem[]>([]);
  const [loadingCases, setLoadingCases] = useState(true);
  const [casesError, setCasesError] = useState<string | null>(null);

  const [record, setRecord] = useState<DecisionDetailRecord | null>(null);
  const [loadingRecord, setLoadingRecord] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const [report, setReport] = useState<ReconstructionReportContract | null>(null);
  const [loadingReport, setLoadingReport] = useState(false);
  const [reportError, setReportError] = useState<string | null>(null);

  const [recent, setRecent] = useState(() => (getSession().mode === "customer" ? null : readRecent()));
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [tourOpen, setTourOpen] = useState(() => readRoute().page === "home" && !tourSeen());
  const [activity, setActivity] = useState(readActivity);
  const mainRef = useRef<HTMLElement>(null);
  const routeRef = useRef(route);
  routeRef.current = route;
  // Only the newest request of each kind may update the screen, so a slow answer for a previous
  // decision, or for a person who has since signed out, can never overwrite what is shown now.
  const seq = useRef({ cases: 0, record: 0, report: 0 });

  const cases = useMemo(() => allCases.filter((c) => !isDemoFixture(c)), [allCases]);

  const loadCases = useCallback(async () => {
    const mine = ++seq.current.cases;
    setLoadingCases(true);
    setCasesError(null);
    if (!hasCredential()) {
      // Nothing to sign in with yet: say so, instead of sending a request that has to fail.
      setAllCases([]);
      setCasesProblem("signin");
      setCasesError("Sign-in is needed to see these decisions.");
      setLoadingCases(false);
      return;
    }
    try {
      const list = await fetchDecisionsList();
      if (mine === seq.current.cases) setAllCases(list);
    } catch (e) {
      if (mine !== seq.current.cases) return;
      setCasesError(errMsg(e));
      setCasesProblem(problemKind(e));
    } finally {
      if (mine === seq.current.cases) setLoadingCases(false);
    }
  }, []);

  const loadReport = useCallback(async (id: string) => {
    const mine = ++seq.current.report;
    setLoadingReport(true);
    setReportError(null);
    try {
      const r = await fetchReconstructionReport(id);
      if (mine === seq.current.report) setReport(r);
    } catch (e) {
      if (mine !== seq.current.report) return;
      setReport(null);
      setReportError(errMsg(e));
    } finally {
      if (mine === seq.current.report) setLoadingReport(false);
    }
  }, []);

  const loadDecision = useCallback(
    async (id: string) => {
      const mine = ++seq.current.record;
      setLoadingRecord(true);
      setRecordError(null);
      setRecord(null);
      setReport(null);
      void loadReport(id);
      try {
        const rec = await fetchDecisionRecord(id);
        if (mine === seq.current.record) setRecord(rec);
      } catch (e) {
        if (mine !== seq.current.record) return;
        setRecordError(errMsg(e));
        setRecordProblem(problemKind(e));
      } finally {
        if (mine === seq.current.record) setLoadingRecord(false);
      }
    },
    [loadReport]
  );

  const loadMe = useCallback(async () => {
    if (!hasCredential()) {
      setMe(null);
      setMeReady(true);
      return;
    }
    try {
      setMe(await fetchMe());
    } catch {
      setMe(null);
    }
    setMeReady(true);
  }, []);

  useEffect(() => {
    void loadMe();
    void loadCases();
  }, [loadMe, loadCases]);

  // Signing in or out (here or on the sign-in page) refreshes who we are and what is shown.
  useEffect(
    () =>
      onSessionChange(() => {
        // Whoever was signed in before must not leave anything behind for whoever comes next.
        seq.current = { cases: seq.current.cases + 1, record: seq.current.record + 1, report: seq.current.report + 1 };
        setAllCases([]); setRecord(null); setReport(null); setRecordError(null); setReportError(null); setMe(null);
        try { window.localStorage.removeItem(RECENT_KEY); } catch { /* storage unavailable */ }
        setRecent(null);
        void loadMe();
        void loadCases();
        const r = routeRef.current;
        if (r.page === "decision" && r.id) void loadDecision(r.id);
      }),
    [loadMe, loadCases, loadDecision]
  );

  // A customer only ever has one decision: go straight to it.
  useEffect(() => {
    if (me?.kind === "customer" && me.decision_id && route.page === "home") navigate({ page: "decision", id: me.decision_id });
  }, [me, route.page]);

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
    if (!record || getSession().mode === "customer") return;  // a customer's decision is never remembered on this computer
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
      <SiteNav
        page={route.page}
        theme={theme}
        onTheme={setTheme}
        me={me}
        onSignOut={() => {
          signOut();
          navigate({ page: "signin" });
        }}
      />

      <main id="main-content" className="cl-main" tabIndex={-1} ref={mainRef}>
        <ErrorBoundary>
          {route.page === "home" && (
            <HomeView
              cases={cases}
              loading={loadingCases}
              error={casesError}
              problem={casesProblem}
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

          {route.page === "decision" && recordError && (recordProblem === "other" && /couldn.t find/i.test(recordError) ? (
            <div className="cl-empty" role="alert">
              <div className="cl-empty-art" aria-hidden="true">⌀</div>
              <h1 className="cl-h2" tabIndex={-1}>We couldn’t find that decision</h1>
              <p>It may have been removed, or the link may be wrong.</p>
              {me?.kind !== "customer" && (
                <button type="button" className="cl-btn cl-btn-primary" onClick={() => navigate({ page: "home" })}>Browse all decisions</button>
              )}
            </div>
          ) : (
            <ServiceProblem kind={recordProblem} what="decision" onRetry={() => route.id && void loadDecision(route.id)} />
          ))}

          {route.page === "decision" && record && (
            <DecisionView
              record={record}
              report={report}
              reportLoading={loadingReport}
              reportError={reportError}
              onRetryReport={() => void loadReport(record.decision_id)}
              onBack={() => navigate({ page: "home" })}
              onSubmitReview={submitReview}
              customer={me?.kind === "customer"}
              staffTools={me?.kind === "staff" ? <StaffTools record={record} me={me} /> : undefined}
              onTrack={(e) => setActivity(track(e, record.decision_id))}
            />
          )}

          {route.page === "insights" && (
            <InsightsPage cases={cases} loading={loadingCases} error={casesError} problem={casesProblem} activity={activity} onActivityReset={refreshActivity} />
          )}
          {route.page === "about" && <AboutPage />}
          {(route.page === "connect" || route.page === "quality" || route.page === "signals") &&
            (me?.kind === "staff" ? (
              <>
                {route.page === "connect" && <ConnectPage />}
                {route.page === "quality" && <QualityPage />}
                {route.page === "signals" && <SignalsPage />}
              </>
            ) : meReady ? (
              <ServiceProblem kind="signin" what="page" offerSample={false} />
            ) : null)}
          {route.page === "signin" && <SignInPage me={me} onChanged={() => { void loadMe(); void loadCases(); }} />}
          {route.page === "help" && <HelpPage onReplayTour={() => setTourOpen(true)} />}
          {(route.page === "privacy" || route.page === "terms" || route.page === "accessibility") && <LegalPage kind={route.page} />}
          {route.page === "notfound" && <NotFoundPage />}
        </ErrorBoundary>
      </main>

      <SiteFooter onTour={() => setTourOpen(true)} staff={me?.kind === "staff"} customer={me?.kind === "customer"} />
      {tourOpen && <WelcomeDialog onClose={closeTour} />}
    </div>
  );
};

export default App;

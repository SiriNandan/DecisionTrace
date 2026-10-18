import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  fetchConsents,
  fetchDecisionRecord,
  fetchDecisionSeal,
  fetchDecisionsPage,
  fetchMe,
  needsSignIn,
  fetchReconstructionReport,
  ProblemKind,
  problemKind,
  setAiSummaryChoice,
  submitDecisionOverride,
} from "./api";
import { hasCredential, isUseMocksEnabled } from "./config";
import { extractClaimToken, getSession, onSessionChange, signInCustomer, signOut } from "./lib/session";
import DecisionView from "./components/DecisionView";
import HomeView from "./components/HomeView";
import ChoicesPanel from "./components/ChoicesPanel";
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
import StaffHome from "./components/StaffHome";
import TeamPage from "./pages/TeamPage";
import ProfilePage from "./pages/ProfilePage";
import HelpPage from "./pages/HelpPage";
import InsightsPage from "./pages/InsightsPage";
import LegalPage from "./pages/LegalPage";
import NotFoundPage from "./pages/NotFoundPage";
import SealsPage from "./pages/SealsPage";
import SignalsPage from "./pages/SignalsPage";
import SignInPage from "./pages/SignInPage";
import QualityPage from "./pages/QualityPage";
import {
  CreateOverridePayload,
  DecisionDetailRecord,
  ConsentState,
  DecisionSummaryItem,
  Me,
  ReconstructionReportContract,
  SealProof,
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
  seals: "Record seals",
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
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);
  const [consentFailed, setConsentFailed] = useState(false);
  const [consentRetry, setConsentRetry] = useState(0);
  const [seal, setSeal] = useState<{ id: string; data: SealProof } | null>(null);
  const [consent, setConsent] = useState<{ id: string; data: ConsentState } | null>(null);
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
    setNextCursor(null);  // a page marker belongs to whoever asked for it and to this list only
    setMoreError(null);
    if (!hasCredential()) {
      // Nothing to sign in with yet: say so, instead of sending a request that has to fail.
      setAllCases([]);
      setCasesProblem("signin");
      setCasesError("Sign-in is needed to see these decisions.");
      setLoadingCases(false);
      return;
    }
    try {
      const page = await fetchDecisionsPage(null);
      if (mine === seq.current.cases) {
        setAllCases(page.items);
        setNextCursor(page.next);
      }
    } catch (e) {
      if (mine !== seq.current.cases) return;
      setCasesError(errMsg(e));
      setCasesProblem(problemKind(e));
    } finally {
      if (mine === seq.current.cases) setLoadingCases(false);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    const mine = seq.current.cases;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await fetchDecisionsPage(nextCursor);
      if (mine !== seq.current.cases) return;
      setAllCases((prev) => [...prev, ...page.items.filter((d) => !prev.some((p) => p.decision_id === d.decision_id))]);
      setNextCursor(page.next);
    } catch (e) {
      if (mine !== seq.current.cases) return;  // the person has changed since: this failure isn't theirs
      setMoreError(needsSignIn(e) ? "Your sign-in is missing or has expired. Please sign in again." : "We couldn’t load older decisions. The ones above are still here. Try again.");
    } finally {
      if (mine === seq.current.cases) setLoadingMore(false);
    }
  }, [nextCursor, loadingMore]);

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
        setAllCases([]); setNextCursor(null); setMoreError(null); setRecord(null); setReport(null); setRecordError(null); setReportError(null); setMe(null);
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

  // The record's seal and the customer's choices come with it; neither exists for the built-in sample decisions.
  useEffect(() => {
    setSeal(null);
    setConsent(null);
    setConsentFailed(false);
    if (!record || isUseMocksEnabled()) return;
    const id = record.decision_id;
    let current = true;  // ignore answers that arrive after the person has moved on
    // Only believe an answer that has the expected shape; anything else just leaves the panel out.
    fetchDecisionSeal(id).then((data) => current && typeof data?.sealed === "boolean" && setSeal({ id, data })).catch(() => undefined);
    fetchConsents(id)
      .then((data) => {
        if (!current) return;
        if (data && typeof data.ai_summary_on === "boolean") setConsent({ id, data });
        else setConsentFailed(true);
      })
      .catch(() => current && setConsentFailed(true));
    return () => { current = false; };
  }, [record, consentRetry]);

  const chooseAiSummary = async (granted: boolean) => {
    if (!record) return;
    const id = record.decision_id;
    const data = await setAiSummaryChoice(id, granted);
    if (routeRef.current.id !== id) return;  // they have moved to another decision while this was saving
    setConsent({ id, data });
    void loadReport(id);  // the explanation may now be written (or not) with the model
  };

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
          {route.page === "home" && me?.kind === "staff" && !me.scopes.includes("decisions:read") && <StaffHome me={me} />}
          {route.page === "home" && !(me?.kind === "staff" && !me.scopes.includes("decisions:read")) && (
            <HomeView
              cases={cases}
              loading={loadingCases}
              error={casesError}
              problem={casesProblem}
              hasMore={nextCursor !== null}
              moreError={moreError}
              loadingMore={loadingMore}
              onLoadMore={() => void loadMore()}
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
              seal={seal && seal.id === record.decision_id ? seal.data : undefined}
              choices={
                consent && consent.id === record.decision_id && me && me.scopes.includes("consent:write") ? (
                  <ChoicesPanel state={consent.data} onChoose={chooseAiSummary} onBehalf={me.kind === "staff"} />
                ) : consentFailed && me && me.scopes.includes("consent:write") ? (
                  <div className="cl-card-rail" data-testid="choices-failed">
                    <h2 className="cl-h3">{me.kind === "staff" ? "The customer’s choices" : "Your choices"}</h2>
                    <p className="cl-muted">We couldn’t load the choices just now.</p>
                    <button type="button" className="cl-btn cl-btn-ghost" onClick={() => setConsentRetry((n) => n + 1)}>Try again</button>
                  </div>
                ) : undefined
              }
              onTrack={(e) => setActivity(track(e, record.decision_id))}
            />
          )}

          {route.page === "insights" && (
            <InsightsPage cases={cases} loading={loadingCases} error={casesError} problem={casesProblem} activity={activity} onActivityReset={refreshActivity} />
          )}
          {route.page === "about" && <AboutPage />}
          {route.page === "profile" && (me?.kind === "staff" ? <ProfilePage me={me} /> : <ServiceProblem kind="signin" what="page" offerSample={false} />)}
          {(route.page === "connect" || route.page === "team" || route.page === "quality" || route.page === "signals" || route.page === "seals") &&
            (me?.kind === "staff" ? (
              <>
                {route.page === "connect" && <ConnectPage me={me} />}
                {route.page === "team" && (me.scopes.includes("team:manage") ? <TeamPage /> : <ServiceProblem kind="signin" what="page" offerSample={false} />)}
                {route.page === "quality" && <QualityPage />}
                {route.page === "signals" && <SignalsPage />}
                {route.page === "seals" && <SealsPage />}
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

      <SiteFooter onTour={() => setTourOpen(true)} staff={me?.kind === "staff"} customer={me?.kind === "customer"} scopes={me?.kind === "staff" ? me.scopes : undefined} />
      {tourOpen && <WelcomeDialog onClose={closeTour} />}
    </div>
  );
};

export default App;

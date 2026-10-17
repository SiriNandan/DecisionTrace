import React, { useEffect, useState } from "react";
import { Link, Page } from "../lib/router";
import { Theme } from "../lib/usage";

const NAV: Array<[Page, string]> = [["home", "Decisions"], ["insights", "Insights"], ["about", "How it works"], ["help", "Help"]];

const THEME_NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const THEME_LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const THEME_ICON: Record<Theme, string> = { system: "◐", light: "☀", dark: "☾" };

export const SiteNav: React.FC<{ page: Page; theme: Theme; onTheme: (t: Theme) => void }> = ({ page, theme, onTheme }) => (
  <header className="cl-nav">
    <Link to={{ page: "home" }} className="cl-brand" aria-label="Clearly home">
      <svg viewBox="0 0 32 32" aria-hidden="true" width="28" height="28">
        <circle cx="16" cy="16" r="14" className="cl-brand-ring" />
        <path d="M8 17 L14 23 L24 10" className="cl-brand-tick" />
      </svg>
      <span>Clearly</span>
    </Link>
    <nav className="cl-nav-links" aria-label="Main">
      {NAV.map(([p, label]) => (
        <Link key={p} to={{ page: p }} aria-current={(page === p || (p === "home" && page === "decision")) ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
    <button type="button" className="cl-theme" onClick={() => onTheme(THEME_NEXT[theme])} aria-label={`Colour theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[THEME_NEXT[theme]]}`} title={`Theme: ${THEME_LABEL[theme]}`}>
      <span aria-hidden="true">{THEME_ICON[theme]}</span><span className="cl-theme-l">{THEME_LABEL[theme]}</span>
    </button>
  </header>
);

export const SiteFooter: React.FC<{ onTour: () => void }> = ({ onTour }) => (
  <footer className="cl-footer">
    <div className="cl-footer-grid">
      <div>
        <p className="cl-footer-brand">Clearly</p>
        <p className="cl-muted">Loan decisions, explained. It shows how each decision was reached. It never makes or changes decisions on its own.</p>
      </div>
      <nav aria-label="Explore"><h2 className="cl-footer-h">Explore</h2>
        <Link to={{ page: "home" }}>Decisions</Link><Link to={{ page: "insights" }}>Insights</Link></nav>
      <nav aria-label="Learn"><h2 className="cl-footer-h">Learn</h2>
        <Link to={{ page: "about" }}>How it works</Link><Link to={{ page: "help" }}>Help &amp; FAQ</Link>
        <button type="button" className="cl-footer-btn" onClick={onTour}>Welcome tour</button></nav>
      <nav aria-label="Legal"><h2 className="cl-footer-h">Legal</h2>
        <Link to={{ page: "privacy" }}>Privacy</Link><Link to={{ page: "terms" }}>Terms</Link><Link to={{ page: "accessibility" }}>Accessibility</Link></nav>
    </div>
    <p className="cl-footer-fine">© {new Date().getFullYear()} Clearly demonstration. All people, credit reports and decisions are fictional.</p>
  </footer>
);

export const OfflineBar: React.FC = () => {
  const [online, setOnline] = useState(typeof navigator === "undefined" ? true : navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    window.addEventListener("online", on); window.addEventListener("offline", off);
    return () => { window.removeEventListener("online", on); window.removeEventListener("offline", off); };
  }, []);
  if (online) return null;
  return <div className="cl-offline" role="status">You’re offline. Some things may not load until you’re back online.</div>;
};

export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.error(error); }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="cl-empty" role="alert" style={{ minHeight: "60vh", alignContent: "center" }}>
        <div className="cl-empty-art" aria-hidden="true">!</div>
        <h1 className="cl-h2">Something went wrong</h1>
        <p>We’re sorry. Please reload the page and try again.</p>
        <button type="button" className="cl-btn cl-btn-primary" onClick={() => window.location.reload()}>Reload</button>
      </div>
    );
  }
}

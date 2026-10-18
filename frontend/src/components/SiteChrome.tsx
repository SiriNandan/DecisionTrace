import React, { useEffect, useRef, useState } from "react";
import { Link, Page } from "../lib/router";
import { Theme } from "../lib/usage";
import type { Me } from "../types";
import { ROLE_LABEL } from "../pages/SignInPage";

const NAV: Array<[Page, string]> = [["home", "Decisions"], ["insights", "Insights"], ["about", "How it works"], ["help", "Help"]];
// A customer opening their own link sees only their decision and the help pages.
const staffNav = (scopes: string[]): Array<[Page, string]> => [
  ...NAV.slice(0, 2),
  ...(scopes.includes("signals:read") ? ([["customers", "Customers"]] as Array<[Page, string]>) : []),
  ...(scopes.includes("mappings:read") ? ([["connect", "Connect your data"]] as Array<[Page, string]>) : []),
  ...NAV.slice(2),
];

const CUSTOMER_NAV: Array<[Page, string]> = [["about", "How it works"], ["help", "Help"]];

const THEME_NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const THEME_LABEL: Record<Theme, string> = { system: "Auto", light: "Light", dark: "Dark" };
const THEME_ICON: Record<Theme, string> = { system: "◐", light: "☀", dark: "☾" };

/** First name for the avatar: from the person's name, else their role (the demo key has no person). */
export function firstName(me: Me): string {
  const n = me.label.trim();
  const word = !n || n === "DT_API_KEY" ? (ROLE_LABEL[me.role] ?? "You") : n.split(/[\s,]+/)[0];
  return word.charAt(0).toUpperCase() + word.slice(1);
}

const roleText = (me: Me) => (me.roles?.length ? me.roles : [me.role]).map((r) => ROLE_LABEL[r] ?? r).join(" + ");

const ProfileMenu: React.FC<{ me: Me; onSignOut?: () => void }> = ({ me, onSignOut }) => {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  const name = firstName(me);
  return (
    <div className="cl-profile" ref={box}>
      <button type="button" className="cl-avatar-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="profile-button">
        <span className="cl-avatar" aria-hidden="true">{name.charAt(0)}</span>
        <span className="cl-avatar-name">{name}</span>
        <span className="cl-sr">{me.kind === "sample" ? "Sample decisions" : me.kind === "customer" ? "Your decision" : `${me.tenant_name} · ${roleText(me)}`}</span>
      </button>
      {open && (
        <div className="cl-menu" role="menu">
          <p className="cl-menu-head"><strong>{name}</strong><small>{me.kind === "staff" ? `${me.tenant_name} · ${roleText(me)}` : "Your decision"}</small></p>
          {me.kind === "staff" && (
            <Link to={{ page: "profile" }} role="menuitem" className="cl-menu-item" onClick={() => setOpen(false)}>View profile</Link>
          )}
          <button type="button" role="menuitem" className="cl-menu-item" onClick={() => { setOpen(false); onSignOut?.(); }}>
            {me.kind === "sample" ? "Leave samples" : "Sign out"}
          </button>
        </div>
      )}
    </div>
  );
};

export const SiteNav: React.FC<{ page: Page; theme: Theme; onTheme: (t: Theme) => void; me?: Me | null; onSignOut?: () => void }> = ({ page, theme, onTheme, me = null, onSignOut }) => (
  <header className="cl-nav">
    <Link to={{ page: "home" }} className="cl-brand" aria-label="DecisionTrace home">
      <svg viewBox="0 0 32 32" aria-hidden="true" width="28" height="28">
        <circle cx="16" cy="16" r="14" className="cl-brand-ring" />
        <path d="M8 17 L14 23 L24 10" className="cl-brand-tick" />
      </svg>
      <span>DecisionTrace</span>
    </Link>
    <nav className="cl-nav-links" aria-label="Main">
      {(me?.kind === "customer" ? CUSTOMER_NAV : me?.kind === "staff" ? staffNav(me.scopes ?? []) : NAV).map(([p, label]) => (
        <Link key={p} to={{ page: p }} aria-current={(page === p || (p === "home" && page === "decision")) ? "page" : undefined}>{label}</Link>
      ))}
    </nav>
    <div className="cl-who" data-testid="who">
      {me ? (
        <ProfileMenu me={me} onSignOut={onSignOut} />
      ) : (
        <Link to={{ page: "signin" }} className="cl-who-btn" aria-current={page === "signin" ? "page" : undefined}>Sign in</Link>
      )}
    </div>
    <button type="button" className="cl-theme" onClick={() => onTheme(THEME_NEXT[theme])} aria-label={`Colour theme: ${THEME_LABEL[theme]}. Switch to ${THEME_LABEL[THEME_NEXT[theme]]}`} title={`Theme: ${THEME_LABEL[theme]}`}>
      <span aria-hidden="true">{THEME_ICON[theme]}</span><span className="cl-theme-l">{THEME_LABEL[theme]}</span>
    </button>
  </header>
);

const LENDER_LINKS: Array<[Page, string, string[]]> = [
  ["customers", "Customers", ["signals:read"]], ["connect", "Connect your data", ["mappings:read"]], ["team", "Your team", ["team:manage"]], ["quality", "Data checks", ["quality:read"]],
  ["signals", "Risk signals", ["signals:read"]], ["seals", "Record seals", ["seals:read"]],
];

export const SiteFooter: React.FC<{ onTour: () => void; staff?: boolean; customer?: boolean; scopes?: string[] }> = ({ onTour, staff = false, customer = false, scopes }) => (
  <footer className="cl-footer">
    <div className="cl-footer-grid">
      <div>
        <p className="cl-footer-brand">DecisionTrace</p>
        <p className="cl-muted">Loan decisions, explained. It shows how each decision was reached. It never makes or changes decisions on its own.</p>
      </div>
      {!customer && (
        <nav aria-label="Explore"><h2 className="cl-footer-h">Explore</h2>
          <Link to={{ page: "home" }}>Decisions</Link><Link to={{ page: "insights" }}>Insights</Link></nav>
      )}
      {staff && (
        <nav aria-label="For lenders"><h2 className="cl-footer-h">For lenders</h2>
          {LENDER_LINKS.filter(([, , needs]) => !scopes || needs.every((n) => scopes.includes(n))).map(([p, label]) => <Link key={p} to={{ page: p }}>{label}</Link>)}</nav>
      )}
      <nav aria-label="Learn"><h2 className="cl-footer-h">Learn</h2>
        <Link to={{ page: "about" }}>How it works</Link><Link to={{ page: "help" }}>Help &amp; FAQ</Link>
        <button type="button" className="cl-footer-btn" onClick={onTour}>Welcome tour</button></nav>
      <nav aria-label="Legal"><h2 className="cl-footer-h">Legal</h2>
        <Link to={{ page: "privacy" }}>Privacy</Link><Link to={{ page: "terms" }}>Terms</Link><Link to={{ page: "accessibility" }}>Accessibility</Link></nav>
    </div>
    <p className="cl-footer-fine">© {new Date().getFullYear()} DecisionTrace demonstration. All people, credit reports and decisions are fictional.</p>
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

import React from "react";

export type Page = "home" | "decision" | "about" | "help" | "insights" | "connect" | "team" | "profile" | "quality" | "signals" | "seals" | "signin" | "privacy" | "terms" | "accessibility" | "notfound";

const PAGES = new Set(["about", "help", "insights", "connect", "team", "profile", "quality", "signals", "seals", "signin", "privacy", "terms", "accessibility"]);

export interface Route {
  page: Page;
  id: string | null;
}

export function readRoute(search = window.location.search): Route {
  const q = new URLSearchParams(search);
  const d = q.get("d");
  if (d) return { page: "decision", id: d };
  const p = q.get("p");
  if (!p) return { page: "home", id: null };
  return PAGES.has(p) ? { page: p as Page, id: null } : { page: "notfound", id: null };
}

export const hrefFor = (r: { page: Page; id?: string | null }): string =>
  r.page === "home" ? window.location.pathname : r.page === "decision" ? `?d=${encodeURIComponent(r.id ?? "")}` : `?p=${r.page}`;

export function navigate(to: { page: Page; id?: string | null }): void {
  window.history.pushState({}, "", hrefFor(to));
  window.dispatchEvent(new PopStateEvent("popstate"));
}

/** Real anchor (right-click / open-in-new-tab work) that navigates without a full reload. */
export const Link: React.FC<
  { to: { page: Page; id?: string | null } } & Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "href">
> = ({ to, onClick, children, ...rest }) => (
  <a
    {...rest}
    href={hrefFor(to)}
    onClick={(e) => {
      onClick?.(e);
      if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      navigate(to);
    }}
  >
    {children}
  </a>
);

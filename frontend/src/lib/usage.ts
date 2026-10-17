/**
 * Private, on-device activity stats ("Your activity"). Nothing is sent anywhere.
 * Every storage access is guarded: the app works with storage blocked.
 */
const KEY = "clearly:activity:v1";

export interface Activity {
  views: number;
  decisions: string[]; // distinct decision ids viewed (most recent last)
  searches: number;
  printed: number;
  linksCopied: number;
  reviewsRequested: number;
  firstSeen: string;
}

const empty = (): Activity => ({ views: 0, decisions: [], searches: 0, printed: 0, linksCopied: 0, reviewsRequested: 0, firstSeen: new Date().toISOString() });

export function readActivity(): Activity {
  try {
    const raw = window.localStorage.getItem(KEY);
    return raw ? { ...empty(), ...(JSON.parse(raw) as Partial<Activity>) } : empty();
  } catch {
    return empty();
  }
}

export type ActivityEvent = "view" | "search" | "print" | "copy" | "review";

export function track(event: ActivityEvent, decisionId?: string): Activity {
  const a = readActivity();
  if (event === "view") {
    a.views += 1;
    if (decisionId) a.decisions = [...a.decisions.filter((d) => d !== decisionId), decisionId].slice(-200);
  }
  if (event === "search") a.searches += 1;
  if (event === "print") a.printed += 1;
  if (event === "copy") a.linksCopied += 1;
  if (event === "review") a.reviewsRequested += 1;
  try {
    window.localStorage.setItem(KEY, JSON.stringify(a));
  } catch {
    /* storage unavailable */
  }
  return a;
}

export function resetActivity(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

const THEME_KEY = "clearly:theme";
export type Theme = "system" | "light" | "dark";

export function readTheme(): Theme {
  try {
    const v = window.localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

export function applyTheme(t: Theme): void {
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
  try {
    if (t === "system") window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, t);
  } catch {
    /* ignore */
  }
}

const TOUR_KEY = "clearly:tour-done";
export const tourSeen = (): boolean => {
  try {
    return window.localStorage.getItem(TOUR_KEY) === "1";
  } catch {
    return true; // can't remember => don't nag
  }
};
export const markTourSeen = (): void => {
  try {
    window.localStorage.setItem(TOUR_KEY, "1");
  } catch {
    /* ignore */
  }
};

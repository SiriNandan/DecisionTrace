/**
 * Who is signed in, for this browser tab only (sessionStorage: it is gone when the tab closes).
 * Three kinds: a lender's staff (access key), a customer (the code from their link), or "sample" (built-in fictional data).
 */

export type SessionMode = "staff" | "customer" | "sample";
export interface Session {
  mode: SessionMode | null;
  key?: string;
  claim?: string;
}

const STORE = "clearly:session";
const EVENT = "clearly:session-changed";

export function getSession(): Session {
  try {
    const raw = window.sessionStorage.getItem(STORE);
    return raw ? (JSON.parse(raw) as Session) : { mode: null };
  } catch {
    return { mode: null };
  }
}

function write(s: Session): void {
  try {
    if (s.mode === null) window.sessionStorage.removeItem(STORE);
    else window.sessionStorage.setItem(STORE, JSON.stringify(s));
  } catch {
    /* storage unavailable: the sign-in just won't survive a reload */
  }
  window.dispatchEvent(new Event(EVENT));
}

export const signInStaff = (key: string): void => write({ mode: "staff", key: key.trim() });
export const signInCustomer = (claim: string): void => write({ mode: "customer", claim: claim.trim() });
export const startSample = (): void => write({ mode: "sample" });
export const signOut = (): void => write({ mode: null });

export const onSessionChange = (fn: () => void): (() => void) => {
  window.addEventListener(EVENT, fn);
  return () => window.removeEventListener(EVENT, fn);
};

export const isSampleMode = (): boolean => getSession().mode === "sample";

/** Pulls the secret out of whatever a customer pastes: the whole link, "?d=…&t=…", or just the code. */
export function extractClaimToken(input: string): string | null {
  const text = input.trim();
  const fromParam = /[?&]t=([A-Za-z0-9_-]+)/.exec(text);
  if (fromParam) return fromParam[1];
  return /^dtc_[A-Za-z0-9_-]{20,}$/.test(text) ? text : null;
}

/** The decision a pasted link points at, if it names one. */
export function extractDecisionId(input: string): string | null {
  const m = /[?&]d=([0-9a-fA-F-]{36})/.exec(input);
  return m ? m[1] : null;
}

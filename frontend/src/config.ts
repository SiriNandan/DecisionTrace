/**
 * Environment configuration helper for DecisionTrace frontend.
 * Uses VITE_API_URL for backend calls and enables src/mocks ONLY when VITE_USE_MOCKS=true.
 */

import { getSession } from "./lib/session";

declare global {
  // Optional runtime override for tests
  var __DECISIONTRACE_ENV__:
    | {
        VITE_API_URL?: string;
        VITE_USE_MOCKS?: string;
      }
    | undefined;
}

export function getApiUrl(): string {
  const runtimeOverride = globalThis.__DECISIONTRACE_ENV__?.VITE_API_URL;
  if (runtimeOverride && runtimeOverride.trim().length > 0) {
    return runtimeOverride.trim().replace(/\/+$/, "");
  }
  const envUrl =
    typeof process !== "undefined" && process.env && process.env.VITE_API_URL
      ? process.env.VITE_API_URL
      : "http://localhost:8000";
  return envUrl.trim().replace(/\/+$/, "");
}

export function isUseMocksEnabled(): boolean {
  if (getSession().mode === "sample") return true; // the visitor chose the built-in fictional decisions
  const runtimeOverride = globalThis.__DECISIONTRACE_ENV__?.VITE_USE_MOCKS;
  if (typeof runtimeOverride === "string") {
    return runtimeOverride.trim().toLowerCase() === "true";
  }
  const envMocks =
    typeof process !== "undefined" && process.env && process.env.VITE_USE_MOCKS
      ? process.env.VITE_USE_MOCKS
      : "false";
  return envMocks.trim().toLowerCase() === "true";
}


/** The key to send: the one entered on the sign-in page, else a development default from the build. */
export function getApiKey(): string {
  const session = getSession();
  if (session.mode === "staff" && session.key) return session.key;
  if (session.mode === "customer") return "";
  const envKey =
    typeof process !== "undefined" && process.env && process.env.VITE_API_KEY
      ? process.env.VITE_API_KEY
      : "";
  return envKey.trim();
}

/** Development hints (addresses, key locations) are for builds you run yourself, never for a published site. */
export function isDevBuild(): boolean {
  return typeof process === "undefined" || !process.env || process.env.NODE_ENV !== "production";
}

/** A customer's link secret, sent as X-Claim-Token. */
export function getClaimToken(): string {
  const session = getSession();
  return session.mode === "customer" && session.claim ? session.claim : "";
}

/** True when there is anything to sign in with, so screens can say "sign in" instead of making a request that must fail. */
export function hasCredential(): boolean {
  return isUseMocksEnabled() || Boolean(getApiKey() || getClaimToken());
}

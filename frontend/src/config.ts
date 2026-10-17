/**
 * Environment configuration helper for DecisionTrace frontend.
 * Uses VITE_API_URL for backend calls and enables src/mocks ONLY when VITE_USE_MOCKS=true.
 */

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


/** Optional write key sent as X-API-Key when the backend sets DT_API_KEY. */
export function getApiKey(): string {
  const envKey =
    typeof process !== "undefined" && process.env && process.env.VITE_API_KEY
      ? process.env.VITE_API_KEY
      : "";
  return envKey.trim();
}

import { getApiKey, getClaimToken, hasCredential, isUseMocksEnabled } from "../config";
import {
  extractClaimToken, extractDecisionId, getSession, onSessionChange, signInCustomer, signInStaff, signOut, startSample,
} from "../lib/session";

const TOKEN = "dtc_" + "a".repeat(43);
const ID = "11111111-1111-4111-8111-111111111111";

describe("who is signed in (this tab only)", () => {
  beforeEach(() => { signOut(); delete process.env.VITE_API_KEY; process.env.VITE_USE_MOCKS = "false"; globalThis.__DECISIONTRACE_ENV__ = undefined; });
  afterAll(() => signOut());

  test("nothing to sign in with means no credential", () => {
    expect(getSession()).toEqual({ mode: null });
    expect(hasCredential()).toBe(false);
    expect(getApiKey()).toBe("");
  });

  test("a lender's key is used, trimmed, and kept only in this tab's session storage", () => {
    signInStaff("  dtk_abc  ");
    expect(getApiKey()).toBe("dtk_abc");
    expect(getClaimToken()).toBe("");
    expect(hasCredential()).toBe(true);
    expect(window.sessionStorage.getItem("clearly:session")).toContain("dtk_abc");
    expect(window.localStorage.getItem("clearly:session")).toBeNull();
    signOut();
    expect(getApiKey()).toBe("");
    expect(window.sessionStorage.getItem("clearly:session")).toBeNull();
  });

  test("a build-time development key is the fallback, and a person's own key wins", () => {
    process.env.VITE_API_KEY = "dev-key";
    expect(getApiKey()).toBe("dev-key");
    signInStaff("their-key");
    expect(getApiKey()).toBe("their-key");
  });

  test("a customer sends only the link's code, never a staff key", () => {
    process.env.VITE_API_KEY = "dev-key";
    signInCustomer(TOKEN);
    expect(getApiKey()).toBe("");
    expect(getClaimToken()).toBe(TOKEN);
    expect(hasCredential()).toBe(true);
  });

  test("sample mode turns on the built-in fictional decisions and needs no key", () => {
    expect(isUseMocksEnabled()).toBe(false);
    startSample();
    expect(isUseMocksEnabled()).toBe(true);
    expect(hasCredential()).toBe(true);
    signOut();
    expect(isUseMocksEnabled()).toBe(false);
  });

  test("listeners hear about changes", () => {
    const seen: string[] = [];
    const off = onSessionChange(() => seen.push(String(getSession().mode)));
    signInStaff("k"); signOut(); off(); signInStaff("k2");
    expect(seen).toEqual(["staff", "null"]);
  });

  test("a pasted link, query or bare code all give the same secret", () => {
    for (const pasted of [`https://clearly.example/?d=${ID}&t=${TOKEN}`, `?d=${ID}&t=${TOKEN}`, `  ${TOKEN}  `, `t=${TOKEN}`.replace("t=", "?t=")]) {
      expect(extractClaimToken(pasted)).toBe(TOKEN);
    }
    expect(extractDecisionId(`https://x/?d=${ID}&t=${TOKEN}`)).toBe(ID);
    for (const bad of ["", "hello", "dtk_notacustomercode", "https://x/?d=1"]) expect(extractClaimToken(bad)).toBeNull();
  });
});

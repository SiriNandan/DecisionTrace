import React from "react";
import { render } from "@testing-library/react";
import { axe, toHaveNoViolations } from "jest-axe";
import App from "../App";
import DecisionView from "../components/DecisionView";
import HomeView from "../components/HomeView";
import { MOCK_DECISION_RECORDS, MOCK_DECISIONS_LIST, MOCK_RECONSTRUCTION_REPORTS } from "../mocks/mockData";

expect.extend(toHaveNoViolations);

// jsdom does not compute layout or colours, so colour-contrast must be checked in a real browser.
const run = (container: HTMLElement) => axe(container, { rules: { "color-contrast": { enabled: false } } });
const noop = () => undefined;

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem("clearly:tour-done", "1");
  window.HTMLElement.prototype.scrollIntoView = jest.fn();
  window.scrollTo = jest.fn() as never;
});

describe("automated accessibility checks (axe, jsdom; colour contrast not covered)", () => {
  test("home", async () => {
    const { container } = render(<HomeView cases={MOCK_DECISIONS_LIST} loading={false} error={null} onRetry={noop} onOpen={noop} recent={null} />);
    expect(await run(container)).toHaveNoViolations();
  });

  test.each([
    ["approved", "00000000-0000-0000-0000-000000000001"],
    ["tampered", "00000000-0000-0000-0000-000000000008"],
    ["evidence removed", "00000000-0000-0000-0000-000000000010"],
  ])("decision page: %s", async (_name, id) => {
    const { container } = render(
      <DecisionView record={MOCK_DECISION_RECORDS[id]} report={MOCK_RECONSTRUCTION_REPORTS[id]} reportLoading={false} reportError={null} onRetryReport={noop} onBack={noop} />
    );
    expect(await run(container)).toHaveNoViolations();
  });

  test.each(["about", "help", "privacy", "terms", "accessibility", "insights", "nope"])("site page: %s", async (page) => {
    window.history.replaceState({}, "", `/?p=${page}`);
    const { container } = render(<App />);
    expect(await run(container)).toHaveNoViolations();
  });

  test("welcome tour dialog", async () => {
    window.localStorage.removeItem("clearly:tour-done");
    window.history.replaceState({}, "", "/");
    const { container } = render(<App />);
    expect(await run(container)).toHaveNoViolations();
  });
});

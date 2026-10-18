import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { axe, toHaveNoViolations } from "jest-axe";
import ConnectPage from "../pages/ConnectPage";
import { parseCsv, chunk } from "../lib/csv";
import type { MappingProposal, MappingSpecContract } from "../types";

expect.extend(toHaveNoViolations);

const rule = (column: string, extra: Record<string, unknown> = {}) => ({
  column, transform: "text", date_formats: [], value_map: {}, confidence: 0.9, note: "", ...extra,
});
const SPEC: MappingSpecContract = {
  spec_version: 1,
  source_format: "csv",
  external_id: rule("ref") as never,
  subject_ref: rule("cust") as never,
  decided_at: rule("when", { transform: "date" }) as never,
  outcome: rule("result", { transform: "enum", value_map: { Yes: "APPROVE" } }) as never,
  score: rule("pd", { transform: "number" }) as never,
  model_version: { const: "not provided in file", transform: "text", date_formats: [], value_map: {}, confidence: 0.2, note: "" },
  policy_version: rule("policy") as never,
  reasons: null,
  evidence: [{ evidence_type: "application", source_id: "form", fields: { income: rule("income", { transform: "number" }) as never } }],
  ignored_columns: { name: "Personal detail, so it isn't stored." },
};
const PROPOSAL: MappingProposal = {
  mapping_id: 7, name: "demo", version: 1, spec: SPEC, spec_sha256: "x".repeat(64), proposed_by: "rules",
  status: "proposed", gaps: ["model"], issues: ["Some values in 'result' aren't recognised as approved, declined or referred: ['R']."],
  columns: ["ref", "cust", "name", "when", "result", "pd", "policy", "income"], outcome_values: ["No", "R", "Yes"],
  preview: { rows_total: 3, rows_checked: 3, valid: 2, invalid: 1, problems: [{ row: 2, problems: ["outcome: The value 'R' isn't in the mapping yet"] }] },
};
const CSV = "ref,cust,name,when,result,pd,policy,income\nA1,c1,Pat,2026-01-01,Yes,0.1,P1,100\nA2,c2,Sam,2026-01-02,R,0.2,P1,200\nA3,c3,Kim,2026-01-03,No,0.3,P1,300\n";

type Call = { url: string; body: Record<string, any>; headers: Record<string, string> };

function installFetch() {
  const calls: Call[] = [];
  (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async (url: string, init: RequestInit) => {
    const body = JSON.parse(String(init.body));
    calls.push({ url, body, headers: init.headers as Record<string, string> });
    const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data });
    if (url.endsWith("/v1/mappings/propose")) return ok(PROPOSAL);
    if (url.endsWith("/v1/mappings")) return ok({ ...PROPOSAL, mapping_id: 8, version: 2, gaps: [], preview: { ...PROPOSAL.preview, valid: 3, invalid: 0, problems: [] } });
    if (url.endsWith("/approve")) return ok({ ...PROPOSAL, mapping_id: 8, status: "approved" });
    if (url.endsWith("/v1/imports"))
      return ok({ mapping_id: 8, version: 2, dry_run: false, created: (body.rows ?? [1, 2, 3]).length, replayed: 0, rejected: 0, results: [] });
    throw new Error(`unexpected ${url}`);
  });
  return calls;
}

describe("Connect your data", () => {
  const realFetch = globalThis.fetch;
  beforeEach(() => {
    process.env.VITE_USE_MOCKS = "false";
    process.env.VITE_API_KEY = "k";
    globalThis.__DECISIONTRACE_ENV__ = { VITE_API_URL: "http://api.test", VITE_USE_MOCKS: "false" };
  });
  afterEach(() => {
    (globalThis as unknown as { fetch: unknown }).fetch = realFetch;
    delete process.env.VITE_API_KEY;
    globalThis.__DECISIONTRACE_ENV__ = undefined;
  });

  test("a file goes from suggestion, through the person's fixes and approval, to an import", async () => {
    const calls = installFetch();
    const { container } = render(<ConnectPage />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File([CSV], "uk-bank.csv", { type: "text/csv" })] } });
    expect(await screen.findByTestId("file-summary")).toHaveTextContent("uk-bank.csv: 3 rows.");
    fireEvent.click(screen.getByRole("button", { name: "Check my file" }));

    const table = await screen.findByTestId("match-table");
    expect(calls[0].url).toBe("http://api.test/v1/mappings/propose");
    expect(calls[0].headers["X-API-Key"]).toBe("k");
    expect(calls[0].body).toMatchObject({ name: "uk-bank", format: "csv", content: CSV });
    expect(screen.getByTestId("issues")).toHaveTextContent("aren't recognised");
    expect(screen.getByTestId("preview")).toHaveTextContent("2 of 3 rows read correctly, 1 can’t be read yet.");
    expect(within(table).getByLabelText("Column for Decision date")).toHaveValue("when");
    expect(screen.getByTestId("gaps")).toHaveTextContent("Still needed: Model version");
    expect(screen.getByRole("button", { name: "Approve this match" })).toBeDisabled();

    // the person fixes what the suggestion missed
    await userEvent.selectOptions(within(table).getByLabelText("Column for Model version"), "A fixed value…");
    await userEvent.type(within(table).getByLabelText("Fixed value for Model version"), "m4");
    const rSelect = screen.getByText("“R”").closest("label")!.querySelector("select")!;
    await userEvent.selectOptions(rSelect, "Referred");
    expect(screen.queryByTestId("gaps")).toBeNull();
    expect(screen.getByText("You’ve made changes. Approving will save them first.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Approve this match" }));
    expect(await screen.findByRole("button", { name: "Approved" })).toBeDisabled();
    const save = calls.find((c) => c.url.endsWith("/v1/mappings"))!;
    expect(save.body.spec.model_version.const).toBe("m4");
    expect(save.body.spec.outcome.value_map).toMatchObject({ Yes: "APPROVE", R: "REFER" });
    expect(save.body.source.format).toBe("csv");
    expect(calls.map((c) => c.url.split("/v1")[1])).toEqual(["/mappings/propose", "/mappings", "/mappings/8/approve"]);

    fireEvent.click(await screen.findByRole("button", { name: "Import 3 decisions" }));
    const done = await screen.findByTestId("import-summary");
    expect(done).toHaveTextContent("3 saved, 0 already there, 0 couldn’t be saved.");
    const imp = calls[calls.length - 1];
    expect(imp.body.mapping_id).toBe(8);
    expect(imp.body.format).toBe("json");
    expect(imp.body.rows[1]).toMatchObject({ ref: "A2", result: "R" });
    expect(await axe(container, { rules: { "color-contrast": { enabled: false } } })).toHaveNoViolations();
  });

  test("a data source is fetched by the server: no rows pass through the browser and secrets are not shown back", async () => {
    const calls = installFetch();
    render(<ConnectPage />);
    fireEvent.click(screen.getByText("Or fetch it from a data source"));
    await userEvent.type(screen.getByLabelText("Web address"), "https://files.bank.example/decisions.csv");
    await userEvent.type(screen.getByLabelText(/Access token/), "tok-secret");
    fireEvent.click(screen.getByRole("button", { name: "Use this data source" }));
    expect(await screen.findByTestId("remote-summary")).toHaveTextContent("web address files.bank.example");
    expect(screen.queryByText(/tok-secret/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Check my data" }));
    await screen.findByTestId("match-table");
    expect(calls[0].body).toMatchObject({
      format: "csv", source: { type: "url", url: "https://files.bank.example/decisions.csv", bearer_token: "tok-secret" },
    });
    expect(calls[0].body.content).toBeUndefined();
    expect(calls[0].body.rows).toBeUndefined();

    const table = screen.getByTestId("match-table");
    await userEvent.selectOptions(within(table).getByLabelText("Column for Model version"), "A fixed value…");
    await userEvent.type(within(table).getByLabelText("Fixed value for Model version"), "m4");
    const rSelect = screen.getByText("“R”").closest("label")!.querySelector("select")!;
    await userEvent.selectOptions(rSelect, "Referred");
    fireEvent.click(screen.getByRole("button", { name: "Approve this match" }));
    fireEvent.click(await screen.findByRole("button", { name: "Fetch and import" }));
    expect(await screen.findByTestId("import-summary")).toBeInTheDocument();
    const imp = calls[calls.length - 1];
    expect(imp.url).toBe("http://api.test/v1/imports");
    expect(imp.body).toMatchObject({ mapping_id: 8, format: "csv", source: { type: "url" } });
    expect(imp.body.rows).toBeUndefined();
  });

  test("a database source asks for one read-only query and is always read as JSON rows", async () => {
    const calls = installFetch();
    render(<ConnectPage />);
    fireEvent.click(screen.getByText("Or fetch it from a data source"));
    await userEvent.selectOptions(screen.getByTestId("remote-kind"), "sql");
    expect(screen.queryByText("What kind of file is it?")).toBeNull();
    await userEvent.type(screen.getByLabelText(/Database address/), "postgresql://ro:pw@db.bank.example:5432/loans");
    await userEvent.type(screen.getByLabelText(/Query/), "SELECT * FROM decisions");
    fireEvent.click(screen.getByRole("button", { name: "Use this data source" }));
    expect(await screen.findByTestId("remote-summary")).toHaveTextContent("database db.bank.example");
    expect(screen.queryByText(/pw@/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Check my data" }));
    await screen.findByTestId("match-table");
    expect(calls[0].body).toMatchObject({ format: "json", source: { type: "sql", query: "SELECT * FROM decisions" } });
  });

  test("explains a refused sign-in in plain words", async () => {
    (globalThis as unknown as { fetch: unknown }).fetch = jest.fn(async () => ({
      ok: false, status: 403, json: async () => ({ detail: "This sign-in isn't allowed to do that." }),
    }));
    render(<ConnectPage />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File([CSV], "a.csv")] } });
    fireEvent.click(await screen.findByRole("button", { name: "Check my file" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("isn’t allowed to do this"));
  });

  test("rejects a JSON file that is not a list, and invalid JSON", async () => {
    render(<ConnectPage />);
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(['{"a":1}'], "x.json")] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("should hold a list");
    fireEvent.change(screen.getByTestId("file-input"), { target: { files: [new File(["[oops"], "y.json")] } });
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("isn’t valid JSON"));
  });

  test("uses no internal terms", () => {
    const { container } = render(<ConnectPage />);
    expect(container.textContent).not.toMatch(/schema|hash|snapshot|envelope|guardrail|idempot|mapping spec/i);
  });
});

describe("CSV reading", () => {
  test("handles quotes, commas, blank lines and Windows line endings", () => {
    const rows = parseCsv('a,b\r\n"x, y","He said ""hi"""\r\n\r\n1,\r\n');
    expect(rows).toEqual([{ a: "x, y", b: 'He said "hi"' }, { a: "1", b: "" }]);
  });
  test("splits into parts", () => {
    expect(chunk([1, 2, 3, 4, 5], 2)).toEqual([[1, 2], [3, 4], [5]]);
  });
});

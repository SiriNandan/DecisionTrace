# DecisionTrace

> **ALL DATA IS SYNTHETIC (`SYNTHETIC DATA - FOR DEMONSTRATION.`)**

When a lender has to explain or defend a past automated credit decision, the evidence has usually moved on: the bureau file changed, the policy was revised, a document was deleted. Reconstructing the decision by hand is slow and cannot be verified.

DecisionTrace is a local-first reference implementation that records each loan decision with **hash-chained, decision-time evidence** and later **reconstructs a cited explanation from the preserved record only**. It *supports audit and explanation* and is *tamper-evident against a retained reference* — it does not claim to prove compliance.

## What it does

| Capability | Where |
| :--- | :--- |
| Record a decision with its evidence snapshots in one transaction, idempotently | `decision_api/` (`POST /decide`) |
| Hash-chain every snapshot and detect edits, reordering and deleted rows | `backend/integrity.py`, `GET /decisions/{id}/verify` |
| Replay the model score from the stored registry within `1e-9` | `backend/scoring.py`, `replay_score` tool |
| Resolve the policy version effective *at decision time* | `backend/policy_search.py` |
| Reconstruct a cited report with an 8-tool-call / 30 s budget (Gemini function calling, or a deterministic `fake` backend) | `recon_agent/` (`POST /reconstruct/{id}`) |
| **Runtime grounding guardrail**: every claim must cite evidence returned in-session, match the cited field/value, and use only returned numbers; violations are removed and counted in `guardrail_rejections` | `recon_agent/guardrails.py` |
| Append-only human overrides that keep the original outcome intact | `POST /overrides` |
| Evaluation, tamper experiments, fairness cohort check | `eval/`, `docs/` |
| Audit workbench UI | `frontend/` |

Read the boundaries before relying on it: [docs/limitations.md](docs/limitations.md), [docs/model_card.md](docs/model_card.md), [docs/regulatory_mapping.md](docs/regulatory_mapping.md).

## Stack

Python 3.11+, FastAPI, PostgreSQL 15 (Docker), psycopg 3, Pydantic v2, scikit-learn, Google Gen AI SDK · Vite, React, TypeScript, Jest.

## Run it (macOS / Linux; Windows commands are in `commands.txt`)

```bash
cp .env.example .env                     # LLM_BACKEND=fake works offline
docker compose up -d                     # PostgreSQL on :5433
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python -m data.generate_applicants       # 2,000 deterministic synthetic applicants
python -m data.generate_documents
python -m data.load_policies             # applies migrations + 3 policy versions
python -m data.train_model
python -m data.seed_scenarios            # 10 canonical scenarios + 20 baseline decisions

uvicorn decision_api.app:app --port 8000          # terminal 1
npm --prefix frontend ci && npm --prefix frontend run dev   # terminal 2 → http://localhost:5173
```

### Suggested 3-minute demo

1. Open the UI and select **`clear_decline`**: the report cites preserved snapshots, rule checks and the policy section.
2. Select **`tampered_payload`**: the chain badge turns red and names the first broken sequence.
3. Select **`erased_evidence`**: status becomes `evidence_unavailable` and the erased reference is never cited.
4. Select **`override_case`**: original outcome and override are shown side by side.

### Using the real Gemini backend

Set `LLM_BACKEND=gemini_api` and a real `GEMINI_API_KEY` in `.env` (the `.env.example` placeholder is ignored). Reports produced by the model pass through the same runtime guardrail as the scripted backend.

### Setup and deployment

```bash
docker compose up -d postgres        # database only (port 5433)
python -m data.seed                  # create schema, load policies, register the model, seed the scenarios
python -m data.seed --reset          # same, after dropping and recreating the database

docker compose --profile app up -d --build   # full stack: seed job + API + web UI on http://localhost:8080
```

In the full stack the web container serves the UI and proxies `/api/` to the API without adding any credentials: people sign in on the page (lenders with an access key, customers with their link). For the demo the lender key is `DT_API_KEY` (default `dev-demo-key` in `docker-compose.yml`, demo only; set your own in `.env`). Set `DT_API_KEY` (and optionally `WEB_PORT`, `LLM_BACKEND`, `GEMINI_API_KEY`) in `.env` or the shell first.

### Securing the API

- Every endpoint now needs an `X-API-Key`; there are no open reads. Each key belongs to one tenant (lender) and one role (`lender_ingest`, `lender_analyst`, `auditor`, `admin`), and a tenant only ever sees its own decisions (other tenants' decisions answer 404). Create tenants and keys with `python -m backend.tenants create-tenant|issue-key|revoke-key` (keys are shown once and stored only as a hash). `DT_API_KEY`, if set, is an admin key for the built-in `demo` tenant, which is what the demo UI and proxy use.
- `POST /v1/decisions` records a decision made by the lender's own engine (envelope schema v1, `Idempotency-Key` required, role `lender_ingest`); `GET /v1/access-log` lists who read or changed what for the caller's tenant (role `auditor` or `admin`). Set `APP_DB_URL` (the `app_role` login) so Postgres row-level security applies as a second barrier, and `DT_ID_SECRET` outside `ENV=dev` (decision IDs from ingest are derived from it and cannot be guessed). Not yet evidenced: behaviour at scale, per-field encryption, consumer sign-in links.
- Decisions sent through `/v1/decisions` are explained from the lender's own reasons, with no model call and no re-scoring (`recording_mode: "recorded"` in the report, contract 1.1.0); the Clearly page shows "The reasons your lender gave". Golden test envelopes (approve, decline, refer, missing document, no reasons) live in `fixtures/envelopes/`. `python -m backend.sandbox --api-url http://localhost:8000 --tenants 5 --decisions 200` creates synthetic sandbox lenders (needs the owner `DB_URL`) and sends their decisions through the real API; it prints each tenant's analyst and auditor keys. All of it is synthetic data. There is no consent flow yet.
- **Connect your data** (`?p=connect` in the UI, or the API): `POST /v1/mappings/propose` reads a CSV or JSON file in any layout and drafts how its columns match the decision envelope, using built-in rules and, when `LLM_BACKEND=gemini_api` and a key are set, an AI model on top. The model only sees column names and a few masked sample values (personal columns are withheld), every column it names must exist in the file, and each rule is re-run on the real rows to measure how often it works. A mapping is plain data, never code. A person completes any gaps and signs it off (`lender_analyst` or `admin`; `lender_ingest` can propose and import but not sign off), then `POST /v1/imports` runs it deterministically, 500 rows per call, one row at a time (bad rows are rejected with a reason and the rest go through). Re-imports replay, a changed row under an old reference is refused, and each decision records which mapping version produced it. Mapping versions and reviews are append-only. Samples in `fixtures/sources/` (UK-style CSV, US-style CSV, nested JSON). Not evidenced: a real Gemini call (only a stand-in model is tested), very large files, and mappings for anything other than envelope v1.
- **Reports** (contract 1.2.0): every report lists `corrections` made after the decision (the explanation still describes the original inputs the decision used). For decisions the lender made, a model may write a short plain-language summary (`LLM_BACKEND=gemini_api` and a key); it is kept only if every `[citation]` and number in it is in the preserved record, otherwise it is dropped and counted in `guardrail_rejections`, and the structured explanation from the lender's own reasons is shown. Not evidenced: summary quality from a real model.
- **Signing in (UI)**: open `?p=signin` (or the "Sign in" button in the header). Lenders paste an access key (for the demo: the `DT_API_KEY` from `.env`); it is kept only for that browser tab (`sessionStorage`), never in the page address. A **customer** opens the link their lender gave them (`?d=<decision>&t=<secret>`): the secret is moved into the tab's session and removed from the address bar, and it opens only that one decision (no other decisions, no lender tools, no internal notes, no risk signals; they can ask for a review, never change the result). Links are made on a decision page under "For your team" (1, 7 or 30 days), are shown once, are stored only as a hash, expire, and can be withdrawn (`POST /v1/claim-links/{id}/revoke`). Anyone holding a link can open its decision until it expires, and there is no extra identity check yet (not built). "Try the sample decisions" shows built-in fictional data with no service or key. `GET /v1/me` says who is signed in, and `GET /health` (open, no data) says whether the service and its database are up to date.
- **Why the screen could say "We couldn't load the decisions"**: if the database was created before newer updates, the API used to fail with an error the browser reports as a network problem. Now, in local development (`ENV=dev`, or `DT_AUTO_MIGRATE=1`) the API updates its own database when it starts, and if it still can't, it answers with a plain message and CORS headers so the screen can say "The service needs an update". Elsewhere, run `python -m data.seed` or apply `migrations/*.sql` as the owner role.
- **Risk signals** (`?p=signals`, `POST /v1/signals/run`, `GET /v1/signals`): patterns for staff to look at, never conclusions or actions, and never shown to customers. Per decision: the same customer reference applying 3 or more times within 7 days, an approval within 30 days of a decline, and identical income, loan amount and credit score across 3 or more different customer references. Across decisions: a sharp shift this week in decline rate or average score against the previous 90 days (needs 30+ decisions in each period). Who is looking: an access key or customer link that asks for 10+ decisions that don't exist in 10 minutes (guessing reference numbers), or opens 200+ different decisions in 10 minutes. Imports check what they just saved. Staff mark a signal "looked at" or "nothing to worry about" with a note; the original is kept. Repeated wrong keys or links are slowed down per client (`DT_AUTH_FAIL_LIMIT`, default 20 a minute; set `DT_TRUST_PROXY=1` only behind a proxy you control) and recorded in `security_events`. Not evidenced: thresholds are fixed starting points, not tuned on real data; the failure limiter is per process.
- **Data checks** (`?p=quality`, or the API): `POST /v1/quality/run` looks through a lender's saved decisions for values that are missing, unlikely (negative income, a percentage where a fraction belongs, a credit score outside any agency's range), in conflict between sources (for example monthly income entered as yearly), inconsistent (an approval with a missing document, evidence dated after the decision, a decline with no reason) or far from the lender's usual range. Imports are checked automatically as they are saved. Findings are saved once and re-running adds nothing twice. When `LLM_BACKEND=gemini_api` and a key are set, a model adds a one-line likely cause and may propose a corrected value, but a value is kept only if it can be traced to numbers already in the same record (the value itself, or ×12, ÷12, ×100, ÷100 of one); everything else is dropped. A person (`lender_analyst` or `admin`) then applies a correction or says the data is right as it is, always with a reason. A correction never edits the original: it is a new row in a separate hash-linked chain per decision (`corrections`), pointing at the hash of the item it corrects, and the decision page shows "Some details were corrected after this decision was made" with the original kept. Editing or reordering a correction row is detected (`corrections_valid` on the decision and on `/verify`); the decision's own chain is unaffected. Not evidenced: removing the *last* correction is not detectable yet, because correction chain heads are not anchored outside the database; the thresholds are fixed starting points rather than tuned on real data; a real Gemini call.
- `VITE_API_KEY` is **only for local development**: Vite inlines it into the public JavaScript bundle, so it is not a secret. Use the proxy in any shared deployment.
- Browser origins come from `CORS_ORIGINS` (both APIs; default: the local Vite server). Only `GET`, `POST` and `OPTIONS` and the documented headers are allowed.
- Both APIs send `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` and a restrictive `Content-Security-Policy`; HSTS is added when `ENV` is not `dev`.
- `POST /reconstruct` is limited per client (`RECON_RATE_LIMIT_PER_MINUTE`, default 20) and successful reports are cached briefly (`RECON_CACHE_TTL_SECONDS`, default 60; cleared when an override is recorded). Both are in-process, so they apply per worker.

### Anchoring chain heads (optional)

Set `DT_ANCHOR_LOG` to a path on storage outside the database's trust domain (and optionally `DT_ANCHOR_KEY` to HMAC-sign entries). Every new decision then appends its chain head to a hash-chained log. `python -m backend.anchoring verify` checks the log and compares it with the database, reporting rewritten heads. The log is only as independent as where you keep it; see `docs/limitations.md` §3.

## Tests

```bash
pytest                         # needs PostgreSQL; runs in a throwaway `decisiontrace_test` database; the live-Gemini test skips without a key
npm --prefix frontend test     # includes axe checks; live-API tests need the APIs on :8000/:8001 and `python -m data.seed`
python -m eval.run_eval        # regenerates eval/results.md and the rating CSVs
```

> Note: `eval/results.md` was generated with the deterministic `fake` backend. Re-run the evaluation with `LLM_BACKEND=gemini_api` for model-generated numbers.

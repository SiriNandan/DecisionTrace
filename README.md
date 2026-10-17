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

In the full stack the web container serves the UI and proxies `/api/` to the API, adding `DT_API_KEY` server-side, so no key ships in the browser bundle. Set `DT_API_KEY` (and optionally `WEB_PORT`, `LLM_BACKEND`, `GEMINI_API_KEY`) in `.env` or the shell first.

### Securing the API

- `DT_API_KEY`: when set, `POST /decide`, `POST /overrides` and `POST /reconstruct/{id}` require an `X-API-Key` header. Reconstruction is included because each call can spend model quota. Reads (`GET /decisions...`) stay open; keep the API behind the web proxy or a private network.
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

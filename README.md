# DecisionTrace

> **ALL DATA IS SYNTHETIC (`SYNTHETIC DATA - FOR DEMONSTRATION.`)**

In lending, regulators and customers can demand justification of a decision years later, and the evidence has moved on: the bureau file changed, the policy was revised, a document was deleted. Reconstructing the decision by hand is slow and cannot be verified.

DecisionTrace is a local-first reference implementation that records each loan decision with **hash-chained, decision-time evidence** and later **reconstructs a cited explanation from the preserved record only**. It *supports audit and explanation* and is *tamper-evident against a retained reference* — it does not claim to prove compliance.

## What it does

| Capability | Where |
| :--- | :--- |
| Record a decision with its evidence snapshots in one transaction, idempotently | `decision_api/` (`POST /decide`) |
| Hash-chain every snapshot and detect edits, reordering and deleted rows | `backend/integrity.py`, `GET /decisions/{id}/verify` |
| Replay the model score from the stored registry within `1e-9` | `backend/scoring.py`, `replay_score` tool |
| Resolve the policy version effective *at decision time* | `backend/policy_search.py` |
| Reconstruct a cited report with an 8-tool-call / 30 s budget (Gemini function calling, or a deterministic `fake` backend) | `recon_agent/` (`POST /reconstruct/{id}`) |
| **Runtime grounding guardrail**: every claim must cite evidence returned in-session, match the cited field/value, and use only returned numbers; violations are removed, counted in `guardrail_rejections`, and shown on each decision page as "Checked against the record" | `recon_agent/guardrails.py` |
| Append-only human overrides that keep the original outcome intact | `POST /overrides` |
| Evaluation, tamper experiments, fairness cohort check | `eval/`, `docs/` |
| A lender administrator who manages the team (data officer, analyst, support, auditor) and limits who sees which portfolio | `decision_api/team_api.py`, `?p=team` |
| Audit workbench UI | `frontend/` |

Read the boundaries before relying on it: [docs/limitations.md](docs/limitations.md), [docs/model_card.md](docs/model_card.md), [docs/regulatory_mapping.md](docs/regulatory_mapping.md).

## Stack

Python 3.11, FastAPI, PostgreSQL 15 (Docker), psycopg 3, Pydantic v2, scikit-learn, Google Gen AI SDK · Vite, React, TypeScript, Jest.

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

### Full stack (Docker)

```bash
docker compose --profile app up -d --build   # seed job + API + web UI on http://localhost:8080
```

Setup and deployment details, API keys, customer links and every feature's details and caveats (including what is not evidenced or not built) are in [docs/features.md](docs/features.md).

## Deployed demo

A single-VM demo on Google Cloud: Docker Compose (`docker-compose.yml` + `docker-compose.prod.yml`, `--profile app`), Caddy for HTTPS in front, secrets in a root-only env file (template: `deploy/app.env.example`; real values are generated and kept in Secret Manager, never in the repo). PostgreSQL and the web container are not published; only Caddy listens, on 80 and 443. With `ENV` other than `dev` the API refuses to start unless the seal key, master key, ID secret, anchor and seal-publish paths and `CORS_ORIGINS` are set safely (`backend/startup_checks.py`).

Limits of this demo, stated plainly:

- **Synthetic data only.** Nothing real should be loaded.
- **Local key provider.** Lender evidence keys are derived from one master secret on the VM (`LocalKeyProvider`); there is no hardware or cloud key service.
- **No SSO or MFA.** Access is by API key or customer link.
- **In-process rate limiting and cache.** They are per process and reset on restart; they do not work across several instances.
- **Anchors are only protected from casual edits.** The anchor log and signed seals are copied every 5 minutes to a bucket with a short (1-day) retention policy as timestamped files, but someone with admin rights on the VM or project could still change what is written before it is copied.
- **Gemini is enabled on this demo** (`LLM_BACKEND=gemini_api`, key kept in Secret Manager). Model output passes the same guardrail as the scripted backend and can vary between runs; the data is synthetic.
- **Large data sources load a page (500 rows) at a time.** The source is fetched again for each page, so a database query that changes between pages can give a mixed result; use a fixed export for big loads.
- **Data sources are off until allowed.** Importing from a URL, a storage bucket or a database needs the operator to list the hosts or buckets in `DT_SOURCE_URL_ALLOWED_HOSTS`, `DT_SOURCE_GCS_ALLOWED_BUCKETS` or `DT_SOURCE_SQL_ALLOWED_HOSTS`.
- **The fairness check does not show a gap with the registered model.** `loan_lr_v1` gives `employment_months` almost no weight; the eval report says so and adds a labelled stress test showing the detector flags a proxy when a model really uses one.
- The API's database role (`app_role`) gets a generated password (`APP_DB_PASSWORD`) when the database is seeded, and the API refuses to start in a deployed setting while it still has the development one. Both database roles are reachable only inside the Compose network.

Next: Cloud SQL for the database, Cloud KMS-backed keys, SSO and MFA, and Cloud Run (which also needs a shared rate limiter and cache).

## Links

- [Feature details and caveats](docs/features.md)
- [Limitations](docs/limitations.md) · [Model card](docs/model_card.md) · [Regulatory mapping](docs/regulatory_mapping.md)
- [Scale measurements](docs/scale.md) · [Contract changelog](docs/contract_changelog.md)

## Tests

```bash
pytest                         # needs PostgreSQL; runs in a throwaway `decisiontrace_test` database; the live-Gemini test skips without a key
npm --prefix frontend test     # includes axe checks; live-API tests need the APIs on :8000/:8001 and `python -m data.seed`
python -m eval.run_eval        # regenerates eval/results.md and the rating CSVs
```

> Note: `eval/results.md` was generated with the deterministic `fake` backend. Re-run the evaluation with `LLM_BACKEND=gemini_api` for model-generated numbers.

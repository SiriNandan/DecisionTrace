# DecisionTrace Task Tracker

| Task | Owner | Status |
| :--- | :--- | :--- |
| A1: Project layout, `docker-compose.yml`, `.env.example`, `.gitignore`, `pyproject.toml`, `README.md`, `TODO.md` | Agent | Done |
| A2: `migrations/001_init.sql` (schema, constraints, `app_role` least-privilege grants) and permission tests | Agent | Done |
| A3: `data/config.yaml` and `data/generate_applicants.py` (2000 deterministic synthetic applicants, bureau snapshots, accounts) | Agent | Done |
| A4: `policies/*.md` (`LP-2025-v1`, `LP-2025-v2`, `LP-2026-v1`), `data/load_policies.py`, and non-overlapping range tests | Agent | Done |
| A5: `data/generate_documents.py` (`payslips/*.json`, `consent_events.csv`, `analyst_notes.csv`) and `backend/redaction.py` with tests | Agent | Done |
| A6: `data/scenarios/scenarios.yaml`, `data/seed_scenarios.py`, `decision_api/service.py`, and scenario verification tests | Agent | Done |
| A7: `data/generate_fairness_scenario.py`, `eval/fairness.py`, and fairness disparity detection tests | Agent | Done |
| B1: `data/train_model.py`, `docs/model_card.md`, `backend/scoring.py`, and `1e-9` `predict_proba` match tests | Agent | Done |
| B2: `backend/integrity.py` (`canonical_json`, `payload_sha256`, `GENESIS`, `entry_hash`, `build_chain`, `verify_chain`) and tamper-detection tests | Agent | Done |
| B3: `backend/sources/*.py`, `decision_api/` (`POST /decide`, `GET /decisions`, `GET /decisions/{id}`, `GET /decisions/{id}/verify`), and transactional/idempotency tests | Agent | Done |
| B4: `backend/contract.py` (Pydantic v2 `ReconstructionReport`, `RuleCheck`, `Reason`, `Claim`, `Override`), `docs/contract_changelog.md`, and validation tests | Agent | Done |
| B5: `backend/policy_search.py` (`search_policies`, `get_policy_as_of`, numbered section splitting, TF-IDF ranking) and date-boundary tests | Agent | Done |
| B6: `recon_agent/` (`POST /reconstruct/{decision_id}`, `LLMBackend` with `gemini_api` and `fake`, 8 preserved-snapshot tools, 8-call/30s limits, retry, `evidence_unavailable`) and tests | Agent | Done |
| B7: `POST /overrides` (`decision_id`, `new_outcome`, `reason` min 20 chars, `actor_role`), overrides-only insertion, byte-identical `decisions`/`decision_snapshots` preservation, and `get_decision` returning both original outcome and override | Agent | Done |
| B8: `frontend/` (Vite, React, TS): rebuilt as the "Clearly" consumer UI (home, decision page, Insights, About, Help, legal pages), `SYNTHETIC DATA - FOR DEMONSTRATION` banner, accessibility/360px layout, `VITE_API_URL`/`VITE_USE_MOCKS`, and Jest tests against the real API | Agent | Done |
| B9: `eval/run_eval.py`, `eval/results.md`, 30-report human rating CSV export (`eval/human_rating_30_reports.csv`), DB-copy tamper detection, separate grounding metrics, latency, manual vs. tool timing table, and fairness report | Agent | Done |
| B10: `docs/regulatory_mapping.md` (feature, expectation it supports, limitation, verified source link placeholder) and `docs/limitations.md` (synthetic data, one decision type, tamper-evident anchoring gap, replay artifact dependency, pseudonymisation vs deletion, fairness demo vs certification) | Agent | Done |

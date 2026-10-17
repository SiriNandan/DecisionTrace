<!-- SYNTHETIC DATA - FOR DEMONSTRATION -->
# System Limitations & Architectural Boundaries (`docs/limitations.md`)

> **SYNTHETIC DATA - FOR DEMONSTRATION.**
> DecisionTrace is a local-first reference implementation that **supports audit and explanation** by preserving decision-time snapshots that are **tamper-evident against a retained reference**. It is designed to illustrate evidence preservation, deterministic score replay, and grounded explanation guardrails—not to serve as a drop-in production compliance system.

---

## 1. Synthetic Data Only (`SYNTHETIC DATA - FOR DEMONSTRATION.`)

- **Simulated Population and Bureau Records**: Every applicant (`data/applicants.csv`), credit bureau profile (`data/bureau_snapshots.csv`), bank account summary (`data/accounts.csv`), payslip JSON (`data/payslips/*.json`), consent record (`data/consent_events.csv`), analyst note (`data/analyst_notes.csv`), and lending policy (`policies/LP-2025-v1.md`, `LP-2025-v2.md`, `LP-2026-v1.md`) is synthetic data generated from fixed pseudorandom seeds (`uk_retail` profile).
- **Non-Representative Risk Dynamics**: The 12-month default labels (`outcome_default_12m`) and feature correlations are generated from a simplified parametric formula for reproducible testing. Model metrics (such as holdout ROC-AUC and accuracy) and policy thresholds reflect this synthetic data generator and must not be extrapolated to real consumer credit populations.

---

## 2. One Decision Type (Single-Product Retail Consumer Loan)

- **Narrow Product Scope**: DecisionTrace implements a single decision type—an initial automated underwriting decision (`APPROVE`, `DECLINE`, `REFER`) for an unsecured retail consumer loan (`policy_family = 'consumer_loan'`).
- **Fixed Rule and Feature Schema**: The decision pipeline evaluates exactly seven numerical model features (`income_annual`, `requested_amount`, `dti_ratio`, `credit_score`, `delinquencies_24m`, `account_age_months`, `employment_months`) and four discrete policy rules (`dti_limit`, `min_credit_score`, `required_documents`, `delinquencies_manual_review`).
- **Out-of-Scope Workflows**: Revolving credit limit adjustments, tiered risk-based pricing, counter-offers, commercial or mortgage underwriting, multi-applicant joint borrowing, and asynchronous fraud/AML screening pipelines are not modelled.

---

## 3. Tamper-Evident, Not Tamper-Proof (The Anchoring Gap)

- **Tamper-Evident Against a Retained Reference**: Each decision snapshot is canonicalized (`canonical_json`), hashed with SHA-256 (`payload_sha256`), and linked sequentially via `prev_hash` and `entry_hash` starting from `GENESIS` (`backend/integrity.py`). Calling `verify_chain` checks the snapshots against `decisions.chain_length` and `decisions.chain_head`, making single-row edits, reordered rows, or deleted rows (including deleting the final row) **tamper-evident against a retained reference**.
- **The Anchoring Gap**: Storing `decisions.chain_head` in the same PostgreSQL database as `decision_snapshots` leaves an **anchoring gap**. Although the application database role (`app_role`) has append-only `SELECT, INSERT` permissions, a privileged database superuser, migration user, or host-level attacker with write access to the underlying PostgreSQL tables could modify a snapshot payload, recompute all subsequent `entry_hash` values, and update `decisions.chain_head` and `decisions.chain_length` so internal verification passes.
- **Mitigation Required for Production**: Closing the anchoring gap requires publishing or signing `(decision_id, chain_length, chain_head, decided_at)` to an external, independently controlled WORM (Write-Once-Read-Many) store, hardware security module (HSM), or append-only cryptographic transparency log at decision time.
- **Partial mitigation implemented (optional)**: `backend/anchoring.py` appends each new decision's `(decision_id, chain_length, chain_head, decided_at)` to a hash-chained, optionally HMAC-signed log (`DT_ANCHOR_LOG`, `DT_ANCHOR_KEY`), and `python -m backend.anchoring verify` flags heads in the database that no longer match it. This closes the gap **only if** the log is kept somewhere a database administrator cannot also rewrite (for example a write-once storage mount or a separately controlled host). A log on the same machine under the same administrator, as in the default local setup, adds no protection. It does not use an HSM or a public transparency log, and it is off unless configured. Decisions made before anchoring was enabled are reported as not anchored.

---

## 4. Replay Needs the Original Artifact

- **Dependency on Stored Model Parameters and Preprocessing**: Score replay (`replay_score` in `recon_agent/tools.py` and `score` in `backend/scoring.py`) reproduces the original decision score within `1e-9` only when both the decision-time feature snapshot (`model` / `application` / `bureau`) and the exact `model_registry` row (`coefficients`, `means`, `stds`, `intercept`, `feature_order`, `preprocessing_version`, `artifact_sha256`) remain intact.
- **Artifact and Environment Drift**:
  - If the original model artifact or preprocessing specification (`standard_scaler_v1`) is lost, overwritten, or migrated without backward-compatible replay logic, historical scores cannot be recomputed.
  - While a standardized linear `LogisticRegression` model can be replayed deterministically from JSON coefficients and scaler statistics, complex ensemble models, deep neural networks, or pipelines relying on external C/CUDA libraries can exhibit floating-point or library-version drift across hardware architectures unless the full execution container and model binary are archived.

---

## 5. Pseudonymisation Is Not a Complete Deletion Fix

- **Scope of Redaction and Pseudonymisation**: DecisionTrace strips common PII patterns (email addresses, phone numbers, and UK National Insurance numbers) from free-text analyst notes prior to snapshot storage (`backend/redaction.py`) and references borrowers via synthetic identifiers (`applicant_id` such as `APP-0011`). It also tracks document retention state in `evidence_status` (`AVAILABLE`, `MISSING`, `erased_by_retention`).
- **Why Pseudonymisation Does Not Equal Erasure**:
  - **Re-identification Risk**: Under data protection frameworks (such as UK GDPR), pseudonymised financial snapshots (exact income, loan amount, debt service, credit score, and timestamps) remain personal data as long as a mapping table or auxiliary record can link `applicant_id` back to a natural person.
  - **Regex Redaction Boundaries**: Pattern-based redaction in `backend/redaction.py` catches structured email, phone, and NI tokens, but cannot guarantee removal of unstructured identifying details written in free text (e.g., employer names or unique personal circumstances).
  - **Immutable Chains vs. Physical Deletion**: Marking `evidence_status = 'erased_by_retention'` prevents `recon_agent` from reading or citing that snapshot (`status = 'evidence_unavailable'`), supporting audit of why an explanation is incomplete. However, if a data subject exercises a valid right to erasure requiring physical deletion of the underlying JSONB payload in `decision_snapshots`, overwriting or deleting that row will cause `payload_sha256` or `verify_chain` to fail unless the system separates encrypted PII blobs from the retained hash commitments (e.g., crypto-shredding per-snapshot keys while keeping the ciphertext hash intact).

---

## 6. Fairness Demo Is Not Certification

- **Observational Proxy Scenario Only**: The fairness evaluation (`data/generate_fairness_scenario.py` and `eval/fairness.py`) constructs a controlled synthetic cohort (`fairness_groups` with `Group A` and `Group B`, `n = 400` each) where `test_group` is never passed to the model, yet `Group B` has a higher share of short `employment_months` (`< 12` months), creating a measurable approval-rate disparity (`selection_rate_difference = 0.3050`, `selection_rate_ratio = 0.4895`).
- **Why This Does Not Constitute Certification**:
  - **Diagnostic Support Only**: Computing group approval, decline, and referral rates, selection-rate differences, and selection-rate ratios **supports audit and explanation** by alerting reviewers to potential proxy effects in non-protected features.
  - **No Causal or Legal Determination**: A threshold check (`difference > 0.15` or `ratio < 0.80`) is a heuristic screening metric, not a legal or statistical certification of fairness. Observational parity metrics do not account for legitimate differences in underlying default risk, calibration across score bands, measurement bias in bureau inputs, or intersectional group disparities.


<!-- SYNTHETIC DATA - FOR DEMONSTRATION -->
# DecisionTrace Evaluation Results (`eval/results.md`)

> **SYNTHETIC DATA - FOR DEMONSTRATION.**
> All applicants, credit bureau records, account histories, policy versions, and loan decisions evaluated in this report are synthetic records generated with a fixed seed (`SEED = 42`). DecisionTrace **supports audit and explanation** and is **tamper-evident against a retained reference**.

## 1. Executive Summary & Evaluation Configuration

- **Random Seed**: `42` (deterministic seeding & sampling)
- **Total Decisions Evaluated**: `32` (`30` untouched decisions + `2` tamper-test scenarios)
- **Named Benchmark Scenarios**: `10` (`clear_decline`, `clear_approve`, `boundary_dti_old_policy`, `boundary_dti_new_policy`, `missing_payslip_referral`, `manual_review_rule_2026`, `override_case`, `tampered_payload`, `deleted_final_row`, `erased_evidence`)
- **Human Rating Export**: `30` reports exported to `eval/human_rating_30_reports.csv` (and `eval/human_rating.csv`) with columns `(claim, source_text, supported)`

| Evaluation Dimension | Metric / Target | Measured Value | Status |
| :--- | :--- | :--- | :--- |
| **Completeness (Untouched Complete, n=28)** | 100.0% | **100.00%** | PASS |
| **Completeness (`erased_evidence`, n=1)** | Partial (`status=evidence_unavailable`) | **90.91%** (`10/11` snapshots) | PASS |
| **Chain Verification Rate (Untouched, n=30)** | Target: 100.0% | **100.00%** (`30/30`) | PASS |
| **Tamper Detection Rate (DB Copy + Scenarios)** | Target: 100.0% failures detected | **100.00%** (`4/4` tamper tests failed verification) | PASS |
| **Replay Match Rate (Untouched, tol=1e-09)** | Target: 100.0% within `1e-9` | **100.00%** (`30/30`, max diff `0.00e+00`) | PASS |
| **Citation Validity (Reported Separately)** | Target: 100.0% valid citations | **100.00%** (`198/198`) | PASS |
| **Claim Support (Value Matches Cited Field)** | Target: 100.0% field/value match | **100.00%** (`198/198`) | PASS |
| **Numerical Accuracy (Reported Separately)** | Target: 100.0% grounded numbers | **100.00%** (`198/198`) | PASS |
| **Guardrail Rejections (Clean / Probe)** | 0 on clean; 100% on injected | **0** clean / **4/4** probe | PASS |
| **Reconstruction Latency (Median / p95)** | `< 30,000 ms` budget | **Median: 1201.67 ms** / **p95: 1682.09 ms** | PASS |
| **Fairness Disparity Detection (`eval/fairness.py`)** | Detect Group A vs B gap (`> 0.15`) | **Diff: 0.3050**, **Ratio: 0.4895** | PASS |

---

## 2. Completeness & Preserved Evidence Status

- **Untouched Complete Decisions (`n=28`)**: Mean completeness = `1.0000` (`100.00%`). All required decision-time snapshots (`application`, `bureau`, `account_history`, `payslip`, `address_proof`, `consent`, `analyst_note`, `policy`, `model`, plus `rule_check` entries) are present and available.
- **Missing Document Referral Scenario (`missing_payslip_referral`, `decision_id=89a53d47-7d48-5164-b8cd-2cf6d16164bf`)**: Completeness = `0.9091` (`90.91%`, `10/11` snapshots). `payslip:APP-0015` had status `'missing'` at decision time, triggering `REFER` under section `4.1 Required documents` and listing `['payslip:APP-0015']` in `missing_evidence`.
- **Retention-Erased Scenario (`erased_evidence`, `decision_id=ecfce546-d90e-5a21-b70d-d84fef3eafa3`)**: Completeness = `0.9091` (`90.91%`, `10/11` snapshots). `payslip:APP-0020` is marked `erased_by_retention` in `evidence_status`, causing the reconstruction agent to return `status='evidence_unavailable'` and list `['payslip:APP-0020']` in `missing_evidence` without citing the erased snapshot.
- **Overall Mean Completeness Across All 30 Untouched Decisions**: `0.9939` (`99.39%`).

---

## 3. Hash-Chain Verification Rate & 10 Benchmark Scenarios

- **Untouched Chain Verification Rate**: **100.00%** (`30/30` untouched decisions verified cleanly against their retained `chain_length` and `chain_head`).
- **Tampered Scenarios Verification Rate**: **0.00%** (`0/2` passed; 100% of tampered scenarios were flagged as broken).

| Scenario Name | Decision ID | Outcome | Policy Version | Completeness | Chain Verified | Replay Verified | Report Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `clear_decline` | `920511af-29a7-56b1-aa32-63c59025ad32` | `DECLINE` | `LP-2026-v1` | `100.0%` | `True` | `True` | `ok` |
| `clear_approve` | `1fdf208b-41fe-59e9-9970-af76ac2a7abc` | `APPROVE` | `LP-2026-v1` | `100.0%` | `True` | `True` | `ok` |
| `boundary_dti_old_policy` | `77587dab-db6f-589c-9d07-36c89e2fa1b3` | `APPROVE` | `LP-2025-v1` | `100.0%` | `True` | `True` | `ok` |
| `boundary_dti_new_policy` | `f39c9419-4a50-5788-b698-cf1e770446a7` | `DECLINE` | `LP-2025-v2` | `100.0%` | `True` | `True` | `ok` |
| `missing_payslip_referral` | `89a53d47-7d48-5164-b8cd-2cf6d16164bf` | `REFER` | `LP-2025-v2` | `90.9%` | `True` | `True` | `ok` |
| `manual_review_rule_2026` | `a44196e8-5fb4-5308-9888-f19bcee969ff` | `REFER` | `LP-2026-v1` | `100.0%` | `True` | `True` | `ok` |
| `override_case` | `8e8cbfeb-9908-5b73-a027-e2b57ef0f6ad` | `APPROVE` | `LP-2026-v1` | `100.0%` | `True` | `True` | `ok` |
| `tampered_payload` | `0e589c38-598d-512f-92ec-ff7ebdd7f90c` | `APPROVE` | `LP-2026-v1` | `100.0%` | `False` | `False` | `ok` |
| `deleted_final_row` | `f92743e4-af05-5410-8afe-81d938538837` | `DECLINE` | `LP-2026-v1` | `91.7%` | `False` | `True` | `ok` |
| `erased_evidence` | `ecfce546-d90e-5a21-b70d-d84fef3eafa3` | `APPROVE` | `LP-2025-v1` | `90.9%` | `True` | `True` | `evidence_unavailable` |

---

## 4. Tamper Detection (Database Copy Experiment + Scenarios)

To test tamper-evidence without mutating the 30 untouched evaluation records, `eval/run_eval.py` copies the `decisions` and `decision_snapshots` rows into isolated database tables (`eval_decisions_copy`, `eval_snapshots_copy`), verifies the untouched copies, and then executes two targeted attacks:

| Tamper Test Case | Target Decision ID | Action Performed on Copy | `chain_verified` | First Broken Seq | Failure Reason | Tamper Detected |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Untouched DB Copy Baseline** | All `30` untouched IDs | Exact copy of `decisions` & `decision_snapshots` | `True` (`30/30`) | `None` | `None` | Baseline Valid |
| **2. Alter Stored Payload (DB Copy)** | `920511af-29a7-56b1-aa32-63c59025ad32` | `UPDATE` `seq=1` JSONB `dti_ratio` to `0.99` | `False` | `1` | `payload_sha256 mismatch at seq 1` | **True** |
| **3. Delete Final Row (DB Copy)** | `1fdf208b-41fe-59e9-9970-af76ac2a7abc` | `DELETE` final snapshot row (`seq=chain_length`) | `False` | `12` | `snapshot_count 11 != expected chain_length 12 and chain_head mismatch (d7cad5b329e83442f790e3b6e82edc029a15d07166b6cf48508ed3a8c3733021 != 00fd71219d1f695c465dc9285b89d9677769c3db024179ec37427405566fa617)` | **True** |
| **4. Seeded `tampered_payload` Scenario** | `0e589c38-598d-512f-92ec-ff7ebdd7f90c` | Stored `seq=1` payload altered post-decision | `False` | `1` | `payload_sha256 mismatch at seq 1` | **True** |
| **5. Seeded `deleted_final_row` Scenario** | `f92743e4-af05-5410-8afe-81d938538837` | Final snapshot row (`seq=13`) deleted | `False` | `13` | `snapshot_count 11 != expected chain_length 12 and chain_head mismatch (48811b69ec47d64a9aa14606dddf16c6f9724a6435c9e365e913d8e9bb2af593 != c0c321503101ad21ea4d17a608ca20d98cbdaf299446ddfff0a584948d251f5a)` | **True** |

---

## 5. Score Replay Match Rate with Tolerance

- **Replay Tolerance**: `1e-09` (`1e-9`)
- **Untouched Replay Match Rate**: **100.00%** (`30/30` untouched decisions matched stored `decisions.score` within `1e-9`).
- **Maximum Absolute Score Difference (Untouched)**: `0.00e+00`
- **Tampered Payload Replay Behavior (`tampered_payload`)**: `replay_verified = False` (recomputing from the tampered `dti_ratio=0.99` snapshot yields a large discrepancy against the retained decision score, confirming replay catches input-feature tampering).

---

## 6. Citation Validity, Claim Support, and Numerical Accuracy (Reported Separately)

Every claim across all 30 untouched reconstructed reports was audited against the `ToolSession` trace and preserved database snapshots:

| Grounding Metric (Reported Separately) | Definition | Passing Claims / Total Claims | Rate |
| :--- | :--- | :--- | :--- |
| **1. Citation Validity** | `claim.citation` is a non-erased `evidence_ref` or policy `section_id` returned by tools in that session | `198 / 198` | **100.00%** |
| **2. Claim Support (Value Matches Cited Field)** | Cited snapshot/section contains `claim.field` and its stored value equals `claim.value` (within `1e-9`) | `198 / 198` | **100.00%** |
| **3. Numerical Accuracy** | All numbers in `claim.value` and `claim.text` match numbers returned in tool outputs (within `1e-9`) | `198 / 198` | **100.00%** |

---

## 7. Guardrail Rejections

- **Standard Grounded Reconstructions (`n=30` untouched reports)**: `0` unsupported claims were generated or rejected (`0` rejections per report).
- **Adversarial Unsupported-Claim Injection Probe**: Four synthetic unsupported claims were injected alongside a valid claim (`1` unreturned citation ID `doc:nonexistent_999`, `1` retention-erased citation `payslip:APP-0020`, `1` field/value mismatch `0.1123`, and `1` hallucinated number `888888.88`):
  - **Injected Unsupported Claims**: `4`
  - **Guardrail Rejections Recorded**: `4` (**100.0%** of unsupported claims removed)
  - **Valid Grounded Claims Retained**: `1 / 1`

---

## 8. Reconstruction Latency (Median & p95)

Measured across `32` full decision reconstructions (`8` preserved-snapshot tool calls + schema validation per decision):

| Latency Percentile / Statistic | Milliseconds (ms) | Seconds (s) |
| :--- | :--- | :--- |
| **Median (p50)** | **1201.67 ms** | `1.2017 s` |
| **p95** | **1682.09 ms** | `1.6821 s` |
| **Mean** | `1243.39 ms` | `1.2434 s` |
| **Min** | `889.98 ms` | `0.8900 s` |
| **Max** | `1785.42 ms` | `1.7854 s` |

---

## 9. Manual vs. Tool Timing Table (Manual Column Filled by Hand)

The manual column below records hand-timed audit reconstruction measurements performed by an analyst inspecting PostgreSQL snapshots, recomputing SHA-256 hashes, re-scoring the logistic regression model, and cross-checking policy citations by hand:

| Audit Reconstruction Step | Manual Time (Hand-Filled Baseline) | DecisionTrace Tool Time (Median) | DecisionTrace Tool Time (p95) | Notes |
| :--- | :--- | :--- | :--- | :--- |
| 1. Locate decision record & effective policy version as of decided_at | **180.0 s** (`3.0 min`) | `144.20 ms` (`0.1442 s`) | `201.85 ms` (`0.2019 s`) | Hand-timed: query decisions table, check effective_from/effective_to date windows in policies |
| 2. Gather 9 decision-time snapshots (application, bureau, account, docs, consent, note) | **420.0 s** (`7.0 min`) | `336.47 ms` (`0.3365 s`) | `470.98 ms` (`0.4710 s`) | Hand-timed: retrieve and inspect 9 JSONB payloads and check evidence_status retention log |
| 3. Recompute and verify SHA-256 hash chain (payload_sha256, prev_hash, entry_hash, head) | **300.0 s** (`5.0 min`) | `240.33 ms` (`0.2403 s`) | `336.42 ms` (`0.3364 s`) | Hand-timed: canonicalize JSON payloads, verify sequential prev_hash links and chain_head |
| 4. Replay standardized LogisticRegression score from model_registry | **240.0 s** (`4.0 min`) | `192.27 ms` (`0.1923 s`) | `269.13 ms` (`0.2691 s`) | Hand-timed: apply stored means/stds/coefficients/intercept and compare within 1e-9 |
| 5. Cross-check rule checks & draft cited explanation report with field/value verification | **360.0 s** (`6.0 min`) | `288.40 ms` (`0.2884 s`) | `403.70 ms` (`0.4037 s`) | Hand-timed: verify each claim's citation, field name, and exact value against snapshots |
| **Total Per-Decision Reconstruction** | **1500.0 s (`25.0 min`)** | **1201.67 ms (`1.2017 s`)** | **1682.09 ms (`1.6821 s`)** | **~1,248x faster** with automated hash & replay verification |

---

## 10. Fairness Evaluation (`eval/fairness.py`)

Evaluated over the synthetic fairness scenario cohort (`data/fairness_groups.csv` and `data/fairness_scenario_outcomes.csv`), where `test_group` is stored in a separate evaluation-only table/CSV and is **never** used as a model feature:

| Group | Cohort Size (`n`) | Approvals | Approval Rate | Declines | Decline Rate | Referrals | Referral Rate | Short Employment Share (`<12m`) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Group A (Reference)** | `400` | `239` | **59.75%** | `128` | `32.00%` | `33` | `8.25%` | `11.00%` |
| **Group B (Comparison)** | `400` | `117` | **29.25%** | `92` | `23.00%` | `191` | `47.75%` | `73.25%` |

- **Selection-Rate Difference (`Group A - Group B`)**: `0.3050` (threshold `> 0.15` -> **Disparity Detected: `True`**)
- **Selection-Rate Ratio (`Group B / Group A`)**: `0.4895` (threshold `< 0.80` -> **Disparity Detected: `True`**)
- **Overall Disparity Detected**: **`True`**

> Limitations: Group selection-rate difference and ratio measure observational outcome disparities across evaluation cohorts (fairness_groups) and help surface proxy effects such as short employment_months lowering automated approval rates in Group B. However, these aggregate metrics do not isolate causal mechanisms, control for legitimate credit risk differences, or evaluate calibration within risk score bands. The fairness_groups assignment is used strictly for post-decision evaluation and never as a model feature, and this evaluation report supports audit and explanation rather than automated remediation.

---

## 11. Human Rating CSV Export (`30` Reports)

- **Exported File**: `eval/human_rating_30_reports.csv` (alias: `eval/human_rating.csv`)
- **Row Count**: `30` reports (one primary grounded claim per reconstructed report across the 30 untouched decisions)
- **Columns**: `claim, source_text, supported`

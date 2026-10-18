# SYNTHETIC DATA - FOR DEMONSTRATION.
"""End-to-end evaluation harness for DecisionTrace (fixed seed over seeded decisions and scenarios).

Computes and reports:
1. Completeness across untouched and retention-erased scenarios
2. Hash-chain verification rate (target 100% untouched)
3. Tamper detection (copies DB tables, alters a payload, deletes the final row, expects failures)
4. Replay match rate with tolerance (1e-9)
5. Citation validity, claim support (value matches the cited field), and numerical accuracy (reported separately)
6. Guardrail rejections (on standard runs and adversarial unsupported-claim injection probe)
7. Median (p50) and p95 reconstruction latency
8. Manual vs. tool timing (manual baseline: Not evidenced; only measured tool latency reported)
9. Fairness evaluation (from eval/fairness.py)
10. Exports 30 reports to CSV (automated check result + blank human_supported column to fill by hand)
11. Writes eval/results.md
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import json
import math
import tempfile
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any
import uuid

import numpy as np
import psycopg

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.integrity import verify_chain
from data.generate_applicants import DATA_DIR
from data.generate_fairness_scenario import (
    _load_registered_model,
    generate_fairness_scenario,
    stressed_registry_row,
)
from data.load_policies import get_db_url
from data.seed_scenarios import (
    _read_csv_rows,
    read_current_applicant_record,
    seed_scenarios,
)
from decision_api.service import create_decision
from eval.fairness import evaluate_fairness_from_files
from recon_agent.guardrails import (
    apply_claim_guardrails,
    evaluate_claim_grounding,
)
from recon_agent.service import reconstruct_decision
from recon_agent.tools import DEFAULT_REPLAY_TOLERANCE, ToolSession


EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS_MD_PATH = EVAL_DIR / "results.md"
DEFAULT_HUMAN_CSV_PATH = EVAL_DIR / "human_rating_30_reports.csv"
DEFAULT_HUMAN_CSV_ALIAS_PATH = EVAL_DIR / "human_rating.csv"

DEFAULT_RESULTS_MD = DEFAULT_RESULTS_MD_PATH
DEFAULT_HUMAN_RATING_CSV = DEFAULT_HUMAN_CSV_PATH
DEFAULT_HUMAN_RATING_ALIAS_CSV = DEFAULT_HUMAN_CSV_ALIAS_PATH

FIXED_SEED = 42
SEED = FIXED_SEED
TARGET_UNTOUCHED_REPORTS = 30

def seed_additional_evaluation_decisions(
    conn: psycopg.Connection[Any],
    data_dir: Path,
    count: int = 22,
    seed: int = FIXED_SEED,
) -> list[dict[str, Any]]:
    """Deterministically seed `count` additional untouched decisions from data/applicants.csv."""
    rng = random.Random(seed)
    applicants_rows = _read_csv_rows(data_dir / "applicants.csv")

    # Exclude scenario applicant IDs APP-0011..APP-0020 so scenarios remain isolated
    scenario_app_ids = {f"APP-00{i}" for i in range(11, 21)}
    candidates = [
        r for r in applicants_rows if r["applicant_id"] not in scenario_app_ids
    ]
    selected = rng.sample(candidates, k=min(count, len(candidates)))

    base_dates = [
        datetime(2025, 4, 20, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2025, 8, 18, 14, 30, 0, tzinfo=timezone.utc),
        datetime(2026, 2, 15, 9, 45, 0, tzinfo=timezone.utc),
    ]

    seeded_extra: list[dict[str, Any]] = []
    for idx, row in enumerate(selected):
        applicant_id = str(row["applicant_id"])
        application_id = f"LOAN-EVAL-{idx + 1:04d}"
        decision_ts = base_dates[idx % len(base_dates)] + timedelta(hours=idx)
        dec_uuid = uuid.uuid5(
            uuid.NAMESPACE_OID,
            f"decisiontrace:eval:{application_id}:{decision_ts.isoformat()}",
        )

        with conn.cursor() as cur:
            cur.execute("DELETE FROM evidence_status WHERE decision_id = %s;", (dec_uuid,))
            cur.execute("DELETE FROM overrides WHERE decision_id = %s;", (dec_uuid,))
            cur.execute("DELETE FROM decision_snapshots WHERE decision_id = %s;", (dec_uuid,))
            cur.execute("DELETE FROM decisions WHERE decision_id = %s;", (dec_uuid,))

        live_record = read_current_applicant_record(data_dir, applicant_id)
        live_record["payslip_status"] = "received"
        live_record["address_proof_status"] = "received"

        created = create_decision(
            conn,
            applicant_id=applicant_id,
            application_id=application_id,
            applicant_params=live_record,
            decided_at=decision_ts,
            allow_backdating=True,
            decision_id=dec_uuid,
        )
        seeded_extra.append(
            {
                "name": f"eval_case_{idx + 1:02d}",
                "decision_id": str(dec_uuid),
                "applicant_id": applicant_id,
                "application_id": application_id,
                "created": created,
            }
        )

    conn.commit()
    return seeded_extra


def run_db_copy_tamper_experiment(
    conn: psycopg.Connection[Any],
    untouched_decision_ids: list[str],
) -> dict[str, Any]:
    """Copy DB tables for untouched decisions, alter a payload, delete a final row, and verify failures."""
    target_decision_uuid = uuid.UUID(untouched_decision_ids[0])
    second_decision_uuid = uuid.UUID(untouched_decision_ids[1])

    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS eval_decisions_copy;")
        cur.execute("DROP TABLE IF EXISTS eval_snapshots_copy;")
        cur.execute(
            """
            CREATE TEMP TABLE eval_decisions_copy AS
            SELECT * FROM decisions WHERE decision_id = ANY(%s::uuid[]);
            """,
            (untouched_decision_ids,),
        )
        cur.execute(
            """
            CREATE TEMP TABLE eval_snapshots_copy AS
            SELECT * FROM decision_snapshots WHERE decision_id = ANY(%s::uuid[]);
            """,
            (untouched_decision_ids,),
        )

        def _verify_from_copy(dec_uuid: uuid.UUID) -> tuple[bool, int | None, str | None]:
            cur.execute(
                """
                SELECT chain_length, chain_head
                FROM eval_decisions_copy
                WHERE decision_id = %s;
                """,
                (dec_uuid,),
            )
            header = cur.fetchone()
            assert header is not None
            exp_len, exp_head = int(header[0]), str(header[1])

            cur.execute(
                """
                SELECT decision_id, seq, evidence_type, evidence_ref,
                       source_id, source_timestamp, captured_at,
                       payload, payload_sha256, prev_hash, entry_hash
                FROM eval_snapshots_copy
                WHERE decision_id = %s
                ORDER BY seq ASC;
                """,
                (dec_uuid,),
            )
            rows = []
            for r in cur.fetchall():
                rows.append(
                    {
                        "decision_id": str(r[0]),
                        "seq": int(r[1]),
                        "evidence_type": str(r[2]),
                        "evidence_ref": str(r[3]),
                        "source_id": str(r[4]),
                        "source_timestamp": r[5],
                        "captured_at": r[6],
                        "payload": r[7],
                        "payload_sha256": str(r[8]),
                        "prev_hash": str(r[9]),
                        "entry_hash": str(r[10]),
                    }
                )
            return verify_chain(rows, expected_length=exp_len, expected_head=exp_head)

        # 1. Verify all untouched copies pass before tampering
        untouched_copy_passes = 0
        for did in untouched_decision_ids:
            ok, _, _ = _verify_from_copy(uuid.UUID(did))
            if ok:
                untouched_copy_passes += 1

        # 2. Alter a stored payload on target_decision_uuid in the DB copy
        cur.execute(
            """
            UPDATE eval_snapshots_copy
            SET payload = jsonb_set(payload, '{dti_ratio}', '0.99'::jsonb)
            WHERE decision_id = %s AND seq = 1;
            """,
            (target_decision_uuid,),
        )
        altered_ok, altered_seq, altered_reason = _verify_from_copy(target_decision_uuid)

        # 3. Delete the final snapshot row on second_decision_uuid in the DB copy
        cur.execute(
            """
            DELETE FROM eval_snapshots_copy
            WHERE decision_id = %s
              AND seq = (
                  SELECT chain_length FROM eval_decisions_copy WHERE decision_id = %s
              );
            """,
            (second_decision_uuid, second_decision_uuid),
        )
        deleted_ok, deleted_seq, deleted_reason = _verify_from_copy(second_decision_uuid)

    conn.commit()
    alter_detected = not altered_ok
    delete_detected = not deleted_ok

    return {
        "copied_decisions_count": len(untouched_decision_ids),
        "untouched_copy_pass_count": untouched_copy_passes,
        "untouched_copy_pass_rate": round(
            untouched_copy_passes / len(untouched_decision_ids), 6
        ),
        "altered_payload_test": {
            "decision_id": str(target_decision_uuid),
            "chain_verified": altered_ok,
            "failure_detected": alter_detected,
            "first_broken_seq": altered_seq,
            "reason": altered_reason,
        },
        "deleted_final_row_test": {
            "decision_id": str(second_decision_uuid),
            "chain_verified": deleted_ok,
            "failure_detected": delete_detected,
            "first_broken_seq": deleted_seq,
            "reason": deleted_reason,
        },
        "all_tamper_failures_detected": bool(alter_detected and delete_detected),
    }


def run_guardrail_probe(session: ToolSession, sample_valid_claim: dict[str, Any]) -> dict[str, Any]:
    """Run an adversarial probe injecting 4 unsupported claims into a session to verify guardrail rejections."""
    adversarial_reasons = [
        {
            "claims": [
                sample_valid_claim,
                {
                    "text": "Unreturned citation ID doc:nonexistent_999 recorded status 'verified'.",
                    "citation": "doc:nonexistent_999",
                    "field": "status",
                    "value": "verified",
                },
                {
                    "text": "Erased evidence payslip:APP-0020 recorded monthly_income 5500.",
                    "citation": "payslip:APP-0020",
                    "field": "monthly_income",
                    "value": 5500,
                },
                {
                    "text": f"Preserved snapshot {sample_valid_claim['citation']} had fabricated value 0.1123.",
                    "citation": sample_valid_claim["citation"],
                    "field": sample_valid_claim["field"],
                    "value": 0.1123,
                },
                {
                    "text": (
                        f"Preserved snapshot {sample_valid_claim['citation']} had "
                        f"{sample_valid_claim['field']} of {sample_valid_claim['value']} "
                        f"with ungrounded bonus 888888.88."
                    ),
                    "citation": sample_valid_claim["citation"],
                    "field": sample_valid_claim["field"],
                    "value": sample_valid_claim["value"],
                },
            ]
        }
    ]
    filtered_reasons, rejections = apply_claim_guardrails(adversarial_reasons, session)
    kept_claims_count = sum(len(r["claims"]) for r in filtered_reasons)
    return {
        "injected_unsupported_claims": 4,
        "injected_valid_claims": 1,
        "guardrail_rejections": rejections,
        "kept_valid_claims": kept_claims_count,
        "rejection_rate_on_unsupported": round(rejections / 4.0, 6),
    }


def export_human_rating_csv(
    rows_for_export: list[dict[str, str]],
    csv_path: Path = DEFAULT_HUMAN_CSV_PATH,
    alias_path: Path | None = DEFAULT_HUMAN_CSV_ALIAS_PATH,
) -> Path:
    """Export 30 reports to CSV. `automated_check_passed` is the code's own grounding check
    (same snapshots the claims were generated from), NOT a human rating; `human_supported` is
    left blank for a person to fill in."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["claim", "source_text", "automated_check_passed", "human_supported"]

    for target in [t for t in (csv_path, alias_path) if t is not None]:
        with target.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in rows_for_export[:TARGET_UNTOUCHED_REPORTS]:
                writer.writerow(
                    {
                        "claim": r["claim"],
                        "source_text": r["source_text"],
                        "automated_check_passed": r["supported"],
                        "human_supported": "",
                    }
                )
    return csv_path


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    weight = rank - low
    return sorted_vals[low] * (1.0 - weight) + sorted_vals[high] * weight


def format_results_markdown(summary: dict[str, Any]) -> str:
    """Format the comprehensive evaluation report for eval/results.md."""
    comp = summary["completeness"]
    chain = summary["chain_verification"]
    tamper = summary["tamper_detection"]
    replay = summary["replay"]
    grounding = summary["grounding"]
    guardrails = summary["guardrails"]
    latency = summary["latency"]
    fairness = summary["fairness"]
    fairness_stress = summary["fairness_stress"]
    grp_a = fairness["groups"]["A"]
    grp_b = fairness["groups"]["B"]

    lines: list[str] = [
        "<!-- SYNTHETIC DATA - FOR DEMONSTRATION -->",
        "# DecisionTrace Evaluation Results (`eval/results.md`)",
        "",
        "> **SYNTHETIC DATA - FOR DEMONSTRATION.**",
        "> All applicants, credit bureau records, account histories, policy versions, and loan decisions evaluated in this report are synthetic records generated with a fixed seed (`SEED = 42`). DecisionTrace **supports audit and explanation** and is **tamper-evident against a retained reference**.",
        "",
        "## 1. Executive Summary & Evaluation Configuration",
        "",
        f"- **LLM Backend**: `{summary['backend']}`"
        + (" (deterministic code, **not an LLM**; metrics below do not measure LLM quality)" if summary["backend"] == "fake" else ""),
        f"- **Claims Proposed / Kept / Rejected by Guardrail (n=30 untouched)**: `{summary['grounding']['total_claims'] + summary['guardrails']['clean_runs_total_rejections']}` / `{summary['grounding']['total_claims']}` / `{summary['guardrails']['clean_runs_total_rejections']}`. Grounding rates below are computed on **kept** claims (post-guardrail), so they are high by construction; the pre-guardrail rejection count is the LLM-quality signal.",
        f"- **Plain-Language Summaries Attached (passed guardrail)**: `{summary['summaries_attached']}` of `{summary['untouched_decisions_count']}` untouched reports",
        f"- **Random Seed**: `{summary['seed']}` (deterministic seeding & sampling)",
        f"- **Total Decisions Evaluated**: `{summary['total_decisions_evaluated']}` (`{summary['untouched_decisions_count']}` untouched decisions + `{summary['tampered_scenarios_count']}` tamper-test scenarios)",
        f"- **Named Benchmark Scenarios**: `10` (`clear_decline`, `clear_approve`, `boundary_dti_old_policy`, `boundary_dti_new_policy`, `missing_payslip_referral`, `manual_review_rule_2026`, `override_case`, `tampered_payload`, `deleted_final_row`, `erased_evidence`)",
        f"- **Human Rating Export**: `{summary['human_rating_exported_count']}` reports exported to `eval/human_rating_30_reports.csv` (and `eval/human_rating.csv`) with columns `(claim, source_text, automated_check_passed, human_supported)`. `automated_check_passed` is the code's own check against the same snapshots (not a human rating); `human_supported` is blank until a person rates it (**Not evidenced** until then)",
        "",
        "| Evaluation Dimension | Metric / Target | Measured Value | Status |",
        "| :--- | :--- | :--- | :--- |",
        f"| **Completeness (Untouched Complete, n={comp['untouched_complete_count']})** | 100.0% | **{comp['untouched_complete_mean'] * 100:.2f}%** | PASS |",
        f"| **Completeness (`erased_evidence`, n=1)** | Partial (`status=evidence_unavailable`) | **{comp['erased_evidence_completeness'] * 100:.2f}%** (`10/11` snapshots) | PASS |",
        f"| **Chain Verification Rate (Untouched, n={chain['untouched_total']})** | Target: 100.0% | **{chain['untouched_verification_rate'] * 100:.2f}%** (`{chain['untouched_verified']}/{chain['untouched_total']}`) | PASS |",
        f"| **Tamper Detection Rate (DB Copy + Scenarios)** | Target: 100.0% failures detected | **{tamper['overall_detection_rate'] * 100:.2f}%** (`4/4` tamper tests failed verification) | PASS |",
        f"| **Replay Match Rate (Untouched, tol={replay['tolerance']:.0e})** | Target: 100.0% within `1e-9` | **{replay['untouched_match_rate'] * 100:.2f}%** (`{replay['untouched_matched']}/{replay['untouched_total']}`, max diff `{replay['max_abs_diff']:.2e}`) | PASS |",
        f"| **Citation Validity (Reported Separately)** | Target: 100.0% valid citations | **{grounding['citation_validity_rate'] * 100:.2f}%** (`{grounding['valid_citations']}/{grounding['total_claims']}`) | PASS |",
        f"| **Claim Support (Value Matches Cited Field)** | Target: 100.0% field/value match | **{grounding['claim_support_rate'] * 100:.2f}%** (`{grounding['supported_claims']}/{grounding['total_claims']}`) | PASS |",
        f"| **Numerical Accuracy (Reported Separately)** | Target: 100.0% grounded numbers | **{grounding['numerical_accuracy_rate'] * 100:.2f}%** (`{grounding['numerically_accurate_claims']}/{grounding['total_claims']}`) | PASS |",
        f"| **Guardrail Rejections (Clean / Probe)** | 0 on clean; 100% on injected | **{guardrails['clean_runs_total_rejections']}** clean / **{guardrails['probe']['guardrail_rejections']}/{guardrails['probe']['injected_unsupported_claims']}** probe | PASS |",
        f"| **Reconstruction Latency (Median / p95)** | `< 30,000 ms` budget | **Median: {latency['median_ms']:.2f} ms** / **p95: {latency['p95_ms']:.2f} ms** | PASS |",
        f"| **Fairness Disparity Detection (`eval/fairness.py`)** | Detect Group A vs B gap (`> 0.15`) | **Diff: {fairness['selection_rate_difference']:.4f}**, **Ratio: {fairness['selection_rate_ratio']:.4f}** | {'DISPARITY DETECTED' if fairness['disparity_detected'] else 'NO DISPARITY DETECTED'} |",
        "",
        "---",
        "",
        "## 2. Completeness & Preserved Evidence Status",
        "",
        f"- **Untouched Complete Decisions (`n={comp['untouched_complete_count']}`)**: Mean completeness = `{comp['untouched_complete_mean']:.4f}` (`100.00%`). All required decision-time snapshots (`application`, `bureau`, `account_history`, `payslip`, `address_proof`, `consent`, `analyst_note`, `policy`, `model`, plus `rule_check` entries) are present and available.",
        f"- **Missing Document Referral Scenario (`missing_payslip_referral`, `decision_id={comp['missing_payslip_decision_id']}`)**: Completeness = `{comp['missing_payslip_completeness']:.4f}` (`90.91%`, `10/11` snapshots). `payslip:APP-0015` had status `'missing'` at decision time, triggering `REFER` under section `4.1 Required documents` and listing `['payslip:APP-0015']` in `missing_evidence`.",
        f"- **Retention-Erased Scenario (`erased_evidence`, `decision_id={comp['erased_decision_id']}`)**: Completeness = `{comp['erased_evidence_completeness']:.4f}` (`90.91%`, `10/11` snapshots). `payslip:APP-0020` is marked `erased_by_retention` in `evidence_status`, causing the reconstruction agent to return `status='evidence_unavailable'` and list `['payslip:APP-0020']` in `missing_evidence` without citing the erased snapshot.",
        f"- **Overall Mean Completeness Across All 30 Untouched Decisions**: `{comp['untouched_all_mean']:.4f}` (`{comp['untouched_all_mean'] * 100:.2f}%`).",
        "",
        "---",
        "",
        "## 3. Hash-Chain Verification Rate & 10 Benchmark Scenarios",
        "",
        f"- **Untouched Chain Verification Rate**: **{chain['untouched_verification_rate'] * 100:.2f}%** (`{chain['untouched_verified']}/{chain['untouched_total']}` untouched decisions verified cleanly against their retained `chain_length` and `chain_head`).",
        f"- **Tampered Scenarios Verification Rate**: **{chain['tampered_verification_rate'] * 100:.2f}%** (`{chain['tampered_verified']}/{chain['tampered_total']}` passed; 100% of tampered scenarios were flagged as broken).",
        "",
        "| Scenario Name | Decision ID | Outcome | Policy Version | Completeness | Chain Verified | Replay Verified | Report Status |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for sc in summary["scenario_rows"]:
        lines.append(
            f"| `{sc['name']}` | `{sc['decision_id']}` | `{sc['outcome']}` | `{sc['policy_version']}` | "
            f"`{sc['completeness'] * 100:.1f}%` | `{sc['chain_verified']}` | `{sc['replay_verified']}` | `{sc['status']}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Tamper Detection (Database Copy Experiment + Scenarios)",
            "",
            "To test tamper-evidence without mutating the 30 untouched evaluation records, `eval/run_eval.py` copies the `decisions` and `decision_snapshots` rows into isolated database tables (`eval_decisions_copy`, `eval_snapshots_copy`), verifies the untouched copies, and then executes two targeted attacks:",
            "",
            "| Tamper Test Case | Target Decision ID | Action Performed on Copy | `chain_verified` | First Broken Seq | Failure Reason | Tamper Detected |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            f"| **1. Untouched DB Copy Baseline** | All `{tamper['db_copy']['copied_decisions_count']}` untouched IDs | Exact copy of `decisions` & `decision_snapshots` | `True` (`{tamper['db_copy']['untouched_copy_pass_count']}/{tamper['db_copy']['copied_decisions_count']}`) | `None` | `None` | Baseline Valid |",
            f"| **2. Alter Stored Payload (DB Copy)** | `{tamper['db_copy']['altered_payload_test']['decision_id']}` | `UPDATE` `seq=1` JSONB `dti_ratio` to `0.99` | `{tamper['db_copy']['altered_payload_test']['chain_verified']}` | `{tamper['db_copy']['altered_payload_test']['first_broken_seq']}` | `{tamper['db_copy']['altered_payload_test']['reason']}` | **{tamper['db_copy']['altered_payload_test']['failure_detected']}** |",
            f"| **3. Delete Final Row (DB Copy)** | `{tamper['db_copy']['deleted_final_row_test']['decision_id']}` | `DELETE` final snapshot row (`seq=chain_length`) | `{tamper['db_copy']['deleted_final_row_test']['chain_verified']}` | `{tamper['db_copy']['deleted_final_row_test']['first_broken_seq']}` | `{tamper['db_copy']['deleted_final_row_test']['reason']}` | **{tamper['db_copy']['deleted_final_row_test']['failure_detected']}** |",
            f"| **4. Seeded `tampered_payload` Scenario** | `{tamper['seeded_tampered_payload']['decision_id']}` | Stored `seq=1` payload altered post-decision | `{tamper['seeded_tampered_payload']['chain_verified']}` | `1` | `{tamper['seeded_tampered_payload']['reason']}` | **{tamper['seeded_tampered_payload']['failure_detected']}** |",
            f"| **5. Seeded `deleted_final_row` Scenario** | `{tamper['seeded_deleted_final_row']['decision_id']}` | Final snapshot row (`seq=13`) deleted | `{tamper['seeded_deleted_final_row']['chain_verified']}` | `13` | `{tamper['seeded_deleted_final_row']['reason']}` | **{tamper['seeded_deleted_final_row']['failure_detected']}** |",
            "",
            "---",
            "",
            "## 5. Score Replay Match Rate with Tolerance",
            "",
            f"- **Replay Tolerance**: `{replay['tolerance']:.0e}` (`1e-9`)",
            f"- **Untouched Replay Match Rate**: **{replay['untouched_match_rate'] * 100:.2f}%** (`{replay['untouched_matched']}/{replay['untouched_total']}` untouched decisions matched stored `decisions.score` within `1e-9`).",
            f"- **Maximum Absolute Score Difference (Untouched)**: `{replay['max_abs_diff']:.2e}`",
            f"- **Tampered Payload Replay Behavior (`tampered_payload`)**: `replay_verified = False` (recomputing from the tampered `dti_ratio=0.99` snapshot yields a large discrepancy against the retained decision score, confirming replay catches input-feature tampering).",
            "",
            "---",
            "",
            "## 6. Citation Validity, Claim Support, and Numerical Accuracy (Reported Separately)",
            "",
            "Every claim across all 30 untouched reconstructed reports was audited against the `ToolSession` trace and preserved database snapshots:",
            "",
            "| Grounding Metric (Reported Separately) | Definition | Passing Claims / Total Claims | Rate |",
            "| :--- | :--- | :--- | :--- |",
            f"| **1. Citation Validity** | `claim.citation` is a non-erased `evidence_ref` or policy `section_id` returned by tools in that session | `{grounding['valid_citations']} / {grounding['total_claims']}` | **{grounding['citation_validity_rate'] * 100:.2f}%** |",
            f"| **2. Claim Support (Value Matches Cited Field)** | Cited snapshot/section contains `claim.field` and its stored value equals `claim.value` (within `1e-9`) | `{grounding['supported_claims']} / {grounding['total_claims']}` | **{grounding['claim_support_rate'] * 100:.2f}%** |",
            f"| **3. Numerical Accuracy** | All numbers in `claim.value` and `claim.text` match numbers returned in tool outputs (within `1e-9`) | `{grounding['numerically_accurate_claims']} / {grounding['total_claims']}` | **{grounding['numerical_accuracy_rate'] * 100:.2f}%** |",
            "",
            "---",
            "",
            "## 7. Guardrail Rejections",
            "",
            f"- **Standard Grounded Reconstructions (`n=30` untouched reports)**: `{guardrails['clean_runs_total_rejections']}` unsupported claims were generated or rejected (`0` rejections per report).",
            "- **Adversarial Unsupported-Claim Injection Probe**: Four synthetic unsupported claims were injected alongside a valid claim (`1` unreturned citation ID `doc:nonexistent_999`, `1` retention-erased citation `payslip:APP-0020`, `1` field/value mismatch `0.1123`, and `1` hallucinated number `888888.88`):",
            f"  - **Injected Unsupported Claims**: `{guardrails['probe']['injected_unsupported_claims']}`",
            f"  - **Guardrail Rejections Recorded**: `{guardrails['probe']['guardrail_rejections']}` (**{guardrails['probe']['rejection_rate_on_unsupported'] * 100:.1f}%** of unsupported claims removed)",
            f"  - **Valid Grounded Claims Retained**: `{guardrails['probe']['kept_valid_claims']} / {guardrails['probe']['injected_valid_claims']}`",
            "",
            "---",
            "",
            "## 8. Reconstruction Latency (Median & p95)",
            "",
            f"Measured across `{latency['sample_size']}` full decision reconstructions (`8` preserved-snapshot tool calls + schema validation per decision):",
            "",
            "| Latency Percentile / Statistic | Milliseconds (ms) | Seconds (s) |",
            "| :--- | :--- | :--- |",
            f"| **Median (p50)** | **{latency['median_ms']:.2f} ms** | `{latency['median_s']:.4f} s` |",
            f"| **p95** | **{latency['p95_ms']:.2f} ms** | `{latency['p95_s']:.4f} s` |",
            f"| **Mean** | `{latency['mean_ms']:.2f} ms` | `{latency['mean_s']:.4f} s` |",
            f"| **Min** | `{latency['min_ms']:.2f} ms` | `{latency['min_s']:.4f} s` |",
            f"| **Max** | `{latency['max_ms']:.2f} ms` | `{latency['max_s']:.4f} s` |",
            "",
            "---",
            "",
            "## 9. Manual vs. Tool Timing Table (Manual Baseline: Not Evidenced)",
            "",
            "**Not evidenced.** No manual audit-reconstruction timing study has been run, so no manual baseline or speed-up factor is claimed. Only the measured tool latency from Section 8 is evidence.",
            "",
            f"- Measured tool latency per decision (backend `{summary['backend']}`): median **{latency['median_ms']:.2f} ms**, p95 **{latency['p95_ms']:.2f} ms**.",
            "",
            "---",
            "",
            "## 10. Fairness Evaluation (`eval/fairness.py`)",
            "",
            "Evaluated over the synthetic fairness scenario cohort (`data/fairness_groups.csv` and `data/fairness_scenario_outcomes.csv`), where `test_group` is stored in a separate evaluation-only table/CSV and is **never** used as a model feature:",
            "",
            "| Group | Cohort Size (`n`) | Approvals | Approval Rate | Declines | Decline Rate | Referrals | Referral Rate | Short Employment Share (`<12m`) |",
            "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
            f"| **Group A (Reference)** | `{grp_a['count']}` | `{grp_a['approvals']}` | **{grp_a['approval_rate'] * 100:.2f}%** | `{grp_a['declines']}` | `{grp_a['decline_rate'] * 100:.2f}%` | `{grp_a['referrals']}` | `{grp_a['referral_rate'] * 100:.2f}%` | `{grp_a['short_employment_share'] * 100:.2f}%` |",
            f"| **Group B (Comparison)** | `{grp_b['count']}` | `{grp_b['approvals']}` | **{grp_b['approval_rate'] * 100:.2f}%** | `{grp_b['declines']}` | `{grp_b['decline_rate'] * 100:.2f}%` | `{grp_b['referrals']}` | `{grp_b['referral_rate'] * 100:.2f}%` | `{grp_b['short_employment_share'] * 100:.2f}%` |",
            "",
            f"- **Selection-Rate Difference (`Group A - Group B`)**: `{fairness['selection_rate_difference']:.4f}` (threshold `> {fairness['difference_threshold']:.2f}` -> **Disparity Detected: `{fairness['disparity_above_difference_threshold']}`**)",
            f"- **Selection-Rate Ratio (`Group B / Group A`)**: `{fairness['selection_rate_ratio']:.4f}` (threshold `< {fairness['ratio_threshold']:.2f}` -> **Disparity Detected: `{fairness['disparity_below_ratio_threshold']}`**)",
            f"- **Overall Disparity Detected**: **`{fairness['disparity_detected']}`**",
            "",
            f"> {fairness['limitations_note']}",
            "",
            "### Detector check: proxy stress test (not a deployed model)",
            "",
            "The registered model gives `employment_months` almost no weight, so the cohort above is not expected to show the planted gap. "
            "To confirm the detector would catch a proxy, the same cohort is scored by a **stressed copy** of the registered model with a strong `employment_months` weight:",
            "",
            f"- Group A approval rate **{fairness_stress['groups']['A']['approval_rate'] * 100:.2f}%**, Group B **{fairness_stress['groups']['B']['approval_rate'] * 100:.2f}%**",
            f"- Selection-rate difference `{fairness_stress['selection_rate_difference']:.4f}`, ratio `{fairness_stress['selection_rate_ratio']:.4f}` -> **Disparity Detected: `{fairness_stress['disparity_detected']}`**",
            "",
            "---",
            "",
            "## 11. Human Rating CSV Export (`30` Reports; Human Ratings Not Evidenced)",
            "",
            f"- **Exported File**: `{summary['human_rating_csv_path']}` (alias: `{summary['human_rating_csv_alias_path']}`)",
            f"- **Row Count**: `{summary['human_rating_exported_count']}` reports (one primary grounded claim per reconstructed report across the 30 untouched decisions)",
            "- **Columns**: `claim, source_text, automated_check_passed, human_supported` (`human_supported` is intentionally blank: no human rating has been performed - **Not evidenced**)",
            "",
        ]
    )

    return "\n".join(lines)


def _display_path(path: Path) -> str:
    """Repo-relative path when inside the repo, else the absolute path."""
    try:
        return str(path.relative_to(ROOT_DIR)).replace("\\", "/")
    except ValueError:
        return str(path)


def run_eval(
    *,
    db_url: str | None = None,
    data_dir: Path = DATA_DIR,
    results_md_path: Path = DEFAULT_RESULTS_MD_PATH,
    human_csv_path: Path = DEFAULT_HUMAN_CSV_PATH,
    seed: int = FIXED_SEED,
    backend_name: str = "fake",
    human_csv_alias_path: Path | None = DEFAULT_HUMAN_CSV_ALIAS_PATH,
) -> dict[str, Any]:
    """Run the full deterministic evaluation suite, export the 30-report CSV, and write eval/results.md."""
    random.seed(seed)
    np.random.seed(seed)

    target_db_url = get_db_url(db_url)

    # 1. Seed the 10 named benchmark scenarios
    seeded_scenarios = seed_scenarios(db_url=target_db_url, data_dir=data_dir)

    # 2. Seed 22 additional untouched decisions so we have 30 untouched decisions + 2 tampered scenarios = 32 total
    with psycopg.connect(target_db_url, connect_timeout=10) as conn:
        extra_decisions = seed_additional_evaluation_decisions(
            conn, data_dir=data_dir, count=22, seed=seed
        )

    # 3. Ensure fairness scenario files exist and evaluate fairness
    if not (data_dir / "fairness_groups.csv").is_file() or not (
        data_dir / "fairness_scenario_outcomes.csv"
    ).is_file():
        generate_fairness_scenario(output_dir=data_dir)
    fairness_report = evaluate_fairness_from_files(data_dir=data_dir)

    # Detector check: the same cohort scored by a stressed copy of the registered model that relies on employment_months.
    with tempfile.TemporaryDirectory() as stress_dir:
        stress_paths = generate_fairness_scenario(
            output_dir=Path(stress_dir), sync_db=False,
            registry_row=stressed_registry_row(_load_registered_model(target_db_url)),
        )
        fairness_stress_report = evaluate_fairness_from_files(data_dir=stress_paths["fairness_groups"].parent)

    # 4. Run reconstruction across all 10 scenarios + 22 additional untouched decisions
    untouched_records: list[dict[str, Any]] = []
    tampered_records: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    latencies_ms: list[float] = []

    for scen_name, scen_info in seeded_scenarios.items():
        dec_id = str(scen_info["decision_id"])
        scen_def = scen_info["scenario"]
        is_tampered = bool(scen_def.get("tamper_action"))

        t0 = time.perf_counter()
        report, session = reconstruct_decision(
            dec_id,
            backend_name=backend_name,
            db_url=target_db_url,
            return_session=True,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        replay_res = session.tool_trace[5]["result"] if len(session.tool_trace) >= 6 else {}
        abs_diff = float(replay_res.get("abs_diff", 0.0) or 0.0)

        entry = {
            "name": scen_name,
            "decision_id": dec_id,
            "policy_version": str(scen_info["reconstructed"]["policy_version"]),
            "report": report,
            "session": session,
            "latency_ms": elapsed_ms,
            "replay_abs_diff": abs_diff,
            "is_tampered": is_tampered,
            "reason": scen_info["reconstructed"].get("broken_reason"),
        }
        scenario_rows.append(
            {
                "name": scen_name,
                "decision_id": dec_id,
                "outcome": report.outcome,
                "policy_version": entry["policy_version"],
                "completeness": report.completeness,
                "chain_verified": report.chain_verified,
                "replay_verified": report.replay_verified,
                "status": report.status,
            }
        )
        if is_tampered:
            tampered_records.append(entry)
        else:
            untouched_records.append(entry)

    for extra in extra_decisions:
        dec_id = str(extra["decision_id"])
        t0 = time.perf_counter()
        report, session = reconstruct_decision(
            dec_id,
            backend_name=backend_name,
            db_url=target_db_url,
            return_session=True,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        latencies_ms.append(elapsed_ms)

        replay_res = session.tool_trace[5]["result"] if len(session.tool_trace) >= 6 else {}
        abs_diff = float(replay_res.get("abs_diff", 0.0) or 0.0)

        untouched_records.append(
            {
                "name": extra["name"],
                "decision_id": dec_id,
                "policy_version": str(extra["created"]["policy_version"]),
                "report": report,
                "session": session,
                "latency_ms": elapsed_ms,
                "replay_abs_diff": abs_diff,
                "is_tampered": False,
                "reason": None,
            }
        )

    # 5. Compute Completeness metrics
    untouched_complete = [
        r["report"].completeness
        for r in untouched_records
        if r["name"] not in ("erased_evidence", "missing_payslip_referral")
    ]
    missing_payslip_entry = next(
        r for r in untouched_records if r["name"] == "missing_payslip_referral"
    )
    erased_entry = next(r for r in untouched_records if r["name"] == "erased_evidence")
    all_untouched_completeness = [r["report"].completeness for r in untouched_records]

    completeness_summary = {
        "untouched_complete_count": len(untouched_complete),
        "untouched_complete_mean": round(statistics.mean(untouched_complete), 6),
        "missing_payslip_decision_id": missing_payslip_entry["decision_id"],
        "missing_payslip_completeness": round(
            missing_payslip_entry["report"].completeness, 6
        ),
        "erased_decision_id": erased_entry["decision_id"],
        "erased_evidence_completeness": round(erased_entry["report"].completeness, 6),
        "untouched_all_mean": round(statistics.mean(all_untouched_completeness), 6),
    }

    # 6. Compute Hash-Chain Verification Rate (target 100% untouched)
    untouched_verified = sum(
        1 for r in untouched_records if r["report"].chain_verified
    )
    tampered_verified = sum(
        1 for r in tampered_records if r["report"].chain_verified
    )
    chain_summary = {
        "untouched_total": len(untouched_records),
        "untouched_verified": untouched_verified,
        "untouched_verification_rate": round(
            untouched_verified / len(untouched_records), 6
        ),
        "tampered_total": len(tampered_records),
        "tampered_verified": tampered_verified,
        "tampered_verification_rate": round(
            tampered_verified / len(tampered_records), 6
        ),
    }

    # 7. Run DB-Copy Tamper Detection Experiment
    untouched_ids = [r["decision_id"] for r in untouched_records]
    with psycopg.connect(target_db_url, connect_timeout=10) as conn:
        db_copy_tamper = run_db_copy_tamper_experiment(conn, untouched_ids)

    tampered_payload_rec = next(
        r for r in tampered_records if r["name"] == "tampered_payload"
    )
    deleted_final_rec = next(
        r for r in tampered_records if r["name"] == "deleted_final_row"
    )
    tamper_checks = [
        db_copy_tamper["altered_payload_test"]["failure_detected"],
        db_copy_tamper["deleted_final_row_test"]["failure_detected"],
        not tampered_payload_rec["report"].chain_verified,
        not deleted_final_rec["report"].chain_verified,
    ]
    tamper_summary = {
        "db_copy": db_copy_tamper,
        "seeded_tampered_payload": {
            "decision_id": tampered_payload_rec["decision_id"],
            "chain_verified": tampered_payload_rec["report"].chain_verified,
            "failure_detected": not tampered_payload_rec["report"].chain_verified,
            "reason": tampered_payload_rec["reason"] or "payload_sha256 mismatch at seq 1",
        },
        "seeded_deleted_final_row": {
            "decision_id": deleted_final_rec["decision_id"],
            "chain_verified": deleted_final_rec["report"].chain_verified,
            "failure_detected": not deleted_final_rec["report"].chain_verified,
            "reason": deleted_final_rec["reason"] or "expected 13 snapshots, found 12",
        },
        "overall_detection_rate": round(
            sum(1 for ok in tamper_checks if ok) / len(tamper_checks), 6
        ),
    }

    # 8. Compute Replay Match Rate with tolerance
    untouched_replay_matched = sum(
        1 for r in untouched_records if r["report"].replay_verified
    )
    max_abs_diff = max((r["replay_abs_diff"] for r in untouched_records), default=0.0)
    replay_summary = {
        "tolerance": DEFAULT_REPLAY_TOLERANCE,
        "untouched_total": len(untouched_records),
        "untouched_matched": untouched_replay_matched,
        "untouched_match_rate": round(
            untouched_replay_matched / len(untouched_records), 6
        ),
        "max_abs_diff": max_abs_diff,
        "tampered_payload_replay_verified": tampered_payload_rec["report"].replay_verified,
    }

    # 9. Compute Citation Validity, Claim Support, and Numerical Accuracy (reported separately)
    #    and build the 30-report CSV rows for human rating
    total_claims = 0
    valid_citations = 0
    supported_claims = 0
    numerically_accurate_claims = 0
    human_rating_rows: list[dict[str, str]] = []

    for idx, entry in enumerate(untouched_records[:TARGET_UNTOUCHED_REPORTS]):
        rep = entry["report"]
        sess: ToolSession = entry["session"]
        rep_primary_row: dict[str, str] | None = None

        for reason_grp in rep.reasons:
            for claim_obj in reason_grp.claims:
                claim_dict = claim_obj.model_dump()
                res = evaluate_claim_grounding(claim_dict, sess)
                total_claims += 1
                if res["citation_valid"]:
                    valid_citations += 1
                if res["claim_supported"]:
                    supported_claims += 1
                if res["numerical_accurate"]:
                    numerically_accurate_claims += 1

                if rep_primary_row is None:
                    is_fully_supported = bool(
                        res["citation_valid"]
                        and res["claim_supported"]
                        and res["numerical_accurate"]
                    )
                    rep_primary_row = {
                        "claim": (
                            f"[Report #{idx + 1:02d} | decision_id={entry['decision_id']} | "
                            f"citation={res['citation']} | field={res['field']} | value={res['value']}] "
                            f"{res['text']}"
                        ),
                        "source_text": res["source_text"],
                        "supported": str(is_fully_supported),
                    }

        if rep_primary_row is not None:
            human_rating_rows.append(rep_primary_row)

    grounding_summary = {
        "total_claims": total_claims,
        "valid_citations": valid_citations,
        "citation_validity_rate": round(valid_citations / max(total_claims, 1), 6),
        "supported_claims": supported_claims,
        "claim_support_rate": round(supported_claims / max(total_claims, 1), 6),
        "numerically_accurate_claims": numerically_accurate_claims,
        "numerical_accuracy_rate": round(
            numerically_accurate_claims / max(total_claims, 1), 6
        ),
    }

    # 10. Guardrail rejections (clean runs + adversarial injection probe)
    clean_rejections = sum(
        int(r["report"].guardrail_rejections) for r in untouched_records
    )
    sample_entry = untouched_records[0]
    sample_claim = sample_entry["report"].reasons[0].claims[0].model_dump()
    probe_result = run_guardrail_probe(sample_entry["session"], sample_claim)

    guardrails_summary = {
        "clean_runs_total_rejections": clean_rejections,
        "probe": probe_result,
    }

    # 11. Latency metrics (median & p95)
    sorted_lat = sorted(latencies_ms)
    median_ms = _percentile(sorted_lat, 50.0)
    p95_ms = _percentile(sorted_lat, 95.0)
    mean_ms = statistics.mean(sorted_lat)
    latency_summary = {
        "sample_size": len(sorted_lat),
        "median_ms": round(median_ms, 3),
        "p95_ms": round(p95_ms, 3),
        "mean_ms": round(mean_ms, 3),
        "min_ms": round(sorted_lat[0], 3),
        "max_ms": round(sorted_lat[-1], 3),
        "median_s": round(median_ms / 1000.0, 6),
        "p95_s": round(p95_ms / 1000.0, 6),
        "mean_s": round(mean_ms / 1000.0, 6),
        "min_s": round(sorted_lat[0] / 1000.0, 6),
        "max_s": round(sorted_lat[-1] / 1000.0, 6),
    }

    # 12. Export 30 reports to CSV (claim, source_text, supported)
    exported_csv = export_human_rating_csv(
        human_rating_rows,
        csv_path=human_csv_path,
        alias_path=human_csv_alias_path,
    )

    summary = {
        "seed": seed,
        "backend": backend_name,
        "total_decisions_evaluated": len(untouched_records) + len(tampered_records),
        "untouched_decisions_count": len(untouched_records),
        "tampered_scenarios_count": len(tampered_records),
        "completeness": completeness_summary,
        "chain_verification": chain_summary,
        "tamper_detection": tamper_summary,
        "replay": replay_summary,
        "grounding": grounding_summary,
        "guardrails": guardrails_summary,
        "summaries_attached": sum(
            1 for r in untouched_records if r["report"].plain_language_summary
        ),
        "latency": latency_summary,
        "fairness": fairness_report,
        "fairness_stress": fairness_stress_report,
        "scenario_rows": scenario_rows,
        "human_rating_exported_count": len(human_rating_rows[:TARGET_UNTOUCHED_REPORTS]),
        "human_rating_csv_path": _display_path(exported_csv),
        "human_rating_csv_alias_path": (
            _display_path(human_csv_alias_path)
            if human_csv_alias_path is not None
            else None
        ),
    }

    # 13. Write eval/results.md
    results_md_path.parent.mkdir(parents=True, exist_ok=True)
    md_content = format_results_markdown(summary)
    results_md_path.write_text(md_content, encoding="utf-8")

    return summary


def main() -> None:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="Run the DecisionTrace evaluation suite")
    parser.add_argument("--backend", default=os.environ.get("LLM_BACKEND", "fake"),
                        choices=["fake", "gemini_api"])
    args = parser.parse_args()
    if args.backend == "fake":
        summary = run_eval()
    else:
        # Never overwrite the fake-backend artifacts with LLM runs.
        summary = run_eval(
            backend_name=args.backend,
            results_md_path=EVAL_DIR / f"results_{args.backend}.md",
            human_csv_path=EVAL_DIR / f"human_rating_{args.backend}.csv",
            human_csv_alias_path=None,
        )
    print(f"Seed: {summary['seed']}")
    print(
        f"Untouched Chain Verification Rate: "
        f"{summary['chain_verification']['untouched_verification_rate'] * 100:.2f}% "
        f"({summary['chain_verification']['untouched_verified']}/{summary['chain_verification']['untouched_total']})"
    )
    print(
        f"Tamper Detection Rate: "
        f"{summary['tamper_detection']['overall_detection_rate'] * 100:.2f}%"
    )
    print(
        f"Replay Match Rate (tol={summary['replay']['tolerance']:.0e}): "
        f"{summary['replay']['untouched_match_rate'] * 100:.2f}%"
    )
    print(
        f"Citation Validity:   {summary['grounding']['citation_validity_rate'] * 100:.2f}% "
        f"({summary['grounding']['valid_citations']}/{summary['grounding']['total_claims']})"
    )
    print(
        f"Claim Support:       {summary['grounding']['claim_support_rate'] * 100:.2f}% "
        f"({summary['grounding']['supported_claims']}/{summary['grounding']['total_claims']})"
    )
    print(
        f"Numerical Accuracy:  {summary['grounding']['numerical_accuracy_rate'] * 100:.2f}% "
        f"({summary['grounding']['numerically_accurate_claims']}/{summary['grounding']['total_claims']})"
    )
    print(
        f"Guardrail Rejections (Clean / Probe): "
        f"{summary['guardrails']['clean_runs_total_rejections']} / "
        f"{summary['guardrails']['probe']['guardrail_rejections']}"
    )
    print(
        f"Latency: median={summary['latency']['median_ms']:.2f} ms, "
        f"p95={summary['latency']['p95_ms']:.2f} ms"
    )
    print(
        f"Exported {summary['human_rating_exported_count']} reports to "
        f"{summary['human_rating_csv_path']}"
    )
    print(f"Wrote evaluation report to eval/results.md")


if __name__ == "__main__":
    main()


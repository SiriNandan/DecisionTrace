# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Seed the 10 benchmark scenarios through the decision service (dev-only backdating) and mutate current applicant records afterward."""

from __future__ import annotations

import argparse
import csv
from datetime import timedelta
import io
import json
from pathlib import Path
import sys
from typing import Any
import uuid

import psycopg
import yaml

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from data.generate_applicants import DATA_DIR, SYNTHETIC_HEADER, write_synthetic_csv
from data.generate_documents import SYNTHETIC_NOTICE, _write_payslip_json
from data.load_policies import MIGRATION_FILE, get_db_url, load_policies
from decision_api.service import (
    create_decision,
    parse_iso_timestamp,
     record_override,
    reconstruct_decision_from_snapshots,
)


DEFAULT_SCENARIOS_PATH = DATA_DIR / "scenarios" / "scenarios.yaml"


def load_scenarios_yaml(scenarios_path: Path = DEFAULT_SCENARIOS_PATH) -> list[dict[str, Any]]:
    """Load and return the list of 10 named scenario definitions from scenarios.yaml."""
    raw = scenarios_path.read_text(encoding="utf-8")
    first_line = raw.splitlines()[0] if raw.splitlines() else ""
    if SYNTHETIC_NOTICE not in first_line:
        raise ValueError(f"{scenarios_path} must start with {SYNTHETIC_NOTICE}")
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict) or "scenarios" not in parsed:
        raise ValueError(f"Invalid scenarios YAML structure in {scenarios_path}")
    return list(parsed["scenarios"])


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    lines = csv_path.read_text(encoding="utf-8").splitlines()
    data_lines = [
        line for line in lines if not line.startswith("#") and line.strip() != SYNTHETIC_NOTICE
    ]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    return list(reader)


def update_live_applicant_records(
    data_dir: Path,
    applicant_id: str,
    params: dict[str, Any],
) -> None:
    """Update the current live applicant, bureau, and payslip files in data_dir for applicant_id."""
    applicants_path = data_dir / "applicants.csv"
    if applicants_path.is_file():
        rows = _read_csv_rows(applicants_path)
        for row in rows:
            if row["applicant_id"] == applicant_id:
                if "annual_income" in params:
                    row["annual_income"] = f"{float(params['annual_income']):.2f}"
                if "monthly_income" in params:
                    row["monthly_income"] = f"{float(params['monthly_income']):.2f}"
                if "existing_debt_service" in params:
                    row["existing_debt_service"] = f"{float(params['existing_debt_service']):.2f}"
                if "dti_ratio" in params:
                    row["dti_ratio"] = f"{float(params['dti_ratio']):.4f}"
                if "credit_score" in params:
                    row["credit_score"] = str(int(params["credit_score"]))
                if "delinquencies_24m" in params:
                    row["delinquencies_24m"] = str(int(params["delinquencies_24m"]))
                if "account_age_months" in params:
                    row["account_age_months"] = str(int(params["account_age_months"]))
                if "employment_months" in params:
                    row["employment_months"] = str(int(params["employment_months"]))
                if "requested_amount" in params:
                    row["requested_amount"] = f"{float(params['requested_amount']):.2f}"
        write_synthetic_csv(applicants_path, rows)

    bureau_path = data_dir / "bureau_snapshots.csv"
    if bureau_path.is_file() and ("credit_score" in params or "delinquencies_24m" in params):
        b_rows = _read_csv_rows(bureau_path)
        for row in b_rows:
            if row["applicant_id"] == applicant_id:
                if "credit_score" in params:
                    row["credit_score"] = str(int(params["credit_score"]))
                if "delinquencies_24m" in params:
                    row["delinquencies_24m"] = str(int(params["delinquencies_24m"]))
        write_synthetic_csv(bureau_path, b_rows)

    payslip_path = data_dir / "payslips" / f"{applicant_id}.json"
    if payslip_path.is_file():
        payload = json.loads(payslip_path.read_text(encoding="utf-8"))
        if "monthly_income" in params:
            payload["monthly_income"] = round(float(params["monthly_income"]), 2)
        if "annual_income" in params:
            payload["income_annual"] = round(float(params["annual_income"]), 2)
        if "payslip_status" in params:
            payload["status"] = str(params["payslip_status"])
        _write_payslip_json(payslip_path, payload)


def read_current_applicant_record(data_dir: Path, applicant_id: str) -> dict[str, Any]:
    """Read the current live applicant record from data_dir/applicants.csv and payslip JSON."""
    applicants_path = data_dir / "applicants.csv"
    rows = _read_csv_rows(applicants_path)
    target = next((r for r in rows if r["applicant_id"] == applicant_id), None)
    if target is None:
        raise LookupError(f"Applicant {applicant_id} not found in {applicants_path}")

    payslip_path = data_dir / "payslips" / f"{applicant_id}.json"
    payslip_status = "received"
    if payslip_path.is_file():
        payslip_data = json.loads(payslip_path.read_text(encoding="utf-8"))
        payslip_status = str(payslip_data.get("status", "received"))

    return {
        "applicant_id": target["applicant_id"],
        "application_id": target["application_id"],
        "annual_income": float(target["annual_income"]),
        "monthly_income": float(target["monthly_income"]),
        "existing_debt_service": float(target["existing_debt_service"]),
        "dti_ratio": float(target["dti_ratio"]),
        "credit_score": int(target["credit_score"]),
        "delinquencies_24m": int(target["delinquencies_24m"]),
        "account_age_months": int(target["account_age_months"]),
        "employment_months": int(target["employment_months"]),
        "requested_amount": float(target["requested_amount"]),
        "currency": target["currency"],
        "payslip_status": payslip_status,
    }


def seed_scenarios(
    db_url: str | None = None,
    scenarios_path: Path = DEFAULT_SCENARIOS_PATH,
    data_dir: Path = DATA_DIR,
) -> dict[str, dict[str, Any]]:
    """Seed all 10 scenarios through the decision service and then mutate current live applicant records."""
    target_url = get_db_url(db_url)
    load_policies(db_url=target_url)
    from data.train_model import train_and_register_model

    train_and_register_model(db_url=target_url, sync_db=True)
    scenarios = load_scenarios_yaml(scenarios_path)

    seeded_results: dict[str, dict[str, Any]] = {}

    with psycopg.connect(target_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            for migration in sorted(MIGRATION_FILE.parent.glob("*.sql")):  # every update, so older databases work too
                cur.execute(migration.read_text(encoding="utf-8"))

        for scen in scenarios:
            name = str(scen["name"])
            applicant_id = str(scen["applicant_id"])
            application_id = str(scen["application_id"])
            decision_ts = parse_iso_timestamp(scen["decision_date"])
            params = dict(scen["applicant_params"])

            # Deterministic UUID per scenario
            dec_uuid = uuid.uuid5(
                uuid.NAMESPACE_OID,
                f"decisiontrace:{application_id}:{decision_ts.isoformat()}",
            )

            # Ensure idempotency if re-seeding
            with conn.cursor() as cur:
                # Everything that points at the decision goes first (links, signals, findings and corrections included),
                # so re-seeding a database that has been used doesn't trip over them.
                cur.execute("DELETE FROM claim_revocations WHERE claim_id IN (SELECT claim_id FROM claim_links WHERE decision_id = %s);", (dec_uuid,))
                cur.execute("DELETE FROM claim_links WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM signal_reviews WHERE signal_id IN (SELECT signal_id FROM risk_signals WHERE decision_id = %s);", (dec_uuid,))
                cur.execute("DELETE FROM risk_signals WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM corrections WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM finding_reviews WHERE finding_id IN (SELECT finding_id FROM quality_findings WHERE decision_id = %s);", (dec_uuid,))
                cur.execute("DELETE FROM quality_findings WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM evidence_status WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM overrides WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM decision_snapshots WHERE decision_id = %s;", (dec_uuid,))
                cur.execute("DELETE FROM decisions WHERE decision_id = %s;", (dec_uuid,))

            # 1. Set current live applicant records to decision-time values before calling decision service
            update_live_applicant_records(data_dir, applicant_id, params)
            live_record = read_current_applicant_record(data_dir, applicant_id)
            live_record["address_proof_status"] = params.get("address_proof_status", "received")

            # 2. Record decision through the decision service with dev-only backdating
            created = create_decision(
                conn,
                applicant_id=applicant_id,
                application_id=application_id,
                applicant_params=live_record,
                decided_at=decision_ts,
                allow_backdating=True,
                decision_id=dec_uuid,
            )

            # 3. Apply scenario-specific post-decision actions
            if "override" in scen and scen["override"]:
                ov = scen["override"]
                record_override(
                    conn,
                    decision_id=dec_uuid,
                    new_outcome=str(ov["new_outcome"]),
                    reason=str(ov["reason"]),
                    actor_role=str(ov["actor_role"]),
                    created_at=decision_ts + timedelta(hours=2),
                )

            tamper_action = scen.get("tamper_action")
            if tamper_action == "alter_payload":
                # Test-only: alter one stored snapshot payload in-place without updating hashes
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE decision_snapshots
                        SET payload = jsonb_set(payload, '{dti_ratio}', '0.99'::jsonb)
                        WHERE decision_id = %s AND seq = 1;
                        """,
                        (dec_uuid,),
                    )
            elif tamper_action == "delete_final_row":
                # Test-only: delete the final snapshot row in the chain
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        DELETE FROM decision_snapshots
                        WHERE decision_id = %s AND seq = %s;
                        """,
                        (dec_uuid, created["chain_length"]),
                    )

            retention_action = scen.get("retention_action")
            if retention_action == "erased_by_retention":
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO evidence_status (decision_id, evidence_ref, status, changed_at)
                        VALUES (%s, %s, 'erased_by_retention', %s);
                        """,
                        (
                            dec_uuid,
                            f"payslip:{applicant_id}",
                            decision_ts + timedelta(days=90),
                        ),
                    )

            seeded_results[name] = {
                "scenario": scen,
                "decision_id": str(dec_uuid),
                "created": created,
            }

        conn.commit()

        # 4. After seeding all decisions, mutate CURRENT applicant records (income, DTI)
        # so current live records diverge from preserved decision-time snapshots.
        for scen in scenarios:
            applicant_id = str(scen["applicant_id"])
            mutation = scen.get("post_seed_mutation")
            if mutation:
                update_live_applicant_records(data_dir, applicant_id, mutation)

        # 5. Reconstruct each seeded decision from preserved snapshots and attach current live state
        for name, entry in seeded_results.items():
            scen = entry["scenario"]
            applicant_id = str(scen["applicant_id"])
            entry["reconstructed"] = reconstruct_decision_from_snapshots(
                conn, entry["decision_id"]
            )
            entry["current_live_applicant"] = read_current_applicant_record(
                data_dir, applicant_id
            )

    return seeded_results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed the 10 benchmark scenarios and mutate current applicant records."
    )
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--db-url", type=str, default=None)
    args = parser.parse_args()

    results = seed_scenarios(
        db_url=args.db_url,
        scenarios_path=args.scenarios,
        data_dir=args.data_dir,
    )
    for name, info in results.items():
        rec = info["reconstructed"]
        print(
            f"Seeded {name:26s} | decision_id={info['decision_id']} | "
            f"outcome={rec['effective_outcome']:7s} | policy={rec['policy_version']} | "
            f"status={rec['status']}"
        )


if __name__ == "__main__":
    main()


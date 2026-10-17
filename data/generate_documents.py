# SYNTHETIC DATA - FOR DEMONSTRATION.
"""Deterministic generator for synthetic payslips, consent_events.csv, and analyst_notes.csv."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from backend.redaction import redact_note_for_snapshot
from data.generate_applicants import (
    DATA_DIR,
    DEFAULT_CONFIG_PATH,
    SYNTHETIC_HEADER,
    generate_all as generate_applicants_all,
    load_config,
    write_synthetic_csv,
)


SYNTHETIC_NOTICE = "SYNTHETIC DATA - FOR DEMONSTRATION."

SYNTHETIC_EMPLOYERS = [
    "Northbridge Logistics Ltd",
    "Albion Healthcare Group",
    "Thames Valley Engineering PLC",
    "Caledonia Software Systems Ltd",
    "Mercia Retail Holdings",
    "Pennine Manufacturing Co",
    "Severn Energy Services Ltd",
    "Wessex Financial Administration",
    "Arundel Civic Services",
    "Cotswold Food & Beverage Ltd",
    "Solent Maritime Operations",
    "Cambridgeshire Biotech Labs",
]

CONSENT_TYPES = [
    "credit_and_income_verification",
    "open_banking_affordability_check",
    "credit_bureau_search_consent",
]

FAKE_PII_SNIPPETS = [
    "Applicant confirmed salary deposit into account 4491-8820-1134 and callback number +44 7700 900123.",
    "Employer payroll verified against sort code 20-45-19 account 83920144; HR contact (020) 7946 0921.",
    "Direct debit mandate active on IBAN GB29NWBK60161331926819; applicant mobile 07700 900456.",
    "Secondary savings held in 5512-9034-2218; verified via telephone 020-7946-0192.",
    "Disputed utility delinquency linked to old account 9182736450; applicant reachable at +44 7700 900789.",
    "Rent standing order from account 3301-7742-9910; landlord reference phone (0161) 496 0321.",
    "Bonus payment cleared in account 1029384756; payroll desk phone 0113-496-0844.",
    "Existing car finance paid from IBAN GB82WEST12345698765432; confirmed on +44 20 7946 0555.",
    "Overdraft buffer maintained on account 6621-4409-1182; applicant daytime phone 07700-900-311.",
    "Settlement statement provided for card 4532 0151 1283 0366; underwriter callback 555-234-5678.",
]

CLEAN_NOTE_TEMPLATES = [
    "Referred case for {applicant_id}: DTI ratio is {dti_ratio} with credit score {credit_score}. Income proof matches declared annual income of {annual_income} {currency}.",
    "Manual underwriting review for {applicant_id}: {delinquencies_24m} delinquency event(s) in 24m window; employment tenure is {employment_months} months.",
    "Borderline affordability check for {applicant_id}: monthly income {monthly_income} {currency} supports requested loan amount {requested_amount} {currency}.",
    "Underwriter assessment for {applicant_id}: account age {account_age_months} months provides stable history despite intermediate score {credit_score}.",
    "Referred under policy review rules for {applicant_id}: verified employer payslip status and consistent debt service obligations.",
]


def _parse_iso_utc(ts_str: str) -> datetime:
    if ts_str.endswith("Z"):
        ts_str = ts_str[:-1] + "+00:00"
    return datetime.fromisoformat(ts_str).astimezone(timezone.utc)


def _format_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_applicants_csv(applicants_path: Path) -> list[dict[str, str]]:
    lines = applicants_path.read_text(encoding="utf-8").splitlines()
    data_lines = [line for line in lines if not line.startswith("#") and line.strip() != SYNTHETIC_NOTICE]
    reader = csv.DictReader(io.StringIO("\n".join(data_lines)))
    return list(reader)


def _write_payslip_json(file_path: Path, payload: dict[str, Any]) -> None:
    """Write payslip JSON with SYNTHETIC DATA notice on line 1."""
    ordered: dict[str, Any] = {"_notice": SYNTHETIC_NOTICE}
    for k, v in payload.items():
        if k != "_notice":
            ordered[k] = v

    raw = json.dumps(ordered, indent=2)
    # Keep first property on line 1 so line 1 starts with {"_notice": "SYNTHETIC DATA - FOR DEMONSTRATION.",
    raw_first_line = raw.replace('{\n  "_notice":', '{"_notice":', 1)
    file_path.write_text(raw_first_line + "\n", encoding="utf-8", newline="\n")


def prepare_note_for_snapshot(note_row: dict[str, Any]) -> dict[str, Any]:
    """Redact phone-like and account-like strings from an analyst note before snapshot storage."""
    return redact_note_for_snapshot(note_row)


def generate_documents(
    config_path: Path = DEFAULT_CONFIG_PATH,
    output_dir: Path = DATA_DIR,
    seed_override: int | None = None,
    num_notes: int = 60,
    num_pii_notes: int = 10,
) -> dict[str, Path]:
    """Deterministically generate payslips/*.json, consent_events.csv, and analyst_notes.csv."""
    cfg = load_config(config_path)
    seed = int(seed_override if seed_override is not None else cfg.get("seed", 42))
    profile = cfg["profiles"][cfg.get("active_profile", "uk_retail")]
    base_ts = _parse_iso_utc(str(profile["reference_timestamp"]))

    applicants_csv = output_dir / "applicants.csv"
    if not applicants_csv.is_file():
        generate_applicants_all(
            config_path=config_path,
            output_dir=output_dir,
            seed_override=seed,
        )

    applicants = _read_applicants_csv(applicants_csv)
    rng = np.random.default_rng(seed + 101)

    payslips_dir = output_dir / "payslips"
    payslips_dir.mkdir(parents=True, exist_ok=True)

    status_choices = ["received", "processing", "missing"]
    status_weights = [0.85, 0.08, 0.07]

    consent_rows: list[dict[str, Any]] = []

    for idx, app in enumerate(applicants, start=1):
        applicant_id = app["applicant_id"]
        monthly_income = float(app["monthly_income"])
        income_annual = float(app["annual_income"])
        currency = app["currency"]

        employer = SYNTHETIC_EMPLOYERS[int(rng.integers(0, len(SYNTHETIC_EMPLOYERS)))]
        status = str(rng.choice(status_choices, p=status_weights))
        doc_offset_mins = int(rng.integers(30, 2880))
        doc_ts = _format_iso_utc(base_ts - timedelta(minutes=doc_offset_mins))

        payslip_payload = {
            "_notice": SYNTHETIC_NOTICE,
            "applicant_id": applicant_id,
            "application_id": app["application_id"],
            "document_id": f"PAYSLIP-{applicant_id}",
            "employer": employer,
            "currency": currency,
            "monthly_income": round(monthly_income, 2),
            "income_annual": round(income_annual, 2),
            "status": status,
            "source_id": "payslip_verifier_uk_syn_v1",
            "source_timestamp": doc_ts,
        }
        _write_payslip_json(payslips_dir / f"{applicant_id}.json", payslip_payload)

        consent_offset_mins = int(rng.integers(60, 4320))
        granted_at = _format_iso_utc(base_ts - timedelta(minutes=consent_offset_mins))
        consent_type = CONSENT_TYPES[int(rng.integers(0, len(CONSENT_TYPES)))]
        consent_rows.append(
            {
                "applicant_id": applicant_id,
                "consent_type": consent_type,
                "granted_at": granted_at,
                "reference": f"CONSENT-{applicant_id}-{idx:04d}",
            }
        )

    # Select ~60 referred / borderline candidates deterministically
    referred_candidates = [
        app
        for app in applicants
        if (
            0.30 <= float(app["dti_ratio"]) <= 0.45
            or 580 <= int(app["credit_score"]) <= 660
            or int(app["delinquencies_24m"]) >= 1
        )
    ]
    if len(referred_candidates) < num_notes:
        referred_candidates = list(applicants)

    selected_indices = rng.choice(len(referred_candidates), size=num_notes, replace=False)
    selected_indices.sort()

    # Deterministically pick num_pii_notes positions to embed fake account/phone strings
    pii_positions = set(
        int(x) for x in rng.choice(num_notes, size=min(num_pii_notes, num_notes), replace=False)
    )

    analyst_notes: list[dict[str, Any]] = []
    pii_counter = 0
    for note_idx, cand_idx in enumerate(selected_indices):
        app = referred_candidates[int(cand_idx)]
        applicant_id = app["applicant_id"]
        template = CLEAN_NOTE_TEMPLATES[note_idx % len(CLEAN_NOTE_TEMPLATES)]
        base_note = template.format(**app)

        if note_idx in pii_positions:
            snippet = FAKE_PII_SNIPPETS[pii_counter % len(FAKE_PII_SNIPPETS)]
            pii_counter += 1
            note_text = f"{base_note} {snippet}"
            has_embedded_pii = True
        else:
            note_text = base_note
            has_embedded_pii = False

        note_offset_mins = int(rng.integers(1, 720))
        created_at = _format_iso_utc(base_ts - timedelta(minutes=note_offset_mins))
        analyst_notes.append(
            {
                "note_id": f"NOTE-{note_idx + 1:04d}",
                "applicant_id": applicant_id,
                "application_id": app["application_id"],
                "author_role": "underwriter" if note_idx % 2 == 0 else "credit_analyst",
                "created_at": created_at,
                "has_embedded_pii": str(has_embedded_pii).lower(),
                "note_text": note_text,
            }
        )

    consent_path = output_dir / "consent_events.csv"
    notes_path = output_dir / "analyst_notes.csv"
    write_synthetic_csv(consent_path, consent_rows)
    write_synthetic_csv(notes_path, analyst_notes)

    return {
        "payslips_dir": payslips_dir,
        "consent_events": consent_path,
        "analyst_notes": notes_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate synthetic payslips, consent_events.csv, and analyst_notes.csv."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    paths = generate_documents(
        config_path=args.config,
        output_dir=args.output_dir,
        seed_override=args.seed,
    )
    print(
        f"Generated payslips in {paths['payslips_dir']}, "
        f"{paths['consent_events'].name}, and {paths['analyst_notes'].name}."
    )


if __name__ == "__main__":
    main()

"""Document verification source reader (reads payslips/<applicant_id>.json and optional document_refs)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.sources._csv_utils import DEFAULT_DATA_DIR


def read_documents(
    applicant_id: str,
    document_refs: list[str] | None = None,
    data_dir: Path | None = None,
    address_proof_status: str = "received",
) -> dict[str, Any]:
    """Read applicant verification documents, returning payload, source_id, and source_timestamp."""
    target_dir = data_dir or DEFAULT_DATA_DIR
    payslip_path = target_dir / "payslips" / f"{applicant_id}.json"

    if payslip_path.is_file():
        raw_data = json.loads(payslip_path.read_text(encoding="utf-8"))
    else:
        raw_data = {
            "applicant_id": applicant_id,
            "application_id": applicant_id,
            "document_id": f"PAYSLIP-{applicant_id}",
            "employer": "Synthetic Employer Ltd",
            "currency": "GBP",
            "monthly_income": 0.0,
            "income_annual": 0.0,
            "status": "missing",
            "source_id": "payslip_verifier_uk_syn_v1",
            "source_timestamp": "2026-01-15T10:00:00Z",
        }

    payslip_status = str(raw_data.get("status", "received"))
    resolved_address_status = str(address_proof_status or "received")

    if document_refs is not None:
        if len(document_refs) == 0:
            payslip_status = "missing"
        else:
            for ref in document_refs:
                ref_lower = str(ref).lower()
                if "payslip:missing" in ref_lower or "income_proof:missing" in ref_lower:
                    payslip_status = "missing"
                elif "payslip:processing" in ref_lower or "income_proof:processing" in ref_lower:
                    payslip_status = "processing"
                elif "payslip:received" in ref_lower or "income_proof:received" in ref_lower:
                    payslip_status = "received"
                if "address_proof:missing" in ref_lower:
                    resolved_address_status = "missing"
                elif "address_proof:processing" in ref_lower:
                    resolved_address_status = "processing"
                elif "address_proof:received" in ref_lower:
                    resolved_address_status = "received"

    source_id = str(raw_data.get("source_id", "payslip_verifier_uk_syn_v1"))
    source_timestamp = str(raw_data.get("source_timestamp", "2026-01-15T10:00:00Z"))
    evidence_ref = f"payslip:{applicant_id}"

    payload: dict[str, Any] = {
        "applicant_id": applicant_id,
        "application_id": str(raw_data.get("application_id", applicant_id)),
        "document_id": str(raw_data.get("document_id", f"PAYSLIP-{applicant_id}")),
        "employer": str(raw_data.get("employer", "Synthetic Employer Ltd")),
        "currency": str(raw_data.get("currency", "GBP")),
        "monthly_income": round(float(raw_data.get("monthly_income", 0.0)), 2),
        "income_annual": round(float(raw_data.get("income_annual", 0.0)), 2),
        "status": payslip_status,
        "address_proof_status": resolved_address_status,
    }
    if document_refs is not None:
        payload["document_refs"] = [str(r) for r in document_refs]

    return {
        "source_id": source_id,
        "source_timestamp": source_timestamp,
        "evidence_ref": evidence_ref,
        "payload": payload,
    }


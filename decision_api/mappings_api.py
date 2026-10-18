"""Field-mapping routes: propose (AI or rules), edit, review, and import files through an approved mapping."""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from backend.anchoring import append_anchor
from backend.auth import Principal, log_access, require_scope, tenant_connection
from backend.data_sources import DataSource, describe, load_from_source
from backend.ingest import record_decision
from backend.mapping import (
    MappingSpec,
    apply_mapping,
    gaps,
    load_rows,
    preview,
    spec_sha256,
)
from backend.mapping_ai import propose

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_IMPORT_ROWS = 500


class SourceData(BaseModel):
    format: Literal["csv", "json"]
    content: str | None = Field(default=None, description="The file's text (CSV)")
    rows: list[dict[str, Any]] | None = Field(default=None, max_length=5000, description="Records (JSON)")
    source: DataSource | None = Field(
        default=None,
        discriminator="type",
        description="Fetch the data from a URL, a Google Cloud Storage object or a read-only SQL query instead of sending it here",
    )


class ProposeRequest(SourceData):
    name: str = Field(..., min_length=1, max_length=100)


class SaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    spec: MappingSpec
    source: SourceData | None = Field(default=None, description="The same file again, to preview the edited mapping")


class ReviewRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class ImportRequest(SourceData):
    mapping_id: int
    dry_run: bool = False
    portfolio: str | None = Field(default=None, min_length=1, max_length=64, description="Label every row loaded with this group (branch, product, region)")


def _status(conn: psycopg.Connection[Any], mapping_id: int) -> tuple[str, str | None]:
    row = conn.execute(
        "SELECT action, reviewer_role FROM mapping_reviews WHERE mapping_id = %s ORDER BY review_id DESC LIMIT 1",
        (mapping_id,),
    ).fetchone()
    return ("proposed", None) if row is None else (str(row[0]), str(row[1]))


def _load(conn: psycopg.Connection[Any], mapping_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT mapping_id, name, version, spec, spec_sha256, proposed_by, created_at FROM mappings WHERE mapping_id = %s",
        (mapping_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="We couldn't find that mapping.")
    status, by = _status(conn, mapping_id)
    spec = MappingSpec.model_validate(row[3])
    return {
        "mapping_id": int(row[0]), "name": str(row[1]), "version": int(row[2]), "spec": spec,
        "spec_sha256": str(row[4]), "proposed_by": str(row[5]), "created_at": row[6].isoformat(),
        "status": status, "reviewed_by_role": by, "gaps": gaps(spec),
    }


def _public(m: dict[str, Any]) -> dict[str, Any]:
    return {**m, "spec": m["spec"].model_dump(mode="json")}


def _insert_version(conn: psycopg.Connection[Any], principal: Principal, name: str, spec: MappingSpec, proposed_by: str) -> int:
    nxt = conn.execute(
        "SELECT COALESCE(MAX(version), 0) + 1 FROM mappings WHERE tenant_id = %s AND name = %s",
        (principal.tenant_id, name),
    ).fetchone()[0]
    row = conn.execute(
        "INSERT INTO mappings (tenant_id, name, version, spec, spec_sha256, proposed_by, created_by_credential) "
        "VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s) RETURNING mapping_id",
        (principal.tenant_id, name, nxt, json.dumps(spec.model_dump(mode="json")), spec_sha256(spec), proposed_by,
         principal.credential_id),
    ).fetchone()
    return int(row[0])


def _rows(src: SourceData) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        if src.source is not None:
            if src.content is not None or src.rows is not None:
                raise ValueError("Send either the data itself or a `source` to fetch it from, not both.")
            content, rows = load_from_source(src.source, src.format)
            return load_rows("json" if rows is not None else src.format, content, rows)
        return load_rows(src.format, src.content, src.rows)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/mappings/propose")
def propose_mapping(req: ProposeRequest, principal: Principal = Depends(require_scope("mappings:write"))) -> dict[str, Any]:
    """Draft a mapping for an unfamiliar file. Nothing is imported, and nothing can be until a person approves it."""
    headers, rows = _rows(req)
    if not rows:
        raise HTTPException(status_code=422, detail="The file has no data rows.")
    with tenant_connection(principal.tenant_id) as conn:
        policy = str((conn.execute("SELECT ai_summary_policy FROM tenants WHERE tenant_id = %s", (principal.tenant_id,)).fetchone() or ["ask"])[0])
    proposal = propose(headers, rows, req.format, use_ai=policy != "never")  # a lender that opted out of AI gets the built-in rules
    with tenant_connection(principal.tenant_id) as conn:
        mapping_id = _insert_version(conn, principal, req.name, proposal.spec, proposal.proposed_by)
        log_access(conn, principal, "mapping_propose", "mapping", str(mapping_id))
        saved = _load(conn, mapping_id)
    outcome_col = proposal.spec.outcome.column if proposal.spec.outcome else None
    outcome_values = sorted({str(r.get(outcome_col)).strip() for r in rows if r.get(outcome_col) not in (None, "")})[:20] if outcome_col else []
    return {
        **_public(saved),
        "issues": proposal.issues,
        "preview": preview(proposal.spec, rows),
        "columns": headers,
        "outcome_values": outcome_values,
    }


@router.post("/v1/mappings")
def save_mapping(req: SaveRequest, principal: Principal = Depends(require_scope("mappings:write"))) -> dict[str, Any]:
    """Save a person's edit as a new version (it needs its own approval)."""
    with tenant_connection(principal.tenant_id) as conn:
        mapping_id = _insert_version(conn, principal, req.name, req.spec, "person")
        log_access(conn, principal, "mapping_edit", "mapping", str(mapping_id))
        saved = _public(_load(conn, mapping_id))
    if req.source is not None:
        _, rows = _rows(req.source)
        saved["preview"] = preview(req.spec, rows)
    return saved


@router.get("/v1/mappings")
def list_mappings(
    principal: Principal = Depends(require_scope("mappings:read")), limit: int = Query(default=100, ge=1, le=500)
) -> list[dict[str, Any]]:
    with tenant_connection(principal.tenant_id) as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT mapping_id FROM mappings WHERE tenant_id = %s ORDER BY mapping_id DESC LIMIT %s",
            (principal.tenant_id, limit)).fetchall()]
        out = []
        for i in ids:
            m = _load(conn, i)
            out.append({k: v for k, v in _public(m).items() if k != "spec"})
        return out


@router.get("/v1/mappings/{mapping_id}")
def get_mapping(mapping_id: int, principal: Principal = Depends(require_scope("mappings:read"))) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        return _public(_load(conn, mapping_id))


def _review(mapping_id: int, action: str, note: str, principal: Principal) -> dict[str, Any]:
    with tenant_connection(principal.tenant_id) as conn:
        m = _load(conn, mapping_id)
        if m["status"] != "proposed":
            raise HTTPException(status_code=409, detail=f"This mapping was already {m['status']}.")
        if action == "approved" and m["gaps"]:
            raise HTTPException(status_code=422, detail="This mapping isn't complete yet: " + " ".join(m["gaps"]))
        conn.execute(
            "INSERT INTO mapping_reviews (mapping_id, tenant_id, action, note, reviewer_credential, reviewer_role) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (mapping_id, principal.tenant_id, action, note, principal.credential_id, principal.role),
        )
        log_access(conn, principal, f"mapping_{action}", "mapping", str(mapping_id))
        return _public(_load(conn, mapping_id))


@router.post("/v1/mappings/{mapping_id}/approve")
def approve_mapping(
    mapping_id: int, req: ReviewRequest, principal: Principal = Depends(require_scope("mappings:approve"))
) -> dict[str, Any]:
    return _review(mapping_id, "approved", req.note, principal)


@router.post("/v1/mappings/{mapping_id}/reject")
def reject_mapping(
    mapping_id: int, req: ReviewRequest, principal: Principal = Depends(require_scope("mappings:approve"))
) -> dict[str, Any]:
    return _review(mapping_id, "rejected", req.note, principal)


@router.post("/v1/imports")
def import_file(req: ImportRequest, principal: Principal = Depends(require_scope("mappings:read"))) -> dict[str, Any]:
    """Run an approved mapping over a file. `dry_run` checks and previews only; a real import needs ingest rights."""
    if not req.dry_run and "ingest:write" not in principal.scopes:
        raise HTTPException(status_code=403, detail="This sign-in isn't allowed to do that.")
    if req.dry_run and "mappings:write" not in principal.scopes and "ingest:write" not in principal.scopes:
        raise HTTPException(status_code=403, detail="This sign-in isn't allowed to do that.")
    _, rows = _rows(req)
    if len(rows) > MAX_IMPORT_ROWS:
        raise HTTPException(status_code=422, detail=f"Please send at most {MAX_IMPORT_ROWS} rows at a time.")

    with tenant_connection(principal.tenant_id) as conn:
        m = _load(conn, req.mapping_id)
        if m["status"] != "approved":
            raise HTTPException(status_code=409, detail="This mapping hasn't been approved yet, so it can't be used.")
        spec: MappingSpec = m["spec"]
        stamp = {"mapping_id": m["mapping_id"], "version": m["version"], "spec_sha256": m["spec_sha256"]}
        results: list[dict[str, Any]] = []
        new_records: list[dict[str, Any]] = []
        tally = {"created": 0, "replayed": 0, "rejected": 0}
        for i, row in enumerate(rows, start=1):
            env, errs = apply_mapping(spec, row, source_mapping=stamp)
            if env is not None and req.portfolio:
                env = env.model_copy(update={"portfolio": req.portfolio})
            if env is None:
                results.append({"row": i, "status": "rejected", "problems": errs})
                tally["rejected"] += 1
                continue
            if req.dry_run:
                results.append({"row": i, "status": "ok", "external_id": env.external_id})
                continue
            try:
                out, replayed = record_decision(
                    conn, tenant_id=principal.tenant_id, envelope=env,
                    idempotency_key=f"import-{m['mapping_id']}-{env.external_id}",
                )
            except FileExistsError:
                results.append({"row": i, "status": "rejected", "external_id": env.external_id,
                                "problems": ["This reference was already recorded with different content."]})
                tally["rejected"] += 1
                continue
            except ValueError as exc:
                results.append({"row": i, "status": "rejected", "external_id": env.external_id, "problems": [str(exc)]})
                tally["rejected"] += 1
                continue
            except Exception:  # one bad row must not lose the rows already saved (they still need their anchors below)
                logger.exception("Import row %d (%s) could not be recorded", i, env.external_id)
                results.append({"row": i, "status": "rejected", "external_id": env.external_id,
                                "problems": ["This row could not be recorded because of a server problem. Try it again on its own."]})
                tally["rejected"] += 1
                continue
            key = "replayed" if replayed else "created"
            if not replayed:
                new_records.append(out)
            tally[key] += 1
            results.append({"row": i, "status": key, "external_id": env.external_id, "decision_id": out["decision_id"]})
        quality = None
        signals = None
        if not req.dry_run:
            where = f" source={describe(req.source)}" if req.source is not None else ""
            log_access(conn, principal, "import", "mapping", f"{m['mapping_id']} rows={len(rows)}{where}")
            if new_records:  # check what was just saved, so problems surface straight away
                from backend.quality import scan

                quality = scan(conn, principal.tenant_id, decision_ids=[r["decision_id"] for r in new_records])
                from backend.signals import scan_signals

                signals = scan_signals(conn, principal.tenant_id, decision_ids=[r["decision_id"] for r in new_records],
                                       include_access=False)
    for rec in new_records:
        try:
            append_anchor(rec["decision_id"], rec["chain_length"], rec["chain_head"], rec["decided_at"])
        except OSError:
            logger.exception("Could not write chain-head anchor for %s", rec["decision_id"])
    return {"mapping_id": m["mapping_id"], "version": m["version"], "dry_run": req.dry_run, **tally,
            "quality": quality, "signals": signals, "results": results}

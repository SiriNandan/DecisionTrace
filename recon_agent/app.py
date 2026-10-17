"""FastAPI application for the DecisionTrace Reconstruction Agent (`recon_agent`)."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.contract import ReconstructionReport
from recon_agent.service import reconstruct_decision


app = FastAPI(
    title="DecisionTrace Reconstruction Agent",
    version="0.1.0",
    description=(
        "Reconstructs cited loan decision explanations exclusively from preserved "
        "decision-time snapshots; supports audit and explanation and is tamper-evident "
        "against a retained reference."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "service": "recon_agent"}


@app.post("/reconstruct/{decision_id}", response_model=ReconstructionReport)
def post_reconstruct(decision_id: str) -> ReconstructionReport:
    """Reconstruct a cited explanation for `decision_id` from preserved decision-time evidence."""
    try:
        uuid.UUID(str(decision_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid decision_id UUID: {decision_id}") from exc

    try:
        return reconstruct_decision(decision_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


"""FastAPI application for the DecisionTrace Reconstruction Agent (`recon_agent`)."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.startup_checks import check_startup_config
from backend.web import configure_app
from recon_agent.router import router as reconstruct_router


@asynccontextmanager
async def _lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    check_startup_config()
    yield


app = FastAPI(
    lifespan=_lifespan,
    title="DecisionTrace Reconstruction Agent",
    version="0.1.0",
    description=(
        "Reconstructs cited loan decision explanations exclusively from preserved "
        "decision-time snapshots; supports audit and explanation and is tamper-evident "
        "against a retained reference."
    ),
)

configure_app(app)
app.include_router(reconstruct_router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status."""
    return {"status": "ok", "service": "recon_agent"}

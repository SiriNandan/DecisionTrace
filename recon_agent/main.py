"""Entrypoint for running the DecisionTrace Reconstruction Agent service."""

from __future__ import annotations

import os
import uvicorn

from recon_agent.app import app


def main() -> None:
    """Start the FastAPI reconstruction agent server."""
    host = os.environ.get("RECON_API_HOST", "127.0.0.1")
    port = int(os.environ.get("RECON_API_PORT", "8002"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()


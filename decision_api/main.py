"""Entrypoint module for running DecisionTrace Decision API with uvicorn."""

from __future__ import annotations

from decision_api.app import app, create_app

__all__ = ["app", "create_app"]


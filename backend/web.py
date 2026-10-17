"""Shared HTTP plumbing for both APIs: CORS, security headers, reconstruct rate limit and cache."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
ALLOWED_METHODS = ["GET", "POST", "OPTIONS"]
ALLOWED_HEADERS = [
    "Content-Type",
    "X-API-Key",
    "X-Claim-Token",
    "Idempotency-Key",
    "X-Idempotency-Key",
    "X-Decision-Date",
]


def cors_origins() -> list[str]:
    """Allowed browser origins from CORS_ORIGINS (comma-separated); defaults to the local Vite server."""
    raw = os.environ.get("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


UPGRADE_MESSAGE = "The service's database needs an update. Restart the service, or ask whoever runs it to apply the latest updates."


def configure_app(app: FastAPI) -> None:
    """Attach error handling, CORS (explicit origins/methods/headers) and baseline security headers.

    Registration order matters: the first middleware added is the innermost. The catch-all sits inside CORS so that
    even a server error comes back with CORS headers; otherwise a browser reports it as "network error".
    """

    @app.middleware("http")
    async def catch_all(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            return await call_next(request)
        except Exception as exc:  # unexpected failure: answer in plain words instead of dropping the connection
            import logging

            import psycopg

            logging.getLogger(__name__).exception("Unhandled error on %s %s", request.method, request.url.path)
            if isinstance(exc, (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedFunction)):
                return JSONResponse(status_code=503, content={"detail": UPGRADE_MESSAGE, "code": "needs_upgrade"})
            from psycopg_pool import PoolTimeout

            if isinstance(exc, PoolTimeout):
                return JSONResponse(status_code=503, content={"detail": "The service is busy. Please try again in a moment.", "code": "busy"},
                                    headers={"Retry-After": "2"})
            if isinstance(exc, psycopg.OperationalError):
                return JSONResponse(status_code=503, content={"detail": "The service can't reach its database right now.", "code": "database_down"})
            return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. Please try again.", "code": "server_error"})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=False,
        allow_methods=ALLOWED_METHODS,
        allow_headers=ALLOWED_HEADERS,
        expose_headers=["X-Next-Cursor"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if os.environ.get("ENV", "dev").lower() != "dev":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


class SlidingWindowLimiter:
    """In-process per-key request limiter (one process only; use a shared store when scaling out)."""

    def __init__(self, limit: int, window_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.limit <= 0:
            return
        now = self.clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry = max(1, int(self.window - (now - hits[0])))
                raise HTTPException(
                    status_code=429,
                    detail="Too many explanation requests; please wait and try again.",
                    headers={"Retry-After": str(retry)},
                )
            hits.append(now)


class TTLCache:
    """Small in-process TTL cache for successful reconstruction reports."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic, max_items: int = 256):
        self.ttl = ttl_seconds
        self.clock = clock
        self.max_items = max_items
        self._items: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        if self.ttl <= 0:
            return None
        with self._lock:
            entry = self._items.get(key)
            if entry is None:
                return None
            if self.clock() - entry[0] > self.ttl:
                del self._items[key]
                return None
            return entry[1]

    def put(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._items) >= self.max_items:
                oldest = min(self._items, key=lambda k: self._items[k][0])
                del self._items[oldest]
            self._items[key] = (self.clock(), value)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def evict_suffix(self, suffix: str) -> None:
        """Drop the entries whose key ends with `suffix` (one decision), leaving everyone else's alone."""
        with self._lock:
            for k in [k for k in self._items if k.endswith(suffix)]:
                del self._items[k]

"""Fetch the rows for an import from somewhere other than an uploaded file.

Three kinds, all default-deny and all producing the same (format, content, rows) that an upload would, so the
approved mapping, row-by-row checks, idempotency and anchoring apply unchanged:

  url  - an https file or a REST endpoint (optionally with a bearer token the lender gives us for this one request)
  gcs  - an object in a Google Cloud Storage bucket, read with the server's own service-account credentials
  sql  - one read-only SELECT against a lender's PostgreSQL

What guards them (see the env allowlists below): hosts, buckets and database hosts must be listed by the operator;
http, redirects, private/loopback/link-local addresses, oversized responses and non-SELECT SQL are refused.
Tokens and database passwords are used for the one request and are never stored or logged.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote, urlparse

import httpx
import psycopg
from pydantic import BaseModel, ConfigDict, Field

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000
TIMEOUT_SECONDS = 15.0
SQL_TIMEOUT_MS = 10_000


class SourceError(ValueError):
    """The data source can't be used (not allowed, unreachable, too big, or not what was expected)."""


class UrlSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["url"]
    url: str = Field(..., max_length=2000)
    bearer_token: str | None = Field(default=None, max_length=4000, description="Sent as 'Authorization: Bearer ...' for this request only")


class GcsSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["gcs"]
    uri: str = Field(..., pattern=r"^gs://[^/]+/.+$", max_length=1200)


class SqlSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["sql"]
    dsn: str = Field(..., max_length=1000, description="postgresql://user:password@host:port/db, used for this request only")
    query: str = Field(..., min_length=1, max_length=5000, description="One SELECT statement")


DataSource = UrlSource | GcsSource | SqlSource


def _allowed(name: str) -> set[str]:
    return {x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()}


# ---------- url ----------

def _resolve(host: str, port: int) -> list[Any]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _check_public_host(host: str, port: int) -> None:
    try:
        infos = _resolve(host, port)
    except socket.gaierror as exc:
        raise SourceError("That address can't be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise SourceError("That address points to a private or internal network, which isn't allowed.")


def _get(url: str, headers: dict[str, str]) -> bytes:
    """One bounded GET: no redirects, a timeout and a size cap. Replaced in tests."""
    try:
        with httpx.stream("GET", url, headers=headers, timeout=TIMEOUT_SECONDS, follow_redirects=False) as r:
            if 300 <= r.status_code < 400:
                raise SourceError("The source tried to redirect us elsewhere, which isn't followed.")
            if r.status_code >= 400:
                raise SourceError(f"The source answered with an error (HTTP {r.status_code}).")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise SourceError(f"The source is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise SourceError("The source couldn't be reached.") from exc


def _fetch_url(src: UrlSource) -> bytes:
    parsed = urlparse(src.url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SourceError("Only https addresses are allowed.")
    if parsed.username or parsed.password:
        raise SourceError("Put credentials in bearer_token, not in the address.")
    host = parsed.hostname.lower()
    allowed = _allowed("DT_SOURCE_URL_ALLOWED_HOSTS")
    if host not in allowed:
        raise SourceError("That host isn't on this service's list of allowed data sources.")
    _check_public_host(host, parsed.port or 443)
    headers = {"Accept": "text/csv, application/json"}
    if src.bearer_token:
        headers["Authorization"] = f"Bearer {src.bearer_token}"
    return _get(src.url, headers)


# ---------- gcs ----------

def _gcs_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_only"])
    creds.refresh(Request())
    return str(creds.token)


def _fetch_gcs(src: GcsSource) -> bytes:
    bucket, _, obj = src.uri[len("gs://"):].partition("/")
    if bucket.lower() not in _allowed("DT_SOURCE_GCS_ALLOWED_BUCKETS"):
        raise SourceError("That bucket isn't on this service's list of allowed data sources.")
    url = f"https://storage.googleapis.com/storage/v1/b/{quote(bucket, safe='')}/o/{quote(obj, safe='')}?alt=media"
    try:
        token = _gcs_token()
    except Exception as exc:  # no credentials on this machine, or Google refused them
        raise SourceError("This service couldn't get Google Cloud credentials to read that bucket.") from exc
    return _get(url, {"Authorization": f"Bearer {token}"})


# ---------- sql ----------

_SELECT = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        raise SourceError("The query returned binary data, which can't be imported.")
    if isinstance(value, (dict, list, str, int, float, bool)) or value is None:
        return value
    return str(value)


def _query_sql(src: SqlSource) -> list[dict[str, Any]]:
    parsed = urlparse(src.dsn)
    if parsed.scheme not in ("postgresql", "postgres") or not parsed.hostname:
        raise SourceError("The database address must look like postgresql://user:password@host:port/database.")
    if parsed.hostname.lower() not in _allowed("DT_SOURCE_SQL_ALLOWED_HOSTS"):
        raise SourceError("That database host isn't on this service's list of allowed data sources.")
    query = src.query.strip().rstrip(";").strip()
    if ";" in query or not _SELECT.match(query):
        raise SourceError("Send exactly one SELECT statement.")
    try:
        with psycopg.connect(
            src.dsn, connect_timeout=int(TIMEOUT_SECONDS),
            options=f"-c default_transaction_read_only=on -c statement_timeout={SQL_TIMEOUT_MS}",
        ) as conn:
            conn.read_only = True
            with conn.cursor() as cur:
                cur.execute(query)  # type: ignore[arg-type]
                names = [d.name for d in cur.description or []]
                fetched = cur.fetchmany(MAX_ROWS + 1)
    except psycopg.Error as exc:
        raise SourceError(f"The database refused the query: {exc.diag.message_primary or 'it could not be run'}.") from exc
    if len(fetched) > MAX_ROWS:
        raise SourceError(f"The query returned more than {MAX_ROWS} rows. Narrow it down.")
    return [{n: _json_safe(v) for n, v in zip(names, row)} for row in fetched]


# ---------- entry point ----------

def _rows_from_json(raw: bytes) -> list[dict[str, Any]]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SourceError("The source didn't return valid JSON.") from exc
    if isinstance(data, dict):
        data = next((data[k] for k in ("rows", "data", "records", "items") if isinstance(data.get(k), list)), None)
    if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
        raise SourceError("Expected a list of records (or an object holding one under rows, data, records or items).")
    if len(data) > MAX_ROWS:
        raise SourceError(f"The source has more than {MAX_ROWS} records.")
    return data


def describe(source: DataSource) -> str:
    """A short, secret-free label for the access log."""
    if isinstance(source, UrlSource):
        return f"url:{(urlparse(source.url).hostname or '').lower()}"
    if isinstance(source, GcsSource):
        return f"gcs:{source.uri.split('/')[2]}"
    return f"sql:{(urlparse(source.dsn).hostname or '').lower()}"


def load_from_source(source: DataSource, fmt: str) -> tuple[str | None, list[dict[str, Any]] | None]:
    """Returns (csv_text, None) or (None, json_rows), matching what an upload would carry for `fmt`."""
    if isinstance(source, SqlSource):
        return None, _query_sql(source)
    raw = _fetch_url(source) if isinstance(source, UrlSource) else _fetch_gcs(source)
    if fmt == "csv":
        try:
            return raw.decode("utf-8-sig"), None
        except UnicodeDecodeError as exc:
            raise SourceError("The source isn't readable as UTF-8 text.") from exc
    return None, _rows_from_json(raw)

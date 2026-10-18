"""Importing from a URL, a GCS object or a read-only SQL query instead of an upload."""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import urlparse

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend import data_sources as ds
from backend.sandbox import provision_tenants
from decision_api.app import create_app
from tests.test_access_and_signals import h
from tests.test_mapping_api import UK, approve, keys, propose


@pytest.fixture(scope="module")
def lenders() -> list[dict[str, Any]]:
    with psycopg.connect(os.environ["DB_URL"], connect_timeout=5) as conn:
        return provision_tenants(conn, 1, prefix=f"src-{uuid.uuid4().hex[:5]}")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("DT_SOURCE_URL_ALLOWED_HOSTS", "DT_SOURCE_GCS_ALLOWED_BUCKETS", "DT_SOURCE_SQL_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)


def public_dns(monkeypatch: pytest.MonkeyPatch, ip: str = "93.184.216.34") -> None:
    monkeypatch.setattr(ds, "_resolve", lambda host, port: [(2, 1, 6, "", (ip, port))])


# ---------- url ----------

def test_url_is_refused_unless_https_and_allow_listed_and_public(monkeypatch: pytest.MonkeyPatch) -> None:
    src = lambda u: ds.UrlSource(type="url", url=u)  # noqa: E731
    with pytest.raises(ds.SourceError, match="allowed data sources"):
        ds.load_from_source(src("https://files.lender.example/a.csv"), "csv")  # default deny
    monkeypatch.setenv("DT_SOURCE_URL_ALLOWED_HOSTS", "files.lender.example,169.254.169.254,internal.lender.example")
    with pytest.raises(ds.SourceError, match="https"):
        ds.load_from_source(src("http://files.lender.example/a.csv"), "csv")
    with pytest.raises(ds.SourceError, match="credentials"):
        ds.load_from_source(src("https://user:pw@files.lender.example/a.csv"), "csv")
    public_dns(monkeypatch, "169.254.169.254")  # cloud metadata address, even if someone listed it
    with pytest.raises(ds.SourceError, match="private or internal"):
        ds.load_from_source(src("https://internal.lender.example/a.csv"), "csv")
    public_dns(monkeypatch, "10.0.0.5")
    with pytest.raises(ds.SourceError, match="private or internal"):
        ds.load_from_source(src("https://files.lender.example/a.csv"), "csv")


def test_url_fetch_sends_token_and_parses_csv_and_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DT_SOURCE_URL_ALLOWED_HOSTS", "api.lender.example")
    public_dns(monkeypatch)
    seen: list[tuple[str, dict[str, str]]] = []
    body = {"v": b"a,b\n1,2\n"}
    monkeypatch.setattr(ds, "_get", lambda url, headers: (seen.append((url, headers)), body["v"])[1])
    src = ds.UrlSource(type="url", url="https://api.lender.example/export", bearer_token="tok-123")
    assert ds.load_from_source(src, "csv") == ("a,b\n1,2\n", None)
    assert seen[0][1]["Authorization"] == "Bearer tok-123"
    body["v"] = b'{"data": [{"a": 1}, {"a": 2}]}'
    assert ds.load_from_source(src, "json") == (None, [{"a": 1}, {"a": 2}])
    body["v"] = b'{"nothing": 1}'
    with pytest.raises(ds.SourceError, match="list of records"):
        ds.load_from_source(src, "json")
    assert "tok-123" not in ds.describe(src)


def test_redirects_and_oversize_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def transport(handler: Any) -> None:
        real = ds.httpx.stream
        monkeypatch.setattr(ds.httpx, "stream", lambda m, u, **kw: real(m, u, **{**kw, "transport": ds.httpx.MockTransport(handler)}) if False else _stream(handler, m, u, **kw))

    class _Ctx:
        def __init__(self, resp: Any) -> None:
            self.resp = resp
        def __enter__(self) -> Any:
            return self.resp
        def __exit__(self, *a: Any) -> None:
            return None

    def _stream(handler: Any, method: str, url: str, **kw: Any) -> Any:
        client = ds.httpx.Client(transport=ds.httpx.MockTransport(handler))
        return client.stream(method, url, headers=kw.get("headers"))

    transport(lambda req: ds.httpx.Response(302, headers={"location": "http://169.254.169.254/"}))
    with pytest.raises(ds.SourceError, match="redirect"):
        ds._get("https://x.example/a", {})
    transport(lambda req: ds.httpx.Response(200, content=b"x" * (ds.MAX_BYTES + 1)))
    with pytest.raises(ds.SourceError, match="larger than"):
        ds._get("https://x.example/a", {})
    transport(lambda req: ds.httpx.Response(500))
    with pytest.raises(ds.SourceError, match="HTTP 500"):
        ds._get("https://x.example/a", {})


# ---------- gcs ----------

def test_gcs_needs_an_allowed_bucket_and_uses_server_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    src = ds.GcsSource(type="gcs", uri="gs://lender-exports/2026/10/decisions.csv")
    with pytest.raises(ds.SourceError, match="allowed data sources"):
        ds.load_from_source(src, "csv")
    monkeypatch.setenv("DT_SOURCE_GCS_ALLOWED_BUCKETS", "lender-exports")
    with pytest.raises(ds.SourceError, match="credentials"):  # none on a laptop/CI
        monkeypatch.setattr(ds, "_gcs_token", lambda: (_ for _ in ()).throw(RuntimeError("no creds")))
        ds.load_from_source(src, "csv")
    seen: list[str] = []
    monkeypatch.setattr(ds, "_gcs_token", lambda: "gcs-token")
    monkeypatch.setattr(ds, "_get", lambda url, headers: (seen.append(url + "|" + headers["Authorization"]), b"a\n1\n")[1])
    assert ds.load_from_source(src, "csv") == ("a\n1\n", None)
    assert seen == ["https://storage.googleapis.com/storage/v1/b/lender-exports/o/2026%2F10%2Fdecisions.csv?alt=media|Bearer gcs-token"]
    with pytest.raises(Exception):
        ds.GcsSource(type="gcs", uri="https://not-gcs")


# ---------- sql ----------

def local_dsn() -> str:
    """The test database as a postgresql:// URL (the suite's DB_URL may be in key=value form)."""
    from urllib.parse import quote

    from psycopg.conninfo import conninfo_to_dict

    d = conninfo_to_dict(os.environ["DB_URL"])
    auth = f"{quote(d.get('user', ''), safe='')}:{quote(d.get('password', ''), safe='')}@"
    return f"postgresql://{auth}{d.get('host', 'localhost')}:{d.get('port', 5432)}/{d['dbname']}"


def test_sql_is_default_deny_read_only_and_single_statement(monkeypatch: pytest.MonkeyPatch) -> None:
    host = urlparse(local_dsn()).hostname or "localhost"
    q = lambda text: ds.SqlSource(type="sql", dsn=local_dsn(), query=text)  # noqa: E731
    with pytest.raises(ds.SourceError, match="allowed data sources"):
        ds.load_from_source(q("SELECT 1 AS a"), "json")
    monkeypatch.setenv("DT_SOURCE_SQL_ALLOWED_HOSTS", host)
    none, rows = ds.load_from_source(q("SELECT 1 AS a, 'x'::text AS b, 2.50::numeric AS c, DATE '2026-01-02' AS d;"), "json")
    assert none is None and rows == [{"a": 1, "b": "x", "c": 2.5, "d": "2026-01-02"}]
    for bad in ("DELETE FROM decisions", "SELECT 1; DROP TABLE decisions", "UPDATE decisions SET outcome='APPROVE'"):
        with pytest.raises(ds.SourceError, match="one SELECT"):
            ds.load_from_source(q(bad), "json")
    # a write hidden inside a SELECT-shaped statement is still stopped by the read-only transaction
    with pytest.raises(ds.SourceError, match="read-only|refused"):
        ds.load_from_source(q("WITH x AS (INSERT INTO access_log(tenant_id) VALUES ('t') RETURNING 1) SELECT * FROM x"), "json")
    with pytest.raises(ds.SourceError, match="more than"):
        monkeypatch.setattr(ds, "MAX_ROWS", 3)
        ds.load_from_source(q("SELECT generate_series(1, 10) AS n"), "json")
    err = None
    try:
        ds.load_from_source(ds.SqlSource(type="sql", dsn=local_dsn().replace("@", "x@", 1), query="SELECT 1"), "json")
    except ds.SourceError as exc:
        err = str(exc)
    assert err is not None and "local_dev" not in err  # never echoes the password


# ---------- through the import endpoint ----------

def test_import_from_a_url_matches_an_upload_and_is_idempotent(lenders: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    t = lenders[0]
    client = TestClient(create_app())
    mid = propose(client, t, "uk-from-url", format="csv", content=UK).json()["mapping_id"]
    approve(client, t, mid)
    monkeypatch.setenv("DT_SOURCE_URL_ALLOWED_HOSTS", "bank.example")
    public_dns(monkeypatch)
    monkeypatch.setattr(ds, "_get", lambda url, headers: UK.encode("utf-8"))
    req = {"mapping_id": mid, "format": "csv", "source": {"type": "url", "url": "https://bank.example/export.csv"}}
    first = client.post("/v1/imports", json=req, headers=h(keys(t)[0]))
    assert first.status_code == 200, first.text
    a = first.json()
    again = client.post("/v1/imports", json=req, headers=h(keys(t)[0])).json()
    assert a["created"] > 0 and a["rejected"] == 0
    assert (again["created"], again["replayed"]) == (0, a["created"])

    both = client.post("/v1/imports", json={**req, "content": UK}, headers=h(keys(t)[0]))
    assert both.status_code == 422 and "not both" in both.text
    monkeypatch.setenv("DT_SOURCE_URL_ALLOWED_HOSTS", "other.example")
    denied = client.post("/v1/imports", json=req, headers=h(keys(t)[0]))
    assert denied.status_code == 422 and "allowed data sources" in denied.text
    log = client.get("/v1/access-log?limit=50", headers=h(t["keys"]["lender_admin"])).json()
    assert any("source=url:bank.example" in str(r.get("object_id", "")) for r in log)

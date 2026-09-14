"""Service authentication (WP-3b, ADR 001 / C18).

Scoped bearer tokens are enforced on every route (read on GET, write on
POST/bulk); the loopback dev path and the in-memory mock stay zero-config; a
fail-closed host guard makes "networked but unauthenticated" unreachable; and
the thin client sends the bearer header only when a token is configured.
"""

import pytest
from fastapi.testclient import TestClient

from picasso_registry import app as app_mod
from picasso_registry import client as client_mod
from picasso_registry.auth import (
    AuthConfig,
    TokenInfo,
    is_loopback_host,
    parse_tokens,
    require_scope,
)
from picasso_registry.export_openapi import build_spec
from picasso_registry.testing import make_memory_app

READ_TOKEN = "read-tok"
WRITE_TOKEN = "write-tok"


def _auth_config():
    return AuthConfig(
        {
            READ_TOKEN: TokenInfo(scope="read", label="dashboard"),
            WRITE_TOKEN: TokenInfo(scope="write", label="microscope-mercury"),
        }
    )


@pytest.fixture
def auth_client():
    app = make_memory_app(auth=_auth_config())
    yield TestClient(app)
    app.dependency_overrides.clear()


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ── request-time enforcement ────────────────────────────────────────────────
def test_missing_token_is_401(auth_client):
    assert auth_client.get("/health").status_code == 401
    assert (
        auth_client.post("/acquisition_run", json={"id": "r"}).status_code
        == 401
    )


def test_unknown_token_is_401(auth_client):
    r = auth_client.get("/health", headers=_bearer("nope"))
    assert r.status_code == 401


def test_read_token_can_read(auth_client):
    r = auth_client.get("/health", headers=_bearer(READ_TOKEN))
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_read_token_cannot_write_is_403(auth_client):
    # the scope-mismatch case: a read token is refused on a POST.
    r = auth_client.post(
        "/acquisition_run", json={"id": "r1"}, headers=_bearer(READ_TOKEN)
    )
    assert r.status_code == 403


def test_write_token_can_write(auth_client):
    r = auth_client.post(
        "/acquisition_run", json={"id": "r1"}, headers=_bearer(WRITE_TOKEN)
    )
    assert r.status_code == 200


def test_write_token_can_also_read(auth_client):
    # write is a superset of read (a writer also reads defaults/cohorts).
    r = auth_client.get("/health", headers=_bearer(WRITE_TOKEN))
    assert r.status_code == 200


def test_bulk_needs_write(auth_client):
    assert (
        auth_client.post(
            "/bulk", json={}, headers=_bearer(READ_TOKEN)
        ).status_code
        == 403
    )
    assert (
        auth_client.post(
            "/bulk", json={}, headers=_bearer(WRITE_TOKEN)
        ).status_code
        == 200
    )


# ── zero-config loopback / mock path unchanged ──────────────────────────────
def test_disabled_auth_needs_no_token():
    # no tokens configured -> unauthenticated (the loopback dev / mock path).
    client = TestClient(make_memory_app())
    assert client.get("/health").status_code == 200
    assert client.post("/acquisition_run", json={"id": "r"}).status_code == 200


def test_mock_registry_path_unchanged():
    from picasso_registry.testing import mock_registry

    with mock_registry() as reg:
        reg.log_acquisition(id="run1", status="done")
        assert reg.get("acquisition_run", "run1")["status"] == "done"


# ── contract: every route is protected (table-driven, no silent gap) ─────────
def test_every_data_route_declares_a_scope():
    spec = build_spec()
    checked = 0
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            assert "security" in op, f"{method.upper()} {path} unprotected"
            checked += 1
    assert checked  # guard against an empty spec silently passing


def test_openapi_declares_bearer_scheme():
    schemes = build_spec()["components"]["securitySchemes"]
    assert schemes["HTTPBearer"]["scheme"] == "bearer"


# ── fail-closed host guard (main) ───────────────────────────────────────────
def _stub_uvicorn(monkeypatch):
    calls = {}

    def fake_run(app, **kw):
        calls.update(kw)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_loopback_bind_needs_no_token(monkeypatch):
    monkeypatch.delenv("PAINT_REGISTRY_TOKENS", raising=False)
    calls = _stub_uvicorn(monkeypatch)
    app_mod.main(["--host", "127.0.0.1", "--port", "0"])
    assert calls["host"] == "127.0.0.1"  # served, no guard trip


def test_non_loopback_without_tokens_refuses(monkeypatch):
    monkeypatch.delenv("PAINT_REGISTRY_TOKENS", raising=False)
    _stub_uvicorn(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        app_mod.main(["--host", "0.0.0.0", "--port", "0"])
    assert exc.value.code == 2  # argparse usage error, service never served


def test_non_loopback_with_tokens_serves(monkeypatch):
    monkeypatch.setenv(
        "PAINT_REGISTRY_TOKENS", "wtok:write:microscope-mercury"
    )
    calls = _stub_uvicorn(monkeypatch)
    app_mod.main(["--host", "0.0.0.0", "--port", "0"])
    assert calls["host"] == "0.0.0.0"  # tokens configured -> guard passes


# ── client sends the bearer header only when a token is set ──────────────────
def test_client_sends_bearer_header(monkeypatch):
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    class _Requests:
        def get(self, url, params=None, timeout=None, headers=None):
            captured["headers"] = headers
            return _Resp()

        def post(self, url, json=None, timeout=None, headers=None):
            captured["headers"] = headers
            return _Resp()

    monkeypatch.setattr(client_mod, "requests", _Requests())

    client_mod.RegistryClient(token="secret").health()
    assert captured["headers"] == {"Authorization": "Bearer secret"}

    client_mod.RegistryClient().health()
    assert captured["headers"] == {}  # no token -> no header


# ── unit: token map parsing, config, host classification ────────────────────
def test_parse_tokens_ok():
    tokens = parse_tokens("a:read:dash, b:write:mercury\nc:write:lab:team")
    assert tokens["a"] == TokenInfo("read", "dash")
    assert tokens["b"] == TokenInfo("write", "mercury")
    assert tokens["c"] == TokenInfo("write", "lab:team")  # label keeps colons


def test_parse_tokens_empty_is_disabled():
    assert parse_tokens("") == {}
    assert parse_tokens(None) == {}
    assert AuthConfig(parse_tokens("")).enabled is False


@pytest.mark.parametrize(
    "raw",
    [
        "a:read",  # missing label
        "a:admin:x",  # unknown scope
        ":read:x",  # empty token
        "a:read:",  # empty label
        "a:read:x,a:write:y",  # duplicate token
    ],
)
def test_parse_tokens_rejects_bad_config(raw):
    with pytest.raises(ValueError):
        parse_tokens(raw)


@pytest.mark.parametrize(
    "host, loopback",
    [
        ("127.0.0.1", True),
        ("127.5.4.3", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.10", False),
        ("registry.lab", False),
    ],
)
def test_is_loopback_host(host, loopback):
    assert is_loopback_host(host) is loopback


def test_require_scope_rejects_unknown_required():
    with pytest.raises(ValueError):
        require_scope("admin")

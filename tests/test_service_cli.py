"""app.main() CLI wiring (WP-3): --db-url actually rebinds the engine (it is
read at import time, so setting the env var alone was silently ignored), and a
malformed PAINT_REGISTRY_PORT fails with a clean usage error, not a traceback.
"""

import pytest

from picasso_registry import app as app_mod
from picasso_registry import db


def _stub_uvicorn(monkeypatch):
    """Replace uvicorn.run so main() wires everything but never serves."""
    calls = {}

    def fake_run(app, **kw):
        calls.update(kw)

    import uvicorn

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_db_url_flag_rebinds_engine(monkeypatch):
    calls = _stub_uvicorn(monkeypatch)
    original = str(db.engine.url)
    try:
        app_mod.main(
            ["--db-url", "sqlite:///./cli_test_reg.db", "--port", "0"]
        )
        # The flag must take effect despite db.py already being imported.
        assert str(db.engine.url).endswith("cli_test_reg.db")
        assert next(db.get_session()).bind.url == db.engine.url
        assert calls["port"] == 0
    finally:
        db.configure(original)  # restore the module-level engine


def test_bad_port_env_exits_cleanly(monkeypatch):
    _stub_uvicorn(monkeypatch)
    monkeypatch.setenv("PAINT_REGISTRY_PORT", "8000/tcp")
    with pytest.raises(SystemExit) as exc:
        app_mod.main([])  # env default is used; int() would have crashed
    assert exc.value.code == 2


def test_env_file_arms_guard_and_served_auth(monkeypatch, tmp_path):
    """Tokens written by `picasso-registry token` into a .env must arm BOTH
    the fail-closed guard and the *served* config: the module-level app was
    created at import (before the .env load), so main() must refresh
    app.state.auth — a guard-passes-but-auth-disabled split would 401 valid
    remote tokens while letting on-box clients write tokenless."""
    import os
    import signal

    calls = _stub_uvicorn(monkeypatch)
    monkeypatch.delenv("PAINT_REGISTRY_TOKENS", raising=False)
    env_file = str(tmp_path / ".env")
    from picasso_registry.tokens import _read_map, token_cli

    token_cli(
        ["add", "--scope", "write", "--label", "m", "--env-file", env_file]
    )
    ((token_value, _),) = _read_map(env_file).items()
    original = signal.getsignal(signal.SIGHUP)
    saved_auth = app_mod.app.state.auth
    try:
        app_mod.main(
            ["--host", "0.0.0.0", "--port", "0", "--env-file", env_file]
        )
        assert calls["host"] == "0.0.0.0"
        served = app_mod.app.state.auth
        assert served.enabled
        assert served.resolve(token_value) is not None
    finally:
        signal.signal(signal.SIGHUP, original)  # main installs the reloader
        app_mod.app.state.auth = saved_auth
        os.environ.pop("PAINT_REGISTRY_TOKENS", None)  # load_dotenv set it


def test_networked_bind_without_tokens_still_refused(monkeypatch, tmp_path):
    _stub_uvicorn(monkeypatch)
    monkeypatch.delenv("PAINT_REGISTRY_TOKENS", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env fallback in reach
    with pytest.raises(SystemExit) as exc:
        app_mod.main(["--host", "0.0.0.0", "--port", "0"])
    assert exc.value.code == 2


def test_missing_explicit_env_file_errors(monkeypatch, tmp_path):
    _stub_uvicorn(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        app_mod.main(["--env-file", str(tmp_path / "absent.env")])
    assert exc.value.code == 2


def test_env_file_db_url_rebinds_engine(monkeypatch, tmp_path):
    """A PAINT_REGISTRY_URL from the .env must rebind the import-time engine,
    like --db-url does — not silently serve against the default SQLite."""
    import os

    _stub_uvicorn(monkeypatch)
    monkeypatch.delenv("PAINT_REGISTRY_URL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PAINT_REGISTRY_URL=sqlite:///./cli_envfile_reg.db\n")
    original = str(db.engine.url)
    try:
        app_mod.main(["--port", "0", "--env-file", str(env_file)])
        assert str(db.engine.url).endswith("cli_envfile_reg.db")
    finally:
        db.configure(original)
        os.environ.pop("PAINT_REGISTRY_URL", None)  # load_dotenv set it


def test_malformed_env_file_tokens_is_clean_usage_error(monkeypatch, tmp_path):
    """A garbage token map from the .env must exit like a bad flag (usage
    error), not a raw ValueError traceback."""
    import os

    _stub_uvicorn(monkeypatch)
    monkeypatch.delenv("PAINT_REGISTRY_TOKENS", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PAINT_REGISTRY_TOKENS=garbage-no-colons\n")
    try:
        with pytest.raises(SystemExit) as exc:
            app_mod.main(["--port", "0", "--env-file", str(env_file)])
        assert exc.value.code == 2
    finally:
        os.environ.pop("PAINT_REGISTRY_TOKENS", None)  # load_dotenv set it

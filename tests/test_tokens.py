"""The `picasso-registry token` CLI: generate/list/revoke/rotate the
PAINT_REGISTRY_TOKENS map in a .env, in the plaintext C18 model — plus the
service-side halves: `main()` dispatch, --env-file loading, and the SIGHUP
auth live-reload.
"""

import os
import signal
import stat

import pytest

from picasso_registry import app as app_mod
from picasso_registry.auth import AuthConfig
from picasso_registry.tokens import _read_map, token_cli


@pytest.fixture(autouse=True)
def _clean_tokens_env():
    """The CLI mirrors the map into os.environ — isolate tests from it."""
    saved = {
        var: os.environ.pop(var, None)
        for var in ("PAINT_REGISTRY_TOKENS", "PAINT_REGISTRY_ENV_FILE")
    }
    yield
    for var, old in saved.items():
        if old is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = old


@pytest.fixture()
def env_file(tmp_path):
    return str(tmp_path / ".env")


def _run(env_file, *args):
    return token_cli([*args, "--env-file", env_file])


def test_add_creates_scoped_labelled_token(env_file):
    assert _run(env_file, "add", "--scope", "write", "--label", "mercury") == 0
    tokens = _read_map(env_file)
    assert len(tokens) == 1
    ((value, info),) = tokens.items()
    assert (info.scope, info.label) == ("write", "mercury")
    # generated value avoids the map separators
    assert not any(c in value for c in ":,;") and not value.isspace()


def test_env_file_is_chmod_600(env_file):
    _run(env_file, "add", "--scope", "read", "--label", "dash")
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600


def test_duplicate_label_rejected(env_file):
    _run(env_file, "add", "--scope", "write", "--label", "m")
    assert _run(env_file, "add", "--scope", "read", "--label", "m") != 0
    assert len(_read_map(env_file)) == 1  # still exactly one token


def test_list_never_prints_token_values(env_file, capsys):
    _run(env_file, "add", "--scope", "read", "--label", "dash")
    ((value, _),) = _read_map(env_file).items()
    capsys.readouterr()
    _run(env_file, "list")
    out = capsys.readouterr().out
    assert "dash" in out and "read" in out
    assert value not in out


def test_revoke_removes_label(env_file):
    _run(env_file, "add", "--scope", "write", "--label", "m")
    assert _run(env_file, "revoke", "--label", "m") == 0
    assert _read_map(env_file) == {}


def test_revoke_unknown_label_errors(env_file):
    assert _run(env_file, "revoke", "--label", "nope") != 0


def test_rotate_changes_value_keeps_scope_label(env_file):
    _run(env_file, "add", "--scope", "write", "--label", "m")
    ((old_value, _),) = _read_map(env_file).items()
    assert _run(env_file, "rotate", "--label", "m") == 0
    ((new_value, info),) = _read_map(env_file).items()
    assert new_value != old_value
    assert (info.scope, info.label) == ("write", "m")


def test_rotate_unknown_label_errors(env_file):
    assert _run(env_file, "rotate", "--label", "nope") != 0


def test_default_env_file_via_env_var(tmp_path, monkeypatch):
    path = str(tmp_path / "registry.env")
    monkeypatch.setenv("PAINT_REGISTRY_ENV_FILE", path)
    assert token_cli(["add", "--scope", "read", "--label", "d"]) == 0
    assert len(_read_map(path)) == 1


def test_main_dispatches_token_subcommand(env_file, capsys):
    _run(env_file, "add", "--scope", "read", "--label", "dash")
    with pytest.raises(SystemExit) as exc:
        app_mod.main(["token", "list", "--env-file", env_file])
    assert exc.value.code == 0
    assert "dash" in capsys.readouterr().out


def test_reload_auth_picks_up_env_file_changes(env_file):
    application = app_mod.create_app(AuthConfig())
    assert not application.state.auth.enabled
    _run(env_file, "add", "--scope", "write", "--label", "m")
    os.environ.pop("PAINT_REGISTRY_TOKENS")  # only the file has the map
    cfg = app_mod.reload_auth(application, env_file)
    assert application.state.auth is cfg
    assert cfg.enabled and len(cfg.tokens) == 1
    # a rotate must land too: reload re-reads the file with override=True
    _run(env_file, "rotate", "--label", "m")
    ((value, _),) = _read_map(env_file).items()
    cfg = app_mod.reload_auth(application, env_file)
    assert cfg.resolve(value) is not None


def test_install_auth_reload_handles_sighup(env_file):
    application = app_mod.create_app(AuthConfig())
    original = signal.getsignal(signal.SIGHUP)
    try:
        assert app_mod.install_auth_reload(application, env_file) is True
        _run(env_file, "add", "--scope", "read", "--label", "d")
        handler = signal.getsignal(signal.SIGHUP)
        handler(signal.SIGHUP, None)  # invoke without delivering a signal
        assert application.state.auth.enabled
    finally:
        signal.signal(signal.SIGHUP, original)

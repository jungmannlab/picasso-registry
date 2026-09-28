"""The `picasso-registry token` CLI: generate/list/revoke/rotate the
PAINT_REGISTRY_TOKENS map in a .env, in the plaintext C18 model — plus the
service-side halves: console dispatch (`cli.main`), and the SIGHUP auth
live-reload (tokens-key-only re-read, fail-closed on an empty map for
non-loopback hosts, missing file = revoked).
"""

import os
import signal
import stat

import pytest

from picasso_registry import app as app_mod
from picasso_registry import cli
from picasso_registry.auth import AuthConfig
from picasso_registry.tokens import _read_map, token_cli


@pytest.fixture(autouse=True)
def _clean_tokens_env():
    """Snapshot/restore by hand: reload_auth mutates os.environ directly, and
    monkeypatch only undoes its *own* changes, not the code-under-test's."""
    names = ("PAINT_REGISTRY_TOKENS", "PAINT_REGISTRY_ENV_FILE")
    saved = {name: os.environ.pop(name, None) for name in names}
    yield
    for name, old in saved.items():
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


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


@pytest.mark.parametrize(
    "label",
    ["a,b", "a;b", "a:b", "a b", "a#b", "a\nb", ""],
)
def test_separator_labels_rejected_before_write(env_file, label):
    """A label carrying the map's metacharacters must fail fast — written
    verbatim it would brick every subsequent read (ValueError from
    parse_tokens) or be silently truncated by dotenv's inline-comment
    parsing."""
    assert _run(env_file, "add", "--scope", "write", "--label", label) == 2
    assert not os.path.exists(env_file)


def test_read_map_ignores_process_env(env_file):
    """File-only: a shell-exported map must not leak into a fresh store."""
    os.environ["PAINT_REGISTRY_TOKENS"] = "envtok:write:legacy"
    assert _read_map(env_file) == {}
    _run(env_file, "add", "--scope", "read", "--label", "dash")
    assert [i.label for i in _read_map(env_file).values()] == ["dash"]


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


def test_cli_dispatches_token_subcommand(env_file, capsys):
    _run(env_file, "add", "--scope", "read", "--label", "dash")
    with pytest.raises(SystemExit) as exc:
        cli.main(["token", "list", "--env-file", env_file])
    assert exc.value.code == 0
    assert "dash" in capsys.readouterr().out


def test_token_cli_survives_malformed_process_env(env_file):
    """The repair tool must run even when the live env map is broken —
    dispatch happens before anything parses PAINT_REGISTRY_TOKENS."""
    os.environ["PAINT_REGISTRY_TOKENS"] = "garbage-no-colons"
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "token",
                "add",
                "--scope",
                "read",
                "--label",
                "d",
                "--env-file",
                env_file,
            ]
        )
    assert exc.value.code == 0
    assert len(_read_map(env_file)) == 1


def test_reload_auth_picks_up_env_file_changes(env_file):
    application = app_mod.create_app(AuthConfig())
    assert not application.state.auth.enabled
    _run(env_file, "add", "--scope", "write", "--label", "m")
    cfg = app_mod.reload_auth(application, env_file)
    assert application.state.auth is cfg
    assert cfg.enabled and len(cfg.tokens) == 1
    # a rotate must land too: reload re-reads the file's token key
    _run(env_file, "rotate", "--label", "m")
    ((value, _),) = _read_map(env_file).items()
    cfg = app_mod.reload_auth(application, env_file)
    assert cfg.resolve(value) is not None


def test_reload_auth_missing_file_revokes_on_loopback(env_file):
    """Deleting the .env + reload = empty map (not silently-kept stale
    tokens), allowed on a loopback bind."""
    os.environ["PAINT_REGISTRY_TOKENS"] = "tok:write:stale"
    application = app_mod.create_app(None)
    assert application.state.auth.enabled
    cfg = app_mod.reload_auth(application, env_file, host="127.0.0.1")
    assert not cfg.enabled
    assert "PAINT_REGISTRY_TOKENS" not in os.environ


def test_reload_auth_refuses_empty_map_on_networked_host(env_file):
    """Fail-closed (ADR 001): a reload may never flip a networked bind into
    disabled-auth mode — the previous config survives."""
    os.environ["PAINT_REGISTRY_TOKENS"] = "tok:write:mercury"
    application = app_mod.create_app(None)
    before = application.state.auth
    with pytest.raises(RuntimeError):
        app_mod.reload_auth(application, env_file, host="0.0.0.0")
    assert application.state.auth is before
    assert os.environ["PAINT_REGISTRY_TOKENS"] == "tok:write:mercury"


def test_reload_auth_prefers_process_env_when_flagged(env_file):
    """prefer_env keeps startup precedence stable across reloads: when the
    process env supplied the map (systemd Environment=), the file never
    overrides it."""
    os.environ["PAINT_REGISTRY_TOKENS"] = "envtok:write:env-owner"
    _run(env_file, "add", "--scope", "write", "--label", "file-owner")
    application = app_mod.create_app(None)
    cfg = app_mod.reload_auth(application, env_file, prefer_env=True)
    assert cfg.resolve("envtok") is not None
    assert [i.label for i in cfg.tokens.values()] == ["env-owner"]


def test_reload_auth_touches_only_the_token_key(env_file):
    """The reload must never clobber other PAINT_REGISTRY_* keys from the
    file into the process env (URL/host/port are startup-owned)."""
    _run(env_file, "add", "--scope", "read", "--label", "d")
    with open(env_file, "a") as fh:
        fh.write("PAINT_REGISTRY_URL=sqlite:///./should-not-leak.db\n")
    application = app_mod.create_app(AuthConfig())
    app_mod.reload_auth(application, env_file)
    assert os.environ.get("PAINT_REGISTRY_URL") != (
        "sqlite:///./should-not-leak.db"
    )


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

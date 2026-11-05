"""Unit tests for the admin console's pure parsing + command building.

These cover the risk surface (parsing the server wrapper's output and building
the right remote commands) without needing paramiko, PyQt6, or an SSH server —
the SSH transport itself is a thin, separately-exercised shim.
"""

from __future__ import annotations

from picasso_registry.admin.core import (
    RegistryAdmin,
    parse_minted_token,
    parse_status,
    parse_token_list,
)


def test_parse_token_list_skips_header_and_empty():
    text = "SCOPE   LABEL\nwrite   microscope-mercury\nread    lab-dashboard\n"
    entries = parse_token_list(text)
    assert [(e.scope, e.label) for e in entries] == [
        ("write", "microscope-mercury"),
        ("read", "lab-dashboard"),
    ]


def test_parse_token_list_handles_no_tokens():
    assert parse_token_list("no tokens configured in /x/registry.env") == []


def test_parse_token_list_ignores_junk_rows():
    # a stray non-scope line must not become a bogus entry
    text = "SCOPE   LABEL\nwrite   a\n(some noise line)\nread   b"
    labels = [(e.scope, e.label) for e in parse_token_list(text)]
    assert labels == [("write", "a"), ("read", "b")]


def test_parse_minted_token_extracts_value():
    out = (
        "Created a write token for 'venus'.\n"
        "    RegistryClient(url, token='abc-123_XYZ')\n"
    )
    assert parse_minted_token(out) == "abc-123_XYZ"


def test_parse_minted_token_absent():
    assert parse_minted_token("no token here") is None


def test_parse_status_running_and_health():
    text = (
        "container: picasso-registry\n"
        "state:     running\n"
        "status:    Up 3 minutes\n"
        "image:     ghcr.io/x/picasso-registry:0.8.0\n"
        "ports:     0.0.0.0:8001->8000/tcp\n"
        "health:    OK (http://127.0.0.1:8001/health)\n"
        "version:   0.8.0\n"
    )
    s = parse_status(text)
    assert s.running is True
    assert s.health_ok is True
    assert s.version == "0.8.0"
    assert s.image == "ghcr.io/x/picasso-registry:0.8.0"
    assert "8001->8000" in (s.ports or "")


def test_parse_status_down():
    s = parse_status("container: picasso-registry\nstate:     exited\n")
    assert s.running is False
    assert s.health_ok is False
    assert s.version is None


def test_admin_builds_quoted_commands():
    # Inject a fake run to capture the exact remote command strings, without
    # opening any SSH connection.
    calls: list[tuple[str, bool]] = []

    admin = RegistryAdmin("host", "user", use_sudo=False)
    admin._run = lambda cmd, sudo=False: calls.append((cmd, sudo)) or "ok"

    admin.token_add("write", "micro-venus")
    admin.restart()
    admin.upgrade("ghcr.io/x/picasso-registry:0.9.0")

    assert calls[0] == (
        "picasso-registry-admin token add --scope write --label micro-venus",
        False,
    )
    assert calls[1] == ("picasso-registry-admin restart", False)
    # upgrade forces sudo regardless of use_sudo
    assert calls[2][1] is True
    assert calls[2][0].startswith("picasso-registry-admin upgrade ")


def test_admin_use_sudo_prefixes_all_ops():
    calls: list[tuple[str, bool]] = []
    admin = RegistryAdmin("host", "user", use_sudo=True)
    admin._run = lambda cmd, sudo=False: calls.append((cmd, sudo)) or ""
    admin.token_list()
    assert calls[0][1] is True  # sudo applied


def test_admin_label_with_shell_metachars_is_quoted():
    # labels are charset-validated server-side, but the core must still quote.
    calls: list[tuple[str, bool]] = []
    admin = RegistryAdmin("host", "user")
    admin._run = lambda cmd, sudo=False: calls.append((cmd, sudo)) or ""
    admin.token_revoke("a b")  # space -> must be quoted as one arg
    assert "'a b'" in calls[0][0]

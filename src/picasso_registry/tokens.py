"""``picasso-registry token`` — manage the service's bearer tokens without
hand-editing files.

Generates high-entropy tokens and maintains the ``PAINT_REGISTRY_TOKENS`` map
(``token:scope:label,…``) in a ``.env`` file, so an operator on the server box
never has to invent random strings or edit the map by hand. This is the
server-side admin tool; it deliberately has no HTTP surface (whoever runs it
already has shell access to the box) — see ADR 001 / C18.

    picasso-registry token add --scope write --label microscope-mercury
    picasso-registry token list
    picasso-registry token revoke --label microscope-mercury
    picasso-registry token rotate --label microscope-mercury

Tokens are stored in **plaintext** in the ``.env`` (the ratified C18 model), so
the value is recoverable from that file by anyone with box access; this tool
prints it once on add/rotate for convenience. A running ``picasso-registry``
started with the same ``--env-file`` (or the default ``./.env``) reads the map
at startup; on Unix, ``kill -HUP <pid>`` live-reloads it without a restart
(``app.install_auth_reload``), else restart the service.

This module is the **shared home** of the token-store tooling, mirroring how
``picasso_registry.auth`` is the shared home of scope enforcement: monet's
``monet token`` binds to it with its own env var / texts instead of keeping a
drifting copy (`monet/tokens.py` predates this module and migrates to a thin
binding in a follow-up). Hence ``token_cli``'s keyword parameters — they are the
binding surface, with registry defaults.

Requires ``python-dotenv`` (in the ``[server]`` extra); the import is lazy so
the base package stays dependency-free.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys

from .auth import DEFAULT_TOKENS_ENV, TokenInfo, parse_tokens

#: Env var naming the .env file both this CLI and the service default to.
DEFAULT_ENV_FILE_ENV = "PAINT_REGISTRY_ENV_FILE"

#: Fallback .env location — cwd-relative, matching the service's default
#: SQLite path (``./picasso_registry.db``).
DEFAULT_ENV_FILE = ".env"

_TOKEN_NBYTES = 32  # -> ~43-char url-safe string

_RESTART_HINT = (
    "  On the server:  restart `picasso-registry`, or `kill -HUP <pid>`\n"
    "                  (Unix) to apply without downtime."
)
_CLIENT_HINT = (
    "  On the client:  pass the value to the client:\n\n"
    "    RegistryClient(url, token={value!r})"
)


def _dotenv():
    """Lazy-import python-dotenv with an actionable error if absent."""
    try:
        import dotenv
    except ModuleNotFoundError:
        raise SystemExit(
            "python-dotenv is required to manage the token .env file — "
            "install the service stack: pip install 'picasso-registry[server]'"
        )
    return dotenv


def _default_env_file() -> str:
    return os.environ.get(DEFAULT_ENV_FILE_ENV) or DEFAULT_ENV_FILE


def load_env_file(path: str | None = None) -> str | None:
    """Best-effort load of PAINT_REGISTRY_* from a .env into the process env.

    ``path=None`` resolves the default (``$PAINT_REGISTRY_ENV_FILE``, else
    ``./.env``) and returns ``None`` silently when the file doesn't exist or
    python-dotenv isn't installed; an explicit ``path`` must exist
    (``FileNotFoundError``). Existing process env vars always win
    (``override=False``). Returns the loaded path.
    """
    explicit = path is not None
    path = path if explicit else _default_env_file()
    if not os.path.exists(path):
        if explicit:
            raise FileNotFoundError(path)
        return None
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        if explicit:
            raise
        return None
    load_dotenv(path, override=False)
    return path


def _read_map(env_file: str, env_var: str = DEFAULT_TOKENS_ENV):
    """Return the current {token: TokenInfo} from the .env file.

    Deliberately file-only: the CLI manages the *file*. Falling back to the
    live process env here would silently copy a shell-exported (possibly
    transient or malformed) map into a fresh .env on the next write.
    """
    raw = None
    if os.path.exists(env_file):
        raw = _dotenv().dotenv_values(env_file).get(env_var)
    return parse_tokens(raw)


def _write_map(env_file: str, tokens, env_var: str = DEFAULT_TOKENS_ENV):
    """Serialize {token: TokenInfo} back to the .env file."""
    serialized = ",".join(
        "{}:{}:{}".format(tok, info.scope, info.label)
        for tok, info in tokens.items()
    )
    # The token must never touch a world-readable file. Create the file 0600
    # *before* set_key writes the secret into it (set_key edits in place), and
    # harden an existing file too. A umask can only remove bits, and 0o600 has
    # no group/other bits, so os.open reliably yields 0o600. If we can't lock
    # an existing file down, warn loudly rather than silently write a secret.
    parent = os.path.dirname(os.path.abspath(env_file))
    if parent:
        os.makedirs(parent, exist_ok=True)
    if not os.path.exists(env_file):
        os.close(os.open(env_file, os.O_CREAT | os.O_WRONLY, 0o600))
    else:
        try:
            os.chmod(env_file, 0o600)
        except OSError as exc:
            print(
                "warning: could not restrict permissions on {}: {}".format(
                    env_file, exc
                ),
                file=sys.stderr,
            )
    _dotenv().set_key(env_file, env_var, serialized, quote_mode="never")


def _new_token() -> str:
    return secrets.token_urlsafe(_TOKEN_NBYTES)


# The map's own separators (',;' between entries, ':' within, '#' starts a
# dotenv inline comment, whitespace/newlines split entries) must never appear
# in a label: parse_tokens would reject the whole store on the next read
# (locking the service out of its token file) or dotenv would silently
# truncate the label. Token *values* are token_urlsafe, so only labels need
# the check.
_LABEL_RE = re.compile(r"[A-Za-z0-9._-]+")


def _check_label(label: str) -> bool:
    if _LABEL_RE.fullmatch(label):
        return True
    print(
        "error: invalid label {!r} — use letters, digits, '.', '_' or '-' "
        "only (',;:#' and whitespace are separators/comments in the stored "
        "map).".format(label),
        file=sys.stderr,
    )
    return False


def _print_new(env_file, value, scope, label, env_var, restart, client):
    print(
        "Created a {} token for {!r}.\n".format(scope, label)
        + "Stored in {} ({}). This tool won't print it "
        "again —\ncopy it now (it is also readable from that file).\n\n"
        "{}\n{}\n\n".format(
            env_file, env_var, restart, client.format(value=value)
        )
        + "(The value was printed to this terminal — clear your shell history "
        "if it is shared or logged.)\n"
    )


def _add(env_file, scope, label, env_var, restart, client):
    if not _check_label(label):
        return 2
    tokens = _read_map(env_file, env_var)
    if any(info.label == label for info in tokens.values()):
        print(
            "error: a token labelled {!r} already exists (use "
            "`token rotate`).".format(label),
            file=sys.stderr,
        )
        return 2
    value = _new_token()
    tokens[value] = TokenInfo(scope=scope, label=label)
    _write_map(env_file, tokens, env_var)
    _print_new(env_file, value, scope, label, env_var, restart, client)
    return 0


def _list(env_file, env_var):
    tokens = _read_map(env_file, env_var)
    if not tokens:
        print("no tokens configured in {}".format(env_file))
        return 0
    print("{:6}  LABEL".format("SCOPE"))
    for info in sorted(tokens.values(), key=lambda i: (i.scope, i.label)):
        # never print the token value itself
        print("{:6}  {}".format(info.scope, info.label))
    return 0


def _revoke(env_file, label, env_var, restart):
    tokens = _read_map(env_file, env_var)
    remaining = {t: i for t, i in tokens.items() if i.label != label}
    removed = len(tokens) - len(remaining)
    if removed == 0:
        print(
            "no token labelled {!r} in {}".format(label, env_file),
            file=sys.stderr,
        )
        return 2
    _write_map(env_file, remaining, env_var)
    print(
        "revoked {} token(s) labelled {!r} — apply it:\n{}".format(
            removed, label, restart
        )
    )
    return 0


def _rotate(env_file, label, env_var, restart, client):
    tokens = _read_map(env_file, env_var)
    matches = [i for i in tokens.values() if i.label == label]
    if not matches:
        print(
            "no token labelled {!r} in {}".format(label, env_file),
            file=sys.stderr,
        )
        return 2
    scope = matches[0].scope
    tokens = {t: i for t, i in tokens.items() if i.label != label}
    value = _new_token()
    tokens[value] = TokenInfo(scope=scope, label=label)
    _write_map(env_file, tokens, env_var)
    _print_new(env_file, value, scope, label, env_var, restart, client)
    return 0


def token_cli(
    argv,
    *,
    env_var: str = DEFAULT_TOKENS_ENV,
    prog: str = "picasso-registry token",
    default_env_file: str | None = None,
    restart_hint: str = _RESTART_HINT,
    client_hint: str = _CLIENT_HINT,
) -> int:
    """Entry point for ``picasso-registry token`` (argv = args after ``token``).

    The keyword parameters are the binding surface for other services (monet)
    that home their token tooling here: pass your own env var, prog name,
    default .env location and hint texts; the store format and file handling
    stay the one audited implementation.
    """
    parser = argparse.ArgumentParser(
        prog=prog,
        description=(
            "Manage the service's bearer tokens ({}) in a .env file.".format(
                env_var
            )
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def _env_arg(p):
        p.add_argument(
            "--env-file",
            default=None,
            help="path to the .env holding {} (default: ${} or ./{}; must be "
            "the file the running service loads — pass the same path to "
            "`picasso-registry --env-file`, or for a systemd EnvironmentFile "
            "deployment its path, e.g. /etc/picasso-registry/registry.env; "
            ".env edits + SIGHUP do NOT reload a systemd "
            "EnvironmentFile).".format(
                env_var, DEFAULT_ENV_FILE_ENV, DEFAULT_ENV_FILE
            ),
        )

    pa = sub.add_parser("add", help="generate and register a new token")
    pa.add_argument("--scope", choices=["read", "write"], required=True)
    pa.add_argument(
        "--label",
        required=True,
        help="holder name, e.g. microscope-mercury or dashboards",
    )
    _env_arg(pa)

    pl = sub.add_parser("list", help="list token scopes + labels (no values)")
    _env_arg(pl)

    pr = sub.add_parser("revoke", help="remove the token(s) for a label")
    pr.add_argument("--label", required=True)
    _env_arg(pr)

    prot = sub.add_parser(
        "rotate", help="replace a label's token with a fresh value"
    )
    prot.add_argument("--label", required=True)
    _env_arg(prot)

    args = parser.parse_args(argv)
    env_file = args.env_file or default_env_file or _default_env_file()

    if args.cmd == "add":
        return _add(
            env_file,
            args.scope,
            args.label,
            env_var,
            restart_hint,
            client_hint,
        )
    if args.cmd == "list":
        return _list(env_file, env_var)
    if args.cmd == "revoke":
        return _revoke(env_file, args.label, env_var, restart_hint)
    if args.cmd == "rotate":
        return _rotate(
            env_file, args.label, env_var, restart_hint, client_hint
        )
    return 1

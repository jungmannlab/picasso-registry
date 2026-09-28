"""Console entry point (``picasso-registry`` / ``python -m picasso_registry``).

Deliberately thin, and deliberately *not* ``app.main``: importing ``.app``
builds the FastAPI app and binds the DB engine at import time, so

* ``picasso-registry token …`` must dispatch **before** that import — a
  malformed token map in the environment would otherwise crash the very tool
  that repairs it, and every token command would pay the full service import;
* the ``.env`` must load **before** that import, so an env-file-supplied
  ``PAINT_REGISTRY_URL`` reaches the engine and the token map reaches the
  module-level app.

Process env always wins over the file (``override=False``). ``app.main``
re-resolves the env file afterwards for proper usage errors — an idempotent
re-load.
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> None:
    import sys

    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "token":
        # `token` has its own sub-command parser (add/list/revoke/rotate).
        from .tokens import token_cli

        raise SystemExit(token_cli(argv[1:]))

    # Best-effort early .env load (see module docstring). A missing explicit
    # --env-file (or missing python-dotenv) is deferred to app.main, which
    # reports it through its parser as a proper usage error.
    explicit = None
    for i, arg in enumerate(argv):
        if arg == "--env-file" and i + 1 < len(argv):
            explicit = argv[i + 1]
        elif arg.startswith("--env-file="):
            explicit = arg.split("=", 1)[1]
    from .tokens import load_env_file

    try:
        load_env_file(explicit)
    except (FileNotFoundError, ModuleNotFoundError):
        pass

    try:
        from .app import main as serve
    except ValueError as exc:
        # create_app() parses the token map at import; surface a malformed
        # map as a clean one-line error instead of a traceback.
        raise SystemExit(f"invalid PAINT_REGISTRY_TOKENS: {exc}")

    serve(argv)

"""Packaging invariant (Option B): the shared surfaces stay lightweight.

`picasso_registry.auth` is imported by monet via the FastAPI-only ``[auth]``
extra, so it must **not** pull the DB/migration stack (sqlalchemy, alembic,
python-ulid) — otherwise ``pip install picasso-registry[auth]`` would drag those
into monet again, defeating the point of slimming the base package.

We prove it by importing the module in a *fresh* interpreter and inspecting
``sys.modules``: even though the full stack is installed in this test env, an
import that doesn't reference those packages won't load them.
"""

import json
import subprocess
import sys


def _top_level_modules(module: str) -> set[str]:
    """Import ``module`` in a clean interpreter; return loaded top-level pkgs."""
    code = (
        f"import {module}; "
        "import sys, json; "
        "print(json.dumps(sorted(m.split('.')[0] for m in sys.modules)))"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return set(json.loads(out))


def test_auth_import_does_not_pull_db_stack():
    loaded = _top_level_modules("picasso_registry.auth")
    leaked = {"sqlalchemy", "alembic", "ulid"} & loaded
    assert not leaked, f"picasso_registry.auth pulled heavy deps: {leaked}"
    # sanity: it really imported and is the FastAPI-based helper
    assert "fastapi" in loaded


def test_bare_package_import_needs_no_runtime_deps():
    loaded = _top_level_modules("picasso_registry")
    leaked = {
        "sqlalchemy",
        "alembic",
        "ulid",
        "fastapi",
        "pydantic",
        "uvicorn",
    } & loaded
    assert not leaked, f"bare `import picasso_registry` pulled: {leaked}"

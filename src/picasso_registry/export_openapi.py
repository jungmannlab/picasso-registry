"""Export the OpenAPI spec to the committed ``openapi.json`` contract artifact.

The spec's ``info.version`` is normalized to a constant so the committed file
stays stable across setuptools-scm version bumps (the tag changes every
release; the contract shape does not). A test asserts the committed file is in
sync with what the app generates.

Regenerate after any schema/route change::

    python -m picasso_registry.export_openapi
"""

from __future__ import annotations

import json
from pathlib import Path

from .app import create_app

CONTRACT_VERSION = "contract"


def repo_root() -> Path:
    # src/picasso_registry/export_openapi.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def build_spec() -> dict:
    """The OpenAPI spec with a normalized (version-independent) info block."""
    spec = create_app().openapi()
    spec.setdefault("info", {})["version"] = CONTRACT_VERSION
    return spec


def dumps(spec: dict) -> str:
    return json.dumps(spec, indent=2, sort_keys=True) + "\n"


def export(path: Path | None = None) -> Path:
    path = path or (repo_root() / "openapi.json")
    path.write_text(dumps(build_spec()))
    return path


def main() -> None:
    out = export()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

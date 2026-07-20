# CLAUDE.md — picasso-registry

Standing context for Claude Code working in this repo. Read this first, then the
work-order brief and the cross-repo contracts.

## What this repo is
A standalone FastAPI + SQLAlchemy provenance & metrics database (service + thin client) for the DNA-PAINT automation stack. Append-only; everything joins on run_id. SQLite now, Postgres-ready. It **owns the schema/API contract** that PycroFlow, picasso-workflow and picasso-agent depend on.

## Conventions (aligned across the DNA-PAINT automation repos)
- **Style:** Black, line length **79** (config in `pyproject.toml [tool.black]`).
- **Lint:** flake8 via Flake8-pyproject; config in `pyproject.toml [tool.flake8]`
  (ignore E203, W503). Run `flake8 .` and `black --check .` before committing.
- **Versioning:** **setuptools-scm** — the version comes from git tags. **Do not
  edit a version string by hand.** Release = `git tag vX.Y.Z && git push --tags`.
- **Pre-commit:** `pre-commit install` once; hooks run black + flake8 + basics.
- **Tests:** `pytest -q`. Write tests with every change; keep CI green.
- **Packaging:** `pyproject.toml` only (no setup.py).

## Build & run
```
python -m pip install -e .[dev]
pre-commit install
pytest -q
```

## Contracts (source of truth — never change silently)
- picasso-registry OpenAPI + pydantic schemas (this repo owns them, if registry).
- metric-vector schema, workflow-YAML schema, `localize_frames` signature,
  picasso-workflow `ModuleSpec`. A contract change is its own work order that
  updates the client and all dependents together.

## Package layout
```
src/picasso_registry/   # importable package
tests/      # pytest
```

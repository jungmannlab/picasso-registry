# CLAUDE.md — picasso-registry

Standing context for Claude Code (claude.ai/code) working in this repo. Read
this first, then the design doc and the cross-repo pointers below.
picasso-registry is one repo in the **DNA-PAINT full-automation** stack
(siblings: PycroFlow, monet, picasso, picasso-workflow, picasso-agent).

## What this repo is

A standalone **FastAPI + SQLAlchemy** provenance & metrics database (a service
plus a thin HTTP client) for the DNA-PAINT automation stack. It is **append-only**
and everything joins on `run_id`; SQLite today, Postgres-ready. This repo **owns
the schema/API contract** that PycroFlow, picasso-workflow and picasso-agent
depend on — so a change to the models/schemas/OpenAPI here is a cross-repo
contract change, never a silent edit (see Contracts below).

The importable package is `picasso_registry/` (under `src/`): `db`, `models`,
`schemas`, `app` (the FastAPI service; `app:main` is the console entry point),
and `client` (the thin requests-based client, `[client]` extra). See `README.md`
for the full picture — this file is the short standing context, not a duplicate.

## Current branch

`feature-FullAutoS0A` — PRs target `main`.

## Commands

```bash
# Install (editable, with dev + test tooling). Use a python>=3.10 env.
python -m pip install -e ".[dev]"
python -m pip install -e ".[client]"   # adds requests for the thin client

# Run the service (console script; equivalent to python -m picasso_registry.app)
picasso-registry

# Test
pytest -q                       # full suite, quiet
pytest -k <name>                # a single test by keyword

# Lint / format — run both before committing (pre-commit runs them too)
black --check .                 # black owns line wrapping @79
flake8 .

# Pre-commit (once), then hooks run on every commit
pre-commit install
pre-commit run --all-files      # run all hooks now
```

## Conventions (aligned across the DNA-PAINT automation repos)

S0A-2 (the repo conventions pack) will complete the alignment for this repo;
the aligned target below is what CLAUDE.md documents.

- **Style:** Black, line length **79** — **Black owns line wrapping**. Config in
  `pyproject.toml [tool.black]` (`_version.py` is `extend-exclude`d).
- **Lint:** flake8 via **Flake8-pyproject**, config in
  `pyproject.toml [tool.flake8]`. Aligned target is
  `extend-ignore = E203, E501, W503` — **E501 is ignored because Black owns line
  length** (it already wraps code; long strings/URLs it can't split are
  intentional). `max-line-length` there is informational only. (Today the repo
  still ignores only `E203, W503`; S0A-2 adds `E501`.)
- **Versioning:** **setuptools-scm** — **the tag IS the version**; there is no
  version string to edit by hand. It writes `src/picasso_registry/_version.py`
  (gitignored, importable as `picasso_registry.__version__`); fallback outside a
  git checkout is `0.0.1.dev0`. Release = `git tag vX.Y.Z && git push --tags`
  (format `vMAJOR.MINOR.PATCH`).
- **Changelog on release:** the changelog is `CHANGELOG.md` at the repo root
  (Keep a Changelog + SemVer, with `Added` / `Changed` / `Fixed` subsections).
  Add an entry under the top **`[Unreleased]`** section in **every PR**; at
  release, promote `[Unreleased]` to a dated, tagged section (e.g.
  `[1.2.3] - YYYY-MM-DD`) and then tag. Because the version comes from git tags,
  the changelog is the human-facing record of what each tag contains.
- **Pre-commit:** `pre-commit install` once; hooks run basics (trailing
  whitespace, end-of-file, yaml) plus **black** and **flake8**.
- **Packaging:** `pyproject.toml` only (no `setup.py` / `setup.cfg`). Runtime
  deps and the `[client]` / `[dev]` extras live there.
- **Tests:** `pytest -q`. Write/extend tests with every change; keep CI green.

## Architecture (short)

The FastAPI **service** (`app`) exposes append-only endpoints backed by
**SQLAlchemy** ORM `models` and validated by pydantic `schemas`; `db` owns the
engine/session (SQLite now, Postgres-ready) and every record joins on `run_id`.
The **thin client** (`client`) is a small requests wrapper over that HTTP API so
dependent repos talk to the registry without importing the service.

## Standing pointers

Paths so later sessions can `@`-reference them. Repo root is
`/workspaces/DNA-PAINT-FullAutomation/repositories/picasso-registry`; the shared
workspace root is `/workspaces/DNA-PAINT-FullAutomation`. The shared planning
docs live in `../../planning/` — start from its document map.

**Live (resolve today):**
- Document map / reading order: `../../planning/README.md`
- Design doc — recommendation & roadmap (strategy, prioritized initiatives
  #1–#9, work packages; **Part VI is the provenance/metrics DB schema** this repo
  implements): `../../planning/DNA-PAINT_Automation-Recommendation.md`
- **Playbook** — Claude Code implementation playbook (operating model, Step 0
  foundations, style/repo alignment, gated dependency-ordered work orders):
  `../../planning/DNA-PAINT_ClaudeCode-Implementation-Playbook.md`
- **Work-order briefs** — self-contained, paste-ready briefs (S0A-1, S0A-2,
  S0B-1/2, WP-1…WP-16); this task is S0A-1:
  `../../planning/DNA-PAINT_Work-Order-Briefs.md`
- **Progress tracker** — tick-off worksheet + gates for the work orders:
  `../../planning/DNA-PAINT_Implementation-Progress-Tracker.md`
- Module-annotations reference (picasso-workflow's `ModuleSpec` layer — part of
  the cross-repo contract set):
  `../../planning/picasso-workflow_Module-Annotations_Reference.md`
- Dev-environment setup (OrbStack dev-container):
  `../../planning/DNA-PAINT_ClaudeCode-DevEnvironment.md`
- Sibling repo standing context / roots:
  - PycroFlow (experiment orchestration): `../PycroFlow/CLAUDE.md`
  - monet (laser-power calibration/control): `../monet/CLAUDE.md`
  - picasso-workflow (analysis workflows): `../picasso-workflow/CLAUDE.md`
  - picasso-agent (agentic layer): `../picasso-agent/CLAUDE.md`
  - picasso (upstream localization/clustering library — `picassosr`; no
    CLAUDE.md yet): `../picasso`

## Contracts (source of truth — never change silently)

This repo **owns** the registry contract. A contract change is its own work order
that updates the client and all dependents together.

- **picasso-registry OpenAPI spec + pydantic schemas** (this repo). As of S0B-1
  these are the in-tree source of truth: SQLAlchemy `models` + pydantic
  `schemas` + the FastAPI `app` under `src/picasso_registry/`, the thin
  `client`, and the exported **OpenAPI spec** at `openapi.json` (repo root).
  Regenerate the spec after any schema/route change with
  `python -m picasso_registry.export_openapi` (a test enforces sync). Alembic
  migrations live in `alembic/`.
- **Shared data contracts** (published S0B-2): the freeze doc is
  `CONTRACTS.md` (repo root) with the importable half in
  `src/picasso_registry/contracts.py`. Four contracts: the **metric-vector**
  model (`MetricVector`, a reuse of `schemas.Metrics`), the **workflow-YAML**
  shape (`Workflow`/`WorkflowStep` — ordered `[{module, parameters}]`; semantic
  validation stays owned by picasso-workflow's `validate_workflow` +
  `MODULE_REGISTRY`), the **`localize_frames(frames, info, params) -> locs`**
  signature (`LocalizeFrames` Protocol — GUI-free; the picasso function is
  *planned*, built in WP-2), and picasso-workflow's **`ModuleSpec`**
  (`../picasso-workflow/picasso_workflow/modulespec.py`, already implemented —
  linked, not rebuilt).

_(The picasso-registry OpenAPI/schema artifacts are **in-tree as of S0B-1**;
the **shared data contracts** are **published as of S0B-2** in `CONTRACTS.md` +
`contracts.py`. The `localize_frames` function itself is still to be built in
picasso (WP-2) — S0B-2 freezes its signature, not its implementation.)_

## Notes for editing

- `.gitignore` **tracks this `CLAUDE.md`** (it is not ignored) but keeps
  `.claude/` and `CLAUDE.local.md` ignored (local settings / personal notes) —
  keep it that way.
- `src/picasso_registry/_version.py` is generated by setuptools-scm and
  gitignored — never commit or hand-edit it.

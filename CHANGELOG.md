# Changelog

All notable changes to **picasso-registry** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/). The version is derived from
git tags via setuptools-scm, so cutting a release means: move the `[Unreleased]` notes into a
new `[x.y.z]` section dated today, then `git tag vx.y.z`.

## [Unreleased]

### Added
- **S0B-1 — completed the registry contract.** Full append-only schema
  (`models.py`): the design-doc Part VI table set (experiment, sample_taxonomy,
  sample_tag, target_channel, reagent_provenance, acquisition_run, fov,
  illumination, environment, fluidics_round, sample_morphology, analysis_run,
  metrics with the full A–D typed columns, resource_usage, qc, feedback,
  artifact) plus the Part VII `interpretation` table — everything joins on
  `run_id`.
- `sample_taxonomy` as an adjacency list + materialized path, with pure
  tree-distance / cascade helpers (`taxonomy.py`).
- pydantic schemas mirroring every table (`schemas.py`); `metrics` uses
  `extra="allow"` so novel metrics ride into the JSON `extra` column.
- Full REST surface (`app.py`, `create_app()`): per-table create/read, plus
  `GET /cohort` (taxon tree-distance fallback, ranked, scoped to the same
  taxonomy root with an optional `max_distance` cap), `GET /node_defaults`
  (inherited cascade), and `POST /bulk` (backfill ingest, depth-sorting
  taxonomy so parents precede children). Persistence via `SessionLocal` /
  `get_session`; all tables wired from one `REGISTRY` source of truth.
- Contract invariants enforced: `metrics.analysis_run_id` and
  `acquisition_run.id` (PycroFlow's run_id) are required; creating a taxonomy
  node under a non-existent parent is rejected (400) rather than silently
  rooted; list endpoints return a stable `ORDER BY id`.
- `openapi.json` exported as the committed contract artifact
  (`export_openapi.py`; a test enforces sync).
- `RegistryClient` fleshed out to mirror the endpoints, and an importable
  in-memory mock (`picasso_registry.testing.mock_registry`) for dependent repos.
- Alembic migrations (`alembic/`, initial revision) — SQLite default, Postgres
  via `PAINT_REGISTRY_URL`.
- Test suite: round-trips every table, cohort tree-distance fallback,
  node_defaults cascade, metrics `extra` keys, bulk ingest, append-only (no
  PUT/DELETE), OpenAPI-in-sync, and migration-matches-models.
- Initial repository scaffold: `pyproject.toml` (setuptools-scm, black @79, flake8),
  pre-commit config, CI workflow, `CLAUDE.md`, and a `src/picasso_registry` package
  (db, models, schemas, app, client) with a passing smoke test.

### Changed
- Aligned style & repo management with the DNA-PAINT stack conventions (S0A-2):
  flake8 now ignores `E501` (Black owns line wrapping @79), matching
  picasso-workflow's rule; tagged an initial `v0.0.1` so setuptools-scm resolves
  a version from the tag; confirmed `pip install -e .` resolves from wheels on
  the 3.10 container with no source builds. No behaviour change.
- Aligned `CLAUDE.md` with the DNA-PAINT stack standing-context template (S0A-1):
  current branch, build/test/lint commands, versioning + changelog-on-release
  rule, a repo-specific architecture summary, standing pointers into the shared
  `planning/` docs, and the contract locations. `.gitignore` now keeps `.claude/`
  and `CLAUDE.local.md` ignored while `CLAUDE.md` stays tracked.
- `persist`/`_row` now fold unknown top-level keys into the JSON `extra` column
  for every table that has one (not just `metrics`), so novel provenance fields
  are preserved rather than silently dropped; an explicit `extra` dict wins over
  a loose top-level key of the same name. `persist_metrics` folded into
  `persist` (one code path).

### Fixed
- **Post-merge code-review findings (S0B-1).**
- `GET /cohort` root filter escapes SQL `LIKE` metacharacters, so a taxon id
  containing `_`/`%` (e.g. `a_b`) can no longer match sibling roots (`axb`).
- Unknown provenance fields posted to any table with an `extra` column are
  preserved in `extra` instead of being silently discarded.
- A caller-supplied `sample_taxonomy.path` is now ignored; the materialized
  path is always derived from `parent_id`, so it can't contradict the tree and
  corrupt cohort distance / node-defaults inheritance.
- Re-posting an already-stored `acquisition_run` id returns **409 Conflict**
  (idempotent-retry safe) instead of a 500 with a SQL stack trace.
- An empty-string `acquisition_run.id` is rejected (422) instead of being
  replaced by a server-minted ULID, upholding the run_id invariant.
- `GET /cohort` selects only the columns it ranks on (not whole ORM rows) and
  falls back to exact-node matches for a path-less taxon rather than scanning
  every root.
- The migration-vs-models test now checks per-table columns and nullability,
  not just the set of table names, catching column drift in CI.

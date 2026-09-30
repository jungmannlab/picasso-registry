# Changelog

All notable changes to **picasso-registry** are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/). The version is derived from
git tags via setuptools-scm, so cutting a release means: move the `[Unreleased]` notes into a
new `[x.y.z]` section dated today, then `git tag vx.y.z`.

## [Unreleased]

### Added
- **Scripted monet-parity deployment** (`deploy/setup-server.sh` +
  `deploy/picasso-registry.service`): dedicated `registry` system user, venv
  under `/opt/picasso-registry`, source checkout owned by the service user
  (no git dubious-ownership, setuptools-scm versions correctly), hardened
  unit (`ProtectSystem=strict`, `ProtectHome=true` — no `/root` paths), DB
  under `/var/lib/picasso-registry`, tokens via `--env-file` so
  `systemctl reload` live-applies token changes, `alembic upgrade head` as
  `ExecStartPre`. Re-running the script IS the upgrade path (`GIT_REF=vX.Y.Z`);
  it preserves an existing DB + token file and chowns them off root,
  migrating a run-as-root deployment in place. README deploy section
  rewritten around it.
- **Dashboard sidebar filters + service version.** The left bar now offers,
  besides the auto-built categorical selects, **numeric min/max range
  filters** for every numeric column that varies in the loaded data (the
  sweep axes conc/power/exposure and the quality metrics), a **date range**
  on `started_at`, and a **reset-filters** button. The header shows the
  running registry version (fetched from the public `/health`). No contract
  change.

## [0.4.0] - 2026-09-30

### Added
- **WP-DASH: browse / compare / rank dashboard** (`picasso_registry.dashboard`,
  served at `GET /dashboard`). monet's tokenized-web-dashboard topology
  (public HTML shell, localStorage read token, 401 → login overlay) over the
  `Databank_Dashboard` template's views: filterable/sortable measurements
  table + CSV export, the composite quality ranking (FRC↓ NeNA↓ specificity↑
  SBR↑ min-max-normalized, direction-aware, slopes excluded — unit-tested
  against the template's ordering), and a Plotly compare scatter (X/Y/color
  from any columns). Data comes from the new read-scoped
  `GET /dashboard/api/measurements` — a derived flat read model (one row per
  acquisition run joining experiment/channel/analysis/metrics incl.
  `data_source` and `quality_score`), a query cache, not a second source of
  truth. `/dashboard` joins `/health` as the only deliberately public routes
  (asserted by the route-coverage test); `openapi.json` regenerated.

## [0.3.0] - 2026-09-30

### Added
- **`picasso-registry-backfill-liveloc`** (`picasso_registry.backfill_liveloc`,
  `[client]` extra): backfill LiveLocalization V0.7/V0.8 `*_qc.json` files
  into the registry via `/bulk`. Deterministic ULIDs from
  `created`+name+position (mount-independent, so re-runs and copies dedupe),
  metric keys renamed onto the typed columns with everything unmapped
  preserved in `extra`, rows marked `analysis_run.kind="liveloc-qc"` +
  `software_version` so tool-computed metrics stay distinguishable from
  pipeline-recomputed (WP-7) ones, the source qc.json linked as an `artifact`
  (sha256), and **no taxonomy guessing** — the raw `sample` block is kept
  verbatim in `experiment.extra` for a later curated descriptor mapping.
  Idempotent file-level sweep: `--dry-run`, per-file failure isolation, and
  `--data-source acquired|simulated` to stamp A15 provenance at ingest.
- **WP-REG-SIM (A15 / C24): `acquisition_run.data_source` + `sim_params`.**
  Closed-vocabulary acquired-vs-simulated provenance (`"acquired" |
  "simulated"`, validated at the schema layer; NULL on legacy rows =
  unknown) plus the generator's ground-truth/settings JSON. Sims share the
  real run_id (ULID) namespace and are distinguished only by this flag, so
  it must be set at ingest (append-only — no retrofitting); learned
  cohort-range consumers exclude `simulated` by default. Additive Alembic
  migration `0004`; `openapi.json` regenerated.
- **README: systemd deployment guide** (production bare-metal via conda/venv):
  pinned tag install, unit file with `WorkingDirectory` = checkout (alembic) +
  `Environment=PAINT_REGISTRY_URL` (absolute DB path, shared by migration and
  service) + tokens via `--env-file` (keeps `systemctl reload` live token
  reload working — an `EnvironmentFile=` would disable it by precedence),
  upgrade procedure, and the field-seen pitfalls (CHDIR, alembic path, env
  file location, relative SQLite URLs, DB backup).

## [0.2.0] - 2026-09-28

### Added
- **`picasso-registry token` admin CLI** (`picasso_registry.tokens`, ported
  from monet's `monet token` and homed here next to the shared auth helper):
  `add`/`list`/`revoke`/`rotate` generate high-entropy bearer tokens and
  maintain the `PAINT_REGISTRY_TOKENS` map in a `0600` `.env` file — no more
  hand-invented token strings. Labels are validated against the map's
  separator characters (a `,;:#`/whitespace label would corrupt the store),
  and the store is file-only (a shell-exported map never leaks into a fresh
  `.env`). Parametrized (`env_var`/`prog`/hints) so monet can bind to it
  instead of keeping a drifting copy (follow-up monet PR).
- **`--env-file` / `PAINT_REGISTRY_ENV_FILE`** on the service: load
  `PAINT_REGISTRY_*` settings (including the token map and the DB URL, which
  rebinds the import-time engine) from a `.env` (defaults to `./.env` if
  present; process env wins). The console entry point is a new thin
  `picasso_registry.cli` that dispatches `token` and loads the `.env` *before*
  importing the service stack, so the repair tool survives a malformed live
  map and token commands skip the FastAPI/SQLAlchemy import; a malformed map
  on the serve path is a clean usage error, not a traceback. The default-env
  token map is honored on the direct-module path
  (`gunicorn picasso_registry.app:app`) too.
- **SIGHUP auth live-reload** (`app.reload_auth` / `app.install_auth_reload`,
  Unix; in Docker the CMD `exec`s the service so `docker kill --signal=HUP`
  reaches it): re-reads *only* the token key, keeps the documented
  process-env-wins precedence across reloads, treats a deleted `.env` as
  revoke-all, and refuses fail-closed to leave a networked bind with an empty
  map (ADR 001). Installed only when auth is armed, so a token-free dev run
  keeps default SIGHUP semantics. `.env`/`*.env` added to `.dockerignore` so
  a local token store is never baked into an image layer.
- `python-dotenv` added to the `[server]` extra (lazy-imported; the base
  package and `[auth]` stay dependency-free).

## [0.1.0] - 2026-09-28

### Changed
- **Dependency-free base package; runtime deps moved into extras.** The base
  package now declares no runtime dependencies — importing `picasso_registry`
  (and the shared `picasso_registry.auth` helper) no longer drags the DB/service
  stack. Install what you use: `[auth]` (FastAPI only — the shared helper monet
  imports), `[contracts]` (pydantic), `[client]` (requests + python-ulid),
  `[server]` (the full FastAPI service: uvicorn/SQLAlchemy/pydantic/Alembic/
  python-ulid). `[test]`/`[dev]` compose over `[server]`.
  - **Why:** `pip install picasso-registry[auth]` used to pull sqlalchemy,
    alembic and python-ulid (unused by the auth module) into consumers like
    monet's server. It is now FastAPI-only, so a lab using monet's serve API no
    longer installs the registry's migration/DB stack. Preserves the ADR-001
    "one audited auth implementation" (no vendoring/fork).
  - **Action for deployers:** install/run the service with `[server]` (or
    `[server,client]`); the Dockerfile now does this. Contributors keep using
    `[dev]`. A new `tests/test_packaging.py` locks the invariant (auth import
    pulls no DB stack; bare import pulls nothing).

### Added
- **WP-3b — service authentication (shared helper for registry + monet).**
  Implements the ratified auth model (ADR
  `docs/adr/001-service-authentication.md`; Open-Decisions **C18**, "was A9").
  - **Shared auth helper** — new `picasso_registry.auth` module (the `[auth]`
    extra) that monet reuses (WP-12a): a `require_scope('read'|'write')` FastAPI
    dependency, a config/env-driven token→`(scope, label)` map (`AuthConfig` /
    `parse_tokens`, from `PAINT_REGISTRY_TOKENS`, format
    `token:scope:label,…`), and a fail-closed host guard (`is_loopback_host`).
    Static bearer tokens, two capability scopes, `write` a superset of `read`.
  - **Scoped enforcement on every route** — `require_scope('read')` on every
    `GET`, `require_scope('write')` on every `POST`/`bulk`. A missing/unknown
    token is **401**, an insufficient scope (read token → POST) is **403**. The
    scheme is table-driven (`create_app` wires it from the same route factory),
    so no route is silently unprotected.
  - **Fail-closed guard, two layers** — `app.main()` refuses to start bound to a
    non-loopback host unless tokens are configured (fast-fail on the
    console-script/Docker path), and a **request-time net** refuses any
    non-loopback request against a disabled-auth service, so the invariant holds
    even when the module app is served directly (gunicorn/uvicorn, bypassing the
    startup guard). The loopback dev path and the in-memory test mock stay
    zero-config (no tokens ⇒ no auth ⇒ unchanged).
  - **`/health` is public** — the liveness/readiness probe is the one
    intentional carve-out from "read on every GET" (probes/monitors carry no
    bearer token; it exposes only `{status, version}`); the route-coverage test
    asserts it is the *only* unguarded route.
  - **`MockRegistryClient(token=…)`** — the in-memory mock can now authenticate
    against an auth-enabled `make_memory_app(auth=…)`, mirroring
    `RegistryClient`, so dependent repos can test their token handling.
  - **Client bearer support** — `RegistryClient(token=…)` (and
    `BufferedRegistryClient(token=…)`) send `Authorization: Bearer …`; absent ⇒
    no header, so loopback dev and the in-memory mock still work.
  - **Contract + docs** — `openapi.json` regenerated with the `HTTPBearer`
    security scheme on every operation (export-sync test enforces it); README
    "Authentication" section (token config, TLS via reverse proxy or uvicorn
    `--ssl-*`, dashboards behind the proxy, per-machine token storage).
- **WP-3 — MVP hardening (deployable + resilient).**
  - **Resilient, non-blocking client** — new
    `picasso_registry.buffered_client.BufferedRegistryClient`, a best-effort
    wrapper over `RegistryClient`. Writes (`log_*` / `create` / `bulk_ingest`,
    every POST) are appended to a durable on-disk **SQLite outbox** and return
    immediately (`{"buffered": True}`); a background daemon thread replays them
    with exponential backoff until the server is reachable. Reads stay
    synchronous. A registry outage never blocks or raises to the caller (the
    acquisition/analysis hot path keeps running); the buffer survives a process
    crash and is replayed by the next client on the same file. `flush(timeout)`
    drains on demand; `close()`/context-manager stops the flusher.
  - **Idempotent analysis-run writes keyed by `(run_id, module, attempt)`** —
    `analysis_run` gains a nullable `attempt` column and a composite UNIQUE
    `(acquisition_run_id, kind, attempt)` (`kind` == module). A replayed/
    duplicated POST of the same triple returns **409** (reusing the existing
    `Conflict` → 409 mapping), so replay can't create a duplicate row; the
    buffered client treats 409 as already-applied and drops it. SQL NULL-is-
    distinct keeps un-keyed rows append-only and backward compatible. Alembic
    migration `0003`; `openapi.json` regenerated for the new field.
  - **Runnable/configurable service** — `app.main()` now takes
    `--host/--port/--db-url/--reload` (env `PAINT_REGISTRY_HOST` /
    `PAINT_REGISTRY_PORT` / `PAINT_REGISTRY_URL`); added a
    `python -m picasso_registry` entry point (`__main__.py`) equivalent to the
    `picasso-registry` console script.
  - **Container/deploy docs** — a `Dockerfile` (+ `.dockerignore`) and a README
    "Deploy / run" section (bare-metal, container, migrations, pointing a
    client at it, resilient-client guarantees). SQLite default, Postgres-ready.
    `.gitignore` now ignores `*.sqlite` (the client buffer file).
- **S0B-2 — published the shared data contracts.** New `CONTRACTS.md` freeze
  doc (repo root) + importable `picasso_registry.contracts` module freezing the
  four cross-repo shapes: `MetricVector` (a verbatim reuse of `schemas.Metrics`
  — typed groups A–D + `extra` passthrough), `Workflow`/`WorkflowStep` (the
  ordered `[{module, parameters}]` workflow-YAML shape; semantic validation
  stays owned by picasso-workflow's `validate_workflow` + `MODULE_REGISTRY`),
  the `LocalizeFrames` Protocol (freezes the GUI-free
  `picasso.localize.localize_frames(frames, info, params) -> locs` signature —
  the picasso function itself is built later in WP-2), and a pointer to
  picasso-workflow's already-implemented `ModuleSpec` (linked, not rebuilt).
  Not wired into the FastAPI app, so `openapi.json` is unchanged.
- **S0B-1b — A2 multi-axis cohort matching (register C12).** Extends the
  sample-taxon-only `/cohort` to the ratified descriptor axes (append-only,
  backward compatible — add, don't repurpose):
  - `models.py`/`schemas.py`: new nullable columns — `experiment.
    acquisition_modality` (axis 3, closed enum TIRF/HILO/spinning_disk/
    light_sheet; single-valued per experiment), and on `target_channel`
    (axis 2) `target_class` (intracellular_protein/membrane_protein/glycan)
    plus the per-target illumination bundle `exposure_ms` / `laser_power_mW`.
    dimensionality + buffer already lived on `experiment`. Leaf vocabularies
    (cell lines, target *names*) stay open strings. Alembic migration `0002`.
  - `GET /cohort`: independent optional filters — axis-2 (`target`/
    `target_set` name overlap, or the closed `target_class`) and axis-3
    (`modality`/`dimensionality`/`buffer`). The caller passes whichever axes a
    comparison needs; *how much* must match is comparison-dependent and left to
    the caller, so the registry does not hard-code a per-metric required-axes
    policy. Tree-distance ranking and root restriction are preserved within the
    constrained set; a bare `taxon_id` call is byte-for-byte the S0B-1
    behaviour. The closed A2 axes (modality, dimensionality, target_class) are
    validated on write, so a stored value can't diverge from the filter enum.
  - `client.cohort(...)` gains keyword-only params for the new axes (explicit
    allow-list, no `**kwargs`, so a typo'd filter raises; an explicit empty
    `target_set` short-circuits to `[]`). `openapi.json` regenerated.
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
- Aligned CI with the hosted-runner merge-gate strategy (S0A-3): split the
  single `ci.yml` into two GitHub-hosted (`ubuntu-latest`) workflows — `Lint`
  (`black --check` + `flake8`) and `Unit Tests (hosted)` (`pytest`, Python
  3.10 + 3.12) — matching picasso-workflow's names so branch-protection required
  checks are consistent across the stack. This repo has no GUI/lab-hardware
  dependency, so there is no self-hosted tier to demote; the unit tier is already
  hermetic (in-memory SQLite via FastAPI `TestClient`, no display/network/config).
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

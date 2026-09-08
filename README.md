# picasso-registry

A standalone FastAPI + SQLAlchemy provenance & metrics database (service + thin
client) for the DNA-PAINT automation stack. Append-only; everything joins on
`run_id` (`acquisition_run.id`). SQLite now, Postgres-ready. It **owns the
schema/API contract** that PycroFlow, picasso-workflow and picasso-agent depend
on.

Part of the DNA-PAINT automation stack. See `CLAUDE.md` for conventions and the
implementation playbook / plan for context.

## Quick start
```bash
python -m pip install -e ".[dev]"     # service + tests
python -m pip install -e ".[client]"  # adds requests for the thin client
pre-commit install
pytest -q
```

## Deploy / run

### Local / bare-metal
```bash
picasso-registry                       # uvicorn on 127.0.0.1:8000
python -m picasso_registry             # identical (module entry point)
# interactive docs at http://127.0.0.1:8000/docs
```

Host, port, and DB URL are configurable via flags or the matching env vars
(flags win):

```bash
picasso-registry --host 0.0.0.0 --port 8080     # PAINT_REGISTRY_HOST/PORT
picasso-registry --db-url sqlite:///./reg.db    # PAINT_REGISTRY_URL
picasso-registry --reload                       # dev auto-reload
```

> **Auth invariant:** do **not** bind a non-loopback host (`--host 0.0.0.0`)
> without the shared auth helper configured — the store is append-only and
> multi-instrument, so an unauthenticated networked bind permanently poisons
> the DB (see `CLAUDE.md` / Open-Decisions **A9**).

### Container
```bash
docker build -t picasso-registry .
# SQLite on a named volume (survives container replacement):
docker run -p 8000:8000 -v registry-data:/data picasso-registry
# Postgres-backed (recommended once volume/concurrency grows):
docker run -p 8000:8000 \
  -e PAINT_REGISTRY_URL=postgresql+psycopg://user:pass@db/picasso_registry \
  picasso-registry
```
The image defaults to `--host 0.0.0.0` and a SQLite DB at `/data`; it runs
`init_db()` on start (a no-op once the tables exist). For production prefer the
Alembic migrate step below as a separate stage and drop the `create_all`.

### Migrations
Alembic owns the production schema; `init_db()` / `create_all()` is a dev/test
convenience.
```bash
export PAINT_REGISTRY_URL=postgresql+psycopg://user:pass@host/picasso_registry
alembic upgrade head
```

### Pointing a client at it
```python
# Synchronous thin client ([client] extra):
from picasso_registry.client import RegistryClient
reg = RegistryClient("http://registry-host:8000")
reg.log_acquisition(id="run1", status="running")   # raises if the registry is down

# Resilient, non-blocking client — for acquisition/analysis code that must
# never stall or crash if the registry is momentarily unreachable:
from picasso_registry.buffered_client import BufferedRegistryClient
with BufferedRegistryClient("http://registry-host:8000",
                            buffer_path="registry_buffer.sqlite") as reg:
    reg.log_acquisition(id="run1", status="running")   # returns immediately;
    # writes are appended to a durable on-disk SQLite buffer and replayed by a
    # background thread once the server is back. Reads stay synchronous.
    reg.log_metrics(analysis_run_id="a1", nena_nm=3.0)
```
See **Resilient client** below for the delivery/idempotency guarantees.

## Resilient client (best-effort, replay-on-failure)

`picasso_registry.buffered_client.BufferedRegistryClient` wraps the plain
`RegistryClient` for callers on the acquisition/analysis hot path:

- **Writes never block or raise.** Each `log_*` / `create` / `bulk_ingest`
  (every `POST`) is appended to a small **on-disk SQLite buffer** and returns
  at once (`{"buffered": True}`). A background thread drains the buffer,
  replaying each POST and retrying with backoff until the server is reachable;
  a registry outage never stalls or crashes the caller. Reads (`get` / `list`
  / `cohort` / `node_defaults` / `health`) stay **synchronous** and raise
  normally. `flush(timeout=…)` drains on demand (tests / clean shutdown);
  `close()` stops the flusher.
- **The buffer is durable** — it survives a process crash or power loss on the
  instrument PC, and is replayed by the next client pointed at the same file.
- **Replay is idempotent.** At-least-once delivery is made safe by server-side
  idempotency keys: `acquisition_run.id`, and `analysis_run`'s composite
  `(acquisition_run_id, kind, attempt)`. A replayed duplicate returns **409**,
  which the flusher treats as already-applied and drops — so no duplicate rows
  (append-only is preserved). Set `attempt` (with `acquisition_run_id` + `kind`)
  on analysis runs to make their retries idempotent.

## The contract

- **Schema** — SQLAlchemy models (`models.py`) mirror design-doc Part VI
  (groups A–J) + Part VII `interpretation`; pydantic schemas (`schemas.py`) are
  the wire contract. Everything is append-only and joins on `run_id`.
- **OpenAPI** — `openapi.json` at the repo root is the committed contract
  artifact. Regenerate after any schema/route change (a test enforces sync):
  ```bash
  python -m picasso_registry.export_openapi
  ```
- **Client** — `picasso_registry.client.RegistryClient` is a thin `requests`
  wrapper mirroring the endpoints (`[client]` extra).

## Shared data contracts (S0B-2)

Beyond the registry's own API, this repo hosts the **frozen cross-repo data
shapes** the automation stack agrees on — see [`CONTRACTS.md`](CONTRACTS.md)
for the full freeze doc. The importable half is `picasso_registry.contracts`:

```python
from picasso_registry.contracts import (
    MetricVector,   # = schemas.Metrics — the metric vector (groups A–D + extra)
    Workflow,       # ordered [{module, parameters}] workflow-YAML shape
    LocalizeFrames, # picasso.localize.localize_frames(frames, info, params)->locs
)
```

Semantic validation of workflows stays owned by picasso-workflow
(`validate_workflow` + `MODULE_REGISTRY`); `localize_frames` is a *planned*
picasso function (WP-2) whose signature S0B-2 freezes; `ModuleSpec` is linked,
not rebuilt.

## REST surface (append-only: POST create, GET read; no update/delete)

- `POST/GET /<table>` and `GET /<table>/{id}` for every table.
- `GET /cohort?taxon_id=…` — acquisition runs ranked by sample-taxon **tree
  distance** (exact node first, then falling back *up* the tree; restricted to
  the same taxonomy root). Optional `max_distance=N` caps how far to generalize.
  The **A2 descriptor** (C12) adds independent optional filters: axis-2
  `target`/`target_set` (name overlap) or `target_class` (closed vocabulary),
  and axis-3 `modality`, `dimensionality`, `buffer`. Pass whichever axes a
  given comparison needs —
  *how much* must match is the caller's choice (the registry doesn't hard-code
  a per-metric policy). Ranking stays tree-distance within the constrained set;
  a bare `taxon_id` call is unchanged.
- `GET /node_defaults?taxon_id=…` — the inherited default **cascade**
  (`defaults` / `expected_metrics` / `qc_rules`), descendant overrides ancestor.
- `POST /bulk` — batch ingest across tables in one transaction (backfill).

## Database & migrations

The URL comes from `PAINT_REGISTRY_URL` (default
`sqlite:///./picasso_registry.db`). **Postgres is recommended** once backfill
volume or concurrent multi-instrument writes grow — point the env var at it:

```bash
export PAINT_REGISTRY_URL=postgresql+psycopg://user:pass@host/picasso_registry
alembic upgrade head
```

Alembic owns the production schema (`alembic upgrade head`); `init_db()` /
`create_all()` is a dev/test convenience.

## Testing against the registry (other repos)

Install the `[test]` extra and import the in-memory mock — no running service,
fresh DB per context:

```python
from picasso_registry.testing import mock_registry

with mock_registry() as reg:
    reg.log_acquisition(id="run1", status="done")      # id = PycroFlow run_id
    assert reg.get("acquisition_run", "run1")["status"] == "done"
```

Notes on the metrics contract: `analysis_run_id` is required (the run_id join
invariant), and novel not-yet-typed metrics may be POSTed as top-level keys —
they are stored in and read back under the JSON `extra` field.

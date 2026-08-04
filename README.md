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

## Run the service
```bash
picasso-registry                       # uvicorn on 127.0.0.1:8000
# interactive docs at http://127.0.0.1:8000/docs
```

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

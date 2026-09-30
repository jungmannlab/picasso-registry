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
python -m pip install -e ".[dev]"     # everything: service + client + tests + tooling
pre-commit install
pytest -q
```

### Install extras

The base package is **dependency-free** (importing `picasso_registry` alone pulls
nothing); each surface installs exactly what it needs, so a consumer that only
wants the shared auth helper never drags the DB/migration stack:

| Extra | Installs | For |
|---|---|---|
| `[auth]` | FastAPI | the shared `picasso_registry.auth` helper (imported by monet) |
| `[contracts]` | pydantic | the frozen cross-repo data contracts (`picasso_registry.contracts`) |
| `[client]` | requests + python-ulid | the thin + buffered REST clients |
| `[server]` | FastAPI, uvicorn, SQLAlchemy, pydantic, Alembic, python-ulid | run the service |
| `[test]` | `[server,client]` + httpx | the in-memory mock for dependent repos' tests |
| `[dev]` | `[server,client,contracts]` + pytest/black/flake8/pre-commit | contributing here |

```bash
python -m pip install -e ".[server]"   # run the FastAPI service
python -m pip install -e ".[auth]"     # just the shared auth helper
python -m pip install -e ".[client]"   # just the REST clients
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
> the DB. This is enforced: the service **refuses to start** on a non-loopback
> host with no tokens (see Authentication below, `CLAUDE.md`, and
> `docs/adr/001-service-authentication.md`).

### systemd service (production bare-metal, conda/venv)

The tested production setup for a non-container host: a pinned install in a
conda env (or venv) run as a systemd unit. A foreground `picasso-registry` in
an SSH session dies with the session — systemd gives restart-on-failure, boot
persistence, and a `reload` verb for live token changes.

**1. Install pinned** (the tag is the version; never deploy an untagged HEAD):

```bash
conda create -n picasso-registry python=3.10 -y
conda run -n picasso-registry pip install \
  "picasso-registry[server] @ git+https://github.com/jungmannlab/picasso-registry@v0.2.0"
# alembic needs its config + migration scripts from a checkout of the SAME tag:
git clone --branch v0.2.0 https://github.com/jungmannlab/picasso-registry \
  ~/GitHub/picasso-registry
```

**2. One-time filesystem setup** — a home for the DB and one for the token
store:

```bash
mkdir -p /var/lib/picasso-registry /etc/picasso-registry
# mint the token map straight into its production location:
picasso-registry token add --scope write --label microscope-mercury \
  --env-file /etc/picasso-registry/registry.env
chmod 600 /etc/picasso-registry/registry.env   # token add sets this already
```

**3. The unit** — `/etc/systemd/system/picasso-registry.service` (adjust the
env path and checkout location):

```ini
[Unit]
Description=picasso-registry provenance/metrics service
After=network-online.target

[Service]
# The checkout: alembic.ini + alembic/ must resolve from the working dir.
# (systemd fails with status=200/CHDIR if this directory doesn't exist.)
WorkingDirectory=/root/GitHub/picasso-registry
# Absolute DB path (sqlite://// = absolute) so the DB never lands in the
# checkout; Environment= is seen by BOTH the migration and the service.
Environment=PAINT_REGISTRY_URL=sqlite:////var/lib/picasso-registry/picasso_registry.db
ExecStartPre=/root/miniconda3/envs/picasso-registry/bin/alembic upgrade head
ExecStart=/root/miniconda3/envs/picasso-registry/bin/picasso-registry \
    --host 0.0.0.0 --port 8000 --env-file /etc/picasso-registry/registry.env
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now picasso-registry
systemctl status picasso-registry          # expect: active (running)
curl -s http://127.0.0.1:8000/health
```

**Why tokens go via `--env-file` and the DB URL via `Environment=`** (don't
"simplify" to one systemd `EnvironmentFile=` for both): if systemd injected
`PAINT_REGISTRY_TOKENS` into the process environment, the service would treat
the *environment* as the token source and SIGHUP would — by the documented
process-env-wins precedence — stop re-reading the file, killing live reload.
With the map only in the `--env-file`, token changes apply without downtime:

```bash
picasso-registry token rotate --label microscope-mercury \
  --env-file /etc/picasso-registry/registry.env
systemctl reload picasso-registry          # SIGHUP → re-reads the token map
```

The DB URL, by contrast, is startup-owned and static — and `ExecStartPre`'s
`alembic upgrade head` must see the *same* URL it migrates, which only
`Environment=` provides to both processes.

**Upgrades:** `git -C ~/GitHub/picasso-registry fetch --tags && git -C
~/GitHub/picasso-registry checkout vX.Y.Z`, pip-install the same tag into the
env, then `systemctl restart picasso-registry` (the `ExecStartPre` migration
brings the schema to head; additive migrations are the norm here).

**Pitfalls seen in the field:**
- `status=200/CHDIR` at start ⇒ `WorkingDirectory` doesn't exist.
- `FAILED: Path doesn't exist: alembic` ⇒ the working dir isn't a checkout
  (alembic resolves `alembic.ini`/`alembic/` relative to it).
- `env file not found` ⇒ `picasso-registry token add` wrote `./.env` in
  whatever directory it ran in; mint with (or move it to) the `--env-file`
  path the unit references.
- Prefer `sqlite:////abs/path.db` (four slashes) — a relative
  `sqlite:///./…` puts the DB inside the checkout.
- **Back up the DB file** (`/var/lib/picasso-registry/`): the registry is the
  append-only source of truth; the checkout and env are re-creatable, the DB
  is not.

Runs as root above for brevity; the tidier end state is a dedicated system
user (`useradd -r registry`, `chown` the two directories, `User=registry` in
the unit) once the service is proven on the host.

### Authentication (scoped bearer tokens)

The registry uses static **bearer tokens with two capability scopes** — `read`
(enforced on every `GET`, except the public `/health` liveness probe) and
`write` (every `POST`/`bulk`); `write` is a superset of `read`. A token maps
server-side to a `(scope, label)`, where
`label` is the owner — a machine role (`microscope-mercury`, `cluster`) or a
person. The same helper (`picasso_registry.auth`, the `[auth]` extra) secures
monet. See `docs/adr/001-service-authentication.md`.

**Mint and manage tokens** with the built-in admin CLI (server-side only — no
HTTP surface; whoever runs it already has shell access to the box). It
generates high-entropy values and maintains the `token:scope:label` map in a
`0600`-permissioned `.env` file, so you never invent or hand-edit token
strings:

```bash
picasso-registry token add --scope write --label microscope-mercury
picasso-registry token add --scope read  --label dashboard
picasso-registry token list                    # scopes + labels, never values
picasso-registry token rotate --label microscope-mercury
picasso-registry token revoke --label microscope-mercury
```

The map lives under `PAINT_REGISTRY_TOKENS` in `./.env` by default (override
per call with `--env-file`, or globally with `PAINT_REGISTRY_ENV_FILE`). The
service picks it up from the same file:

```bash
picasso-registry --host 0.0.0.0 --port 8000    # loads ./.env if present
picasso-registry --env-file /etc/picasso-registry/registry.env --host 0.0.0.0
```

`--env-file` loads every `PAINT_REGISTRY_*` setting (host/port/DB URL/tokens);
explicit process env vars win over the file. On Unix a token-armed service
live-reloads the token map on `kill -HUP <pid>` (in Docker:
`docker kill --signal=HUP <container>`), so add/rotate/revoke apply without
downtime — only the token key is re-read, and a reload that would leave a
*networked* bind with an empty map is refused fail-closed (restart instead;
the startup guard then refuses the bind). systemd `EnvironmentFile`
deployments need a restart — SIGHUP re-reads the `.env`, not systemd's
environment. Serving the module app directly
(`gunicorn picasso_registry.app:app`) honors `$PAINT_REGISTRY_ENV_FILE` /
`./.env` for the **token map** too; the `--env-file` flag and a
`.env`-supplied **DB URL** are console-script conveniences — on the gunicorn
path set `PAINT_REGISTRY_URL` in the process environment. Setting
`PAINT_REGISTRY_TOKENS` directly in the environment still works:

```bash
export PAINT_REGISTRY_TOKENS="$MERCURY_TOKEN:write:microscope-mercury,
$CLUSTER_TOKEN:write:cluster,
$DASH_TOKEN:read:dashboard"
picasso-registry --host 0.0.0.0 --port 8000
```

- **Zero-config on loopback.** With no tokens set the service is
  unauthenticated — allowed **only** on a loopback bind (`127.0.0.1`). Two
  layers keep "networked but unauthenticated" from happening: a **startup host
  guard** refuses a non-loopback bind with no tokens (the `picasso-registry` /
  Docker path), and a **request-time net** — a disabled-auth service refuses any
  request from a non-loopback peer — which holds even if you serve the app
  directly (`gunicorn picasso_registry.app:app`, bypassing the startup guard).
  The in-memory test mock (`picasso_registry.testing`) is likewise token-free.
- **Token storage.** The server-side map lives in the `0600` `.env` the token
  CLI maintains (or the machine's environment) — **never commit tokens, never
  store them in the DB.** On the client, keep the single token value in that
  machine's environment or its own gitignored secrets file. Per-writer tokens
  let one instrument be revoked/rotated without touching the others.
- **TLS.** Never send bearer tokens in cleartext. Terminate TLS at a reverse
  proxy (caddy/nginx) on the service host, or run uvicorn with
  `--ssl-keyfile`/`--ssl-certfile`. An internal-CA / self-signed cert is fine on
  a trusted lab LAN.
- **Dashboards / browsers** sit **behind the reverse proxy**, which does the
  human auth (HTTP Basic, or lab SSO); the API itself stays bearer-token.
  Dashboard view-vs-edit is exactly `read` vs `write`: a `read` token is safe to
  share lab-wide, the `write` token is the sensitive one.

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
The image defaults to `--host 0.0.0.0` and a SQLite DB at `/data`; on start it
runs `alembic upgrade head` (the single schema authority; idempotent once the
DB is at head) and then serves. For a dedicated migrate stage, drop the
`alembic upgrade head` from the image `CMD` and run it as its own step (below).

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
reg = RegistryClient("http://registry-host:8000", token="…")  # token optional
reg.log_acquisition(id="run1", status="running")   # raises if the registry is down
# token= adds `Authorization: Bearer …`; omit it against a loopback dev instance
# (no header, still works). BufferedRegistryClient takes the same token= kwarg.

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

## Backfill: LiveLocalization qc.json history

`picasso-registry-backfill-liveloc` (in the `[client]` extra) ingests the
V0.7/V0.8 LiveLocalization `*_qc.json` archive — identity, sample metadata,
acquisition parameters and final metrics per measurement position:

```bash
export PAINT_REGISTRY_TOKEN=…          # write-scope token (omit on loopback)
picasso-registry-backfill-liveloc /pool/users --url http://registry:8000 \
  --data-source acquired --dry-run     # preview; then drop --dry-run
picasso-registry-backfill-liveloc /pool/users --url http://registry:8000 \
  --data-source acquired
# simulation databanks MUST be stamped (A15/C24 — sims are default-excluded
# from learned cohort ranges via this flag, and it cannot be retrofitted):
picasso-registry-backfill-liveloc /pool/sim_runs --url http://registry:8000 \
  --data-source simulated
```

Idempotent and re-runnable: row ids are deterministic ULIDs from
`created`+name+position (mount- and timezone-independent — naive timestamps
are pinned to UTC for minting), already-ingested measurements are skipped
(within one sweep, an identical duplicate copy skips quietly; same identity
with *different* content fails loudly), and a broken file fails alone without
aborting the sweep — but an unreachable/broken registry aborts it. Metrics land
on the typed columns (`n_locs`, `nena_nm` = whole-FOV NeNA, `frc_nm`,
`photons_median`, `density_locs_um2`, `sbr`); everything unmapped (zoom NeNA,
specificity, per-batch series) is preserved in `extra`, the source file is
linked as an `artifact` (sha256), and rows carry
`analysis_run.kind="liveloc-qc"` + the tool's `software_version` so
tool-computed metrics stay distinguishable from pipeline-recomputed (WP-7)
ones. The free-text `sample` block is kept verbatim in `experiment.extra` —
the descriptor-taxonomy mapping is a later, curated step (see the module
docstring in `picasso_registry/backfill_liveloc.py`).

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

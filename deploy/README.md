# Deploying picasso-registry (cheat-sheet)

Two supported deploys, same data/token layout — pick one:

| | [`setup-server.sh`](setup-server.sh) (bare-metal) | [`install-docker.sh`](install-docker.sh) (container) |
|---|---|---|
| Runs as | systemd-managed venv, dedicated `registry` user | Docker container (`--restart unless-stopped`) |
| Needs on host | **Python ≥3.11** | **Docker** (the script installs it if missing) |
| Best when | you want native systemd / no Docker | the host has no suitable Python (e.g. Ubuntu 20.04) |

Both are **idempotent installers *and* updaters** — there is no separate update
script; **re-running is the upgrade path**. Both preserve the DB and token file,
keep the DB at `/var/lib/picasso-registry` and tokens at
`/etc/picasso-registry/registry.env`, and run `alembic upgrade head` on start.

Run everything below as root (`sudo`).

## Bare-metal (systemd) — install & update

```bash
# First install (tracks main — fine for initial bring-up)
sudo bash deploy/setup-server.sh

# Update an already-running server (ALWAYS pin the tag in production)
sudo GIT_REF=v0.7.0 bash deploy/setup-server.sh
```

That second command **is** the update. It stops the service, checks out
`GIT_REF`, reinstalls the venv, runs `alembic upgrade head`, rewrites the unit,
and `restart`s it (so the old process is actually replaced, not left running).

It is safe to re-run. It **preserves your DB and token file** (and chowns them
to the `registry` user), and never touches `/root`.

> Pin `GIT_REF` to a released tag in production. Latest is `v0.7.0`
> (`git tag --sort=-v:refname | head` lists them).

## Verify after a deploy

```bash
curl -s http://127.0.0.1:8000/health       # should return OK
journalctl -u picasso-registry -f          # live logs
systemctl status picasso-registry          # unit state
```

The script also warns if the running process isn't the venv it just installed —
watch for that line in its output.

## Containerized (Docker) — install & update

No host Python needed — the interpreter lives in the image, which is why this
path is the answer on hosts without a Python ≥3.11 (e.g. Ubuntu 20.04). The
script installs Docker if it's missing, builds the image, mints tokens on first
run, and (re)starts the container.

```bash
# First install (installs Docker if absent, tracks main)
sudo bash deploy/install-docker.sh

# Update a running container (ALWAYS pin the tag in production)
sudo GIT_REF=v0.7.0 bash deploy/install-docker.sh
```

Re-running is the update: it checks out `GIT_REF`, rebuilds the image, and
replaces the container (`docker rm -f` + fresh `docker run`). The DB
(`/var/lib/picasso-registry` → `/data`) and token file survive. Verify:

```bash
curl -s http://127.0.0.1:8000/health       # should return OK
docker logs -f picasso-registry            # live logs
docker ps --filter name=picasso-registry   # container state
```

Tokens live-reload without downtime — the token file is **mounted** (not baked
into the image or injected as `-e`, which would disable reload):

```bash
# mint via a throwaway container (the token CLI ships in the image):
docker run --rm -v /etc/picasso-registry:/etc/picasso-registry picasso-registry \
  picasso-registry token add --scope write --label microscope-mercury \
  --env-file /etc/picasso-registry/registry.env
docker kill --signal=HUP picasso-registry  # re-read the token map, no restart
```

**Pull a prebuilt image instead of building** (the fast path for a new lab box
— no git clone, no build toolchain). On a `v*` release tag, CI publishes
`ghcr.io/jungmannlab/picasso-registry:<version>` (see
`.github/workflows/docker-image.yml`). Point `PULL_IMAGE` at it:

```bash
sudo PULL_IMAGE=ghcr.io/jungmannlab/picasso-registry:v0.7.0 \
  bash deploy/install-docker.sh
```

If the GHCR package is private, `docker login ghcr.io` once first; if it's
public, nothing extra is needed.

Container-path overrides: `REGISTRY_PORT` (host port, default `8000`), `IMAGE`,
`CONTAINER`, `GIT_REF`, `PULL_IMAGE` (pull instead of build), plus
`SKIP_DOCKER_INSTALL=1` to refuse auto-installing Docker. `DATA_DIR` /
`ETC_DIR` / `SRC_DIR` match the bare-metal paths.

## Where everything lives

| Path | What it is |
|------|-----------|
| `/opt/picasso-registry/src/` | the git checkout (the upgrade target; holds alembic config) |
| `/opt/picasso-registry/.venv/` | the venv the service runs from |
| `/etc/picasso-registry/registry.env` | the token map (`0600`) |
| `/var/lib/picasso-registry/picasso_registry.db` | the append-only DB — **the one thing to back up** |
| `/etc/systemd/system/picasso-registry.service` | the systemd unit |

Everything except the DB is re-creatable by re-running the script. Back up the
DB file.

## Tokens (day-2)

Tokens ride `--env-file`, so they live-reload on SIGHUP — no downtime, no
restart:

```bash
sudo -u registry picasso-registry token add --scope write --label microscope-mercury \
  --env-file /etc/picasso-registry/registry.env
sudo systemctl reload picasso-registry     # SIGHUP — re-reads the token map
```

```bash
sudo -u registry picasso-registry token list --env-file /etc/picasso-registry/registry.env
```

(`list` shows scopes + labels, never the token values.)

## Overrides

The script reads config from the environment — override inline as needed:

| Var | Default | |
|-----|---------|-|
| `GIT_REF` | `main` | **pin a tag in production** |
| `REGISTRY_HOST` | `0.0.0.0` | networked bind requires tokens (fail-closed) |
| `REGISTRY_PORT` | `8000` | |
| `PYTHON` | autodetected | needs >= 3.11; **not** under `/root` (deadsnakes `python3.11` is the safe bet) |
| `REPO_URL` | the GitHub repo | |
| `APP_DIR` / `DATA_DIR` / `ETC_DIR` | see script head | |

See the comments at the top of [`setup-server.sh`](setup-server.sh) for the full
list, and the top-level [`README.md`](../README.md#deploy--run) for the
narrative version (auth invariant, why tokens use `--env-file`, etc.).

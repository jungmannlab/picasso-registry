#!/usr/bin/env bash
# picasso-registry — containerized deploy (Docker).
#
# Companion to the bare-metal deploy/setup-server.sh: SAME data/token layout
# (DB under /var/lib/picasso-registry, token file under /etc/picasso-registry),
# but the service runs in a Docker container instead of a systemd-managed venv.
# No host Python needed — the interpreter lives in the image (which is why this
# path sidesteps the "host has no Python 3.11" problem on older distros).
#
# Safe to re-run — RE-RUNNING IS ALSO HOW YOU UPGRADE: it updates the source
# checkout to GIT_REF, rebuilds the image, and replaces the running container.
# It PRESERVES an existing token file and database.
#
# What it does:
#   1. installs Docker if missing (official get.docker.com; SKIP_DOCKER_INSTALL=1 to opt out)
#   2. clones/updates the source to SRC_DIR at GIT_REF (.git is needed for the version)
#   3. builds the image
#   4. mints a write + read token on first run (preserves an existing env file)
#   5. (re)starts the container: DB on a host bind-mount, token file mounted
#      read-only so `docker kill --signal=HUP` live-reloads tokens
#
# Usage (as root):
#   sudo bash deploy/install-docker.sh
#   sudo GIT_REF=v0.7.0 bash deploy/install-docker.sh      # pinned install / upgrade
set -euo pipefail

# ---- configuration (override via environment) -------------------------------
IMAGE="${IMAGE:-picasso-registry}"
CONTAINER="${CONTAINER:-picasso-registry}"
APP_DIR="${APP_DIR:-/opt/picasso-registry}"
# Own checkout dir (NOT $APP_DIR/src, which setup-server.sh owns as the
# `registry` user — root git ops there would trip git's dubious-ownership
# guard). DATA_DIR + ETC_DIR ARE shared with the bare-metal path on purpose,
# so a host can switch between the two deploys over the same DB and tokens.
SRC_DIR="${SRC_DIR:-$APP_DIR/docker-src}"          # source clone (build context)
DATA_DIR="${DATA_DIR:-/var/lib/picasso-registry}"  # append-only DB (-> /data)
ETC_DIR="${ETC_DIR:-/etc/picasso-registry}"        # registry.env (tokens)
ENV_FILE="${ENV_FILE:-$ETC_DIR/registry.env}"
REPO_URL="${REPO_URL:-https://github.com/jungmannlab/picasso-registry.git}"
GIT_REF="${GIT_REF:-main}"                         # pin a tag in production!
# Set PULL_IMAGE to a published image ref (e.g.
# ghcr.io/jungmannlab/picasso-registry:v0.7.0) to pull a prebuilt image
# instead of cloning + building locally — the fast path for a new lab box.
# A private GHCR package needs `docker login ghcr.io` first; a public one
# needs nothing. Unset (default) = clone $REPO_URL@$GIT_REF and build.
PULL_IMAGE="${PULL_IMAGE:-}"
REGISTRY_PORT="${REGISTRY_PORT:-8000}"             # host port -> container 8000
# The token file path INSIDE the container (where $ETC_DIR is mounted):
CONTAINER_ETC="/etc/picasso-registry"
CONTAINER_ENV_FILE="$CONTAINER_ETC/registry.env"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || {
  echo "run as root (sudo bash deploy/install-docker.sh)"; exit 1;
}

# ---- 1. Docker ---------------------------------------------------------------
if command -v docker >/dev/null 2>&1; then
  log "docker present ($(docker --version))"
elif [ "${SKIP_DOCKER_INSTALL:-0}" = "1" ]; then
  echo "ERROR: docker not found and SKIP_DOCKER_INSTALL=1 set — install Docker first." >&2
  exit 1
else
  log "installing Docker (official get.docker.com convenience script)"
  curl -fsSL https://get.docker.com | sh
fi
# Ensure the daemon is up now and starts on boot (harmless if already enabled;
# tolerated where systemd isn't the init, e.g. inside some VMs).
systemctl enable --now docker 2>/dev/null || true
docker info >/dev/null 2>&1 || {
  echo "ERROR: the Docker daemon is not reachable — start it and re-run." >&2
  exit 1
}

# ---- 2. obtain the image (pull a prebuilt one, or clone + build) ------------
if [ -n "$PULL_IMAGE" ]; then
  log "pulling prebuilt image $PULL_IMAGE"
  docker pull "$PULL_IMAGE" || {
    echo "ERROR: could not pull $PULL_IMAGE. If the GHCR package is private," >&2
    echo "run 'docker login ghcr.io' first, or unset PULL_IMAGE to build locally." >&2
    exit 1
  }
  # Normalize to $IMAGE:latest so the steps below are pull/build agnostic.
  docker tag "$PULL_IMAGE" "$IMAGE:latest"
else
  # .git is part of the build context: setuptools-scm derives the version from
  # the tags at build time (see Dockerfile). mkdir -p (not install -d) so an
  # existing $APP_DIR created by setup-server.sh keeps its owner/mode.
  mkdir -p "$APP_DIR"
  if [ -d "$SRC_DIR/.git" ]; then
    log "updating source in $SRC_DIR -> $GIT_REF"
    git -C "$SRC_DIR" fetch --all --tags --quiet
    git -C "$SRC_DIR" checkout --quiet "$GIT_REF"
    git -C "$SRC_DIR" pull --ff-only --quiet 2>/dev/null || true
  else
    log "cloning $REPO_URL -> $SRC_DIR ($GIT_REF)"
    git clone --quiet "$REPO_URL" "$SRC_DIR"
    git -C "$SRC_DIR" checkout --quiet "$GIT_REF"
  fi
  # Tag with the resolved ref AND latest so the running container is traceable.
  TAG="$(git -C "$SRC_DIR" describe --tags --always 2>/dev/null || echo "$GIT_REF")"
  log "building image $IMAGE:$TAG"
  docker build -t "$IMAGE:$TAG" -t "$IMAGE:latest" "$SRC_DIR"
fi

# ---- 4. directories + token env file (preserve if it already has content) ---
install -d -m 750 "$DATA_DIR" "$ETC_DIR"
if [ -s "$ENV_FILE" ]; then
  log "keeping existing token file $ENV_FILE"
else
  log "no tokens at $ENV_FILE — minting one write ($(hostname -s)) + one read (lab-dashboard) token"
  # The token CLI lives in the image; mint via throwaway containers with
  # $ETC_DIR mounted rw so the file is created on the host.
  docker run --rm -v "$ETC_DIR:$CONTAINER_ETC" "$IMAGE:latest" \
    picasso-registry token add --scope write --label "$(hostname -s)" \
    --env-file "$CONTAINER_ENV_FILE"
  docker run --rm -v "$ETC_DIR:$CONTAINER_ETC" "$IMAGE:latest" \
    picasso-registry token add --scope read --label lab-dashboard \
    --env-file "$CONTAINER_ENV_FILE"
fi
chmod 600 "$ENV_FILE"

# ---- 5. (re)start the container ----------------------------------------------
# Replace any existing container (an upgrade re-run): rm -f, then run fresh.
log "replacing any existing container $CONTAINER"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true

# Tokens ride a MOUNTED file + PAINT_REGISTRY_ENV_FILE — NOT `-e
# PAINT_REGISTRY_TOKENS`. Injecting the map into the process env would, by the
# documented process-env-wins precedence, disable the SIGHUP live-reload;
# reading it from the mounted file keeps `docker kill --signal=HUP $CONTAINER`
# re-reading add/rotate/revoke without downtime. The DB is a host bind-mount
# under $DATA_DIR (the one thing to back up). Migrations run from the image
# CMD (alembic upgrade head) before the service binds.
log "starting container $CONTAINER (host port $REGISTRY_PORT -> 8000)"
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "$REGISTRY_PORT:8000" \
  -v "$DATA_DIR:/data" \
  -v "$ETC_DIR:$CONTAINER_ETC:ro" \
  -e "PAINT_REGISTRY_ENV_FILE=$CONTAINER_ENV_FILE" \
  "$IMAGE:latest" >/dev/null

# ---- 6. health ---------------------------------------------------------------
log "waiting for the service to answer /health"
ok=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://localhost:$REGISTRY_PORT/health" >/dev/null 2>&1; then
    ok=1; break
  fi
  sleep 1
done
docker ps --filter "name=$CONTAINER" \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
if [ -z "$ok" ]; then
  echo "WARNING: /health did not answer within ~10s — check: docker logs $CONTAINER" >&2
fi

echo
log "health:    curl -s http://localhost:$REGISTRY_PORT/health"
log "logs:      docker logs -f $CONTAINER"
log "dashboard: http://$(hostname -s):$REGISTRY_PORT/dashboard"
log "tokens:    docker run --rm -v $ETC_DIR:$CONTAINER_ETC $IMAGE:latest \\"
log "             picasso-registry token add --scope write --label <holder> --env-file $CONTAINER_ENV_FILE"
log "           then: docker kill --signal=HUP $CONTAINER   # live-reload, no downtime"

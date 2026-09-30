#!/usr/bin/env bash
# picasso-registry — one-shot setup for a dedicated systemd service
# (monet-parity: mirrors monet's deploy/setup-server.sh).
#
# Creates a locked-down `registry` system user, installs the service into a
# venv under /opt/picasso-registry, keeps the DB under /var/lib/picasso-registry
# and the token file under /etc/picasso-registry, runs the Alembic migration,
# then installs and starts the systemd unit.
#
# Safe to re-run — RE-RUNNING IS ALSO HOW YOU UPGRADE: it updates the source
# checkout to GIT_REF, reinstalls, migrates, restarts. It PRESERVES an
# existing token file and database (and chowns them away from root, fixing a
# previous run-as-root deployment). It does NOT touch /root checkouts/conda.
#
# Usage (as root):
#   sudo bash deploy/setup-server.sh
#   sudo GIT_REF=v0.5.0 bash deploy/setup-server.sh        # pinned upgrade
set -euo pipefail

# ---- configuration (override via environment) -------------------------------
REGISTRY_USER="${REGISTRY_USER:-registry}"
APP_DIR="${APP_DIR:-/opt/picasso-registry}"       # venv + source clone
SRC_DIR="${SRC_DIR:-$APP_DIR/src}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
DATA_DIR="${DATA_DIR:-/var/lib/picasso-registry}" # the append-only DB
ETC_DIR="${ETC_DIR:-/etc/picasso-registry}"       # registry.env (tokens)
ENV_FILE="${ENV_FILE:-$ETC_DIR/registry.env}"
DB_PATH="${DB_PATH:-$DATA_DIR/picasso_registry.db}"
REPO_URL="${REPO_URL:-https://github.com/jungmannlab/picasso-registry.git}"
GIT_REF="${GIT_REF:-main}"                        # pin a tag in production!
REGISTRY_HOST="${REGISTRY_HOST:-0.0.0.0}"
REGISTRY_PORT="${REGISTRY_PORT:-8000}"
PYTHON="${PYTHON:-python3}"
UNIT="/etc/systemd/system/picasso-registry.service"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
[ "$(id -u)" -eq 0 ] || { echo "run as root (sudo bash deploy/setup-server.sh)"; exit 1; }

# git/pip run AS THE SERVICE USER (the owner of /opt/picasso-registry), so
# git's "dubious ownership" guard never trips and setuptools-scm can read the
# tags (the version IS the tag in this repo).
as_registry() { sudo -u "$REGISTRY_USER" env "HOME=$APP_DIR" "$@"; }

# ---- 1. stop any existing service -------------------------------------------
# Unconditional: the previous `list-unit-files | grep -q` gate could fail
# spuriously (grep -q SIGPIPEs systemctl under pipefail) and skip the stop,
# leaving the OLD process serving after the unit file was replaced.
log "stopping any existing picasso-registry.service"
systemctl stop picasso-registry 2>/dev/null || true

# ---- 2. dedicated system user ------------------------------------------------
if id -u "$REGISTRY_USER" >/dev/null 2>&1; then
  log "user $REGISTRY_USER already exists"
else
  log "creating system user $REGISTRY_USER"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$REGISTRY_USER"
fi

# ---- 3. directories ----------------------------------------------------------
log "ensuring $APP_DIR $DATA_DIR $ETC_DIR"
install -d -o "$REGISTRY_USER" -g "$REGISTRY_USER" -m 750 \
  "$APP_DIR" "$DATA_DIR" "$ETC_DIR"

# ---- 4. source + venv (idempotent) ------------------------------------------
[ -e "$SRC_DIR" ] && chown -R "$REGISTRY_USER:$REGISTRY_USER" "$SRC_DIR"
if [ -d "$SRC_DIR/.git" ]; then
  log "updating source in $SRC_DIR -> $GIT_REF"
  as_registry git -C "$SRC_DIR" fetch --all --tags --quiet
  as_registry git -C "$SRC_DIR" checkout --quiet "$GIT_REF"
  as_registry git -C "$SRC_DIR" pull --ff-only --quiet 2>/dev/null || true
else
  log "cloning $REPO_URL -> $SRC_DIR ($GIT_REF)"
  as_registry git clone --quiet "$REPO_URL" "$SRC_DIR"
  as_registry git -C "$SRC_DIR" checkout --quiet "$GIT_REF"
fi

# Python >=3.10 (the repo floor). The system python3 on older distros
# (Ubuntu 20.04 = 3.8) is too old — see monet's deployment doc for deadsnakes.
pick_python() {
  local c
  for c in "${PYTHON:-}" python3.12 python3.11 python3.10; do
    [ -n "$c" ] || continue
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys;raise SystemExit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
PYBIN="$(pick_python)" || {
  echo "ERROR: picasso-registry needs Python >=3.10 and none was found." >&2
  echo "Install one (e.g. deadsnakes python3.10 + python3.10-venv) and re-run," >&2
  echo "or pass PYTHON=/path/to/python3.10 (NOT under /root)." >&2
  exit 1
}
case "$PYBIN" in
  /root/*) echo "WARNING: $PYBIN is under /root; ProtectHome hides it from the '$REGISTRY_USER' user — the service WILL fail. Use a system python." >&2 ;;
esac

if [ ! -x "$VENV_DIR/bin/pip" ]; then
  log "creating venv $VENV_DIR from $PYBIN ($("$PYBIN" -V 2>&1))"
  rm -rf "$VENV_DIR"
  as_registry "$PYBIN" -m venv "$VENV_DIR"
fi
log "installing picasso-registry[server,client] into the venv"
as_registry "$VENV_DIR/bin/pip" install --quiet --upgrade pip
as_registry "$VENV_DIR/bin/pip" install --quiet -e "$SRC_DIR[server,client]"
chown -R "$REGISTRY_USER:$REGISTRY_USER" "$APP_DIR"

# `picasso-registry` on PATH for admins:
#   sudo -u registry picasso-registry token list --env-file /etc/picasso-registry/registry.env
ln -sf "$VENV_DIR/bin/picasso-registry" /usr/local/bin/picasso-registry
ln -sf "$VENV_DIR/bin/picasso-registry-backfill-liveloc" \
  /usr/local/bin/picasso-registry-backfill-liveloc

# ---- 5. token env file (preserve if it already has content) -----------------
if [ -s "$ENV_FILE" ]; then
  log "keeping existing token file $ENV_FILE"
else
  log "no tokens at $ENV_FILE — minting one write ($(hostname -s)) + one read (lab-dashboard) token"
  as_registry "$VENV_DIR/bin/picasso-registry" token add \
    --scope write --label "$(hostname -s)" --env-file "$ENV_FILE"
  as_registry "$VENV_DIR/bin/picasso-registry" token add \
    --scope read --label lab-dashboard --env-file "$ENV_FILE"
fi
chmod 600 "$ENV_FILE"
chown "$REGISTRY_USER:$REGISTRY_USER" "$ENV_FILE"

# ---- 6. hand existing data to the service user (root-deploy migration) ------
chown -R "$REGISTRY_USER:$REGISTRY_USER" "$DATA_DIR"

# ---- 7. systemd unit (see deploy/picasso-registry.service for the comments) --
log "writing $UNIT"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=picasso-registry provenance/metrics service
Documentation=https://github.com/jungmannlab/picasso-registry
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$REGISTRY_USER
Group=$REGISTRY_USER
WorkingDirectory=$SRC_DIR
Environment=HOME=$DATA_DIR
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PAINT_REGISTRY_URL=sqlite:///$DB_PATH
ExecStartPre=$VENV_DIR/bin/alembic upgrade head
ExecStart=$VENV_DIR/bin/picasso-registry --host $REGISTRY_HOST --port $REGISTRY_PORT --env-file $ENV_FILE
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
UNITEOF

# ---- 8. start ----------------------------------------------------------------
log "reloading + (re)starting"
systemctl daemon-reload
systemctl reset-failed picasso-registry 2>/dev/null || true
systemctl enable picasso-registry
# restart, not `enable --now`: --now is a plain `start`, which is a NO-OP on
# an already-running unit — an upgrade re-run would leave the old process
# (old code, old interpreter) serving the new unit's name.
systemctl restart picasso-registry
sleep 1
systemctl --no-pager --full status picasso-registry || true
# Belt-and-braces: the serving process must be the venv we just installed.
MAIN_PID=$(systemctl show -p MainPID --value picasso-registry)
if [ -n "$MAIN_PID" ] && [ "$MAIN_PID" != "0" ] \
    && ! readlink "/proc/$MAIN_PID/exe" | grep -q "^$VENV_DIR/"; then
  echo "WARNING: the running service ($(readlink /proc/$MAIN_PID/exe)) is NOT" >&2
  echo "the freshly installed venv ($VENV_DIR) — investigate before trusting it." >&2
fi

echo
log "health:    curl -s http://localhost:$REGISTRY_PORT/health"
log "dashboard: http://$(hostname -s):$REGISTRY_PORT/dashboard  (read token: picasso-registry token list --env-file $ENV_FILE)"
log "tokens:    sudo -u $REGISTRY_USER picasso-registry token add --scope write --label <holder> --env-file $ENV_FILE && systemctl reload picasso-registry"
log "logs:      journalctl -u picasso-registry -f"

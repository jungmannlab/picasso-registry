# picasso-registry service image.
#
# Builds the FastAPI provenance/metrics service into a small runtime image.
# SQLite by default (a mounted volume keeps the DB across restarts); point
# PAINT_REGISTRY_URL at Postgres for the networked, multi-instrument
# deployment (see the README "Deploy / run" section).
#
# Multi-stage: the builder has git so setuptools-scm reads the tags and bakes
# the REAL version into the wheel (the version IS the git tag in this repo).
# The runtime stage installs that wheel offline — no git, no .git — so a
# `docker build` from a tagged checkout reports e.g. 0.7.0, not the
# 0.0.1.dev0 fallback a git-less slim image would produce.

# ---- builder: resolve version from git, build wheels ------------------------
FROM python:3.11-slim AS builder
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
ENV PIP_NO_CACHE_DIR=1
WORKDIR /app
# .git is needed here (setuptools-scm reads the tags); it is NOT copied into
# the runtime image below.
COPY . /app
# Build the package wheel (version stamped from the tags) plus wheels for the
# [server,client] dependency closure, so the runtime install is fully offline.
RUN pip wheel --wheel-dir /wheels ".[server,client]"

# ---- runtime: slim, git-less ------------------------------------------------
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAINT_REGISTRY_HOST=0.0.0.0 \
    PAINT_REGISTRY_PORT=8000 \
    PAINT_REGISTRY_URL=sqlite:////data/picasso_registry.db

WORKDIR /app
# Install the service + client extras from the pre-built wheels (no index, no
# network, no build tools in the runtime image).
COPY --from=builder /wheels /wheels
RUN pip install --no-index --find-links=/wheels "picasso-registry[server,client]" \
    && rm -rf /wheels

# Alembic config lives at the repo root (outside the wheel); the start-up
# migration resolves alembic.ini + alembic/ relative to WORKDIR. env.py imports
# the (installed) picasso_registry package for the metadata.
COPY alembic.ini /app/alembic.ini
COPY alembic /app/alembic

# Default DB lives on a volume so it survives container replacement.
VOLUME ["/data"]
EXPOSE 8000

# Apply migrations (the single schema authority), then serve. Alembic owns the
# schema; `alembic upgrade head` on start is idempotent (a no-op once the DB is
# at head) and avoids the create_all-vs-Alembic conflict of mixing the two on
# one DB (create_all bypasses Alembic's version bookkeeping). For a dedicated
# migrate stage, drop this from CMD and run `alembic upgrade head` as its own
# step (see README). `exec` makes the service PID 1 so signals reach it —
# `docker kill --signal=HUP <container>` live-reloads the token map; without
# it, sh stays PID 1 and swallows the HUP.
CMD ["sh", "-c", "alembic upgrade head && exec picasso-registry --host \"$PAINT_REGISTRY_HOST\" --port \"$PAINT_REGISTRY_PORT\""]

# picasso-registry service image.
#
# Builds the FastAPI provenance/metrics service into a small runtime image.
# SQLite by default (a mounted volume keeps the DB across restarts); point
# PAINT_REGISTRY_URL at Postgres for the networked, multi-instrument
# deployment (see the README "Deploy / run" section).
FROM python:3.10-slim

# setuptools-scm derives the version from git metadata at build time; copying
# .git in lets the installed package report a real version instead of the
# 0.0.1.dev0 fallback. (Nothing in the runtime path needs git.)
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PAINT_REGISTRY_HOST=0.0.0.0 \
    PAINT_REGISTRY_PORT=8000 \
    PAINT_REGISTRY_URL=sqlite:////data/picasso_registry.db

WORKDIR /app
COPY . /app

# Service + client extras (client is handy for in-container smoke tests). No
# [dev] tooling in the runtime image.
RUN pip install ".[client]"

# Default DB lives on a volume so it survives container replacement.
VOLUME ["/data"]
EXPOSE 8000

# Apply migrations (the single schema authority), then serve. Alembic owns the
# schema; `alembic upgrade head` on start is idempotent (a no-op once the DB is
# at head) and avoids the create_all-vs-Alembic conflict of mixing the two on
# one DB (create_all bypasses Alembic's version bookkeeping). For a dedicated
# migrate stage, drop this from CMD and run `alembic upgrade head` as its own
# step (see README).
CMD ["sh", "-c", "alembic upgrade head && picasso-registry --host \"$PAINT_REGISTRY_HOST\" --port \"$PAINT_REGISTRY_PORT\""]

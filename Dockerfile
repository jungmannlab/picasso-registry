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

# Create the schema, then serve. In production prefer running
# `alembic upgrade head` as a separate migrate step (see README); create_all
# here keeps the single-container quick-start turnkey and is a no-op once the
# tables exist.
CMD ["sh", "-c", "python -c 'from picasso_registry.db import init_db; init_db()' && picasso-registry --host \"$PAINT_REGISTRY_HOST\" --port \"$PAINT_REGISTRY_PORT\""]

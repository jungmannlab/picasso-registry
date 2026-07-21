"""Engine / session setup.

SQLite by default; point ``PAINT_REGISTRY_URL`` at Postgres (recommended once
backfill volume or concurrent multi-instrument writes grow) to switch. The
app persists through ``get_session`` (a FastAPI dependency), so tests and the
in-memory mock can swap in their own session factory via
``app.dependency_overrides``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

DATABASE_URL = os.environ.get(
    "PAINT_REGISTRY_URL", "sqlite:///./picasso_registry.db"
)

Base = declarative_base()


def make_engine(url: str | None = None) -> Engine:
    """Create an engine for ``url`` (defaults to ``PAINT_REGISTRY_URL``)."""
    url = url or DATABASE_URL
    connect_args = (
        {"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    return create_engine(url, future=True, connect_args=connect_args)


engine = make_engine()
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, expire_on_commit=False, future=True
)


def create_all(bind: Engine | None = None) -> None:
    """Create every table on ``bind`` (dev/test convenience).

    Production deployments use the Alembic migrations instead.
    """
    from . import models  # noqa: F401  (register models on Base)

    Base.metadata.create_all(bind or engine)


def init_db() -> None:
    """Create tables on the default engine."""
    create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a session bound to the default engine."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

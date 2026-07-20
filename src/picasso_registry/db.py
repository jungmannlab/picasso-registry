"""Engine / session setup. SQLite now; switch the URL to Postgres later."""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.environ.get(
    "PAINT_REGISTRY_URL", "sqlite:///./picasso_registry.db"
)

Base = declarative_base()
engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


def init_db() -> None:
    """Create tables. Replace with Alembic migrations for production."""
    from . import models  # noqa: F401  (register models on Base)

    Base.metadata.create_all(engine)

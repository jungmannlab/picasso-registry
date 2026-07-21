"""The Alembic initial migration must build exactly the model tables."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from picasso_registry import models  # noqa: F401  (register tables)
from picasso_registry.db import Base

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_migration_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("PAINT_REGISTRY_URL", url)

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    command.upgrade(cfg, "head")

    inspector = inspect(create_engine(url))
    tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert tables == set(Base.metadata.tables.keys())

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

    # Guard column-level drift too, not just the table set: a column added to
    # a model but forgotten in the migration (or vice versa) must fail CI
    # rather than blow up at insert time in production Postgres.
    for name, table in Base.metadata.tables.items():
        migrated = {c["name"]: c for c in inspector.get_columns(name)}
        model_cols = {c.name: c for c in table.columns}
        assert set(migrated) == set(model_cols), f"{name}: column set drift"
        for col_name, col in model_cols.items():
            assert (
                migrated[col_name]["nullable"] == col.nullable
            ), f"{name}.{col_name}: nullability drift"


def test_a2_axes_migration_up_down(tmp_path, monkeypatch):
    # 0002 adds the A2 axis-2/3 columns and cleanly removes them on downgrade.
    url = f"sqlite:///{tmp_path / 'ad.db'}"
    monkeypatch.setenv("PAINT_REGISTRY_URL", url)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))

    added = {
        "experiment": {"acquisition_modality"},
        "target_channel": {"target_class", "exposure_ms", "laser_power_mW"},
    }
    engine = create_engine(url)

    command.upgrade(cfg, "head")
    for table, cols in added.items():
        present = {c["name"] for c in inspect(engine).get_columns(table)}
        assert cols <= present, f"{table}: missing {cols - present}"

    command.downgrade(cfg, "0001_initial")
    for table, cols in added.items():
        present = {c["name"] for c in inspect(engine).get_columns(table)}
        assert not (cols & present), f"{table}: {cols & present} not dropped"

    command.upgrade(cfg, "head")  # re-upgrades cleanly

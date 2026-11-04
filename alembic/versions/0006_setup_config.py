"""setup_config: versioned, content-addressed microscope-setup configs

Adds the ``setup_config`` table — a versioned rig config, deduped by
``content_hash`` (UNIQUE) so a client registers one row and references it from
many runs (``crud.persist_setup_config`` is an idempotent get-or-create). Also
adds a nullable ``setup_config_id`` column to ``acquisition_run`` pointing at
it. PycroFlow's per-rig setups are user-local / uncommitted, so this is how a
dataset records *which* config produced it.

The FK is declared on the model (honoured by ``create_all``). In the migration
the real FK constraint is added on dialects that support ALTER-ADD (Postgres —
the production DB), and skipped on SQLite, which cannot ``ALTER TABLE ADD`` a
named FK outside a full table rebuild; the SQLite migration test only compares
tables/columns/nullability, so it stays green while Postgres gets the same
referential integrity the model declares.

Revision ID: 0006_setup_config
Revises: 0005b_handoff
Create Date: 2026-11-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0006_setup_config"
down_revision: Union[str, None] = "0005b_handoff"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "setup_config",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("config_yaml", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "content_hash", name="uq_setup_config_content_hash"
        ),
    )
    op.add_column(
        "acquisition_run",
        sa.Column("setup_config_id", sa.String(), nullable=True),
    )
    # Real FK on Postgres (prod); SQLite can't ALTER-ADD a named FK — its
    # create_all-built test DB already has it from the model.
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_acquisition_run_setup_config_id",
            "acquisition_run",
            "setup_config",
            ["setup_config_id"],
            ["id"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(
            "fk_acquisition_run_setup_config_id",
            "acquisition_run",
            type_="foreignkey",
        )
    op.drop_column("acquisition_run", "setup_config_id")
    op.drop_table("setup_config")

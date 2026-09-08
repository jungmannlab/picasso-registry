"""analysis_run idempotency key (run_id, module, attempt)

WP-3. Adds the nullable ``attempt`` column and a composite UNIQUE constraint
``(acquisition_run_id, kind, attempt)`` on ``analysis_run`` so a replayed or
duplicated POST for the same natural key is a clean 409 instead of a duplicate
row. Append-only and backward compatible: the column is nullable and SQL treats
NULLs as distinct, so existing / un-keyed rows (all three not fully supplied)
keep inserting freely.

Revision ID: 0003_analysis_run_idem
Revises: 0002_a2_axes
Create Date: 2026-09-08 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003_analysis_run_idem"
down_revision: Union[str, None] = "0002_a2_axes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table so both ADD COLUMN and ADD CONSTRAINT work on SQLite
    # (which can't ALTER ... ADD CONSTRAINT directly); it recreates the table.
    with op.batch_alter_table("analysis_run") as batch:
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=True))
        batch.create_unique_constraint(
            "uq_analysis_run_natural_key",
            ["acquisition_run_id", "kind", "attempt"],
        )


def downgrade() -> None:
    with op.batch_alter_table("analysis_run") as batch:
        batch.drop_constraint("uq_analysis_run_natural_key", type_="unique")
        batch.drop_column("attempt")

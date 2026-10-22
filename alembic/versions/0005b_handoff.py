"""handoff: WP-9 dataset hand-off state machine (MUTABLE job-queue state)

WP-9 Phase A. Adds the ``handoff`` table — the one mutable table in this
otherwise append-only store. One row per QC-passed dataset
(``acquisition_run_id`` UNIQUE — one hand-off per dataset), carrying it through
``ready -> claimed -> running -> done/failed``. The exactly-once cluster claim
is an atomic compare-and-set UPDATE on this table (see ``crud.claim_handoff``);
the provenance of the run itself still lives append-only in ``analysis_run`` /
``qc``. The UNIQUE constraint mirrors ``0003``'s natural-key style so a second
ready for the same dataset is a clean 409, not a duplicate queue entry.

Revision ID: 0005b_handoff
Revises: 0005_cohort_metric_range
Create Date: 2026-10-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005b_handoff"
down_revision: Union[str, None] = "0005_cohort_metric_range"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "handoff",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("acquisition_run_id", sa.String(), nullable=False),
        sa.Column("analysis_run_id", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("worker", sa.String(), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["acquisition_run_id"],
            ["acquisition_run.id"],
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_run.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "acquisition_run_id", name="uq_handoff_acquisition_run_id"
        ),
    )


def downgrade() -> None:
    op.drop_table("handoff")

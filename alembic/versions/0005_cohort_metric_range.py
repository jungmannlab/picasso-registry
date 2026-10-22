"""cohort_metric_range: WP-7 learned per-(taxon, metric) ranges (append-only)

WP-7 range write-back. Adds the ``cohort_metric_range`` table so the backfill's
learned per-taxon expected-metric ranges (p05/p50/p95 + n) are stored queryably
in the registry instead of only in a JSON sidecar. One row per (taxon_id,
metric) learned-range snapshot; the store is append-only, so a re-derivation
inserts a new row and consumers read the LATEST per (taxon_id, metric) by
``created_at`` / insertion order. Deliberately does NOT touch
``sample_taxonomy.expected_metrics`` (the recommender-read join is WP-10's
concern).

Revision ID: 0005_cohort_metric_range
Revises: 0004_acq_data_source
Create Date: 2026-10-22 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0005_cohort_metric_range"
down_revision: Union[str, None] = "0004_acq_data_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cohort_metric_range",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("taxon_id", sa.String(), nullable=True),
        sa.Column("metric", sa.String(), nullable=True),
        sa.Column("p05", sa.Float(), nullable=True),
        sa.Column("p50", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("n", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["taxon_id"],
            ["sample_taxonomy.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("cohort_metric_range")

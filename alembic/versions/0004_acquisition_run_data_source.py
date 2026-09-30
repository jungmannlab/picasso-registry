"""acquisition_run acquired-vs-simulated provenance (A15 / C24)

WP-REG-SIM. Adds ``data_source`` ('acquired' | 'simulated', validated as a
closed enum at the schema layer) and ``sim_params`` (the generator's
ground-truth/settings JSON) to ``acquisition_run``. Sims share the real
run_id (ULID) namespace and are distinguished only by this flag; learned
cohort-range consumers exclude ``data_source = 'simulated'`` by default.
Additive and backward compatible: both columns are nullable, NULL on legacy
rows reads as "unknown". The store is append-only, so the flag must be set
at ingest — it cannot be retrofitted.

Revision ID: 0004_acq_data_source
Revises: 0003_analysis_run_idem
Create Date: 2026-09-30 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004_acq_data_source"
down_revision: Union[str, None] = "0003_analysis_run_idem"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "acquisition_run",
        sa.Column("data_source", sa.String(), nullable=True),
    )
    op.add_column(
        "acquisition_run",
        sa.Column("sim_params", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("acquisition_run", "sim_params")
    op.drop_column("acquisition_run", "data_source")

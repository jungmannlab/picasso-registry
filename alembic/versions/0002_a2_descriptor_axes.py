"""A2 three-axis descriptor: acquisition modality + per-target bundle

Register C12 (ratified 2026-08-04). Adds the axis-2/axis-3 columns needed for
metric-dependent cohort matching. All new columns are nullable (append-only:
existing rows stay NULL). dimensionality + buffer already exist on experiment
from the initial schema, so only modality (on acquisition_run) and the
per-target bundle + target_class (on target_channel) are added here.

Revision ID: 0002_a2_axes
Revises: 0001_initial
Create Date: 2026-08-04 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_a2_axes"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "acquisition_run",
        sa.Column("acquisition_modality", sa.String(), nullable=True),
    )
    op.add_column(
        "target_channel",
        sa.Column("target_class", sa.String(), nullable=True),
    )
    op.add_column(
        "target_channel",
        sa.Column("exposure_ms", sa.Float(), nullable=True),
    )
    op.add_column(
        "target_channel",
        sa.Column("laser_power_mW", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    # batch_alter_table so DROP COLUMN works on SQLite too (older engines
    # can't ALTER ... DROP COLUMN directly).
    with op.batch_alter_table("target_channel") as batch:
        batch.drop_column("laser_power_mW")
        batch.drop_column("exposure_ms")
        batch.drop_column("target_class")
    with op.batch_alter_table("acquisition_run") as batch:
        batch.drop_column("acquisition_modality")

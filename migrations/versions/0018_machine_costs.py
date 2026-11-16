"""Machine costs: hourly machine rate in the settings, machine cost per print (issue #135).

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ALTER TABLE: existing rows get 0 and stay as they are (the old wear of a print
    # stays in wear_cost), and no table is rebuilt, so dependent rows are not touched.
    op.add_column(
        "app_settings",
        sa.Column("machine_rate", sa.String(length=40), server_default="0", nullable=False),
    )
    op.add_column(
        "production_prints",
        sa.Column("machine_cost", sa.String(length=40), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("production_prints", "machine_cost")
    op.drop_column("app_settings", "machine_rate")

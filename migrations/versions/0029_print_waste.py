"""Waste per print: share in percent and amount in euro as values (issue #216).

Revision ID: 0029
Revises: 0028
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing productions count as 0 % waste
    with op.batch_alter_table("production_prints") as batch:
        batch.add_column(
            sa.Column("waste_share", sa.String(length=40), server_default="0", nullable=False)
        )
        batch.add_column(
            sa.Column("waste_cost", sa.String(length=40), server_default="0", nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("production_prints") as batch:
        batch.drop_column("waste_cost")
        batch.drop_column("waste_share")

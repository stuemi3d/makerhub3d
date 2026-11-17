"""Settings: the single printer rate is dropped, the rate comes from the devices (issue #162).

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("machine_rate")


def downgrade() -> None:
    op.add_column(
        "app_settings",
        sa.Column("machine_rate", sa.String(length=40), nullable=False, server_default="0"),
    )

"""Setting: show only business spools in the calculation (issue #186).

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Off for everybody (also existing installations): all spools are offered
    op.add_column(
        "app_settings",
        sa.Column("spool_business_only", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("spool_business_only")

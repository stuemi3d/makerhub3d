"""Printer models: the order of the rows of a power table (issue #177).

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "printer_model_powers",
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    # Existing rows keep the order they were created in (the id grows with every new row).
    op.execute("UPDATE printer_model_powers SET position = id")


def downgrade() -> None:
    with op.batch_alter_table("printer_model_powers") as batch:
        batch.drop_column("position")

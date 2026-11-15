"""Address and key of the Bambuddy connection (issue #117).

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    table = op.create_table(
        "bambuddy_connection",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("url", sa.String(length=200), nullable=False),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.CheckConstraint("id = 1", name="ck_bambuddy_connection_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.bulk_insert(table, [{"id": 1, "url": "", "api_key_encrypted": None}])


def downgrade() -> None:
    op.drop_table("bambuddy_connection")

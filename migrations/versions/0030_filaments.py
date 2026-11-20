"""Filaments as parent objects for Bambuddy spools (issue #235).

Revision ID: 0030
Revises: 0029
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filaments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("brand", sa.String(length=50), server_default="", nullable=False),
        sa.Column("material", sa.String(length=50), nullable=False),
        sa.Column("subtype", sa.String(length=50), server_default="", nullable=False),
        sa.Column("color_name", sa.String(length=50), server_default="", nullable=False),
        sa.Column("color", sa.String(length=7), server_default="", nullable=False),
        sa.Column("standard_price", sa.String(length=40), nullable=True),
        sa.Column("min_stock_kg", sa.String(length=40), nullable=True),
        sa.Column("note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("identity_key", sa.String(length=250), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_filaments_identity_key", "filaments", ["identity_key"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_filaments_identity_key", table_name="filaments")
    op.drop_table("filaments")

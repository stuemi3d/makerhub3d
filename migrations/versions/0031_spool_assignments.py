"""Bambuddy spools assigned to filaments (issue #236).

Revision ID: 0031
Revises: 0030
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "spool_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("spool_id", sa.Integer(), nullable=False),
        sa.Column("filament_id", sa.Integer(), nullable=False),
        sa.Column("price_per_kg", sa.String(length=40), nullable=True),
        sa.Column("weight_g", sa.String(length=40), nullable=True),
        sa.Column("spool_key", sa.String(length=250), nullable=False),
        sa.Column("assigned_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["filament_id"], ["filaments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ux_spool_assignments_spool_id", "spool_assignments", ["spool_id"], unique=True)
    op.create_index("ix_spool_assignments_filament_id", "spool_assignments", ["filament_id"])
    op.create_index("ix_spool_assignments_spool_key", "spool_assignments", ["spool_key"])


def downgrade() -> None:
    op.drop_index("ix_spool_assignments_spool_key", table_name="spool_assignments")
    op.drop_index("ix_spool_assignments_filament_id", table_name="spool_assignments")
    op.drop_index("ux_spool_assignments_spool_id", table_name="spool_assignments")
    op.drop_table("spool_assignments")

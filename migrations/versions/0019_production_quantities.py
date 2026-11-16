"""Production: print time, filament, electricity and failed pieces (issue #148).

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ALTER TABLE: old prints get empty values (nobody knows them), old productions 0
    # failed pieces. No table is rebuilt, so dependent rows are not touched.
    op.add_column("production_prints", sa.Column("print_seconds", sa.Integer(), nullable=True))
    op.add_column(
        "production_prints", sa.Column("filament_grams", sa.String(length=40), nullable=True)
    )
    op.add_column("production_prints", sa.Column("energy_kwh", sa.String(length=40), nullable=True))
    op.add_column(
        "production_prints", sa.Column("filament_type", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "production_prints", sa.Column("filament_colors", sa.String(length=200), nullable=True)
    )
    op.add_column(
        "productions",
        sa.Column("failed_pieces", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("productions", "failed_pieces")
    op.drop_column("production_prints", "filament_colors")
    op.drop_column("production_prints", "filament_type")
    op.drop_column("production_prints", "energy_kwh")
    op.drop_column("production_prints", "filament_grams")
    op.drop_column("production_prints", "print_seconds")

"""Price rules and filaments in calculations (issue #237).

Revision ID: 0032
Revises: 0031
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep working as before: "spool" is a single spool
    op.add_column(
        "app_settings",
        sa.Column("price_rule", sa.String(length=20), nullable=False, server_default="spool"),
    )
    op.add_column(
        "calculation_materials",
        sa.Column("price_rule", sa.String(length=20), nullable=False, server_default="spool"),
    )
    op.add_column("calculation_materials", sa.Column("filament_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("calculation_spools") as batch:
        batch.alter_column("spool_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # Positions with a filament rule have no spool; they cannot go back and are removed first
    op.execute("DELETE FROM calculation_spools WHERE spool_id IS NULL")
    with op.batch_alter_table("calculation_spools") as batch:
        batch.alter_column("spool_id", existing_type=sa.Integer(), nullable=False)
    # Plain drops: rebuilding "calculation_materials" would break its spool lines
    op.drop_column("calculation_materials", "filament_id")
    op.drop_column("calculation_materials", "price_rule")
    with op.batch_alter_table("app_settings") as batch:
        batch.drop_column("price_rule")

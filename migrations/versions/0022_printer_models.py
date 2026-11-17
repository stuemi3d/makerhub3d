"""Printer models with a power table per material; printers point to a model (issue #174).

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "printer_models",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ux_printer_models_name", "printer_models", ["name"], unique=True)
    op.create_table(
        "printer_model_powers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model_id", sa.Integer(), sa.ForeignKey("printer_models.id"), nullable=False),
        sa.Column("material", sa.String(50), nullable=False),
        sa.Column("kwh_per_hour", sa.String(40), nullable=False),
    )
    op.create_index(
        "ux_printer_model_powers_material",
        "printer_model_powers",
        ["model_id", "material"],
        unique=True,
    )
    # Existing printers stay without a model.
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("printer_model_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_devices_printer_model", "printer_models", ["printer_model_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_constraint("fk_devices_printer_model", type_="foreignkey")
        batch.drop_column("printer_model_id")
    op.drop_index("ux_printer_model_powers_material", table_name="printer_model_powers")
    op.drop_table("printer_model_powers")
    op.drop_index("ux_printer_models_name", table_name="printer_models")
    op.drop_table("printer_models")

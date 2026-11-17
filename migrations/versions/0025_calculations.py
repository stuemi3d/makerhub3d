"""Saved calculations with their materials and spools (issue #161).

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEXT = sa.String(length=40)


def upgrade() -> None:
    op.create_table(
        "calculations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("article_id", sa.Integer(), sa.ForeignKey("articles.id"), nullable=False),
        sa.Column("created_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False, server_default="hand"),
        sa.Column("is_main", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("printer_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL")),
        sa.Column("printer_name", sa.String(length=100), nullable=False),
        sa.Column("print_minutes", TEXT, nullable=False),
        sa.Column("pieces", sa.Integer(), nullable=False),
        sa.Column("setup_minutes", TEXT, nullable=False),
        sa.Column("rework_minutes", TEXT, nullable=False),
        sa.Column("scrap_percent", TEXT, nullable=False),
        sa.Column("margin_percent", TEXT, nullable=False),
        sa.Column("power_price", TEXT, nullable=False),
        sa.Column("machine_rate", TEXT, nullable=False),
        sa.Column("labour_rate", TEXT, nullable=False),
        sa.Column("rounding", sa.String(length=20), nullable=False),
        sa.Column("target_price", TEXT),
        sa.Column("material_per_plate", TEXT, nullable=False),
        sa.Column("power_per_plate", TEXT, nullable=False),
        sa.Column("machine_per_plate", TEXT, nullable=False),
        sa.Column("labour_cost", TEXT, nullable=False),
        sa.Column("cost_without_labour", TEXT, nullable=False),
        sa.Column("cost_with_labour", TEXT, nullable=False),
        sa.Column("suggested_price", TEXT, nullable=False),
        sa.Column("selling_price", TEXT, nullable=False),
        sa.Column("profit", TEXT, nullable=False),
        sa.Column("hourly_wage", TEXT),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id")),
        sa.CheckConstraint("kind IN ('hand', 'file')", name="ck_calculations_kind"),
    )
    op.create_index("ix_calculations_article_id", "calculations", ["article_id"])
    op.create_table(
        "calculation_materials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("calculation_id", sa.Integer(), sa.ForeignKey("calculations.id"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("material", sa.String(length=50), nullable=False),
        sa.Column("grams", TEXT, nullable=False),
        sa.Column("minutes", TEXT, nullable=False),
        sa.Column("kwh_per_hour", TEXT, nullable=False),
    )
    op.create_index(
        "ix_calculation_materials_calculation_id", "calculation_materials", ["calculation_id"]
    )
    op.create_table(
        "calculation_spools",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "material_id", sa.Integer(), sa.ForeignKey("calculation_materials.id"), nullable=False
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("spool_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("grams", TEXT, nullable=False),
        sa.Column("price_per_kg", TEXT, nullable=False),
    )
    op.create_index("ix_calculation_spools_material_id", "calculation_spools", ["material_id"])


def downgrade() -> None:
    op.drop_table("calculation_spools")
    op.drop_table("calculation_materials")
    op.drop_table("calculations")

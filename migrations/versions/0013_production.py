"""Production: prints, result articles and ignored prints (issue #118).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "productions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("booked_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(length=500), server_default="", nullable=False),
        sa.Column("total_cost", sa.String(length=40), nullable=False),
        sa.Column("cancelled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "production_prints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("production_id", sa.Integer(), nullable=False),
        sa.Column("archive_id", sa.Integer(), nullable=False),
        sa.Column("material_cost", sa.String(length=40), nullable=False),
        sa.Column("electricity_cost", sa.String(length=40), nullable=False),
        sa.Column("wear_cost", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.CheckConstraint("status IN ('success', 'failed')", name="ck_production_prints_status"),
        sa.ForeignKeyConstraint(["production_id"], ["productions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_production_prints_production_id", "production_prints", ["production_id"])
    op.create_index("ix_production_prints_archive_id", "production_prints", ["archive_id"])
    op.create_table(
        "production_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("production_id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("weight", sa.String(length=40), nullable=False),
        sa.Column("allocated_cost", sa.String(length=40), nullable=False),
        sa.Column("split", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("movement_id", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_production_results_quantity"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["movement_id"], ["stock_movements.id"]),
        sa.ForeignKeyConstraint(["production_id"], ["productions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("production_id", "article_id", name="uq_production_results_article"),
    )
    op.create_index("ix_production_results_production_id", "production_results", ["production_id"])
    op.create_index("ix_production_results_article_id", "production_results", ["article_id"])
    op.create_table(
        "ignored_prints",
        sa.Column("archive_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("ignored_on", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("archive_id"),
    )


def downgrade() -> None:
    op.drop_table("ignored_prints")
    op.drop_index("ix_production_results_article_id", table_name="production_results")
    op.drop_index("ix_production_results_production_id", table_name="production_results")
    op.drop_table("production_results")
    op.drop_index("ix_production_prints_archive_id", table_name="production_prints")
    op.drop_index("ix_production_prints_production_id", table_name="production_prints")
    op.drop_table("production_prints")
    op.drop_table("productions")

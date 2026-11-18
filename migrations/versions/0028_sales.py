"""Sales by hand (issue #201).

Revision ID: 0028
Revises: 0027
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("booked_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=200), server_default="", nullable=False),
        sa.Column("source", sa.String(length=20), server_default="hand", nullable=False),
        sa.Column("movement_id", sa.Integer(), nullable=False),
        sa.Column("cancelled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("quantity > 0", name="ck_sales_quantity"),
        sa.CheckConstraint("source IN ('hand', 'rechnungsfee', 'haben')", name="ck_sales_source"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["movement_id"], ["stock_movements.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sales_article_id", "sales", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_article_id", table_name="sales")
    op.drop_table("sales")

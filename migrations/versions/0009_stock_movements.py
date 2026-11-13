"""Stock movements (issue #11).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("booked_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=False),
        sa.Column("cancels_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "kind IN ('production', 'sale', 'correction', 'cancellation')",
            name="ck_stock_movements_kind",
        ),
        sa.CheckConstraint("quantity <> 0", name="ck_stock_movements_quantity"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"]),
        sa.ForeignKeyConstraint(["cancels_id"], ["stock_movements.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cancels_id", name="uq_stock_movements_cancels_id"),
    )
    op.create_index("ix_stock_movements_article_id", "stock_movements", ["article_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_movements_article_id", table_name="stock_movements")
    op.drop_table("stock_movements")

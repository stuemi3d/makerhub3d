"""Attributes and article templates (issue #44).

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attributes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("name_key", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name_key", name="uq_attributes_name_key"),
    )
    op.create_table(
        "attribute_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("attribute_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=50), nullable=False),
        sa.Column("value_key", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["attribute_id"], ["attributes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("attribute_id", "value_key", name="uq_attribute_values_key"),
    )
    op.create_table(
        "article_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("selling_price", sa.String(length=40), nullable=True),
        sa.Column("weight", sa.String(length=40), nullable=True),
        sa.Column("min_stock", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "template_attributes",
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("attribute_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["article_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attribute_id"], ["attributes.id"]),
        sa.PrimaryKeyConstraint("template_id", "attribute_id"),
    )


def downgrade() -> None:
    op.drop_table("template_attributes")
    op.drop_table("article_templates")
    op.drop_table("attribute_values")
    op.drop_table("attributes")

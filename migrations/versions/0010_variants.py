"""Variants of templates: template link, values and combination key (issue #49).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("articles") as batch:
        batch.add_column(sa.Column("template_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("combo_key", sa.String(length=500), nullable=True))
        batch.create_foreign_key(
            "fk_articles_template_id", "article_templates", ["template_id"], ["id"]
        )
        batch.create_unique_constraint("uq_articles_template_combo", ["template_id", "combo_key"])
    op.create_table(
        "article_attribute_values",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("attribute_id", sa.Integer(), nullable=False),
        sa.Column("value_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attribute_id"], ["attributes.id"]),
        sa.ForeignKeyConstraint(["value_id"], ["attribute_values.id"]),
        sa.PrimaryKeyConstraint("article_id", "attribute_id"),
    )


def downgrade() -> None:
    op.drop_table("article_attribute_values")
    with op.batch_alter_table("articles") as batch:
        batch.drop_constraint("uq_articles_template_combo", type_="unique")
        batch.drop_constraint("fk_articles_template_id", type_="foreignkey")
        batch.drop_column("combo_key")
        batch.drop_column("template_id")

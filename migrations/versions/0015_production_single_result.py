"""Production: exactly one result article, no weight and no split (issue #140).

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Productions with several result articles were possible before this change. They cannot
    # be turned into one-article productions automatically, so stop with a clear message
    # instead of a database error.
    several = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT COUNT(*) FROM (SELECT production_id FROM production_results"
                " GROUP BY production_id HAVING COUNT(*) > 1)"
            )
        )
        .scalar()
    )
    if several:
        raise RuntimeError(
            f"{several} production(s) with several result articles exist. "
            "Cancel and delete them (or delete their articles) before updating."
        )
    with op.batch_alter_table("production_results") as batch:
        batch.drop_constraint("uq_production_results_article", type_="unique")
        batch.drop_column("weight")
        batch.drop_column("split")
        batch.create_unique_constraint("uq_production_results_production", ["production_id"])


def downgrade() -> None:
    with op.batch_alter_table("production_results") as batch:
        batch.drop_constraint("uq_production_results_production", type_="unique")
        batch.add_column(
            sa.Column("weight", sa.String(length=40), server_default="1", nullable=False)
        )
        batch.add_column(
            sa.Column("split", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
        batch.create_unique_constraint(
            "uq_production_results_article", ["production_id", "article_id"]
        )

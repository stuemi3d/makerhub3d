"""Articles and templates: drop the weight column (issue #141).

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("articles", "article_templates"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("weight")


def downgrade() -> None:
    for table in ("articles", "article_templates"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("weight", sa.String(length=40), nullable=True))

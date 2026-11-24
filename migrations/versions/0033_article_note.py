"""A free note on every article (issue #266).

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing articles get an empty note
    op.add_column(
        "articles",
        sa.Column("note", sa.String(length=2000), nullable=False, server_default=""),
    )


def downgrade() -> None:
    # Plain drop: rebuilding "articles" would break the tables that point to it
    op.drop_column("articles", "note")

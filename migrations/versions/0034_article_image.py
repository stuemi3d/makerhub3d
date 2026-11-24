"""An optional image on every article (issue #267).

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing articles have no image
    op.add_column("articles", sa.Column("image_stored", sa.String(length=50), nullable=True))
    op.add_column("articles", sa.Column("image_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    # Plain drop: rebuilding "articles" would break the tables that point to it
    op.drop_column("articles", "image_name")
    op.drop_column("articles", "image_stored")

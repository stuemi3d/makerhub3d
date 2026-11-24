"""An optional .gcode.3mf file on every article (issue #268).

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing articles have no file
    op.add_column("articles", sa.Column("print_file_stored", sa.String(length=50), nullable=True))
    op.add_column("articles", sa.Column("print_file_name", sa.String(length=80), nullable=True))


def downgrade() -> None:
    # Plain drop: rebuilding "articles" would break the tables that point to it
    op.drop_column("articles", "print_file_name")
    op.drop_column("articles", "print_file_stored")

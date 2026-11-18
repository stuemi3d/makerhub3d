"""The .gcode.3mf file of a calculation (issue #182).

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing calculations have no file
    op.add_column("calculations", sa.Column("file_name", sa.String(length=80), nullable=True))
    op.add_column("calculations", sa.Column("file_stored", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("calculations") as batch:
        batch.drop_column("file_stored")
        batch.drop_column("file_name")

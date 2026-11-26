"""The printer of every print of a production (issue #259).

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Productions booked before this have no printer (the name is not known afterwards)
    op.add_column("production_prints", sa.Column("printer_name", sa.String(length=100)))


def downgrade() -> None:
    # Plain drop: rebuilding "production_prints" is not needed for a column without constraints
    op.drop_column("production_prints", "printer_name")

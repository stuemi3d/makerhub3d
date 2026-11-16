"""Production: labour minutes, hourly rate and labour cost as values (issue #134).

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("labor_minutes", "labor_rate", "labor_cost")


def upgrade() -> None:
    # Existing productions get 0 (no labour); their total cost stays as stored.
    # Plain ALTER TABLE: no table rebuild, so the prints and results that point at a
    # production are not touched.
    for name in COLUMNS:
        op.add_column(
            "productions",
            sa.Column(name, sa.String(length=40), server_default="0", nullable=False),
        )


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("productions", name)

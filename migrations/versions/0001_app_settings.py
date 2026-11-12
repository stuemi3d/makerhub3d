"""Settings table with the basic values and their defaults.

Revision ID: 0001
Revises:
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    settings = op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("electricity_price", sa.String(length=40), nullable=False),
        sa.Column("labour_rate", sa.String(length=40), nullable=False),
        sa.Column("scrap_rate", sa.String(length=40), nullable=False),
        sa.Column("markup", sa.String(length=40), nullable=False),
        sa.Column("rounding", sa.String(length=20), nullable=False),
        sa.Column("spool_category", sa.String(length=50), nullable=False),
        sa.Column("deviation_threshold", sa.String(length=40), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_app_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Defaults agreed in issue #7; all of them can be changed on the settings page.
    op.bulk_insert(
        settings,
        [
            {
                "id": 1,
                "electricity_price": "0.30",
                "labour_rate": "15.00",
                "scrap_rate": "5",
                "markup": "30",
                "rounding": "ends_90",
                "spool_category": "Geschäftlich",
                "deviation_threshold": "15",
            }
        ],
    )


def downgrade() -> None:
    op.drop_table("app_settings")

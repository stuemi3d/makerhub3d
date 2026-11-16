"""Devices: printers and dryers with the values for their hourly rates (issue #157).

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("bought_on", sa.Date(), nullable=False),
        sa.Column("price", sa.String(length=40), nullable=False),
        sa.Column("years", sa.String(length=40), nullable=False),
        sa.Column("hours_per_year", sa.String(length=40), nullable=True),
        sa.Column("maintenance_pct", sa.String(length=40), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("kind IN ('printer', 'dryer')", name="ck_devices_kind"),
        sa.CheckConstraint(
            "kind = 'dryer' OR hours_per_year IS NOT NULL", name="ck_devices_printer_hours"
        ),
    )


def downgrade() -> None:
    op.drop_table("devices")

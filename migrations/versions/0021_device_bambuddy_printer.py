"""Devices: the id of the Bambuddy printer a device was taken over from (issue #163).

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain ALTER TABLE: existing devices were made by hand and stay without an id.
    op.add_column("devices", sa.Column("bambuddy_printer_id", sa.Integer(), nullable=True))
    # Empty values do not collide, so any number of devices may stay without an id.
    op.create_index(
        "ux_devices_bambuddy_printer_id", "devices", ["bambuddy_printer_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ux_devices_bambuddy_printer_id", table_name="devices")
    op.drop_column("devices", "bambuddy_printer_id")

"""Start date for the prints shown from Bambuddy (issue #119).

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-15
"""

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("bambuddy_connection") as batch:
        batch.add_column(sa.Column("prints_from", sa.Date(), nullable=True))
    # An existing connection starts today, so no history appears by itself. An empty one gets
    # its date when it is first saved.
    op.execute(
        sa.text(
            "UPDATE bambuddy_connection SET prints_from = :today"
            " WHERE url <> '' OR api_key_encrypted IS NOT NULL"
        ).bindparams(today=date.today().isoformat())
    )


def downgrade() -> None:
    with op.batch_alter_table("bambuddy_connection") as batch:
        batch.drop_column("prints_from")

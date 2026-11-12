"""Users: active flag and case-insensitive unique names (issue #28).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("username_key", sa.String(length=50), nullable=True))
        batch.add_column(
            sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False)
        )
    # Filled in Python: SQLite's lower() only knows A-Z, casefold() also knows umlauts.
    connection = op.get_bind()
    users = connection.execute(sa.text("SELECT id, username FROM users")).fetchall()
    for user_id, username in users:
        connection.execute(
            sa.text("UPDATE users SET username_key = :key WHERE id = :id"),
            {"key": username.strip().casefold(), "id": user_id},
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username_key", existing_type=sa.String(length=50), nullable=False)
        batch.drop_constraint("uq_users_username", type_="unique")
        batch.create_unique_constraint("uq_users_username_key", ["username_key"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("uq_users_username_key", type_="unique")
        batch.create_unique_constraint("uq_users_username", ["username"])
        batch.drop_column("active")
        batch.drop_column("username_key")

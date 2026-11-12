"""Change log and login log (issue #29).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "change_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("subject", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_change_log_created_at", "change_log", ["created_at"])
    op.create_table(
        "login_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("event", sa.String(length=30), nullable=False),
        sa.CheckConstraint(
            "event IN ('success', 'failed', 'locked', 'logout', 'wrong_current_password')",
            name="ck_login_log_event",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_log_created_at", "login_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_login_log_created_at", table_name="login_log")
    op.drop_table("login_log")
    op.drop_index("ix_change_log_created_at", table_name="change_log")
    op.drop_table("change_log")

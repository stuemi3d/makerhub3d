"""Two-factor login: encrypted secret, recovery codes, pending logins (issue #30).

Also: change log entries without user (commands on the server) and two new login events.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EVENTS = "event IN ('success', 'failed', 'locked', 'logout', 'wrong_current_password')"
NEW_EVENTS = (
    "event IN ('success', 'failed', 'locked', 'logout', 'wrong_current_password',"
    " 'wrong_code', 'recovery_code_used')"
)


def upgrade() -> None:
    # Existing users start without two-factor login.
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("totp_secret", sa.Text(), nullable=True))
        batch.add_column(sa.Column("totp_pending_secret", sa.Text(), nullable=True))
        batch.add_column(sa.Column("totp_last_step", sa.Integer(), nullable=True))
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_codes_user_id", "recovery_codes", ["user_id"])
    op.create_table(
        "pending_logins",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("change_log") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("login_log") as batch:
        batch.drop_constraint("ck_login_log_event", type_="check")
        batch.create_check_constraint("ck_login_log_event", NEW_EVENTS)


def downgrade() -> None:
    # Entries of the new kinds cannot be kept under the old rules.
    op.execute("DELETE FROM login_log WHERE event IN ('wrong_code', 'recovery_code_used')")
    op.execute("DELETE FROM change_log WHERE user_id IS NULL")
    with op.batch_alter_table("login_log") as batch:
        batch.drop_constraint("ck_login_log_event", type_="check")
        batch.create_check_constraint("ck_login_log_event", OLD_EVENTS)
    with op.batch_alter_table("change_log") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("pending_logins")
    op.drop_index("ix_recovery_codes_user_id", table_name="recovery_codes")
    op.drop_table("recovery_codes")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("totp_last_step")
        batch.drop_column("totp_pending_secret")
        batch.drop_column("totp_secret")

"""Database tables. Every change here needs an Alembic migration (see migrations/)."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, DecimalText


class Rounding(StrEnum):
    """How the suggested selling price is rounded (always upwards, applied in M3)."""

    NONE = "none"
    TEN_CENTS = "ten_cents"
    FIFTY_CENTS = "fifty_cents"
    ENDS_90 = "ends_90"
    WHOLE_EURO = "whole_euro"


class AppSettings(Base):
    """The basic values of the settings page. There is exactly one row (id = 1)."""

    __tablename__ = "app_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_app_settings_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Euro per kWh
    electricity_price: Mapped[Decimal] = mapped_column(DecimalText)
    # Euro per hour of work
    labour_rate: Mapped[Decimal] = mapped_column(DecimalText)
    # Percent
    scrap_rate: Mapped[Decimal] = mapped_column(DecimalText)
    # Percent
    markup: Mapped[Decimal] = mapped_column(DecimalText)
    rounding: Mapped[Rounding] = mapped_column(String(20))
    # Bambuddy spool category that marks business spools
    spool_category: Mapped[str] = mapped_column(String(50))
    # Percent; warn when actual costs differ more than this from the calculation
    deviation_threshold: Mapped[Decimal] = mapped_column(DecimalText)


class Article(Base):
    """An article (issue #10). Never deleted, only deactivated.

    Stock is not stored here; it is always summed from stock movements (issue #11).
    """

    __tablename__ = "articles"
    __table_args__ = (UniqueConstraint("number_key", name="uq_articles_number_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Shown as entered; the key to the invoicing software, so freely chosen
    number: Mapped[str] = mapped_column(String(50))
    # Lower-case form (casefold) for a unique number regardless of case; casefold can make
    # text longer (ß becomes ss), hence the extra room
    number_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    # Euro per piece; empty = no target price set
    selling_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    # Share when production costs are split between articles (plan, section 4)
    weight: Mapped[Decimal | None] = mapped_column(DecimalText)
    # Pieces; empty = no minimum
    min_stock: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime)


class UserRole(StrEnum):
    """Fixed roles from the plan (section 8); their rights are in app/permissions.py."""

    ADMIN = "admin"
    EMPLOYEE = "employee"
    READ_ONLY = "read_only"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username_key", name="uq_users_username_key"),
        CheckConstraint("role IN ('admin', 'employee', 'read_only')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Shown as entered
    username: Mapped[str] = mapped_column(String(50))
    # Lower-case form (casefold) for unique names and login regardless of case
    username_key: Mapped[str] = mapped_column(String(50))
    # Argon2id hash; the password itself is never stored.
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(20))
    # Failed logins in a row; reset by a successful login or when a lock starts.
    failed_logins: Mapped[int] = mapped_column(Integer)
    # All times are UTC without time zone.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Users are never deleted, only deactivated (issue #28).
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    # Set for initial passwords given by an admin; the user must change it first (issue #32).
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Two-factor login (issue #30): the secret is encrypted with MAKERHUB_2FA_KEY.
    # Set = two-factor login is on.
    totp_secret: Mapped[str | None] = mapped_column(Text)
    # Secret shown during setup, until the user confirms it with a first code.
    totp_pending_secret: Mapped[str | None] = mapped_column(Text)
    # Last used 30-second step; codes of this or earlier steps are refused (no reuse).
    totp_last_step: Mapped[int | None] = mapped_column(Integer)


class LoginSession(Base):
    """One login. The browser holds a random token; only its SHA-256 fingerprint is stored."""

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Form token (CSRF) for this login
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)


class RecoveryCode(Base):
    """One-time code for a lost phone (issue #30). Only a SHA-256 fingerprint is stored."""

    __tablename__ = "recovery_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))


class PendingLogin(Base):
    """Right password, code not yet entered (issue #30). Like LoginSession, the browser
    holds a random token and only its fingerprint is stored."""

    __tablename__ = "pending_logins"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class ChangeLogEntry(Base):
    """Who changed what and when (plan, section 8). Never changed or deleted."""

    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    # Users are never deleted, so the reference always stays valid. Empty for changes made
    # by a command on the server (no user logged in).
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    # Translation key below "log.action.", e.g. "settings.changed"
    action: Mapped[str] = mapped_column(String(50))
    # The changed object as text, e.g. the user name; empty for the settings
    subject: Mapped[str] = mapped_column(String(100))
    # JSON list of {"field", "old", "new"} with stored (not formatted) values; never passwords
    details: Mapped[str] = mapped_column(Text)


class LoginLogEntry(Base):
    """Logins, failed logins, locks and logouts; deleted after 365 days. No IP addresses."""

    __tablename__ = "login_log"
    __table_args__ = (
        CheckConstraint(
            "event IN ('success', 'failed', 'locked', 'logout', 'wrong_current_password',"
            " 'wrong_code', 'recovery_code_used')",
            name="ck_login_log_event",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    # Empty for unknown user names; the typed name is not stored.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event: Mapped[str] = mapped_column(String(30))

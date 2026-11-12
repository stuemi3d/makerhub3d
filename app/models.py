"""Database tables. Every change here needs an Alembic migration (see migrations/)."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint
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


class UserRole(StrEnum):
    """Fixed roles from the plan (section 8). Until issue #28 only the first admin exists."""

    ADMIN = "admin"
    EMPLOYEE = "employee"
    READ_ONLY = "read_only"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_users_username"),
        CheckConstraint("role IN ('admin', 'employee', 'read_only')", name="ck_users_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50))
    # Argon2id hash; the password itself is never stored.
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(String(20))
    # Failed logins in a row; reset by a successful login or when a lock starts.
    failed_logins: Mapped[int] = mapped_column(Integer)
    # All times are UTC without time zone.
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class LoginSession(Base):
    """One login. The browser holds a random token; only its SHA-256 fingerprint is stored."""

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # Form token (CSRF) for this login
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)

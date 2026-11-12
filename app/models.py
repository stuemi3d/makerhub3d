"""Database tables. Every change here needs an Alembic migration (see migrations/)."""

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, String
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

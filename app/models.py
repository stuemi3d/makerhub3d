"""Database tables. Every change here needs an Alembic migration (see migrations/)."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # Calculation: offer only the spools of that category (issue #186); off = all spools
    spool_business_only: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # Percent; warn when actual costs differ more than this from the calculation
    deviation_threshold: Mapped[Decimal] = mapped_column(DecimalText)


class BambuddyConnection(Base):
    """Address and key of Bambuddy (issue #117). There is exactly one row (id = 1)."""

    __tablename__ = "bambuddy_connection"
    __table_args__ = (CheckConstraint("id = 1", name="ck_bambuddy_connection_single_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Empty = not set up
    url: Mapped[str] = mapped_column(String(200), default="")
    # The read-only API key, encrypted (never stored or logged in plain text)
    api_key_encrypted: Mapped[str | None] = mapped_column(Text, default=None)
    # Only prints from this day on are shown (issue #119); empty = not set up yet
    prints_from: Mapped[date | None] = mapped_column(Date, default=None)


class Article(Base):
    """An article (issue #10). Never deleted, only deactivated.

    Stock is not stored here; it is always summed from stock movements (issue #11).
    """

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("number_key", name="uq_articles_number_key"),
        UniqueConstraint("template_id", "combo_key", name="uq_articles_template_combo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Shown as entered; the key to the invoicing software, so freely chosen
    number: Mapped[str] = mapped_column(String(50))
    # Lower-case form (casefold) for a unique number regardless of case; casefold can make
    # text longer (ß becomes ss), hence the extra room
    number_key: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    # Euro per piece; empty = no target price set
    selling_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    # Pieces; empty = no minimum
    min_stock: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Set for a variant (issue #49): the template it belongs to. Price and minimum
    # stock that are empty here are inherited from the template.
    template_id: Mapped[int | None] = mapped_column(ForeignKey("article_templates.id"))
    # The attribute values of a variant as text ("attribute id:value id", sorted), so that the
    # database itself refuses two variants of a template with the same combination.
    combo_key: Mapped[str | None] = mapped_column(String(500))
    template: Mapped["ArticleTemplate | None"] = relationship(lazy="joined")
    attribute_values: Mapped[list["ArticleAttributeValue"]] = relationship(
        cascade="all, delete-orphan", order_by="ArticleAttributeValue.attribute_id"
    )


class StockMovementKind(StrEnum):
    """Why the stock changed. Production and sale come with M2 and M5."""

    PRODUCTION = "production"
    SALE = "sale"
    CORRECTION = "correction"
    CANCELLATION = "cancellation"


class StockMovement(Base):
    """One change of an article's stock (issue #11). Never changed or deleted, only cancelled
    by a counter booking (kind cancellation, pointing to the original with cancels_id)."""

    __tablename__ = "stock_movements"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('production', 'sale', 'correction', 'cancellation')",
            name="ck_stock_movements_kind",
        ),
        CheckConstraint("quantity <> 0", name="ck_stock_movements_quantity"),
        # A booking can be cancelled only once, even if two cancels arrive at the same moment.
        UniqueConstraint("cancels_id", name="uq_stock_movements_cancels_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    # The day the movement is about (may differ from the day it was entered)
    booked_on: Mapped[date] = mapped_column(Date)
    # UTC without time zone
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Pieces, plus for stock in, minus for stock out; never 0
    quantity: Mapped[int] = mapped_column(Integer)
    kind: Mapped[StockMovementKind] = mapped_column(String(20))
    # Free text for manual bookings; empty for cancellations
    reason: Mapped[str] = mapped_column(String(200))
    # Set on a cancellation: the booking it reverses
    cancels_id: Mapped[int | None] = mapped_column(ForeignKey("stock_movements.id"))
    # Empty for movements made by the system
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class PrintStatus(StrEnum):
    """How a print of a production ended."""

    SUCCESS = "success"
    FAILED = "failed"


class Production(Base):
    """A production (issues #118, #140): any number of prints and exactly one result article.

    Never deleted, only cancelled (the one exception is deleting an article for good). The
    costs are stored as values when booking.
    """

    __tablename__ = "productions"

    id: Mapped[int] = mapped_column(primary_key=True)
    booked_on: Mapped[date] = mapped_column(Date)
    # UTC without time zone
    created_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(String(500), server_default="")
    # Euro; the sum of all print costs plus the labour cost
    total_cost: Mapped[Decimal] = mapped_column(DecimalText)
    # Labour (issue #134), stored as values: minutes, the hourly rate used (Euro per hour) and
    # the labour cost (Euro, already part of total_cost)
    labor_minutes: Mapped[Decimal] = mapped_column(DecimalText, server_default="0")
    labor_rate: Mapped[Decimal] = mapped_column(DecimalText, server_default="0")
    labor_cost: Mapped[Decimal] = mapped_column(DecimalText, server_default="0")
    # Pieces that failed (issue #148); information only, changes neither stock nor cost
    failed_pieces: Mapped[int] = mapped_column(Integer, server_default="0")
    cancelled: Mapped[bool] = mapped_column(Boolean, server_default=false())
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    prints: Mapped[list["ProductionPrint"]] = relationship(
        cascade="all, delete-orphan", order_by="ProductionPrint.id"
    )
    results: Mapped[list["ProductionResult"]] = relationship(
        cascade="all, delete-orphan", order_by="ProductionResult.id"
    )


class ProductionPrint(Base):
    """One print (a Bambuddy archive entry) of a production, with its costs as values."""

    __tablename__ = "production_prints"
    __table_args__ = (
        CheckConstraint("status IN ('success', 'failed')", name="ck_production_prints_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("productions.id"), index=True)
    # Not unique: the prints of a cancelled production are free again
    archive_id: Mapped[int] = mapped_column(Integer, index=True)
    # Euro
    material_cost: Mapped[Decimal] = mapped_column(DecimalText)
    electricity_cost: Mapped[Decimal] = mapped_column(DecimalText)
    # Only filled in productions booked before issue #135; new ones store 0
    wear_cost: Mapped[Decimal] = mapped_column(DecimalText)
    machine_cost: Mapped[Decimal] = mapped_column(DecimalText, server_default="0")
    status: Mapped[PrintStatus] = mapped_column(String(10))
    # Amounts of the print as values when booking (issue #148); empty = Bambuddy did not know
    print_seconds: Mapped[int | None] = mapped_column(Integer)
    filament_grams: Mapped[Decimal | None] = mapped_column(DecimalText)
    energy_kwh: Mapped[Decimal | None] = mapped_column(DecimalText)
    # Material text and the valid colour codes, comma separated
    filament_type: Mapped[str | None] = mapped_column(String(50))
    filament_colors: Mapped[str | None] = mapped_column(String(200))


class ProductionResult(Base):
    """The result article of a production: good pieces and all costs of the production."""

    __tablename__ = "production_results"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_production_results_quantity"),
        # One result article per production (issue #140)
        UniqueConstraint("production_id", name="uq_production_results_production"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    production_id: Mapped[int] = mapped_column(ForeignKey("productions.id"), index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    # Euro; all costs of the production
    allocated_cost: Mapped[Decimal] = mapped_column(DecimalText)
    # The stock movement this row booked
    movement_id: Mapped[int] = mapped_column(ForeignKey("stock_movements.id"))


class Calculation(Base):
    """A saved calculation of an article or variant (issue #161), all values as they were
    calculated: later changes of prices, rates or spools never change it."""

    __tablename__ = "calculations"
    __table_args__ = (CheckConstraint("kind IN ('hand', 'file')", name="ck_calculations_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    created_on: Mapped[date] = mapped_column(Date)
    # UTC without time zone
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # By hand or from a file (the file way comes with issue #181)
    kind: Mapped[str] = mapped_column(String(10), server_default="hand")
    # The calculation that counts as the target value of the article (at most one per article)
    is_main: Mapped[bool] = mapped_column(Boolean, server_default=false())
    # The printer; its name is kept as text because a device can be deleted later
    printer_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    printer_name: Mapped[str] = mapped_column(String(100))
    print_minutes: Mapped[Decimal] = mapped_column(DecimalText)
    pieces: Mapped[int] = mapped_column(Integer)
    setup_minutes: Mapped[Decimal] = mapped_column(DecimalText)
    rework_minutes: Mapped[Decimal] = mapped_column(DecimalText)
    scrap_percent: Mapped[Decimal] = mapped_column(DecimalText)
    margin_percent: Mapped[Decimal] = mapped_column(DecimalText)
    # The rates used (Euro per kWh, Euro per print hour, Euro per hour of work) and the rounding
    power_price: Mapped[Decimal] = mapped_column(DecimalText)
    machine_rate: Mapped[Decimal] = mapped_column(DecimalText)
    labour_rate: Mapped[Decimal] = mapped_column(DecimalText)
    rounding: Mapped[str] = mapped_column(String(20))
    # The target price of the article at that time, if there was one
    target_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    # The result (Euro), per plate or per piece as named
    material_per_plate: Mapped[Decimal] = mapped_column(DecimalText)
    power_per_plate: Mapped[Decimal] = mapped_column(DecimalText)
    machine_per_plate: Mapped[Decimal] = mapped_column(DecimalText)
    labour_cost: Mapped[Decimal] = mapped_column(DecimalText)
    cost_without_labour: Mapped[Decimal] = mapped_column(DecimalText)
    cost_with_labour: Mapped[Decimal] = mapped_column(DecimalText)
    suggested_price: Mapped[Decimal] = mapped_column(DecimalText)
    selling_price: Mapped[Decimal] = mapped_column(DecimalText)
    profit: Mapped[Decimal] = mapped_column(DecimalText)
    # Empty when there is no labour time
    hourly_wage: Mapped[Decimal | None] = mapped_column(DecimalText)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    materials: Mapped[list["CalculationMaterial"]] = relationship(
        cascade="all, delete-orphan", order_by="CalculationMaterial.position"
    )


class CalculationMaterial(Base):
    """One material (position) of a calculation with its time share and power use."""

    __tablename__ = "calculation_materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    calculation_id: Mapped[int] = mapped_column(ForeignKey("calculations.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    material: Mapped[str] = mapped_column(String(50))
    grams: Mapped[Decimal] = mapped_column(DecimalText)
    # Time share of the material in minutes and its kWh per hour as used
    minutes: Mapped[Decimal] = mapped_column(DecimalText)
    kwh_per_hour: Mapped[Decimal] = mapped_column(DecimalText)
    spools: Mapped[list["CalculationSpool"]] = relationship(
        cascade="all, delete-orphan", order_by="CalculationSpool.position"
    )


class CalculationSpool(Base):
    """The part of a position that comes from one Bambuddy spool, with its price as a value."""

    __tablename__ = "calculation_spools"

    id: Mapped[int] = mapped_column(primary_key=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("calculation_materials.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    # The Bambuddy spool id (the spool itself is only read, never stored) and a text for it
    spool_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200))
    grams: Mapped[Decimal] = mapped_column(DecimalText)
    price_per_kg: Mapped[Decimal] = mapped_column(DecimalText)


class IgnoredPrint(Base):
    """A Bambuddy print that does not matter and is hidden from the list of open prints."""

    __tablename__ = "ignored_prints"

    archive_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    ignored_on: Mapped[date] = mapped_column(Date)


class PrinterModel(Base):
    """A printer model (e.g. "P2S") with its power table (issue #174).

    Created only from the models Bambuddy reports. Printers point to it, so all printers of one
    model share one table. It can only be deleted while no printer uses it.
    """

    __tablename__ = "printer_models"
    __table_args__ = (Index("ux_printer_models_name", "name", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime)


class PrinterModelPower(Base):
    """Electricity use of a printer model for one material, in kWh per print hour."""

    __tablename__ = "printer_model_powers"
    __table_args__ = (
        Index("ux_printer_model_powers_material", "model_id", "material", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_id: Mapped[int] = mapped_column(ForeignKey("printer_models.id"))
    material: Mapped[str] = mapped_column(String(50))
    kwh_per_hour: Mapped[Decimal] = mapped_column(DecimalText)
    # Place in the table, as the person ordered the rows (issue #177)
    position: Mapped[int] = mapped_column(Integer, server_default="0")


class Device(Base):
    """A printer or a dryer with the values for its hourly rate (issue #157).

    Residual value and rates are calculated on the pages (app/devices.py), never stored.
    Devices are never deleted, only switched off.
    """

    __tablename__ = "devices"
    __table_args__ = (
        Index("ux_devices_bambuddy_printer_id", "bambuddy_printer_id", unique=True),
        CheckConstraint("kind IN ('printer', 'dryer')", name="ck_devices_kind"),
        CheckConstraint(
            "kind = 'dryer' OR hours_per_year IS NOT NULL", name="ck_devices_printer_hours"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(100))
    bought_on: Mapped[date] = mapped_column(Date)
    # Euro
    price: Mapped[Decimal] = mapped_column(DecimalText)
    years: Mapped[Decimal] = mapped_column(DecimalText)
    # Print hours per year; only printers have them
    hours_per_year: Mapped[Decimal | None] = mapped_column(DecimalText, nullable=True)
    # Percent
    maintenance_pct: Mapped[Decimal] = mapped_column(DecimalText)
    # Id of the Bambuddy printer this device was taken over from (empty: made by hand)
    bambuddy_printer_id: Mapped[int | None] = mapped_column(nullable=True)
    # Printer model with the power table (issue #174); only printers have one, it is optional
    printer_model_id: Mapped[int | None] = mapped_column(
        ForeignKey("printer_models.id"), nullable=True
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime)


class UserRole(StrEnum):
    """Fixed roles from the plan (section 8); their rights are in app/permissions.py."""

    ADMIN = "admin"
    EMPLOYEE = "employee"
    READ_ONLY = "read_only"


class Attribute(Base):
    """A feature such as colour or size (issue #44), shared by several templates."""

    __tablename__ = "attributes"
    __table_args__ = (UniqueConstraint("name_key", name="uq_attributes_name_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    # Lower-case form (casefold), for a unique name regardless of case
    name_key: Mapped[str] = mapped_column(String(100))
    values_rel: Mapped[list["AttributeValue"]] = relationship(
        cascade="all, delete-orphan", order_by="AttributeValue.id"
    )


class AttributeValue(Base):
    """One value of an attribute, e.g. "red"."""

    __tablename__ = "attribute_values"
    __table_args__ = (
        UniqueConstraint("attribute_id", "value_key", name="uq_attribute_values_key"),
        UniqueConstraint(
            "attribute_id", "abbreviation_key", name="uq_attribute_values_abbreviation"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    attribute_id: Mapped[int] = mapped_column(ForeignKey("attributes.id", ondelete="CASCADE"))
    value: Mapped[str] = mapped_column(String(50))
    value_key: Mapped[str] = mapped_column(String(100))
    # Short form used in variant numbers (issue #62), e.g. "bl" for blue; unique per attribute
    abbreviation: Mapped[str] = mapped_column(String(10))
    abbreviation_key: Mapped[str] = mapped_column(String(20))


class ArticleTemplate(Base):
    """A template for variants (issue #44). Has no stock and no sales, so it is not an article.

    Deleted only while nothing hangs on it (plan, section 4); otherwise deactivated.
    """

    __tablename__ = "article_templates"
    __table_args__ = (UniqueConstraint("number_key", name="uq_article_templates_number_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Template number (issue #62), the start of the variant numbers; empty only for templates
    # that existed before the number was introduced
    number: Mapped[str | None] = mapped_column(String(50))
    number_key: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, server_default="")
    # Defaults that variants inherit; empty = not set (same meaning as for articles)
    selling_price: Mapped[Decimal | None] = mapped_column(DecimalText)
    min_stock: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime)


class TemplateAttribute(Base):
    """Which attributes a template uses, in the order they are shown."""

    __tablename__ = "template_attributes"

    template_id: Mapped[int] = mapped_column(
        ForeignKey("article_templates.id", ondelete="CASCADE"), primary_key=True
    )
    # No cascade here: an attribute that a template uses cannot be deleted.
    attribute_id: Mapped[int] = mapped_column(ForeignKey("attributes.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer)


class ArticleAttributeValue(Base):
    """The value a variant has for one attribute of its template (issue #49)."""

    __tablename__ = "article_attribute_values"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    attribute_id: Mapped[int] = mapped_column(ForeignKey("attributes.id"), primary_key=True)
    value_id: Mapped[int] = mapped_column(ForeignKey("attribute_values.id"))
    attribute: Mapped[Attribute] = relationship(lazy="joined")
    value: Mapped[AttributeValue] = relationship(lazy="joined")


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
    # Two-factor login (issue #30): the secret is encrypted with MAKERHUB_SECRET_KEY.
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

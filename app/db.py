"""Database access: SQLite engine, exact decimal column type and Alembic migrations."""

from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, String, create_engine, event
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.config import DATABASE_FILE

__all__ = ["DATABASE_FILE", "Base", "DecimalText", "alembic_config", "make_engine"]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


class Base(DeclarativeBase):
    pass


class DecimalText(TypeDecorator[Decimal]):
    """Stores a Decimal as text.

    SQLite has no exact decimal type: its NUMERIC columns turn values into floating point
    numbers. Text keeps amounts exact, e.g. 0.3215 stays 0.3215.
    """

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: Decimal | None, dialect: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("DecimalText only accepts finite Decimal values")
        return str(value)

    def process_result_value(self, value: str | None, dialect: object) -> Decimal | None:
        return None if value is None else Decimal(value)


def make_engine(database_path: Path) -> Engine:
    # Creating an engine does not open the file yet; that happens on the first connection.
    engine = create_engine(f"sqlite:///{database_path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.attributes["connection"] = connection
    return config


class MigrationError(RuntimeError):
    """Raised when a migration leaves references between tables broken."""


def run_migrations(engine: Engine) -> None:
    """Brings the database to the newest version. Safe to call on every start.

    Foreign keys are switched off meanwhile: SQLite changes a table by copying and dropping
    it, and the drop would otherwise delete dependent rows (ON DELETE CASCADE). Afterwards
    all references are checked.
    """
    with engine.connect() as connection:
        # Only effective outside a transaction, hence the commit before the migration.
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            with connection.begin():
                command.upgrade(alembic_config(connection), "head")
                broken = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
                if broken:
                    raise MigrationError("Migration left broken references between tables.")
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()

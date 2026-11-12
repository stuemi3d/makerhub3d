"""Alembic environment.

The app passes its open connection in config.attributes["connection"]. When Alembic is
called from the command line instead, the database from MAKERHUB_DATA_DIR is used.
"""

from alembic import context

import app.models  # noqa: F401  (registers the tables on Base.metadata)
from app.config import Settings
from app.db import Base, make_engine

target_metadata = Base.metadata


def run_with(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite cannot change most columns in place; batch mode rebuilds the table.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        run_with(connection)
        return
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_path)
    with engine.begin() as new_connection:
        run_with(new_connection)


if context.is_offline_mode():
    raise SystemExit("Offline migrations (SQL scripts) are not supported.")
run_migrations_online()

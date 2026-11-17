"""The setting "only business spools" (issue #186, cases 11-13). All data is invented."""

import json
import re
import sqlite3
from pathlib import Path

from alembic import command
from fastapi.testclient import TestClient

from app.db import alembic_config, make_engine, run_migrations
from app.models import ChangeLogEntry
from tests.forms import valid_form
from tests.test_settings_page import stored
from tests.test_stock import db

ROOT = Path(__file__).resolve().parent.parent
DE = json.loads((ROOT / "app" / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))


def checkbox(html: str) -> str | None:
    match = re.search(r'<input\b[^>]*name="spool_business_only"[^>]*>', html)
    return match.group(0) if match else None


def test_11_the_setting_is_off_by_default_and_shown_as_a_checkbox(client: TestClient) -> None:
    assert stored().spool_business_only is False
    box = checkbox(client.get("/settings").text)
    assert box is not None and 'type="checkbox"' in box and "checked" not in box
    assert DE["settings.field.spool_business_only"] in client.get("/settings").text


def test_11_the_setting_is_saved_and_shown_again(client: TestClient) -> None:
    response = client.post("/settings", data=valid_form(spool_business_only="1"))
    assert response.status_code == 200 or response.status_code == 303
    assert stored().spool_business_only is True
    assert "checked" in checkbox(client.get("/settings").text)

    client.post("/settings", data=valid_form())
    assert stored().spool_business_only is False
    assert "checked" not in checkbox(client.get("/settings").text)


def test_11_a_change_is_written_to_the_change_log(client: TestClient) -> None:
    client.post("/settings", data=valid_form(spool_business_only="1"))
    with db() as session:
        entries = session.query(ChangeLogEntry).filter_by(action="settings.changed").all()
    assert any("spool_business_only" in entry.details for entry in entries)


def test_11_an_invalid_form_keeps_the_checkbox_as_sent(client: TestClient) -> None:
    response = client.post("/settings", data=valid_form(spool_business_only="1", labour_rate="abc"))
    assert response.status_code == 422
    assert "checked" in checkbox(response.text)
    assert stored().spool_business_only is False


def test_11_the_texts_exist_in_both_languages() -> None:
    for key in ("settings.field.spool_business_only", "settings.hint.spool_business_only"):
        assert DE[key] and EN[key]


def test_13_migration_adds_the_setting_off_and_goes_back(tmp_path: Path) -> None:
    database = tmp_path / "setting.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0025")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE app_settings SET spool_category = 'Firma'")

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute("SELECT spool_category, spool_business_only FROM app_settings")
        assert row.fetchone() == ("Firma", 0)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0025")
    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(app_settings)")}
        assert "spool_business_only" not in columns
        assert connection.execute("SELECT spool_category FROM app_settings").fetchone() == (
            "Firma",
        )
    run_migrations(engine)

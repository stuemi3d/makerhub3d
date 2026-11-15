"""Issue #141: the weight factor is gone from articles and templates (migration 0016)."""

import json
import sqlite3
from pathlib import Path

from alembic import command

from app.db import alembic_config, make_engine, run_migrations
from tests.auth import AppClient
from tests.test_variants import Setup, make_variant
from tests.test_variants import setup as setup


def columns(database: Path, table: str) -> list[str]:
    with sqlite3.connect(database) as connection:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def test_migration_drops_the_column_keeps_data_and_can_be_undone(tmp_path: Path) -> None:
    database = tmp_path / "weight.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0015")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO article_templates (id, number, number_key, name, description,
              selling_price, weight, min_stock, active, created_at)
              VALUES (1, 'V-1', 'v-1', 'Vorlage', '', '9.90', '2', 3, 1, '2026-01-01 00:00:00');
            INSERT INTO articles (id, number, number_key, name, selling_price, weight, min_stock,
              active, created_at, template_id)
              VALUES (1, 'V-1-RT', 'v-1-rt', 'Variante', '12.50', '3', 4, 1,
              '2026-01-01 00:00:00', 1);
            """
        )

    run_migrations(engine)

    assert "weight" not in columns(database, "articles")
    assert "weight" not in columns(database, "article_templates")
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT number, name, selling_price, min_stock, active, template_id FROM articles"
        ).fetchall() == [("V-1-RT", "Variante", "12.50", 4, 1, 1)]
        assert connection.execute(
            "SELECT number, name, selling_price, min_stock, active FROM article_templates"
        ).fetchall() == [("V-1", "Vorlage", "9.90", 3, 1)]

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0015")
    assert "weight" in columns(database, "articles")
    assert "weight" in columns(database, "article_templates")
    run_migrations(engine)
    assert "weight" not in columns(database, "articles")


def test_migration_runs_on_an_empty_database_and_back(tmp_path: Path) -> None:
    engine = make_engine(tmp_path / "empty.db")
    run_migrations(engine)
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0015")
    run_migrations(engine)
    assert "weight" not in columns(tmp_path / "empty.db", "article_templates")


def test_no_weight_field_in_article_and_template_forms(client: AppClient) -> None:
    for url in ("/articles/new", "/templates/new"):
        html = client.get(url).text
        assert 'name="weight"' not in html and 'id="weight"' not in html
        assert "Gewichtung" not in html


def test_no_weight_in_variant_forms_and_inheritance_hint(
    client: AppClient,
    setup: Setup,
) -> None:
    html = client.get(f"/templates/{setup.template_id}/variants/new").text
    assert 'id="weight"' not in html and "Gewichtung" not in html
    assert "Preis und Mindestbestand" in html
    make_variant(client, setup)
    detail = client.get(f"/templates/{setup.template_id}").text
    assert "Gewichtung" not in detail


def test_texts_no_longer_mention_the_weight() -> None:
    folder = Path(__file__).parent.parent / "app" / "locales"
    for language in ("de", "en"):
        texts = json.loads((folder / f"{language}.json").read_text(encoding="utf-8"))
        for key, text in texts.items():
            assert "Gewichtung" not in text and "weight" not in text.lower(), key

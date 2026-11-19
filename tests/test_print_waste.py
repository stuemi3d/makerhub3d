"""Waste per print (issue #216, test cases 1-8). All data is invented. The test case numbers
refer to the comment on the issue. Case 8 (the analysis page on desktop and phone) is checked
in tests/browser/test_analysis_total.py.
"""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app import analysis_total, production
from app.db import alembic_config, make_engine, run_migrations
from app.models import Article, Production, ProductionPrint, ProductionResult
from app.production import PrintInput, ResultInput
from tests.auth import AppClient, signed_in_client
from tests.test_analysis_total import DE, TODAY, YEAR, figure, page, totals
from tests.test_bambuddy_settings import TEST_SECRET_KEY
from tests.test_stock import add_article, db

D = Decimal
DAY = date(2026, 6, 1)
_archive = iter(range(50_000, 60_000))


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


@pytest.fixture
def client() -> AppClient:
    with signed_in_client() as test_client:
        yield test_client


def print_of(cost: str, share: str = "0", failed: bool = False) -> PrintInput:
    return PrintInput(next(_archive), D(cost), D(0), D(0), failed=failed, waste_share=D(share))


def an_article() -> int:
    """The one article of the test (made on the first call)."""
    with db() as session:
        found = session.scalars(select(Article.id)).first()
    return found or add_article()


def book(
    prints: list[PrintInput], quantity: int = 10, day: date = DAY, article_id: int | None = None
) -> int:
    article_id = article_id or an_article()
    with db() as session, session.begin():
        production_id, errors = production.book(
            session, None, day, "", prints, ResultInput(article_id, quantity)
        )
    assert not errors, errors
    return production_id


def stored_prints(production_id: int) -> list[ProductionPrint]:
    with db() as session:
        found = list(
            session.scalars(
                select(ProductionPrint)
                .where(ProductionPrint.production_id == production_id)
                .order_by(ProductionPrint.id)
            )
        )
        session.expunge_all()
        return found


def test_1_waste_amount_is_share_times_print_cost() -> None:
    # 50 pieces per plate, 10 good, 40 waste: 80 % of 10 EUR
    production_id = book([print_of("10.00", "0.8")], quantity=10)
    (stored,) = stored_prints(production_id)
    assert stored.waste_share == D("80") and stored.waste_cost == D("8.00")
    assert totals().failed_cost == D("8.00")


def test_1b_the_amount_covers_material_electricity_and_machine() -> None:
    item = PrintInput(next(_archive), D("6"), D("2"), D("2"), waste_share=D("0.25"))
    production_id = book([item])
    (stored,) = stored_prints(production_id)
    assert stored.waste_cost == D("2.50")  # 25 % of 10 EUR


def test_2_a_whole_failed_print_counts_in_full_besides_the_waste() -> None:
    book([print_of("10.00", "0.8"), print_of("6.00", failed=True)])
    result = totals()
    assert result.failed_cost == D("14.00")  # 8 + 6
    with db() as session:
        failed = session.scalars(
            select(ProductionPrint).where(ProductionPrint.material_cost == D("6.00"))
        ).one()
    assert failed.waste_share == D("100") and failed.waste_cost == D("6.00")


def test_3_cancelled_productions_and_other_periods_do_not_count() -> None:
    book([print_of("10.00", "0.5")])  # in the period: 5 EUR
    book([print_of("10.00", "0.5")], day=date(2025, 6, 1))  # a year before
    cancelled = book([print_of("10.00", "0.5")])
    with db() as session, session.begin():
        assert production.cancel(session, None, cancelled) is None

    assert totals().failed_cost == D("5.00")
    assert totals((date(2025, 1, 1), date(2025, 12, 31))).failed_cost == D("5.00")


def test_4_the_share_of_the_production_cost_of_the_period() -> None:
    # 14 EUR of failed cost from 56 EUR production cost = 25 %
    book([print_of("50.00", "0.16"), print_of("6.00", failed=True)])
    result = totals()
    assert result.failed_cost == D("14.00")
    assert result.failed_share == D("25")
    texts = analysis_total.texts(result, "de")
    assert texts["failed_share"] == "25 %"


def test_4b_labour_is_part_of_the_production_cost_but_not_of_the_failed_cost() -> None:
    article_id = an_article()
    with db() as session, session.begin():
        production.book(
            session,
            None,
            DAY,
            "",
            [print_of("50.00", "0.2")],  # 10 EUR waste
            ResultInput(article_id, 5),
            production.LaborInput(D("60"), D("50")),  # 50 EUR labour
        )
    result = totals()
    assert result.failed_cost == D("10.00")
    assert result.failed_share == D("10")  # of 50 + 50 = 100 EUR


def test_4c_without_a_production_there_is_no_share() -> None:
    result = totals()
    assert result.failed_cost == D("0") and result.failed_share is None
    assert analysis_total.texts(result, "de")["failed_share"] == "\u2013"


def test_5_the_cost_per_piece_stays_as_before() -> None:
    production_id = book([print_of("10.00", "0.8")], quantity=10)
    with db() as session:
        item = session.get(Production, production_id)
        result = session.scalars(
            select(ProductionResult).where(ProductionResult.production_id == production_id)
        ).one()
        assert item.total_cost == D("10.00")
        assert result.allocated_cost / result.quantity == D("1")  # all cost on the good pieces


def test_6_the_migration_keeps_existing_productions_at_zero_percent(tmp_path: Path) -> None:
    database = tmp_path / "waste.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0028")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO productions (id, booked_on, created_at, total_cost)
              VALUES (1, '2026-06-01', '2026-06-01 10:00:00', '9.00');
            INSERT INTO production_prints (id, production_id, archive_id, material_cost,
              electricity_cost, wear_cost, machine_cost, status)
              VALUES (1, 1, 7, '8.00', '1.00', '0', '0', 'success');
            """
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT waste_share, waste_cost, material_cost FROM production_prints"
        ).fetchone()
    assert row == ("0", "0", "8.00")

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0028")
    with sqlite3.connect(database) as connection:
        names = [r[1] for r in connection.execute("PRAGMA table_info(production_prints)")]
        assert "waste_share" not in names and "waste_cost" not in names
        assert connection.execute("SELECT material_cost FROM production_prints").fetchone() == (
            "8.00",
        )
    run_migrations(engine)  # and up again


def test_7_money_and_percent_are_decimal_and_rounded_to_the_cent() -> None:
    # one third of 10.00 EUR: 3.33 EUR, and the share is kept with two decimals
    production_id = book([print_of("10.00", "0.3333333333")])
    (stored,) = stored_prints(production_id)
    assert isinstance(stored.waste_cost, Decimal) and isinstance(stored.waste_share, Decimal)
    assert stored.waste_cost == D("3.33") and stored.waste_share == D("33.33")
    # round half up at the cent: 0.5 % of 2.50 EUR = 0.0125 -> 0.01; 10 % of 0.05 = 0.005 -> 0.01
    (second,) = stored_prints(book([print_of("0.05", "0.1")]))
    assert second.waste_cost == D("0.01")


def test_a_share_outside_0_to_1_is_refused_and_nothing_is_stored() -> None:
    article_id = an_article()
    for bad in ("-0.1", "1.5", "NaN"):
        with db() as session, session.begin():
            production_id, errors = production.book(
                session, None, DAY, "", [print_of("1.00", bad)], ResultInput(article_id, 1)
            )
        assert production_id is None and errors == {"prints": "production.error.waste_share"}
    with db() as session:
        assert session.scalars(select(Production)).all() == []


def test_the_analysis_page_shows_the_waste_and_its_share(client: AppClient) -> None:
    book([print_of("50.00", "0.16"), print_of("6.00", failed=True)])
    html = page(client, "?period=year")
    assert figure(html, "failed_cost") == "14,00 €"
    assert DE["analysis_total.failed_share"] in html and "25 %" in html
    assert DE["analysis_total.failed_note"] in html
    assert YEAR[0] <= TODAY <= YEAR[1]

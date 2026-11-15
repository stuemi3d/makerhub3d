"""Production in the database: booking, cost split, cancelling, prints (issue #118, cases 1-23).

All data is invented. The test case numbers refer to the comment on the issue.
"""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from app import article_purge, production
from app.db import alembic_config, make_engine, run_migrations
from app.models import (
    Article,
    ChangeLogEntry,
    IgnoredPrint,
    Production,
    ProductionPrint,
    ProductionResult,
    StockMovement,
)
from app.production import PrintInput, ResultInput
from tests.test_stock import add_article, add_movement, db, stock

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
EN = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
TODAY = date(2026, 11, 15)


def print_(archive_id: int, material="3.00", power="0.50", wear="1.00", failed=False):
    return PrintInput(archive_id, D(material), D(power), D(wear), failed)


def book(prints, results, note: str = ""):
    """Books a production; returns (production id, errors)."""
    with db() as session, session.begin():
        return production.book(session, None, TODAY, note, prints, results)


def one_production(quantity: int = 10, archive_id: int = 1) -> tuple[int, int]:
    article_id = add_article()
    production_id, errors = book([print_(archive_id)], [ResultInput(article_id, quantity)])
    assert not errors
    return production_id, article_id


def cancel(production_id: int) -> str | None:
    with db() as session, session.begin():
        return production.cancel(session, None, production_id)


def get_production(production_id: int) -> Production:
    with db() as session:
        item = session.get(Production, production_id)
        len(item.prints), len(item.results)  # load before the session closes
        session.expunge(item)
        return item


def count(model) -> int:
    with db() as session:
        return len(session.scalars(select(model)).all())


# --- Cases 1-2: booking raises the stock, costs are stored ------------------------------------


def test_booking_raises_the_stock_and_stores_the_total_cost() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "2.00", "0.25", "0.50")],
        [ResultInput(article_id, 10)],
    )

    assert errors == {}
    assert stock(article_id) == 10
    item = get_production(production_id)
    assert item.total_cost == D("7.25")
    assert not item.cancelled
    assert len(item.prints) == 2
    row = item.results[0]
    assert (row.quantity, row.allocated_cost, row.split) == (10, D("7.25"), False)
    with db() as session:
        movement = session.get(StockMovement, row.movement_id)
        assert (movement.quantity, movement.kind) == (10, "production")


# --- Cases 3-4 and the three combinations -----------------------------------------------------


def test_costs_are_split_by_quantity_and_add_up_exactly() -> None:
    first, second = add_article("A-1"), add_article("A-2")

    production_id, errors = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "2.00", "0.25", "0.50")],
        [ResultInput(first, 6), ResultInput(second, 4)],
    )

    assert errors == {}
    rows = get_production(production_id).results
    assert [r.allocated_cost for r in rows] == [D("4.35"), D("2.90")]
    assert all(r.split for r in rows)
    assert sum(r.allocated_cost for r in rows) == D("7.25")
    assert (stock(first), stock(second)) == (6, 4)


def test_rounding_remainder_goes_to_one_row_and_nothing_is_lost() -> None:
    ids = [add_article(f"R-{n}") for n in range(3)]

    production_id, _ = book([print_(1, "1.00", "0.00", "0.00")], [ResultInput(i, 1) for i in ids])

    rows = get_production(production_id).results
    assert sum(r.allocated_cost for r in rows) == D("1.00")
    assert sorted(r.allocated_cost for r in rows) == [D("0.33"), D("0.33"), D("0.34")]


def test_split_with_weights() -> None:
    holder, clip = add_article("H-1"), add_article("C-1")

    production_id, _ = book(
        [print_(1, "8.00", "0.00", "0.00")],
        [ResultInput(holder, 2, D("3")), ResultInput(clip, 2, D("1"))],
    )

    rows = get_production(production_id).results
    assert [r.allocated_cost for r in rows] == [D("6.00"), D("2.00")]
    assert [r.weight for r in rows] == [D("3"), D("1")]


def test_one_article_on_five_plates_gets_all_costs() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(n, "1.00", "0.10", "0.20") for n in range(1, 6)], [ResultInput(article_id, 1)]
    )

    assert errors == {}
    row = get_production(production_id).results[0]
    assert (row.allocated_cost, row.split) == (D("6.50"), False)


def test_two_articles_on_one_plate() -> None:
    first, second = add_article("A-1"), add_article("A-2")

    production_id, errors = book(
        [print_(1, "5.00", "0.00", "0.00")], [ResultInput(first, 3), ResultInput(second, 2)]
    )

    assert errors == {}
    rows = get_production(production_id).results
    assert [r.allocated_cost for r in rows] == [D("3.00"), D("2.00")]


def test_two_articles_on_three_plates() -> None:
    first, second = add_article("A-1"), add_article("A-2")

    production_id, errors = book(
        [print_(n, "2.00", "0.00", "0.00") for n in (1, 2, 3)],
        [ResultInput(first, 4), ResultInput(second, 4)],
    )

    assert errors == {}
    item = get_production(production_id)
    assert item.total_cost == D("6.00")
    assert [r.allocated_cost for r in item.results] == [D("3.00"), D("3.00")]


# --- Case 5: later changes do not change the past ---------------------------------------------


def test_stored_costs_stay_when_the_article_changes() -> None:
    production_id, article_id = one_production()

    with db() as session, session.begin():
        article = session.get(Article, article_id)
        article.selling_price = D("99.00")
        article.weight = D("5")

    row = get_production(production_id).results[0]
    assert row.allocated_cost == D("4.50")
    assert get_production(production_id).total_cost == D("4.50")


# --- Case 6: invalid input books nothing ------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, -3])
def test_zero_or_negative_pieces_are_refused(quantity: int) -> None:
    article_id = add_article()

    production_id, errors = book([print_(1)], [ResultInput(article_id, quantity)])

    assert production_id is None
    assert "results" in errors
    assert (count(Production), count(StockMovement)) == (0, 0)


def test_no_result_article_is_refused() -> None:
    production_id, errors = book([print_(1)], [])

    assert production_id is None and "results" in errors
    assert count(Production) == 0


def test_inactive_or_missing_article_is_refused() -> None:
    inactive = add_article(active=False)

    _, errors_inactive = book([print_(1)], [ResultInput(inactive, 1)])
    _, errors_missing = book([print_(1)], [ResultInput(999, 1)])

    assert "results" in errors_inactive and "results" in errors_missing
    assert (count(Production), count(StockMovement)) == (0, 0)


def test_one_of_several_bad_rows_books_nothing() -> None:
    good = add_article("A-1")

    _, errors = book([print_(1)], [ResultInput(good, 2), ResultInput(999, 1)])

    assert errors
    assert (count(Production), count(StockMovement), stock(good)) == (0, 0, 0)


def test_same_article_twice_and_no_print_are_refused() -> None:
    article_id = add_article()

    _, twice = book([print_(1)], [ResultInput(article_id, 1), ResultInput(article_id, 2)])
    _, no_print = book([], [ResultInput(article_id, 1)])

    assert twice and no_print
    assert count(Production) == 0


# --- Case 7: failed prints --------------------------------------------------------------------


def test_failed_prints_count_into_the_costs_and_are_marked() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "1.00", "0.10", "0.20", failed=True)],
        [ResultInput(article_id, 5)],
    )

    assert errors == {}
    item = get_production(production_id)
    assert item.total_cost == D("5.80")
    assert [p.status for p in item.prints] == ["success", "failed"]


# --- Case 8: change log -----------------------------------------------------------------------


def test_booking_and_cancelling_are_in_the_change_log() -> None:
    production_id, _ = one_production()
    cancel(production_id)

    with db() as session:
        actions = [
            e.action for e in session.scalars(select(ChangeLogEntry).order_by(ChangeLogEntry.id))
        ]
    assert actions == ["production.booked", "production.cancelled"]
    assert DE["log.action.production.booked"] and DE["log.action.production.cancelled"]


# --- Cases 9-11: prints belong to one production, ignored prints ------------------------------


def test_a_print_cannot_be_in_two_active_productions() -> None:
    article_id = add_article()
    book([print_(7)], [ResultInput(article_id, 1)])

    production_id, errors = book([print_(7)], [ResultInput(article_id, 1)])

    assert production_id is None and "prints" in errors
    assert count(Production) == 1


def test_the_same_print_twice_in_one_production_is_refused() -> None:
    article_id = add_article()

    production_id, errors = book([print_(7), print_(7)], [ResultInput(article_id, 1)])

    assert production_id is None and "prints" in errors


def test_an_ignored_print_cannot_be_booked_until_it_is_not_ignored_any_more() -> None:
    article_id = add_article()
    with db() as session, session.begin():
        production.ignore(session, None, 5)

    refused, errors = book([print_(5)], [ResultInput(article_id, 1)])
    with db() as session, session.begin():
        production.unignore(session, None, 5)
    accepted, no_errors = book([print_(5)], [ResultInput(article_id, 1)])

    assert refused is None and "prints" in errors
    assert accepted is not None and no_errors == {}


def test_a_print_in_an_active_production_cannot_be_ignored() -> None:
    one_production(archive_id=3)

    with db() as session, session.begin():
        error = production.ignore(session, None, 3)

    assert error is not None
    assert count(IgnoredPrint) == 0


def test_ignoring_twice_is_harmless() -> None:
    with db() as session, session.begin():
        assert production.ignore(session, None, 4) is None
        assert production.ignore(session, None, 4) is None
    assert count(IgnoredPrint) == 1


# --- Case 12-14, 16: cancelling ---------------------------------------------------------------


def test_cancelling_books_a_counter_movement_and_keeps_the_original() -> None:
    production_id, article_id = one_production(10)

    assert cancel(production_id) is None

    assert stock(article_id) == 0
    with db() as session:
        movements = session.scalars(select(StockMovement).order_by(StockMovement.id)).all()
    assert [(m.quantity, m.kind) for m in movements] == [(10, "production"), (-10, "cancellation")]
    assert movements[1].cancels_id == movements[0].id
    item = get_production(production_id)
    assert item.cancelled
    assert item.total_cost == D("4.50")


def test_the_prints_of_a_cancelled_production_are_free_again() -> None:
    production_id, article_id = one_production(archive_id=9)
    cancel(production_id)

    new_id, errors = book([print_(9)], [ResultInput(article_id, 2)])

    assert errors == {} and new_id is not None
    assert stock(article_id) == 2


def test_a_production_cannot_be_cancelled_twice() -> None:
    production_id, article_id = one_production(10)
    cancel(production_id)

    assert cancel(production_id) == "production.error.cancelled_already"
    assert stock(article_id) == 0
    assert count(StockMovement) == 2


def test_cancelling_a_missing_production_raises() -> None:
    with pytest.raises(LookupError):
        cancel(404)


# --- Case 15: not enough stock left -----------------------------------------------------------


def test_cancelling_is_refused_when_the_stock_would_go_negative() -> None:
    production_id, article_id = one_production(10)
    add_movement(article_id, -4, reason="Verschenkt")

    assert cancel(production_id) == "production.error.insufficient"

    assert stock(article_id) == 6
    assert not get_production(production_id).cancelled
    assert count(StockMovement) == 2
    with db() as session:
        assert (
            session.scalars(
                select(ChangeLogEntry).where(ChangeLogEntry.action == "production.cancelled")
            ).all()
            == []
        )


# --- Case 17: several articles are cancelled together or not at all ---------------------------


def test_with_several_articles_both_are_cancelled_together() -> None:
    first, second = add_article("A-1"), add_article("A-2")
    production_id, _ = book([print_(1)], [ResultInput(first, 6), ResultInput(second, 4)])

    assert cancel(production_id) is None

    assert (stock(first), stock(second)) == (0, 0)
    assert count(StockMovement) == 4


def test_with_several_articles_nothing_is_cancelled_if_one_cannot_be() -> None:
    first, second = add_article("A-1"), add_article("A-2")
    production_id, _ = book([print_(1)], [ResultInput(first, 6), ResultInput(second, 4)])
    add_movement(second, -1, reason="Verschenkt")

    assert cancel(production_id) == "production.error.insufficient"

    assert (stock(first), stock(second)) == (6, 3)
    assert not get_production(production_id).cancelled
    assert count(StockMovement) == 3


# --- Case 18: the movement is cancelled only through the production ---------------------------


def test_a_production_movement_cannot_be_cancelled_on_its_own() -> None:
    from app import stock as stock_module

    production_id, article_id = one_production(10)
    movement_id = get_production(production_id).results[0].movement_id

    with db() as session, session.begin():
        error = stock_module.cancel(session, None, movement_id)

    assert error == "stock.error.cancel_at_source"
    assert stock(article_id) == 10


# --- Case 19: migration -----------------------------------------------------------------------


def test_production_migration_on_filled_database_and_back(tmp_path: Path) -> None:
    database = tmp_path / "production.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0012")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO articles (number, number_key, name, active, created_at)"
            " VALUES ('3D-1', '3d-1', 'Erfunden', 1, '2026-01-01 00:00:00')"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT number FROM articles").fetchall() == [("3D-1",)]
        counts = [
            connection.execute("SELECT COUNT(*) FROM productions").fetchone(),
            connection.execute("SELECT COUNT(*) FROM production_prints").fetchone(),
            connection.execute("SELECT COUNT(*) FROM production_results").fetchone(),
            connection.execute("SELECT COUNT(*) FROM ignored_prints").fetchone(),
        ]
        assert counts == [(0,)] * 4
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_prints (production_id, archive_id, material_cost,"
                " electricity_cost, wear_cost, status) VALUES (99, 1, '1', '1', '1', 'success')"
            )

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0012")
    run_migrations(engine)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM productions").fetchone() == (0,)


# --- Cases 20-21: deleting an article for good ------------------------------------------------


def purge(article_id: int) -> None:
    with db() as session, session.begin():
        article_purge.purge(session, None, session.get(Article, article_id))


def test_a_production_hangs_on_the_article_and_blocks_deleting() -> None:
    _, article_id = one_production()
    with db() as session:
        assert article_purge.counts(session, article_id)["productions"] == 1
        assert article_purge.blocking(session, article_id)


def test_purging_the_only_article_removes_the_production_with_its_prints() -> None:
    _, article_id = one_production()

    purge(article_id)

    assert (count(Production), count(ProductionPrint), count(ProductionResult)) == (0, 0, 0)
    assert count(StockMovement) == 0
    assert count(Article) == 0


def test_purging_one_of_two_articles_keeps_the_production_unchanged() -> None:
    first, second = add_article("A-1"), add_article("A-2")
    production_id, _ = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "2.00", "0.25", "0.50")],
        [ResultInput(first, 6), ResultInput(second, 4)],
    )

    purge(first)

    item = get_production(production_id)
    assert item.total_cost == D("7.25")
    assert len(item.prints) == 2
    assert [(r.article_id, r.allocated_cost) for r in item.results] == [(second, D("2.90"))]
    assert stock(second) == 4


def test_purge_of_a_cancelled_production_article_still_works() -> None:
    production_id, article_id = one_production()
    cancel(production_id)

    purge(article_id)

    assert (count(Production), count(StockMovement)) == (0, 0)


# --- Case 23: prints that are not assigned yet ------------------------------------------------


def test_unassigned_prints_are_those_neither_booked_nor_ignored() -> None:
    article_id = add_article()
    production_id, _ = book([print_(1), print_(2)], [ResultInput(article_id, 1)])
    with db() as session, session.begin():
        production.ignore(session, None, 3)

    with db() as session:
        assert production.unassigned(session, [1, 2, 3, 4, 5]) == [4, 5]

    cancel(production_id)
    with db() as session:
        assert production.unassigned(session, [1, 2, 3, 4, 5]) == [1, 2, 4, 5]


# --- Texts ------------------------------------------------------------------------------------


def test_all_production_texts_exist_in_both_languages() -> None:
    prefixes = ("production.error", "log.action.production")
    german = {key for key in DE if key.startswith(prefixes)}
    english = {key for key in EN if key.startswith(prefixes)}

    assert german
    assert german == english


def test_bad_print_ids_and_costs_are_refused() -> None:
    article_id = add_article()
    results = [ResultInput(article_id, 1)]

    _, zero = book([print_(0)], results)
    _, negative = book([print_(1, material="-1.00")], results)
    _, infinite = book([print_(1, material="Infinity")], results)

    assert zero and negative and infinite
    assert count(Production) == 0


def test_the_production_count_covers_several_productions() -> None:
    article_id = add_article()
    first, _ = book([print_(1)], [ResultInput(article_id, 2)])
    book([print_(2)], [ResultInput(article_id, 3)])
    cancel(first)

    with db() as session:
        assert article_purge.counts(session, article_id)["productions"] == 2

    purge(article_id)
    assert (count(Production), count(ProductionPrint), count(StockMovement)) == (0, 0, 0)

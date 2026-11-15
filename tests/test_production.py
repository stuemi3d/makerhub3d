"""Production in the database: booking, cancelling, prints (issues #118 and #140).

A production has exactly one result article that gets all costs. All data is invented. The
test case numbers of #118 refer to the comment on that issue.
"""

import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


def book(prints, result, note: str = ""):
    """Books a production; returns (production id, errors)."""
    with db() as session, session.begin():
        return production.book(session, None, TODAY, note, prints, result)


def one_production(quantity: int = 10, archive_id: int = 1) -> tuple[int, int]:
    article_id = add_article()
    production_id, errors = book([print_(archive_id)], ResultInput(article_id, quantity))
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


# --- Cases 1-2: booking raises the stock, costs are stored -------------------------------------


def test_booking_raises_the_stock_and_stores_the_total_cost() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "2.00", "0.25", "0.50")],
        ResultInput(article_id, 10),
    )

    assert errors == {}
    assert stock(article_id) == 10
    item = get_production(production_id)
    assert item.total_cost == D("7.25")
    assert not item.cancelled
    assert len(item.prints) == 2
    (row,) = item.results
    assert (row.quantity, row.allocated_cost) == (10, D("7.25"))
    with db() as session:
        movement = session.get(StockMovement, row.movement_id)
        assert (movement.quantity, movement.kind) == (10, "production")


# --- Issue #140: the four cases with one article get all costs ---------------------------------


def test_one_article_one_plate_one_piece() -> None:
    article_id = add_article()

    production_id, errors = book([print_(1, "5.00", "0.00", "0.00")], ResultInput(article_id, 1))

    assert errors == {}
    assert get_production(production_id).results[0].allocated_cost == D("5.00")


def test_the_same_article_several_times_on_one_plate() -> None:
    article_id = add_article()

    production_id, errors = book([print_(1, "12.00", "0.00", "0.00")], ResultInput(article_id, 4))

    assert errors == {}
    row = get_production(production_id).results[0]
    assert (row.quantity, row.allocated_cost) == (4, D("12.00"))
    assert stock(article_id) == 4


def test_one_article_on_five_plates_gets_all_costs() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(n, "1.00", "0.10", "0.20") for n in range(1, 6)], ResultInput(article_id, 1)
    )

    assert errors == {}
    assert get_production(production_id).results[0].allocated_cost == D("6.50")


def test_the_same_article_several_times_on_three_plates() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(n, "2.00", "0.00", "0.00") for n in (1, 2, 3)], ResultInput(article_id, 12)
    )

    assert errors == {}
    item = get_production(production_id)
    assert (item.total_cost, item.results[0].allocated_cost) == (D("6.00"), D("6.00"))
    assert stock(article_id) == 12


def test_a_production_cannot_hold_a_second_result_article() -> None:
    first, second = add_article("A-1"), add_article("A-2")
    production_id, _ = book([print_(1)], ResultInput(first, 2))

    with db() as session:
        movement_id = session.scalars(select(StockMovement.id)).first()
    with db() as session, pytest.raises(IntegrityError), session.begin():
        session.add(
            ProductionResult(
                production_id=production_id,
                article_id=second,
                quantity=1,
                allocated_cost=D("1.00"),
                movement_id=movement_id,
            )
        )


# --- Case 5: later changes do not change the past ----------------------------------------------


def test_stored_costs_stay_when_the_article_changes() -> None:
    production_id, article_id = one_production()

    with db() as session, session.begin():
        article = session.get(Article, article_id)
        article.selling_price = D("99.00")

    row = get_production(production_id).results[0]
    assert row.allocated_cost == D("4.50")
    assert get_production(production_id).total_cost == D("4.50")


# --- Case 6: invalid input books nothing -------------------------------------------------------


@pytest.mark.parametrize("quantity", [0, -3])
def test_zero_or_negative_pieces_are_refused(quantity: int) -> None:
    article_id = add_article()

    production_id, errors = book([print_(1)], ResultInput(article_id, quantity))

    assert production_id is None
    assert errors == {"quantity": "production.error.quantity"}
    assert (count(Production), count(StockMovement)) == (0, 0)


def test_inactive_or_missing_article_is_refused() -> None:
    inactive = add_article(active=False)

    _, errors_inactive = book([print_(1)], ResultInput(inactive, 1))
    _, errors_missing = book([print_(1)], ResultInput(999, 1))

    assert errors_inactive == errors_missing == {"article": "production.error.article"}
    assert (count(Production), count(StockMovement)) == (0, 0)


def test_no_print_is_refused() -> None:
    article_id = add_article()

    production_id, errors = book([], ResultInput(article_id, 1))

    assert production_id is None and "prints" in errors
    assert count(Production) == 0


# --- Case 7: failed prints ---------------------------------------------------------------------


def test_failed_prints_count_into_the_costs_and_are_marked() -> None:
    article_id = add_article()

    production_id, errors = book(
        [print_(1, "3.00", "0.50", "1.00"), print_(2, "1.00", "0.10", "0.20", failed=True)],
        ResultInput(article_id, 5),
    )

    assert errors == {}
    item = get_production(production_id)
    assert item.total_cost == D("5.80")
    assert [p.status for p in item.prints] == ["success", "failed"]


# --- Case 8: change log ------------------------------------------------------------------------


def test_booking_and_cancelling_are_in_the_change_log() -> None:
    production_id, _ = one_production()
    cancel(production_id)

    with db() as session:
        actions = [
            e.action for e in session.scalars(select(ChangeLogEntry).order_by(ChangeLogEntry.id))
        ]
    assert actions == ["production.booked", "production.cancelled"]
    assert DE["log.action.production.booked"] and DE["log.action.production.cancelled"]


# --- Cases 9-11: prints belong to one production, ignored prints -------------------------------


def test_a_print_cannot_be_in_two_active_productions() -> None:
    article_id = add_article()
    book([print_(7)], ResultInput(article_id, 1))

    production_id, errors = book([print_(7)], ResultInput(article_id, 1))

    assert production_id is None and "prints" in errors
    assert count(Production) == 1


def test_the_same_print_twice_in_one_production_is_refused() -> None:
    article_id = add_article()

    production_id, errors = book([print_(7), print_(7)], ResultInput(article_id, 1))

    assert production_id is None and "prints" in errors


def test_an_ignored_print_cannot_be_booked_until_it_is_not_ignored_any_more() -> None:
    article_id = add_article()
    with db() as session, session.begin():
        production.ignore(session, None, 5)

    refused, errors = book([print_(5)], ResultInput(article_id, 1))
    with db() as session, session.begin():
        production.unignore(session, None, 5)
    accepted, no_errors = book([print_(5)], ResultInput(article_id, 1))

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


# --- Case 12-14, 16: cancelling ----------------------------------------------------------------


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

    new_id, errors = book([print_(9)], ResultInput(article_id, 2))

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


# --- Case 15: not enough stock left ------------------------------------------------------------


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


# --- Case 18: the movement is cancelled only through the production ----------------------------


def test_a_production_movement_cannot_be_cancelled_on_its_own() -> None:
    from app import stock as stock_module

    production_id, article_id = one_production(10)
    movement_id = get_production(production_id).results[0].movement_id

    with db() as session, session.begin():
        error = stock_module.cancel(session, None, movement_id)

    assert error == "stock.error.cancel_at_source"
    assert stock(article_id) == 10


# --- Case 19: migration ------------------------------------------------------------------------


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


# --- Cases 20-21: deleting an article for good -------------------------------------------------


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


def test_purge_of_a_cancelled_production_article_still_works() -> None:
    production_id, article_id = one_production()
    cancel(production_id)

    purge(article_id)

    assert (count(Production), count(StockMovement)) == (0, 0)


# --- Case 23: prints that are not assigned yet -------------------------------------------------


def test_unassigned_prints_are_those_neither_booked_nor_ignored() -> None:
    article_id = add_article()
    production_id, _ = book([print_(1), print_(2)], ResultInput(article_id, 1))
    with db() as session, session.begin():
        production.ignore(session, None, 3)

    with db() as session:
        assert production.unassigned(session, [1, 2, 3, 4, 5]) == [4, 5]

    cancel(production_id)
    with db() as session:
        assert production.unassigned(session, [1, 2, 3, 4, 5]) == [1, 2, 4, 5]


# --- Texts -------------------------------------------------------------------------------------


def test_all_production_texts_exist_in_both_languages() -> None:
    prefixes = ("production.error", "log.action.production")
    german = {key for key in DE if key.startswith(prefixes)}
    english = {key for key in EN if key.startswith(prefixes)}

    assert german
    assert german == english


def test_bad_print_ids_and_costs_are_refused() -> None:
    article_id = add_article()
    results = ResultInput(article_id, 1)

    _, zero = book([print_(0)], results)
    _, negative = book([print_(1, material="-1.00")], results)
    _, infinite = book([print_(1, material="Infinity")], results)

    assert zero and negative and infinite
    assert count(Production) == 0


def test_the_production_count_covers_several_productions() -> None:
    article_id = add_article()
    first, _ = book([print_(1)], ResultInput(article_id, 2))
    book([print_(2)], ResultInput(article_id, 3))
    cancel(first)

    with db() as session:
        assert article_purge.counts(session, article_id)["productions"] == 2

    purge(article_id)
    assert (count(Production), count(ProductionPrint), count(StockMovement)) == (0, 0, 0)


# --- Issue #127: rounding of the total, split by hand, preview ---------------------------------


def test_the_total_is_rounded_to_the_cent_when_booking() -> None:
    article_id = add_article()

    production_id, _ = book([print_(1, "0.005", "0", "0")], ResultInput(article_id, 1))

    item = get_production(production_id)
    assert item.total_cost == D("0.01")
    assert item.results[0].allocated_cost == D("0.01")


def test_the_preview_changes_nothing_and_gives_all_costs_to_the_one_article() -> None:
    article_id = add_article()
    with db() as session:
        errors, total, row = production.preview(
            session, [print_(1, "7.25", "0", "0")], ResultInput(article_id, 5)
        )

    assert errors == {} and total == D("7.25")
    assert (row.quantity, row.cost) == (5, D("7.25"))
    assert (count(Production), count(StockMovement)) == (0, 0)


def test_the_preview_names_the_same_errors_as_booking() -> None:
    with db() as session:
        errors, total, row = production.preview(session, [print_(1)], ResultInput(999, 0))

    assert errors == {
        "article": "production.error.article",
        "quantity": "production.error.quantity",
    }
    assert (total, row) == (D(0), None)


# --- Issue #140: migration 0015 drops weight and split and allows one result per production ----


def test_single_result_migration_keeps_the_data_and_can_be_undone(tmp_path: Path) -> None:
    database = tmp_path / "single.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0014")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO articles (id, number, number_key, name, active, created_at)
              VALUES (1, '3D-1', '3d-1', 'Erfunden', 1, '2026-01-01 00:00:00');
            INSERT INTO productions (id, booked_on, created_at, note, total_cost, cancelled)
              VALUES (1, '2026-11-15', '2026-11-15 10:00:00', '', '4.50', 0);
            INSERT INTO stock_movements (id, article_id, booked_on, created_at, quantity, kind,
              reason) VALUES (1, 1, '2026-11-15', '2026-11-15 10:00:00', 10, 'production', '');
            INSERT INTO production_results (production_id, article_id, quantity, weight,
              allocated_cost, split, movement_id) VALUES (1, 1, 10, '1', '4.50', 0, 1);
            """
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        columns = [r[1] for r in connection.execute("PRAGMA table_info(production_results)")]
        assert "weight" not in columns and "split" not in columns
        assert connection.execute(
            "SELECT production_id, article_id, quantity, allocated_cost FROM production_results"
        ).fetchall() == [(1, 1, 10, "4.50")]
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO production_results (production_id, article_id, quantity,"
                " allocated_cost, movement_id) VALUES (1, 1, 1, '1', 1)"
            )

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0014")
    with sqlite3.connect(database) as connection:
        columns = [r[1] for r in connection.execute("PRAGMA table_info(production_results)")]
        assert "weight" in columns and "split" in columns
        assert connection.execute("SELECT COUNT(*) FROM production_results").fetchone() == (1,)
    run_migrations(engine)


def test_single_result_migration_stops_clearly_when_a_production_has_two_articles(
    tmp_path: Path,
) -> None:
    database = tmp_path / "several.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0014")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            INSERT INTO articles (id, number, number_key, name, active, created_at)
              VALUES (1, 'A-1', 'a-1', 'Erfunden', 1, '2026-01-01 00:00:00'),
                     (2, 'A-2', 'a-2', 'Erfunden', 1, '2026-01-01 00:00:00');
            INSERT INTO productions (id, booked_on, created_at, note, total_cost, cancelled)
              VALUES (1, '2026-11-15', '2026-11-15 10:00:00', '', '4.50', 0);
            INSERT INTO stock_movements (id, article_id, booked_on, created_at, quantity, kind,
              reason) VALUES (1, 1, '2026-11-15', '2026-11-15 10:00:00', 3, 'production', ''),
                             (2, 2, '2026-11-15', '2026-11-15 10:00:00', 2, 'production', '');
            INSERT INTO production_results (production_id, article_id, quantity, weight,
              allocated_cost, split, movement_id) VALUES (1, 1, 3, '1', '2.70', 1, 1),
                                                         (1, 2, 2, '1', '1.80', 1, 2);
            """
        )

    with pytest.raises(RuntimeError, match="several result articles"):
        run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM production_results").fetchone() == (2,)

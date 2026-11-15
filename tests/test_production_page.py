"""Page Production: list of Bambuddy prints, ignoring, dashboard list, start date
(issue #119, test cases 1-24). All data is invented; Bambuddy answers come from a mock.
The test case numbers refer to the comment on the issue.
"""

import json
import re
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx2
import pytest
from alembic import command
from sqlalchemy import select

from app import bambuddy, production
from app.db import alembic_config, make_engine, run_migrations
from app.main import create_app
from app.models import ChangeLogEntry, IgnoredPrint, Production
from app.production import PrintInput, ResultInput
from tests.auth import EMPLOYEE_NAME, READER_NAME, AppClient, add_user, client_as
from tests.test_bambuddy_client import BASE
from tests.test_bambuddy_settings import (
    DE,
    KEY,
    TEST_SECRET_KEY,
    URL,
    answer_with,
    save,
    stored,
)
from tests.test_stock import add_article, db

ROOT = Path(__file__).resolve().parent.parent
EN = json.loads((ROOT / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
D = Decimal
LONG_AGO = "2026-01-01"


@pytest.fixture(autouse=True)
def secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_SECRET_KEY", TEST_SECRET_KEY)


def raw(
    archive_id: int,
    started: str = "2026-11-15T08:00:00",
    status: str = "completed",
    name: str | None = None,
    cost: float | None = 0.5,
    energy: float | None = 0.1,
    wear: float | None = None,
    seconds: int = 3900,
) -> dict:
    return {
        "id": archive_id,
        "printer_id": 1,
        "print_name": name or f"Druck {archive_id}",
        "filename": f"datei{archive_id}.gcode.3mf",
        "status": status,
        "started_at": started,
        "completed_at": None,
        "print_time_seconds": seconds,
        "actual_time_seconds": seconds,
        "filament_used_grams": 10.5,
        "energy_kwh": 0.05,
        "energy_cost": energy,
        "wear_cost": wear,
        "cost": cost,
        "failure_reason": None,
        "created_at": started,
    }


def serve(client: AppClient, archives: list[dict], requests: list | None = None) -> None:
    """Bambuddy answers the archive list page by page (newest first, like the real one)."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        if requests is not None:
            requests.append(request)
        if request.url.path != "/api/v1/archives/":
            return httpx2.Response(404)
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        return httpx2.Response(200, json=archives[offset : offset + limit])

    answer_with(client, handler)


def set_up(client: AppClient, prints_from: str = LONG_AGO) -> None:
    assert save(client, prints_from=prints_from).status_code == 303


def prepared(client: AppClient, archives: list[dict], prints_from: str = LONG_AGO) -> None:
    set_up(client, prints_from)
    serve(client, archives)


def rows(html: str) -> list[str]:
    return re.findall(r'<tr class="print-row[^"]*".*?</tr>', html, re.S)


def names(html: str) -> list[str]:
    return [re.search(r'data-name="([^"]*)"', row).group(1) for row in rows(html)]


def book_print(archive_id: int) -> int:
    article_id = add_article()
    with db() as session, session.begin():
        production_id, errors = production.book(
            session,
            None,
            date(2026, 11, 15),
            "",
            [PrintInput(archive_id, D(1), D(1), D(1))],
            [ResultInput(article_id, 1)],
        )
    assert not errors
    return production_id


def ignore(client: AppClient, archive_id: int, action: str = "ignore"):
    return client.post(
        f"/production/{action}", data={"archive_id": str(archive_id)}, follow_redirects=False
    )


SAMPLE = [
    raw(5, "2026-11-15T10:00:00", name="Neuer Druck"),
    raw(4, "2026-11-14T10:00:00", status="failed", name="Fehldruck", cost=None, energy=None),
    raw(3, "2026-11-13T10:00:00", name="Alter Druck"),
]


# --- Cases 1-2: page and rights ----------------------------------------------------------------


def test_the_page_is_in_the_navigation_and_needs_a_login(client: AppClient) -> None:
    prepared(client, SAMPLE)

    home = client.get("/").text
    assert 'href="/production"' in home
    assert client.get("/production").status_code == 200
    with AppClient(create_app()) as anonymous:
        response = anonymous.get("/production", follow_redirects=False)
        assert response.status_code in (302, 303)
        assert "/login" in response.headers["location"]


def test_everybody_sees_the_list_but_only_admin_and_employee_may_ignore(
    client: AppClient,
) -> None:
    prepared(client, SAMPLE)
    add_user(EMPLOYEE_NAME, "employee")
    add_user(READER_NAME, "read_only")

    with client_as(EMPLOYEE_NAME) as employee:
        employee.app.state.bambuddy_transport = client.app.state.bambuddy_transport
        page = employee.get("/production").text
        assert "/production/ignore" in page
        assert ignore(employee, 5).status_code == 303
    with client_as(READER_NAME) as reader:
        reader.app.state.bambuddy_transport = client.app.state.bambuddy_transport
        page = reader.get("/production").text
        assert "Alter Druck" in page
        assert "/production/ignore" not in page
        assert ignore(reader, 4).status_code == 403
        assert ignore(reader, 5, "unignore").status_code == 403
    with db() as session:
        assert [i.archive_id for i in session.scalars(select(IgnoredPrint))] == [5]


# --- Cases 3-7: the list -----------------------------------------------------------------------


def test_the_list_shows_name_date_duration_status_and_costs(client: AppClient) -> None:
    prepared(client, [raw(7, name="Beispiel Clip", cost=0.5, energy=0.01, wear=0.25)])

    html = client.get("/production").text

    (row,) = rows(html)
    assert "Beispiel Clip" in row
    assert "15.11.2026 08:00" in row
    assert "1 h 5 min" in row
    assert DE["production.status.completed"] in row
    assert "0,5" in row and "0,01" in row and "0,25" in row


def test_failed_prints_are_red_and_others_are_not(client: AppClient) -> None:
    prepared(client, SAMPLE)

    html = client.get("/production").text
    by_name = dict(zip(names(html), rows(html), strict=True))

    assert "print-failed" in by_name["Fehldruck"]
    assert "print-failed" not in by_name["Neuer Druck"]
    assert DE["production.status.failed"] in by_name["Fehldruck"]


def test_the_open_view_is_the_default_and_newest_first(client: AppClient) -> None:
    prepared(client, SAMPLE)

    assert names(client.get("/production").text) == ["Neuer Druck", "Fehldruck", "Alter Druck"]


def test_the_views_split_open_assigned_and_ignored_prints(client: AppClient) -> None:
    prepared(client, SAMPLE)
    book_print(4)
    assert ignore(client, 3).status_code == 303

    assert names(client.get("/production").text) == ["Neuer Druck"]
    assert names(client.get("/production?view=assigned").text) == ["Fehldruck"]
    assert names(client.get("/production?view=ignored").text) == ["Alter Druck"]


def test_missing_costs_show_a_dash(client: AppClient) -> None:
    prepared(client, [raw(4, status="failed", cost=None, energy=None, wear=None)])

    (row,) = rows(client.get("/production").text)

    assert row.count(DE["table.empty_value"]) >= 3


def test_the_list_can_be_sorted_by_date_and_name(client: AppClient) -> None:
    prepared(client, SAMPLE)

    assert names(client.get("/production?sort=name&dir=asc").text) == [
        "Alter Druck",
        "Fehldruck",
        "Neuer Druck",
    ]
    assert names(client.get("/production?sort=name&dir=desc").text)[0] == "Neuer Druck"
    assert names(client.get("/production?sort=date&dir=asc").text)[0] == "Alter Druck"
    # An unknown column or direction means the default order.
    assert names(client.get("/production?sort=nonsense&dir=up").text)[0] == "Neuer Druck"


# --- Cases 8-11: ignoring ----------------------------------------------------------------------


def test_ignoring_moves_a_print_and_bringing_it_back_undoes_that(client: AppClient) -> None:
    prepared(client, SAMPLE)

    assert ignore(client, 5).status_code == 303
    assert "Neuer Druck" not in names(client.get("/production").text)
    assert "Neuer Druck" in names(client.get("/production?view=ignored").text)

    assert ignore(client, 5, "unignore").status_code == 303
    assert "Neuer Druck" in names(client.get("/production").text)
    assert names(client.get("/production?view=ignored").text) == []


def test_a_print_in_an_active_production_cannot_be_ignored(client: AppClient) -> None:
    prepared(client, SAMPLE)
    book_print(4)

    response = ignore(client, 4)

    assert response.status_code == 422
    assert DE["production.error.print_taken"] in response.text
    with db() as session:
        assert session.scalars(select(IgnoredPrint)).all() == []


def test_ignoring_is_logged_and_needs_the_form_token(client: AppClient) -> None:
    prepared(client, SAMPLE)
    ignore(client, 5)
    ignore(client, 5, "unignore")
    client.csrf_token = None
    for data in ({}, {"csrf_token": "falsch"}):
        response = client.post("/production/ignore", data={"archive_id": "3", **data})
        assert response.status_code == 403

    with db() as session:
        actions = [e.action for e in session.scalars(select(ChangeLogEntry))]
        assert session.scalars(select(IgnoredPrint)).all() == []
    assert actions.count("production.ignored") == 1
    assert actions.count("production.unignored") == 1


@pytest.mark.parametrize("value", ["", "abc", "-1", "0", "9" * 20])
def test_a_bad_print_number_is_refused(client: AppClient, value: str) -> None:
    prepared(client, SAMPLE)

    response = client.post("/production/ignore", data={"archive_id": value})

    assert response.status_code in (400, 422)
    with db() as session:
        assert session.scalars(select(IgnoredPrint)).all() == []


# --- Cases 12-13: Bambuddy missing or not reachable --------------------------------------------


def test_without_bambuddy_the_page_points_to_the_settings(client: AppClient) -> None:
    response = client.get("/production")

    assert response.status_code == 200
    assert DE["production.not_set_up"] in response.text
    assert 'href="/settings/bambuddy"' in response.text
    assert rows(response.text) == []


def test_an_unreachable_bambuddy_gives_a_plain_message_and_keeps_the_data(
    client: AppClient,
) -> None:
    prepared(client, SAMPLE)
    book_print(4)
    ignore(client, 3)

    def broken(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom http://secret.example.com")

    answer_with(client, broken)
    response = client.get("/production")

    assert response.status_code == 200
    assert DE["bambuddy.error.unreachable"] in response.text
    for secret in (URL, KEY, "secret.example.com", "boom"):
        assert secret not in response.text
    with db() as session:
        assert len(session.scalars(select(Production)).all()) == 1
        assert [i.archive_id for i in session.scalars(select(IgnoredPrint))] == [3]


@pytest.mark.parametrize(
    ("status", "key"), [(401, "rejected"), (403, "forbidden"), (500, "server_error")]
)
def test_refused_or_failing_bambuddy_is_named(client: AppClient, status: int, key: str) -> None:
    set_up(client)
    answer_with(client, lambda request: httpx2.Response(status))

    response = client.get("/production")

    assert response.status_code == 200
    assert DE[f"bambuddy.error.{key}"] in response.text


# --- Cases 14-16: dashboard --------------------------------------------------------------------


def test_dashboard_counts_the_open_prints_and_links_to_the_list(client: AppClient) -> None:
    prepared(client, SAMPLE)
    ignore(client, 3)

    html = client.get("/").text

    block = re.search(r'<section id="open-prints".*?</section>', html, re.S).group(0)
    assert re.search(r'class="stat-value open-count">\s*2\s*<', block)
    assert "Neuer Druck" in block and "Fehldruck" in block
    assert "Alter Druck" not in block
    assert 'href="/production"' in block


def test_dashboard_says_when_everything_is_assigned(client: AppClient) -> None:
    prepared(client, SAMPLE[:1])
    ignore(client, 5)

    block = re.search(r'<section id="open-prints".*?</section>', client.get("/").text, re.S)

    assert DE["dashboard.open.ok"] in block.group(0)


def test_dashboard_shows_only_the_latest_open_prints(client: AppClient) -> None:
    prepared(client, [raw(n, f"2026-11-15T{n:02d}:00:00") for n in range(20, 9, -1)])

    block = re.search(r'<section id="open-prints".*?</section>', client.get("/").text, re.S)

    assert re.search(r'open-count">\s*11\s*<', block.group(0))
    assert block.group(0).count("<li") <= 5


def test_dashboard_still_works_without_or_with_a_broken_bambuddy(client: AppClient) -> None:
    page = client.get("/")
    assert page.status_code == 200
    assert DE["dashboard.open.not_set_up"] in page.text

    set_up(client)
    answer_with(client, lambda request: httpx2.Response(500))
    page = client.get("/")
    assert page.status_code == 200
    assert DE["dashboard.open.unavailable"] in page.text
    assert "Traceback" not in page.text


# --- Cases 18-19, 22-23: the start date in the settings ----------------------------------------


def test_only_the_admin_changes_the_date_and_the_field_is_there(client: AppClient) -> None:
    assert 'name="prints_from"' in client.get("/settings/bambuddy").text
    add_user(EMPLOYEE_NAME, "employee")
    with client_as(EMPLOYEE_NAME) as employee:
        assert save(employee, prints_from="2026-02-02").status_code == 403
    assert stored().prints_from is None


def test_the_first_setup_sets_the_date_to_today(client: AppClient) -> None:
    assert save(client, prints_from="").status_code == 303

    assert stored().prints_from == date.today()


def test_later_saves_without_a_date_are_refused_and_keep_the_old_one(client: AppClient) -> None:
    set_up(client, "2026-03-03")

    response = save(client, prints_from="")

    assert response.status_code == 422
    assert DE["bambuddy.error.prints_from"] in response.text
    assert stored().prints_from == date(2026, 3, 3)


@pytest.mark.parametrize("value", ["gestern", "2026-13-40", "15.11.2026", "2999-01-01"])
def test_an_invalid_or_future_date_is_refused(client: AppClient, value: str) -> None:
    set_up(client, "2026-03-03")

    response = save(client, prints_from=value)

    assert response.status_code == 422
    assert DE["bambuddy.error.prints_from"] in response.text
    assert stored().prints_from == date(2026, 3, 3)


def test_changing_the_date_is_logged_with_old_and_new_value(client: AppClient) -> None:
    set_up(client, "2026-03-03")
    save(client, prints_from="2026-04-04")

    with db() as session:
        entries = list(session.scalars(select(ChangeLogEntry).order_by(ChangeLogEntry.id)))
    details = json.loads(entries[-1].details)
    assert {"field": "prints_from", "old": "2026-03-03", "new": "2026-04-04"} in details
    assert KEY not in entries[-1].details


# --- Cases 20-21: older prints are not shown ---------------------------------------------------


OLD_AND_NEW = [
    raw(3, "2026-11-10T10:00:00", name="Neuer Druck"),
    raw(2, "2026-06-10T10:00:00", name="Mittlerer Druck"),
    raw(1, "2026-01-10T10:00:00", name="Uralter Druck"),
]


def test_prints_before_the_date_are_nowhere_to_be_seen(client: AppClient) -> None:
    prepared(client, OLD_AND_NEW, prints_from="2026-11-01")

    for view in ("open", "assigned", "ignored"):
        assert "Mittlerer Druck" not in client.get(f"/production?view={view}").text
    assert names(client.get("/production").text) == ["Neuer Druck"]
    assert re.search(r'open-count">\s*1\s*<', client.get("/").text)


def test_moving_the_date_back_and_forward_only_changes_what_is_seen(client: AppClient) -> None:
    prepared(client, OLD_AND_NEW, prints_from="2026-11-01")
    ignore(client, 3)

    save(client, prints_from="2026-05-01")
    assert names(client.get("/production").text) == ["Mittlerer Druck"]
    save(client, prints_from="2026-11-05")
    assert names(client.get("/production").text) == []
    with db() as session:
        # Nothing was deleted: the ignored print stays stored.
        assert [i.archive_id for i in session.scalars(select(IgnoredPrint))] == [3]
    assert names(client.get("/production?view=ignored").text) == ["Neuer Druck"]


# --- Case 24: more than 500 prints and the upper limit -----------------------------------------


def many(count: int) -> list[dict]:
    return [raw(n, "2026-11-15T10:00:00") for n in range(count, 0, -1)]


def test_more_than_500_prints_are_read_completely(client: AppClient) -> None:
    prepared(client, many(1200))

    html = client.get("/production").text

    assert len(rows(html)) == 1200
    assert DE["production.truncated"] not in html


def test_a_huge_archive_is_cut_with_a_notice(
    client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bambuddy, "MAX_ARCHIVES", 250)
    prepared(client, many(400))

    html = client.get("/production").text

    assert len(rows(html)) == 250
    assert DE["production.truncated"] in html


# --- Case 19: migration ------------------------------------------------------------------------


def test_migration_sets_the_date_of_an_existing_connection_to_the_migration_day(
    tmp_path: Path,
) -> None:
    database = tmp_path / "from.db"
    engine = make_engine(database)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "0013")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE bambuddy_connection SET url = 'http://x.example.com' WHERE id = 1"
        )

    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        value = connection.execute("SELECT prints_from FROM bambuddy_connection").fetchone()[0]
        assert value == date.today().isoformat()
        assert connection.execute("SELECT url FROM bambuddy_connection").fetchone() == (
            "http://x.example.com",
        )


def test_migration_leaves_an_empty_connection_without_a_date_and_can_be_undone(
    tmp_path: Path,
) -> None:
    database = tmp_path / "empty.db"
    engine = make_engine(database)
    run_migrations(engine)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT prints_from FROM bambuddy_connection").fetchone() == (
            None,
        )

    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0013")
    run_migrations(engine)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM bambuddy_connection").fetchone() == (1,)


# --- Client: reading since a date --------------------------------------------------------------


def test_the_client_reads_page_by_page_until_the_date() -> None:
    requests: list[httpx2.Request] = []
    archives = many(250)
    archives += [raw(n, "2026-01-01T10:00:00") for n in range(1000, 900, -1)]

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        limit = int(request.url.params["limit"])
        offset = int(request.url.params["offset"])
        return httpx2.Response(200, json=archives[offset : offset + limit])

    client = bambuddy.BambuddyClient(BASE, KEY, transport=httpx2.MockTransport(handler))
    found, truncated = client.archives_since(date(2026, 11, 1))

    assert len(found) == 250
    assert not truncated
    # It stops at the first page that has only older prints, not at the end of the history.
    assert len(requests) <= 4
    assert all(request.method == "GET" for request in requests)


def test_the_client_says_when_the_limit_cuts_the_list() -> None:
    archives = many(400)
    client = bambuddy.BambuddyClient(
        BASE,
        KEY,
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(
                200,
                json=archives[
                    int(r.url.params["offset"]) : int(r.url.params["offset"])
                    + int(r.url.params["limit"])
                ],
            )
        ),
    )

    found, truncated = client.archives_since(date(2026, 1, 1), max_items=150)

    assert len(found) == 150 and truncated


def test_a_slow_bambuddy_ends_in_a_timeout_for_the_whole_reading() -> None:
    archives = many(250)
    client = bambuddy.BambuddyClient(
        BASE,
        KEY,
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(
                200,
                json=archives[
                    int(r.url.params["offset"]) : int(r.url.params["offset"])
                    + int(r.url.params["limit"])
                ],
            )
        ),
    )

    with pytest.raises(bambuddy.BambuddyError) as error:
        client.archives_since(date(2026, 1, 1), budget=0.0)

    assert error.value.code == "timeout"


def test_times_with_and_without_a_zone_do_not_break_the_list(client: AppClient) -> None:
    prepared(
        client,
        [
            raw(3, "2026-11-15T10:00:00+00:00", name="Mit Zone"),
            raw(2, "2026-11-14T10:00:00", name="Ohne Zone"),
            {
                **raw(1, "2026-11-13T10:00:00", name="Ohne Zeit"),
                "started_at": None,
                "created_at": None,
            },
        ],
    )

    response = client.get("/production")

    assert response.status_code == 200
    assert names(response.text) == ["Mit Zone", "Ohne Zone", "Ohne Zeit"]
    assert client.get("/").status_code == 200


# --- Texts -------------------------------------------------------------------------------------


def test_all_texts_exist_in_both_languages() -> None:
    prefixes = (
        "production.",
        "dashboard.open",
        "bambuddy.field.prints_from",
        "bambuddy.error.prints",
    )
    german = {key for key in DE if key.startswith(prefixes)}
    english = {key for key in EN if key.startswith(prefixes)}
    assert german
    assert german == english

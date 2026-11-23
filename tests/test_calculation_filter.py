"""Calculation list: columns per piece, jump to the article, filter bar (issue #260).
All data is invented. The numbers refer to the test cases in the comment on the issue."""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.models import Calculation
from tests.auth import AppClient
from tests.test_calculation_store import DE
from tests.test_stock import add_article, db

D = Decimal
COLUMNS = [
    *("number", "name", "date", "printer", "material", "power"),
    *("machine", "labour", "cost", "margin", "price", "profit"),
]


def add_calc(
    article_id: int,
    day: date = date(2026, 3, 10),
    printer: str = "Testdrucker",
    pieces: int = 4,
    material: str = "12",
    power: str = "8",
    machine: str = "4",
    labour: str = "0.5",
    scrap: str = "10",
    margin: str = "30",
    suggested: str = "9.23",
    selling: str | None = None,
    main: bool = False,
) -> int:
    """A saved calculation written straight into the database. The result is per piece,
    the three print parts per plate (like the real ones)."""
    plate = D(material) + D(power) + D(machine)
    cost = plate / pieces * (1 + D(scrap) / 100) + D(labour)
    price = D(selling) if selling is not None else D(suggested)
    with db() as session, session.begin():
        item = Calculation(
            article_id=article_id,
            created_on=day,
            created_at=datetime(day.year, day.month, day.day, 12, 0),
            kind="hand",
            is_main=main,
            printer_name=printer,
            print_minutes=D("60"),
            pieces=pieces,
            setup_minutes=D("0"),
            rework_minutes=D("0"),
            scrap_percent=D(scrap),
            margin_percent=D(margin),
            power_price=D("0.3"),
            machine_rate=D("1"),
            labour_rate=D("20"),
            rounding="none",
            material_per_plate=D(material),
            power_per_plate=D(power),
            machine_per_plate=D(machine),
            labour_cost=D(labour),
            cost_without_labour=cost - D(labour),
            cost_with_labour=cost,
            suggested_price=D(suggested),
            selling_price=price,
            profit=price - cost,
        )
        session.add(item)
        session.flush()
        return item.id


def rows(html: str) -> list[str]:
    return re.findall(r'<tr class="calc-row".*?</tr>', html, re.S)


def cell(row: str, column: str) -> str:
    match = re.search(rf'<td class="[^"]*\bcol-{column}\b[^"]*">(.*?)</td>', row, re.S)
    assert match, column
    return re.sub(r"\s+", " ", match.group(1)).strip()


def column(client: AppClient, query: str, name: str) -> list[str]:
    html = client.get("/calculation" + query).text
    return [re.sub(r"<[^>]+>", "", cell(row, name)).strip() for row in rows(html)]


def money(text: str) -> Decimal:
    return D(text.replace(" €", "").replace(",", "."))


@pytest.fixture
def two(client: AppClient) -> tuple[int, int]:
    one = add_article("3D-0001", "Würfel klein")
    other = add_article("3D-0002", "Kabelhalter")
    return one, other


# --- Cases 1-3: table and jump to the article -------------------------------------------------


def test_1_number_and_name_are_separate_sortable_columns(client: AppClient, two) -> None:
    add_calc(two[0])
    add_calc(two[1], day=date(2026, 3, 11))
    html = client.get("/calculation").text
    assert 'href="/calculation?sort=number&amp;dir=asc"' in html
    assert 'href="/calculation?sort=name&amp;dir=asc"' in html
    assert column(client, "?sort=number&dir=asc", "number") == ["3D-0001", "3D-0002"]
    assert column(client, "?sort=name&dir=asc", "name") == ["Kabelhalter", "Würfel klein"]
    assert column(client, "?sort=name&dir=desc", "number") == ["3D-0001", "3D-0002"]


def test_2_the_date_is_no_link_and_the_eye_opens_the_calculation(client: AppClient, two) -> None:
    calculation_id = add_calc(two[0])
    (row,) = rows(client.get("/calculation").text)
    assert "<a" not in cell(row, "date") and "10.03.2026" in cell(row, "date")
    assert f'hx-get="/calculation/{calculation_id}"' in row
    assert row.count(f'hx-get="/calculation/{calculation_id}"') == 1


def test_3_a_symbol_on_the_right_leads_to_the_article(client: AppClient, two) -> None:
    add_calc(two[1])
    (row,) = rows(client.get("/calculation").text)
    assert "<a" not in cell(row, "number") and "<a" not in cell(row, "name")
    actions = re.search(r'<td class="row-actions">(.*?)</td>', row, re.S).group(1)
    link = re.search(rf'<a class="icon-btn" href="/articles/{two[1]}"[^>]*>', actions)
    assert link and DE["table.action.article"] in link.group(0)


# --- Cases 4-7: parts per piece, margin, profit, sorting --------------------------------------


def test_4_the_parts_are_shown_per_piece_with_the_scrap_share(client: AppClient, two) -> None:
    add_calc(two[0])  # 4 pieces, 12 material, 8 power, 4 machine, 10 % scrap, labour 0.50
    (row,) = rows(client.get("/calculation").text)
    assert cell(row, "material") == "3,30"
    assert cell(row, "power") == "2,20"
    assert cell(row, "machine") == "1,10"
    assert cell(row, "labour") == "0,50"
    assert cell(row, "cost") == "7,10"


def test_5_the_parts_add_up_to_the_cost(client: AppClient, two) -> None:
    add_calc(two[0], pieces=3, material="10", power="5", machine="2", scrap="0", labour="1.25")
    add_calc(two[0], pieces=7, material="21", power="3.5", machine="6", scrap="12.5", labour="0")
    for row in rows(client.get("/calculation").text):
        parts = sum(money(cell(row, name)) for name in ("material", "power", "machine", "labour"))
        assert parts == money(cell(row, "cost")), row


def test_6_margin_and_profit_per_piece(client: AppClient, two) -> None:
    add_calc(two[0], margin="30", suggested="9.23")
    add_calc(two[1], margin="25", suggested="9.23", selling="10.00", day=date(2026, 3, 9))
    first, second = rows(client.get("/calculation").text)
    assert cell(first, "margin") == "30 %" and cell(first, "price") == "9,23"
    assert cell(first, "profit") == "2,13"
    # A target price replaces the suggestion: profit = selling price minus cost
    assert cell(second, "margin") == "25 %" and cell(second, "profit") == "2,90"


def test_7_all_columns_sort_by_the_real_value(client: AppClient, two) -> None:
    add_calc(two[0], day=date(2026, 3, 1), printer="B", material="38", suggested="9.50")
    add_calc(two[1], day=date(2026, 3, 2), printer="A", material="40", suggested="10.00")
    assert column(client, "?sort=date&dir=asc", "date") == ["01.03.2026", "02.03.2026"]
    assert column(client, "?sort=printer&dir=asc", "printer") == ["A", "B"]
    # 9.50 comes before 10.00 (numbers, not text)
    assert column(client, "?sort=price&dir=asc", "price") == ["9,50", "10,00"]
    assert column(client, "?sort=price&dir=desc", "price") == ["10,00", "9,50"]
    assert column(client, "?sort=material&dir=asc", "material") == ["10,45", "11,00"]
    assert column(client, "?sort=material&dir=desc", "material") == ["11,00", "10,45"]
    for key in COLUMNS:
        for direction in ("asc", "desc"):
            response = client.get(f"/calculation?sort={key}&dir={direction}")
            assert response.status_code == 200 and len(rows(response.text)) == 2, key
    assert 'aria-sort="descending"' in client.get("/calculation").text


# --- Cases 8-13: filter -----------------------------------------------------------------------


def test_8_search_finds_number_name_and_printer_case_blind(client: AppClient, two) -> None:
    add_calc(two[0], printer="Blaudruck")
    add_calc(two[1], printer="Rotdruck")
    assert column(client, "?q=wÜRFEL", "number") == ["3D-0001"]
    assert column(client, "?q=0002", "number") == ["3D-0002"]
    assert column(client, "?q=ROTdr", "number") == ["3D-0002"]
    assert column(client, "?q=%25", "number") == []  # no wildcard meaning


def test_9_the_article_filter_shows_only_that_article(client: AppClient, two) -> None:
    add_calc(two[0])
    add_calc(two[1])
    assert column(client, f"?article={two[1]}", "number") == ["3D-0002"]
    html = client.get(f"/calculation?article={two[1]}").text
    assert f'<option value="{two[1]}" selected>' in html
    assert "3D-0002" in re.search(r'<li class="filter-chip">(.*?)</li>', html, re.S).group(1)
    assert client.get("/calculation?article=999").status_code == 404


def test_10_the_period_includes_both_limits_and_ignores_bad_dates(client: AppClient, two) -> None:
    for day in (1, 10, 20):
        add_calc(two[0], day=date(2026, 3, day))
    assert column(client, "?from=2026-03-10&to=2026-03-20", "date") == ["20.03.2026", "10.03.2026"]
    assert column(client, "?from=2026-03-11", "date") == ["20.03.2026"]
    assert column(client, "?to=2026-03-10", "date") == ["10.03.2026", "01.03.2026"]
    assert len(column(client, "?from=morgen&to=2026-13-45", "date")) == 3
    assert column(client, "?from=2026-03-20&to=2026-03-10", "date") == []


def test_11_the_printer_filter_shows_only_that_printer(client: AppClient, two) -> None:
    add_calc(two[0], printer="Blaudruck")
    add_calc(two[1], printer="Rotdruck")
    assert column(client, "?printer=Rotdruck", "number") == ["3D-0002"]
    assert len(column(client, "?printer=Gibtsnicht", "number")) == 2
    html = client.get("/calculation").text
    assert '<option value="Blaudruck"' in html and '<option value="Rotdruck"' in html


def test_12_only_main_calculations(client: AppClient, two) -> None:
    add_calc(two[0], main=True)
    add_calc(two[1], main=False)
    assert column(client, "?state=main", "number") == ["3D-0001"]
    assert len(column(client, "?state=bogus", "number")) == 2


def test_13_filters_combine_with_chips_reset_and_sort(client: AppClient, two) -> None:
    add_calc(two[0], day=date(2026, 3, 1), main=True)
    add_calc(two[0], day=date(2026, 3, 5))
    add_calc(two[1], day=date(2026, 3, 6))
    query = "?q=würfel&from=2026-03-02&state=main&sort=price&dir=desc"
    assert column(client, query, "number") == []
    html = client.get("/calculation?q=w%C3%BCrfel&from=2026-03-02&sort=date&dir=asc").text
    assert len(re.findall(r'class="filter-chip"', html)) == 2
    # Removing one chip keeps the other filter and the sort
    assert 'href="/calculation?from=2026-03-02&amp;sort=date&amp;dir=asc"' in html
    assert 'href="/calculation?q=w%C3%BCrfel&amp;sort=date&amp;dir=asc"' in html
    assert re.search(
        r'<a [^>]*href="/calculation\?sort=date&amp;dir=asc"[^>]*>\s*' + DE["filter.reset"], html
    )
    # The sort links keep the filter
    assert "q=w%C3%BCrfel" in re.search(r'href="(/calculation\?[^"]*sort=price[^"]*)"', html).group(
        1
    )


# --- Cases 14-15: article tile, hints ---------------------------------------------------------


def test_14_the_article_list_stays_after_a_change_and_names_the_article(
    client: AppClient, two
) -> None:
    add_calc(two[0])
    html = client.get(f"/calculation?article={two[0]}").text
    assert f'href="/articles/{two[0]}"' in html  # breadcrumb
    assert 'hx-get="/calculation/new?copy=' in html and f"filter={two[0]}" in html


def test_15_hints_for_no_hits_and_for_no_calculation_at_all(client: AppClient, two) -> None:
    empty = client.get("/calculation").text
    assert DE["calc.list.empty"] in empty and DE["filter.no_results"] not in empty
    add_calc(two[0])
    html = client.get("/calculation?q=gibtesnicht").text
    assert DE["filter.no_results"] in html and DE["calc.list.empty"] not in html


def test_5_the_shown_parts_add_up_even_when_rounding_would_drift(client: AppClient, two) -> None:
    # Four parts of about 0.0049 each: single rounding gives 0.00 x 4, the cost rounds to 0.02
    add_calc(
        two[0], pieces=1, material="0.0049", power="0.0049", machine="0.0049", labour="0.0049",
        scrap="0", suggested="0.05",
    )  # fmt: skip
    (row,) = rows(client.get("/calculation").text)
    parts = sum(money(cell(row, name)) for name in ("material", "power", "machine", "labour"))
    assert parts == money(cell(row, "cost")) == D("0.02")


def test_10_week_dates_are_not_dates_of_the_filter(client: AppClient, two) -> None:
    add_calc(two[0])
    assert len(column(client, "?from=2026-W01-1", "date")) == 1
    assert "2026-W01-1" not in client.get("/calculation?from=2026-W01-1").text

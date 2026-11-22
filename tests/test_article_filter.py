"""Filter bar on the article list (issue #257, test cases 1-14). All data is invented."""

import re
from decimal import Decimal

import pytest

from tests.auth import READER_NAME, AppClient, add_user, client_as
from tests.test_article_table import DE, add_article, add_stock, numbers


@pytest.fixture
def three(client: AppClient) -> None:
    add_article("3D-0001", "Würfel klein", min_stock=10)  # stock 4: low
    add_article("3D-0002", "Kabelhalter")  # stock 0
    add_article("3D-0003", "Würfel groß", active=False)
    add_stock(1, 4)


def ids(client: AppClient, query: str) -> list[str]:
    return numbers(client, query)


def test_1_search_finds_number_or_name_case_blind(client: AppClient, three: None) -> None:
    assert ids(client, "?q=wÜrfel") == ["3D-0001"]
    assert ids(client, "?q=0002") == ["3D-0002"]


def test_2_no_hits_shows_a_hint(client: AppClient, three: None) -> None:
    html = client.get("/articles?q=gibtesnicht").text
    assert DE["filter.no_results"] in html
    assert DE["articles.empty"] not in html


def test_3_active_filters_are_chips_with_removal(client: AppClient, three: None) -> None:
    html = client.get("/articles?q=W&stock=zero").text
    assert 'class="filter-chip"' in html
    assert 'href="/articles?stock=zero"' in html
    assert 'href="/articles?q=W"' in html


def test_4_reset_shows_the_default_view(client: AppClient, three: None) -> None:
    html = client.get("/articles?q=W&stock=zero").text
    assert re.search(r'<a [^>]*href="/articles"[^>]*>\s*' + DE["filter.reset"], html)
    assert ids(client, "") == ["3D-0001", "3D-0002"]


def test_5_filter_comes_from_the_address(client: AppClient, three: None) -> None:
    assert ids(client, "?status=inactive") == ["3D-0003"]
    html = client.get("/articles?q=Cube").text
    assert 'value="Cube"' in html


def test_6_sort_and_filter_keep_each_other(client: AppClient, three: None) -> None:
    html = client.get("/articles?q=W&status=all&sort=name&dir=desc").text
    link = re.search(r'href="(/articles\?[^"]*sort=number[^"]*)"', html).group(1)
    assert "q=W" in link and "status=all" in link
    assert 'name="sort" value="name"' in html and 'name="dir" value="desc"' in html
    assert ids(client, "?q=W&status=all&sort=name&dir=desc") == ["3D-0001", "3D-0003"]


def test_7_bad_values_give_the_default_view(client: AppClient, three: None) -> None:
    response = client.get("/articles?status=zzz&stock=zzz&sort=zzz&dir=zzz")
    assert response.status_code == 200
    assert ids(client, "?status=zzz&stock=zzz") == ["3D-0001", "3D-0002"]


def test_9_status_active_inactive_all(client: AppClient, three: None) -> None:
    assert ids(client, "?status=active") == ["3D-0001", "3D-0002"]
    assert ids(client, "?status=inactive") == ["3D-0003"]
    assert ids(client, "?status=all") == ["3D-0001", "3D-0002", "3D-0003"]
    # The old address still shows everything
    assert ids(client, "?inactive=1") == ["3D-0001", "3D-0002", "3D-0003"]
    assert DE["articles.show_inactive"] not in client.get("/articles").text


def test_10_below_minimum(client: AppClient, three: None) -> None:
    assert ids(client, "?stock=low") == ["3D-0001"]


def test_11_zero_and_available(client: AppClient, three: None) -> None:
    assert ids(client, "?stock=zero") == ["3D-0002"]
    assert ids(client, "?stock=available") == ["3D-0001"]


def test_12_filters_work_together(client: AppClient, three: None) -> None:
    assert ids(client, "?status=all&q=würfel&stock=zero") == ["3D-0003"]
    assert ids(client, "?status=all&q=würfel&stock=available") == ["3D-0001"]
    assert ids(client, "?q=kabel&stock=low") == []


def test_13_special_characters_are_searched_literally_and_escaped(
    client: AppClient, three: None
) -> None:
    add_article("3D-0004", "Rabatt 50% Box")
    assert ids(client, "?q=%25") == ["3D-0004"]
    assert ids(client, "?q=_") == []
    html = client.get("/articles?q=<script>alert(1)</script>").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_14_login_and_view_right_are_needed(anon_client: AppClient) -> None:
    response = anon_client.get("/articles?q=x", follow_redirects=False)
    assert response.status_code in (303, 307)


def test_14_a_reader_may_filter(client: AppClient, three: None) -> None:
    add_user(READER_NAME, "read_only")
    with client_as(READER_NAME) as reader:
        assert numbers(reader, "?q=Kabel") == ["3D-0002"]


def test_price_is_kept_in_filtered_rows(client: AppClient) -> None:
    add_article("3D-0009", "Clip", selling_price=Decimal("2.5"))
    assert "2,50" in client.get("/articles?q=clip").text

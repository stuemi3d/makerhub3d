"""Settings page: show, save and validate the basic values."""

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import make_engine
from app.main import create_app
from app.models import AppSettings, Rounding
from tests.forms import valid_form

APP_DIR = Path(__file__).resolve().parent.parent / "app"
DE = json.loads((APP_DIR / "locales" / "de.json").read_text(encoding="utf-8"))
FIELDS = [
    "electricity_price",
    "labour_rate",
    "scrap_rate",
    "markup",
    "rounding",
    "spool_category",
    "deviation_threshold",
]


def stored() -> AppSettings:
    with Session(make_engine(Settings.from_env().database_path)) as session:
        return session.get(AppSettings, 1)


def field_value(html: str, name: str) -> str:
    match = re.search(rf'<input\b[^>]*name="{name}"[^>]*value="([^"]*)"', html)
    assert match is not None, f"field {name} not found"
    return match.group(1)


def field_error(html: str, name: str) -> str | None:
    match = re.search(rf'<small id="{name}-error"[^>]*>(.*?)</small>', html, re.DOTALL)
    return match.group(1).strip() if match else None


# --- Test case 7: navigation -----------------------------------------------------------


def test_settings_entry_is_a_link_and_marked_current(client: TestClient) -> None:
    response = client.get("/settings")
    nav = re.search(r"<nav\b.*?</nav>", response.text, re.DOTALL).group(0)

    assert response.status_code == 200
    current = re.findall(r'<a\b[^>]*aria-current="page"[^>]*>', nav)
    assert len(current) == 1
    assert 'href="/settings"' in current[0]


def test_settings_link_is_reachable_from_start_page(client: TestClient) -> None:
    nav = re.search(r"<nav\b.*?</nav>", client.get("/").text, re.DOTALL).group(0)

    assert re.search(rf'<a href="/settings">{DE["nav.settings"]}</a>', nav)


# --- Test case 8: fields with defaults -------------------------------------------------


def test_page_shows_all_fields_with_defaults(client: TestClient) -> None:
    html = client.get("/settings").text

    assert field_value(html, "electricity_price") == "0,30"
    assert field_value(html, "labour_rate") == "15,00"
    assert field_value(html, "scrap_rate") == "5"
    assert field_value(html, "markup") == "30"
    assert field_value(html, "spool_category") == "Geschäftlich"
    assert field_value(html, "deviation_threshold") == "15"
    assert re.search(r'<option value="ends_90" selected>', html)
    for name in FIELDS:
        assert DE[f"settings.field.{name}"] in html


def test_all_five_rounding_choices_are_offered(client: TestClient) -> None:
    html = client.get("/settings").text

    options = re.findall(r'<option value="([^"]+)"', html)
    assert options == ["none", "ten_cents", "fifty_cents", "ends_90", "whole_euro"]


# --- Test case 9 + 12: save exactly and confirm ----------------------------------------


def test_saved_value_is_shown_exactly_after_reload(client: TestClient) -> None:
    response = client.post("/settings", data=valid_form(electricity_price="0,3215"))

    assert response.status_code == 200
    assert DE["settings.saved"] in response.text
    assert field_value(client.get("/settings").text, "electricity_price") == "0,3215"
    assert stored().electricity_price == Decimal("0.3215")


def test_saving_redirects_so_reload_does_not_resend(client: TestClient) -> None:
    response = client.post("/settings", data=valid_form(), follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"


def test_saved_message_only_after_saving(client: TestClient) -> None:
    assert DE["settings.saved"] not in client.get("/settings").text


def test_all_fields_are_saved(client: TestClient) -> None:
    client.post(
        "/settings",
        data=valid_form(
            electricity_price="0,4",
            labour_rate="22,50",
            scrap_rate="7,5",
            markup="250",
            rounding="whole_euro",
            spool_category="  Firma  ",
            deviation_threshold="20",
        ),
    )

    row = stored()
    assert row.electricity_price == Decimal("0.4")
    assert row.labour_rate == Decimal("22.50")
    assert row.scrap_rate == Decimal("7.5")
    assert row.markup == Decimal("250")
    assert row.rounding == Rounding.WHOLE_EURO
    assert row.spool_category == "Firma"
    assert row.deviation_threshold == Decimal("20")


# --- Test case 10: comma and dot -------------------------------------------------------


@pytest.mark.parametrize("value", ["0,35", "0.35"])
def test_comma_and_dot_are_accepted(client: TestClient, value: str) -> None:
    client.post("/settings", data=valid_form(electricity_price=value))

    assert stored().electricity_price == Decimal("0.35")


# --- Test case 11: invalid input -------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "message_key"),
    [
        ("electricity_price", "abc", "settings.error.number"),
        ("electricity_price", "", "settings.error.required"),
        ("electricity_price", "   ", "settings.error.required"),
        ("electricity_price", "1.234,5", "settings.error.number"),
        ("electricity_price", "1e3", "settings.error.number"),
        ("electricity_price", "NaN", "settings.error.number"),
        ("electricity_price", "-0,30", "settings.error.negative"),
        ("labour_rate", "-1", "settings.error.negative"),
        ("scrap_rate", "100,01", "settings.error.too_large"),
        ("deviation_threshold", "101", "settings.error.too_large"),
        ("markup", "1000,01", "settings.error.too_large"),
        ("labour_rate", "15,001", "settings.error.decimals"),
        ("electricity_price", "0,32151", "settings.error.decimals"),
        ("scrap_rate", "5,123", "settings.error.decimals"),
        ("spool_category", "", "settings.error.required"),
        ("spool_category", "   ", "settings.error.required"),
        ("spool_category", "x" * 51, "settings.error.too_long"),
        ("rounding", "ends_99", "settings.error.choice"),
        ("rounding", "", "settings.error.choice"),
    ],
)
def test_invalid_input_is_rejected_with_message(
    client: TestClient, field: str, value: str, message_key: str
) -> None:
    # A changed, valid markup shows that valid fields are not saved either.
    form = {**valid_form(markup="45"), field: value}
    unchanged = Decimal("30")

    response = client.post("/settings", data=form)

    assert response.status_code == 422
    assert field_error(response.text, field) == DE[message_key]
    assert DE["settings.saved"] not in response.text
    # Nothing is saved, not even the valid fields.
    assert stored().markup == unchanged


def test_other_inputs_stay_in_the_form_after_an_error(client: TestClient) -> None:
    response = client.post(
        "/settings", data=valid_form(labour_rate="abc", markup="45", spool_category="Firma")
    )
    html = response.text

    assert field_value(html, "labour_rate") == "abc"
    assert field_value(html, "markup") == "45"
    assert field_value(html, "spool_category") == "Firma"
    assert field_error(html, "markup") is None
    assert re.search(r'<input\b[^>]*name="labour_rate"[^>]*aria-invalid="true"', html)


def test_missing_field_is_rejected(client: TestClient) -> None:
    form = valid_form()
    del form["labour_rate"]

    response = client.post("/settings", data=form)

    assert response.status_code == 422
    assert field_error(response.text, "labour_rate") == DE["settings.error.required"]


def test_entered_text_is_escaped(client: TestClient) -> None:
    response = client.post("/settings", data=valid_form(labour_rate='"><script>x</script>'))

    assert "<script>x</script>" not in response.text
    assert "&lt;script&gt;" in response.text


def test_boundaries_are_allowed(client: TestClient) -> None:
    response = client.post(
        "/settings",
        data=valid_form(
            electricity_price="0",
            scrap_rate="100",
            markup="1000",
            deviation_threshold="0",
            spool_category="x" * 50,
        ),
    )

    assert response.status_code == 200
    assert stored().markup == Decimal("1000")


# --- Test case 13: texts from translation files ----------------------------------------


def test_english_page(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAKERHUB_LANGUAGE", "en")
    en = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))

    with TestClient(create_app()) as client:
        html = client.get("/settings").text

    assert en["settings.title"] in html
    assert field_value(html, "electricity_price") == "0.30"


# --- Test case 14: saving from a foreign website ---------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://testserver.evil.example"},
        {"Sec-Fetch-Site": "cross-site", "Origin": "http://testserver"},
        {"Referer": "https://evil.example/form"},
    ],
)
def test_saving_from_foreign_site_is_rejected(client: TestClient, headers: dict) -> None:
    response = client.post("/settings", data=valid_form(markup="99"), headers=headers)

    assert response.status_code == 403
    assert response.text == DE["error.forbidden"]
    assert stored().markup == Decimal("30")
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"},
        {"Origin": "http://testserver"},
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "https://testserver"},
        {"Referer": "http://testserver/settings"},
        # A reverse proxy may rewrite the Host header; the browser's own statement counts.
        {"Sec-Fetch-Site": "same-origin", "Origin": "https://makerhub.example.com"},
    ],
)
def test_saving_from_the_app_itself_is_allowed(client: TestClient, headers: dict) -> None:
    response = client.post("/settings", data=valid_form(markup="99"), headers=headers)

    assert response.status_code == 200
    assert stored().markup == Decimal("99")

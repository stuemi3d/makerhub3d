"""Two-factor login in a real browser, on desktop and phone (issue #30, test case 20)."""

from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session

from app import auth, two_factor
from app.config import DATABASE_FILE
from app.db import make_engine
from app.models import UserRole
from app.two_factor import Cipher
from tests.browser.conftest import ADMIN_PASSWORD, TWO_FACTOR_KEY, LiveServer, t

USER_PASSWORD = "ein-zweites-test-passwort"


def fits_screen_width(page: Page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def add_two_factor_user(live_server: LiveServer, username: str) -> str:
    """Creates a user with two-factor login switched on; returns the secret."""
    secret = two_factor.new_secret()
    engine = make_engine(live_server.data_dir / DATABASE_FILE)
    with Session(engine) as db, db.begin():
        user = auth.create_user(
            db, username, USER_PASSWORD, UserRole.EMPLOYEE, must_change_password=False
        )
        user.totp_secret = Cipher(TWO_FACTOR_KEY).encrypt(secret)
    engine.dispose()
    return secret


def test_login_with_code(login_page: Page, live_server: LiveServer, viewport_name: str) -> None:
    username = f"zweifaktor-{viewport_name}"
    secret = add_two_factor_user(live_server, username)
    login_page.fill("#username", username)
    login_page.fill("#password", USER_PASSWORD)
    login_page.click(f"button[type=submit]:has-text('{t('login.submit')}')")

    expect(login_page.locator("h1")).to_have_text(t("login_code.title"))
    assert fits_screen_width(login_page)
    code = two_factor.code_for_step(secret, two_factor.time_step(auth.utcnow()))
    login_page.fill("#code", code)
    login_page.click(f"button[type=submit]:has-text('{t('login_code.submit')}')")

    expect(login_page.locator("h1")).to_have_text(t("nav.dashboard"))


def test_setup_page_shows_the_whole_qr_code(admin_page: Page) -> None:
    admin_page.goto("/account")
    admin_page.fill("#tfa_password", ADMIN_PASSWORD)
    admin_page.click(f"button[type=submit]:has-text('{t('two_factor.setup.start')}')")

    expect(admin_page.locator("h1")).to_have_text(t("two_factor.setup.title"))
    qr_code = admin_page.locator(".qr-code img")
    expect(qr_code).to_be_visible()
    box = qr_code.bounding_box()
    width = admin_page.viewport_size["width"]
    assert box is not None
    assert box["x"] >= 0 and box["x"] + box["width"] <= width
    # Big enough for a phone camera.
    assert box["width"] >= 150
    assert fits_screen_width(admin_page)

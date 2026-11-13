"""FastAPI application factory and routes."""

import asyncio
import contextlib
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from app import APP_NAME, audit, auth, backup, dashboard, two_factor
from app.account_two_factor import add_two_factor_routes
from app.article_pages import add_article_routes
from app.backup_pages import add_backup_routes
from app.config import Settings
from app.db import make_engine, run_migrations
from app.i18n import translate
from app.log_pages import add_log_routes
from app.models import AppSettings, Rounding, User, UserRole
from app.permissions import Permission, PermissionDenied, has_permission, requires
from app.settings_form import FIELD_NAMES, form_values, validate
from app.stock_pages import add_stock_routes
from app.template_pages import add_template_routes
from app.user_admin import add_user_admin_routes
from app.variant_bulk import add_variant_bulk_routes
from app.variant_pages import add_variant_routes

APP_DIR = Path(__file__).parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_name"] = APP_NAME

# Navigation from the plan (section 5). Entries without href are not built yet; entries are
# only shown to roles with the given permission.
NAV_ITEMS = (
    {"key": "nav.dashboard", "href": "/", "permission": Permission.VIEW},
    {"key": "nav.articles", "href": "/articles", "permission": Permission.VIEW},
    {"key": "nav.calculation", "href": None, "permission": Permission.VIEW},
    {"key": "nav.production", "href": None, "permission": Permission.VIEW},
    {"key": "nav.stock", "href": "/stock", "permission": Permission.VIEW},
    {"key": "nav.analysis", "href": None, "permission": Permission.VIEW},
    {"key": "nav.settings", "href": "/settings", "permission": Permission.MANAGE},
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Reachable without login (agreed in issue #8): health check and style files carry no data.
PUBLIC_PATHS = frozenset({"/healthz"})
PUBLIC_PREFIX = "/static/"
# Reachable without login, but still with form token: login and setup of the first admin.
LOGIN_PATHS = frozenset({"/login", "/login/code", "/setup"})
FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str
    must_change_password: bool


# While an initial password must be changed, only these pages are open (issue #32).
CHANGE_PASSWORD_PATHS = frozenset({"/account", "/account/password", "/logout"})


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


async def form_token_ok(request: Request, expected: str | None) -> bool:
    """Checks the form token (CSRF) of a form sent to the app."""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith(FORM_CONTENT_TYPES):
        return False
    # Reading the body first keeps it available for the route behind this middleware.
    await request.body()
    token = (await request.form()).get("csrf_token")
    return isinstance(token, str) and auth.tokens_match(expected, token)


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def is_foreign_request(request: Request) -> bool:
    """True when a browser sends a form from another website (cross-site request forgery).

    An extra layer in front of the form tokens. In the fallback only the host is compared,
    not the scheme: behind the reverse proxy the app sees plain http.
    Requests without any of these headers do not come from a browser form and are allowed.
    """
    # Modern browsers send Sec-Fetch-Site; it alone decides, so that a reverse proxy which
    # rewrites the Host header does not block the app's own forms.
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site not in {"same-origin", "none"}
    # Fallback for older browsers: compare Origin (or Referer) with the Host header.
    own_host = request.headers.get("host", "").lower()
    source = request.headers.get("origin") or request.headers.get("referer")
    if source is None:
        return False
    return not own_host or _host_of(source) != own_host


def create_app(settings: Settings | None = None) -> FastAPI:
    # Reading the settings here makes an invalid configuration stop the start.
    settings = settings or Settings.from_env()
    language = settings.language
    # No file is opened here; the database is created and migrated when the app starts.
    engine = make_engine(settings.database_path)
    new_session = sessionmaker(engine)
    secure_cookies = not settings.insecure_cookies
    session_cookie, csrf_cookie = auth.cookie_names(secure_cookies)
    # Cookie of a pending two-factor login (right password, code not yet entered).
    pending_cookie = ("__Host-" if secure_cookies else "") + "makerhub_2fa"
    cipher = two_factor.Cipher(settings.two_factor_key) if settings.two_factor_key else None
    # Only one first admin, even if two setup forms arrive at the same moment.
    setup_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # An existing database that is behind gets a backup first; if that fails, no start.
        run_migrations(engine, lambda: backup.backup_before_migration(settings))
        with new_session() as db:
            users_exist = auth.has_users(db)
            key_matches = auth.two_factor_key_matches(db, cipher)
        if not key_matches:
            # Running on would lock out every two-factor user, or (without a check) turn
            # their second factor off unnoticed. The message names no secret values.
            print(translate(language, "two_factor.key_mismatch"), file=sys.stderr, flush=True)
            engine.dispose()
            raise RuntimeError("MAKERHUB_2FA_KEY does not match the stored two-factor secrets")
        # A new code on every start while nobody is set up; it goes to the container log.
        app.state.setup_code = None if users_exist else auth.new_setup_code()
        if app.state.setup_code:
            message = translate(language, "setup.log_code").format(code=app.state.setup_code)
            print(message, file=sys.stderr, flush=True)
        nightly = asyncio.create_task(backup.nightly_loop(settings))
        yield
        nightly.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await nightly
        engine.dispose()

    # The generated API docs are disabled: they would be reachable without login.
    app = FastAPI(
        title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.setup_code = None

    def render(
        request: Request, template: str, current_nav: str, status_code: int = 200, **context
    ) -> HTMLResponse:
        user = getattr(request.state, "user", None)
        return templates.TemplateResponse(
            request,
            template,
            {
                "language": language,
                "t": lambda key: translate(language, key),
                "nav_items": NAV_ITEMS,
                "current_nav": current_nav,
                "user": user,
                "can": lambda permission: (
                    user is not None and has_permission(user.role, permission)
                ),
                "csrf_token": getattr(request.state, "csrf_token", ""),
                **context,
            },
            status_code=status_code,
        )

    def set_cookie(response: Response, name: str, value: str) -> None:
        response.set_cookie(
            name, value, path="/", secure=secure_cookies, httponly=True, samesite="lax"
        )

    def delete_cookie(response: Response, name: str) -> None:
        response.delete_cookie(name, path="/", secure=secure_cookies, httponly=True, samesite="lax")

    def load_login(token: str | None) -> tuple[CurrentUser | None, str | None, bool]:
        with new_session() as db, db.begin():
            found = auth.find_session(db, token)
            if found is None:
                return None, None, auth.has_users(db)
            login, user = found
            # Read on every request, so that a changed role works at once.
            current = CurrentUser(user.id, user.username, user.role, user.must_change_password)
            return current, login.csrf_token, True

    # Every page needs a login, except the agreed exceptions. The middlewares defined later
    # wrap this one, so its answers also get the checks and headers below.
    @app.middleware("http")
    async def require_login(request: Request, call_next) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIX):
            return await call_next(request)
        user, csrf_token, users_exist = await run_in_threadpool(
            load_login, request.cookies.get(session_cookie)
        )
        new_csrf_cookie = None
        if user is None:
            # Before login, the form token lives in its own cookie (login and setup forms).
            csrf_token = request.cookies.get(csrf_cookie)
            if not csrf_token or len(csrf_token) > 100:
                csrf_token = new_csrf_cookie = auth.new_token()
            if not users_exist and path != "/setup":
                return redirect("/setup")
            if users_exist and path not in LOGIN_PATHS:
                target = "/login"
                if request.method == "GET":
                    wanted = path + (f"?{request.url.query}" if request.url.query else "")
                    target += "?" + urlencode({"next": wanted})
                return redirect(target)
        elif path == "/login" and request.method == "POST":
            # Already logged in, e.g. from another tab: the old login form is not needed.
            return redirect("/")
        elif user.must_change_password and path not in CHANGE_PASSWORD_PATHS:
            return redirect("/account")
        request.state.user = user
        request.state.csrf_token = csrf_token
        if request.method not in SAFE_METHODS and not await form_token_ok(request, csrf_token):
            return PlainTextResponse(translate(language, "error.forbidden"), status_code=403)
        response = await call_next(request)
        if new_csrf_cookie:
            set_cookie(response, csrf_cookie, new_csrf_cookie)
        return response

    # Defined before the header middleware below, so that its 403 answer gets those headers.
    @app.middleware("http")
    async def reject_foreign_forms(request: Request, call_next) -> Response:
        if request.method not in SAFE_METHODS and is_foreign_request(request):
            return PlainTextResponse(translate(language, "error.forbidden"), status_code=403)
        return await call_next(request)

    # Embedding (iframe) is allowed only for the configured origins, on every response.
    if settings.embed_origins:
        frame_ancestors = "frame-ancestors 'self' " + " ".join(settings.embed_origins)
    else:
        frame_ancestors = "frame-ancestors 'none'"

    @app.middleware("http")
    async def embedding_headers(request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = frame_ancestors
        if not settings.embed_origins:
            # X-Frame-Options cannot name origins, so it is only set when nobody may embed.
            response.headers["X-Frame-Options"] = "DENY"
        return response

    # Unhandled errors are answered outside the middleware above, so set the header here too.
    # The body stays generic: no internal details.
    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> Response:
        headers = {"Content-Security-Policy": frame_ancestors}
        if not settings.embed_origins:
            headers["X-Frame-Options"] = "DENY"
        return PlainTextResponse("Internal Server Error", status_code=500, headers=headers)

    @app.exception_handler(PermissionDenied)
    async def permission_denied(request: Request, exc: PermissionDenied) -> Response:
        return render(request, "forbidden.html", "", status_code=403)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def logged_in(response: Response, token: str) -> Response:
        set_cookie(response, session_cookie, token)
        delete_cookie(response, csrf_cookie)
        return response

    async def read_form(request: Request, names: tuple[str, ...]) -> dict[str, str]:
        form = await request.form()
        return {name: value for name in names if isinstance(value := form.get(name), str)}

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page(request: Request) -> HTMLResponse:
        with new_session() as db:
            if auth.has_users(db):
                raise HTTPException(status_code=404)
        return render(request, "setup.html", "", values={}, errors={})

    def create_first_admin(raw: dict[str, str]) -> tuple[dict[str, str], str | None]:
        """Returns the errors, or no errors and the session token for the new admin."""
        with setup_lock, new_session() as db, db.begin():
            if auth.has_users(db):
                raise HTTPException(status_code=404)
            errors = auth.validate_setup(raw, app.state.setup_code)
            if errors:
                return errors, None
            # The first admin chose the password, so there is nothing to change.
            user = auth.create_user(
                db, raw["username"], raw["password"], UserRole.ADMIN, must_change_password=False
            )
            audit.record_change(db, user.id, "setup.admin_created", user.username)
            token = auth.start_session(db, user)
        app.state.setup_code = None
        return {}, token

    @app.post("/setup", response_class=HTMLResponse)
    async def setup(request: Request) -> Response:
        raw = await read_form(request, auth.SETUP_FIELDS)
        errors, token = await run_in_threadpool(create_first_admin, raw)
        if token is None:
            # Passwords and the code are never sent back; only the name stays in the form.
            values = {"username": raw.get("username", "")}
            return render(request, "setup.html", "", status_code=422, values=values, errors=errors)
        return logged_in(redirect("/"), token)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "") -> Response:
        if request.state.user is not None:
            return redirect("/")
        return render(request, "login.html", "", username="", next_url=next, failed=False)

    def try_login(raw: dict[str, str]) -> auth.LoginStep | None:
        return auth.log_in(new_session, raw.get("username", ""), raw.get("password", ""))

    def with_next(path: str, next_url: str) -> str:
        return path + ("?" + urlencode({"next": next_url}) if next_url else "")

    async def finish_login(request: Request, token: str, next_url: str) -> Response:
        # An earlier login in this browser ends; the new one gets a fresh token.
        old_token = request.cookies.get(session_cookie)
        if old_token:
            await run_in_threadpool(end_login, old_token)
        response = logged_in(redirect(auth.safe_next(next_url)), token)
        delete_cookie(response, pending_cookie)
        return response

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request) -> Response:
        raw = await read_form(request, ("username", "password", "next"))
        step = await run_in_threadpool(try_login, raw)
        if step is None:
            return render(
                request,
                "login.html",
                "",
                status_code=401,
                username=raw.get("username", ""),
                next_url=raw.get("next", ""),
                failed=True,
            )
        next_url = raw.get("next", "")
        if step.needs_code:
            response = redirect(with_next("/login/code", next_url))
            set_cookie(response, pending_cookie, step.token)
            return response
        return await finish_login(request, step.token, next_url)

    def back_to_login() -> Response:
        response = redirect("/login")
        delete_cookie(response, pending_cookie)
        return response

    @app.get("/login/code", response_class=HTMLResponse)
    async def login_code_page(request: Request, next: str = "") -> Response:
        if request.state.user is not None:
            return redirect("/")
        token = request.cookies.get(pending_cookie)
        if not await run_in_threadpool(auth.pending_login_exists, new_session, token):
            return back_to_login()
        return render(request, "login_code.html", "", next_url=next, failed=False)

    @app.post("/login/code", response_class=HTMLResponse)
    async def login_code(request: Request) -> Response:
        if request.state.user is not None:
            return redirect("/")
        raw = await read_form(request, ("code", "next"))
        token, restart = await run_in_threadpool(
            auth.confirm_login_code,
            new_session,
            cipher,
            request.cookies.get(pending_cookie),
            raw.get("code", ""),
        )
        if restart:
            return back_to_login()
        if token is None:
            # The typed code is never sent back.
            next_url = raw.get("next", "")
            return render(
                request, "login_code.html", "", status_code=401, next_url=next_url, failed=True
            )
        return await finish_login(request, token, raw.get("next", ""))

    def end_login(token: str) -> None:
        with new_session() as db, db.begin():
            auth.end_session(db, token)

    def log_out(user_id: int, token: str) -> None:
        with new_session() as db, db.begin():
            auth.end_session(db, token)
            audit.record_login(db, user_id, "logout")

    @app.post("/logout", dependencies=[requires(Permission.VIEW)])
    async def logout(request: Request) -> Response:
        token = request.cookies.get(session_cookie, "")
        await run_in_threadpool(log_out, request.state.user.id, token)
        response = redirect("/login")
        delete_cookie(response, session_cookie)
        return response

    @app.get("/", response_class=HTMLResponse, dependencies=[requires(Permission.VIEW)])
    def index(request: Request) -> HTMLResponse:
        with new_session() as session:
            data = dashboard.build(session)
        return render(request, "index.html", "nav.dashboard", data=data)

    # Login is checked by the middleware above, the role by the dependency of each route.
    @app.get("/settings", response_class=HTMLResponse, dependencies=[requires(Permission.MANAGE)])
    def settings_page(request: Request, saved: str | None = None) -> HTMLResponse:
        with new_session() as session:
            values = form_values(session.get(AppSettings, 1), language)
        return render(
            request,
            "settings.html",
            "nav.settings",
            values=values,
            errors={},
            saved=saved == "1",
            roundings=list(Rounding),
        )

    @app.post("/settings", response_class=HTMLResponse, dependencies=[requires(Permission.MANAGE)])
    async def save_settings(request: Request) -> Response:
        form = await request.form()
        raw = {name: value for name in FIELD_NAMES if isinstance(value := form.get(name), str)}
        values, errors = validate(raw)
        if errors:
            return render(
                request,
                "settings.html",
                "nav.settings",
                status_code=422,
                values={name: raw.get(name, "") for name in FIELD_NAMES},
                errors=errors,
                saved=False,
                roundings=list(Rounding),
            )
        with new_session() as session, session.begin():
            row = session.get(AppSettings, 1)
            old = {name: getattr(row, name) for name in values}
            changed = audit.field_changes(old, values)
            for name, value in values.items():
                setattr(row, name, value)
            # Saving without any change leaves no entry in the change log.
            if changed:
                audit.record_change(session, request.state.user.id, "settings.changed", "", changed)
        # Redirect after saving, so that reloading the page does not send the form again.
        return RedirectResponse("/settings?saved=1", status_code=303)

    def two_factor_status(user_id: int) -> dict:
        with new_session() as db:
            user = db.get(User, user_id)
            enabled = user is not None and user.totp_secret is not None
            return {
                "available": cipher is not None,
                "enabled": enabled,
                "codes_left": auth.recovery_codes_left(db, user_id) if enabled else 0,
            }

    def render_account(request: Request, status_code: int = 200, **context) -> HTMLResponse:
        context.setdefault("errors", {})
        status = two_factor_status(request.state.user.id)
        return render(
            request, "account.html", "", status_code=status_code, two_factor=status, **context
        )

    def logged_out_response() -> Response:
        response = redirect("/login")
        delete_cookie(response, session_cookie)
        return response

    @app.get("/account", response_class=HTMLResponse, dependencies=[requires(Permission.VIEW)])
    def account_page(request: Request, changed: str | None = None) -> HTMLResponse:
        # "changed" names what was saved: 1 = password, others come from two-factor pages.
        return render_account(request, changed=changed)

    @app.post(
        "/account/password", response_class=HTMLResponse, dependencies=[requires(Permission.VIEW)]
    )
    async def change_password(request: Request) -> Response:
        raw = await read_form(request, ("current_password", "password", "password_repeat"))
        token = request.cookies.get(session_cookie, "")
        errors, logged_out = await run_in_threadpool(
            auth.change_own_password, new_session, request.state.user.id, token, raw
        )
        if logged_out:
            return logged_out_response()
        if errors:
            # Passwords are never sent back.
            return render_account(request, status_code=422, errors=errors)
        return redirect("/account?changed=1")

    add_two_factor_routes(
        app, new_session, cipher, render, render_account, read_form, logged_out_response
    )
    add_user_admin_routes(app, new_session, render, read_form)
    add_log_routes(app, new_session, render, language, settings.timezone)
    add_backup_routes(app, new_session, render, settings)
    add_article_routes(app, new_session, render, read_form, language)
    add_template_routes(app, new_session, render, read_form, language)
    add_variant_routes(app, new_session, render, read_form, language)
    add_variant_bulk_routes(app, new_session, render, read_form)
    add_stock_routes(app, new_session, render, read_form, language)

    # Deliberately without login: the container health check calls it. Returns no details.
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


app = create_app()

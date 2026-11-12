"""FastAPI application factory and routes."""

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

from app import APP_NAME, auth
from app.config import Settings
from app.db import make_engine, run_migrations
from app.i18n import translate
from app.models import AppSettings, Rounding
from app.settings_form import FIELD_NAMES, form_values, validate

APP_DIR = Path(__file__).parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_name"] = APP_NAME

# Navigation from the plan (section 5). Entries without href are not built yet.
NAV_ITEMS = (
    {"key": "nav.dashboard", "href": "/"},
    {"key": "nav.articles", "href": None},
    {"key": "nav.calculation", "href": None},
    {"key": "nav.production", "href": None},
    {"key": "nav.stock", "href": None},
    {"key": "nav.analysis", "href": None},
    {"key": "nav.settings", "href": "/settings"},
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# Reachable without login (agreed in issue #8): health check and style files carry no data.
PUBLIC_PATHS = frozenset({"/healthz"})
PUBLIC_PREFIX = "/static/"
# Reachable without login, but still with form token: login and setup of the first admin.
LOGIN_PATHS = frozenset({"/login", "/setup"})
FORM_CONTENT_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    role: str


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
    # Only one first admin, even if two setup forms arrive at the same moment.
    setup_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        run_migrations(engine)
        with new_session() as db:
            users_exist = auth.has_users(db)
        # A new code on every start while nobody is set up; it goes to the container log.
        app.state.setup_code = None if users_exist else auth.new_setup_code()
        if app.state.setup_code:
            message = translate(language, "setup.log_code").format(code=app.state.setup_code)
            print(message, file=sys.stderr, flush=True)
        yield
        engine.dispose()

    # The generated API docs are disabled: they would be reachable without login.
    app = FastAPI(
        title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )
    app.state.setup_code = None

    def render(
        request: Request, template: str, current_nav: str, status_code: int = 200, **context
    ) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            template,
            {
                "language": language,
                "t": lambda key: translate(language, key),
                "nav_items": NAV_ITEMS,
                "current_nav": current_nav,
                "user": getattr(request.state, "user", None),
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
            return CurrentUser(user.id, user.username, user.role), login.csrf_token, True

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
            user = auth.create_admin(db, raw["username"], raw["password"])
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

    def try_login(raw: dict[str, str]) -> str | None:
        with new_session() as db, db.begin():
            user = auth.check_login(db, raw.get("username", ""), raw.get("password", ""))
            return None if user is None else auth.start_session(db, user)

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request) -> Response:
        raw = await read_form(request, ("username", "password", "next"))
        token = await run_in_threadpool(try_login, raw)
        if token is None:
            return render(
                request,
                "login.html",
                "",
                status_code=401,
                username=raw.get("username", ""),
                next_url=raw.get("next", ""),
                failed=True,
            )
        # An earlier login in this browser ends; the new one gets a fresh token.
        old_token = request.cookies.get(session_cookie)
        if old_token:
            await run_in_threadpool(end_login, old_token)
        return logged_in(redirect(auth.safe_next(raw.get("next"))), token)

    def end_login(token: str) -> None:
        with new_session() as db, db.begin():
            auth.end_session(db, token)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        await run_in_threadpool(end_login, request.cookies.get(session_cookie, ""))
        response = redirect("/login")
        delete_cookie(response, session_cookie)
        return response

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render(request, "index.html", "nav.dashboard")

    # Login is checked by the middleware above.
    # TODO(#28): allow both settings routes for the admin role only.
    @app.get("/settings", response_class=HTMLResponse)
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

    @app.post("/settings", response_class=HTMLResponse)
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
            for name, value in values.items():
                setattr(row, name, value)
        # Redirect after saving, so that reloading the page does not send the form again.
        return RedirectResponse("/settings?saved=1", status_code=303)

    # Deliberately without login: the container health check calls it. Returns no details.
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


app = create_app()

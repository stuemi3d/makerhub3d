"""FastAPI application factory and routes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import sessionmaker

from app import APP_NAME
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


def _host_of(url: str) -> str:
    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def is_foreign_request(request: Request) -> bool:
    """True when a browser sends a form from another website (cross-site request forgery).

    Preliminary protection until login and CSRF tokens arrive (issue #8). Only the host is
    compared, not the scheme: behind the reverse proxy the app itself sees plain http.
    Requests without any of these headers do not come from a browser form and are allowed.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in {"same-origin", "none"}:
        return True
    own_host = request.headers.get("host", "").lower()
    origin = request.headers.get("origin")
    if origin is not None:
        return not own_host or _host_of(origin) != own_host
    referer = request.headers.get("referer")
    if fetch_site is None and referer is not None:
        return not own_host or _host_of(referer) != own_host
    return False


def create_app(settings: Settings | None = None) -> FastAPI:
    # Reading the settings here makes an invalid configuration stop the start.
    settings = settings or Settings.from_env()
    language = settings.language
    # No file is opened here; the database is created and migrated when the app starts.
    engine = make_engine(settings.database_path)
    new_session = sessionmaker(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        run_migrations(engine)
        yield
        engine.dispose()

    # The generated API docs are disabled: they would be reachable without login.
    app = FastAPI(
        title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan
    )

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
                **context,
            },
            status_code=status_code,
        )

    # Defined before the header middleware below, so that its 403 answer gets those headers.
    @app.middleware("http")
    async def reject_foreign_forms(request: Request, call_next) -> Response:
        if request.method not in SAFE_METHODS and is_foreign_request(request):
            return PlainTextResponse("Forbidden", status_code=403)
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

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render(request, "index.html", "nav.dashboard")

    # TODO(#8): require login and the admin role for both settings routes.
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

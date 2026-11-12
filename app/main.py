"""FastAPI application factory and routes."""

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import APP_NAME
from app.config import Settings
from app.i18n import translate

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
    {"key": "nav.settings", "href": None},
)


def create_app(settings: Settings | None = None) -> FastAPI:
    # Reading the settings here makes an invalid configuration stop the start.
    settings = settings or Settings.from_env()
    language = settings.language

    # The generated API docs are disabled: they would be reachable without login.
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)

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
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "language": language,
                "t": lambda key: translate(language, key),
                "nav_items": NAV_ITEMS,
                "current_nav": "nav.dashboard",
            },
        )

    # Deliberately without login: the container health check calls it. Returns no details.
    @app.get("/healthz", response_class=PlainTextResponse)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    return app


app = create_app()

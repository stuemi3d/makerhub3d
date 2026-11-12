"""FastAPI application factory and routes."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import APP_NAME

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["app_name"] = APP_NAME


def create_app() -> FastAPI:
    # The generated API docs are disabled: they would be reachable without login.
    app = FastAPI(title=APP_NAME, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html")

    return app


app = create_app()

import pytest
from fastapi.testclient import TestClient

from app import APP_NAME


def test_start_page_shows_app_name(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert APP_NAME in response.text


def test_app_name_is_makerhub_3d() -> None:
    assert APP_NAME == "MakerHub 3D"


def test_unknown_path_returns_404_without_details(client: TestClient) -> None:
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert "Traceback" not in response.text


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"])
def test_generated_developer_pages_are_disabled(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404

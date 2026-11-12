from fastapi.testclient import TestClient


def test_healthz_answers_ok_without_login(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.text == "ok"


def test_healthz_reveals_no_internal_details(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.headers["content-type"].startswith("text/plain")
    assert len(response.text) <= 2

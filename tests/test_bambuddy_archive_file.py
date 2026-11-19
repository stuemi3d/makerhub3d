"""Reading the file of an archive entry from Bambuddy (issue #214, test case 8): read-only,
through the one client, with a size limit. Only invented bytes and a mock transport; the real
API is never called.
"""

import httpx2
import pytest

from app.bambuddy import BambuddyClient, BambuddyError

BASE = "http://bambuddy.example.com:8000"
KEY = "test-key-nur-fuer-tests"


def client(handler) -> BambuddyClient:
    return BambuddyClient(BASE, KEY, transport=httpx2.MockTransport(handler))


def test_file_is_fetched_with_get_and_the_key() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"PK\x03\x04data")

    assert client(handler).archive_file(260) == b"PK\x03\x04data"
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/api/v1/archives/260/download"
    assert request.headers["X-API-Key"] == KEY


def test_too_large_file_is_refused() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"x" * 100)

    with pytest.raises(BambuddyError) as error:
        client(handler).archive_file(1, max_bytes=50)
    assert error.value.code == "bad_response"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "rejected"),
        (403, "forbidden"),
        (404, "not_found"),
        (418, "bad_response"),
        (500, "server_error"),
    ],
)
def test_errors_carry_only_a_code(status: int, code: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, text=f"secret {KEY} detail")

    with pytest.raises(BambuddyError) as error:
        client(handler).archive_file(1)
    assert error.value.code == code
    assert KEY not in str(error.value) and "secret" not in str(error.value)


def test_no_writing_methods_exist() -> None:
    names = [n for n in dir(BambuddyClient) if not n.startswith("__")]
    assert not [n for n in names if n.startswith(("post", "put", "delete", "patch"))]

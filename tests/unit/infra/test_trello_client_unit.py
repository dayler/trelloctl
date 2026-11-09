"""Unit tests for the Trello client using the transport stub."""

from __future__ import annotations

import logging
import urllib.error
from typing import Any

import pytest
from tests.fakes.transport import StubTransport, response

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.trello_client import DEFAULT_BASE_URL, TrelloClient

KEY = "k" * 8
TOKEN = "t" * 12


def client(stub: StubTransport, **kwargs: Any) -> TrelloClient:
    return TrelloClient("https://api.trello.com/1/", KEY, TOKEN, 7, transport=stub, **kwargs)


def code_of(call: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        call()
    return info.value


def test_builds_url_with_params_and_passes_timeout() -> None:
    stub = StubTransport(response(200, '{"ok": true}'))
    result = client(stub).get("/boards/b1", {"fields": "name", "closed": False, "skip": None})
    assert result == {"ok": True}
    url = stub.requests[0].url
    assert url.startswith("https://api.trello.com/1/boards/b1?")
    assert f"key={KEY}" in url
    assert f"token={TOKEN}" in url
    assert "fields=name" in url
    assert "closed=false" in url
    assert "skip" not in url
    assert stub.timeouts == [7.0]


def test_verbs_and_empty_body() -> None:
    stub = StubTransport(*[response(200, b"") for _ in range(4)])
    c = client(stub)
    assert c.post("/a") is None
    assert c.put("/a") is None
    assert c.delete("/a") is None
    assert c.request("get", "/a") is None
    assert [r.method for r in stub.requests] == ["POST", "PUT", "DELETE", "GET"]


def test_empty_base_url_defaults() -> None:
    c = TrelloClient("", KEY, TOKEN, 1, transport=StubTransport())
    assert c.base_url == DEFAULT_BASE_URL
    assert c.secrets == (KEY, TOKEN)


def test_timeout_variants() -> None:
    for failure in (TimeoutError(), urllib.error.URLError(TimeoutError())):
        assert code_of(lambda f=failure: client(StubTransport(f)).get("/x")).code == "TIMEOUT"


def test_unreadable_responses_and_masking_of_failure_reason() -> None:
    error = code_of(lambda: client(StubTransport(OSError(f"boom {TOKEN}"))).get("/x"))
    assert error.code == "NETWORK_ERROR"
    assert TOKEN not in str(error.details)


def test_unknown_rate_limit_name_uses_the_header_family_present() -> None:
    headers = {"x-rate-limit-api-key-remaining": "4", "x-rate-limit-api-key-interval-ms": "900"}
    stub = StubTransport(response(429, "SOMETHING_ELSE", headers))
    error = code_of(lambda: client(stub).get("/x"))
    assert error.code == "RATE_LIMITED"
    assert error.details["limit"] is None
    assert error.details["remaining"] == 4
    assert error.details["retry_in_seconds"] == 1


def test_rate_limit_ignores_malformed_header_values() -> None:
    headers = {
        "x-rate-limit-api-token-remaining": "abc",
        "x-rate-limit-api-token-interval-ms": "1500",
    }
    stub = StubTransport(response(429, "API_TOKEN_LIMIT_EXCEEDED", headers))
    error = code_of(lambda: client(stub).get("/x"))
    assert error.details["remaining"] is None
    assert error.details["retry_in_seconds"] == 2


def test_download_failure_translates_status() -> None:
    stub = StubTransport(response(404, "not found"))
    error = code_of(
        lambda: client(stub).download("https://trello.com/1/cards/c/attachments/a/download/f")
    )
    assert error.code == "API_ERROR"
    assert error.details["status"] == 404


def test_download_url_query_is_preserved_and_oauth_header_set() -> None:
    stub = StubTransport(response(200, b"data"))
    c = client(stub)
    assert c.download("https://api.trello.com/1/cards/c/attachments/a/download/f?x=1") == b"data"
    request = stub.requests[0]
    assert request.url == "https://api.trello.com/1/cards/c/attachments/a/download/f?x=1"
    assert request.headers["Authorization"].startswith("OAuth oauth_consumer_key=")


def test_upload_without_name_has_no_name_field() -> None:
    stub = StubTransport(response(200, '{"id": "x"}'))
    result = client(stub).upload("/cards/c/attachments", filename='we"ird.txt', content=b"abc")
    body = stub.requests[0].body or b""
    assert result == {"id": "x"}
    assert b'name="name"' not in body
    assert b'filename="we%22ird.txt"' in body


def test_every_call_logs_one_debug_line(caplog: pytest.LogCaptureFixture) -> None:
    ticks = iter([1.0, 1.25, 2.0, 2.5])
    stub = StubTransport(response(200, "{}"), TimeoutError())
    c = client(stub, monotonic=lambda: next(ticks))
    with caplog.at_level(logging.DEBUG, logger="trelloctl"):
        c.get("/ok")
        code_of(lambda: c.get("/slow"))
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "http GET /ok status=200 duration_ms=250",
        "http GET /slow status=error duration_ms=500",
    ]

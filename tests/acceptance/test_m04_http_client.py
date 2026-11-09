"""M04 acceptance tests: the Trello HTTP client against the fake Trello server."""

from __future__ import annotations

import ast
import inspect
import json
import logging
import re
import socket
import ssl
import urllib.error
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.transport import StubTransport, response
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import closed_port

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra import trello_client
from trelloctl.infra.logging import LOG_FILE, LOGGER_NAME, configure_logging, shutdown_logging
from trelloctl.infra.trello_client import TransportRequest, TrelloClient, urllib_transport

DOWNLOAD = "/cards/c1/attachments/a1/download/file.txt"


@pytest.fixture(autouse=True)
def _close_logging() -> Iterator[None]:
    yield
    shutdown_logging()


def make_client(fake: FakeTrello, timeout: float = 5, **kwargs: Any) -> TrelloClient:
    return TrelloClient(fake.base_url, fake.api_key, fake.token, timeout, **kwargs)


def raises(code: str, call: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        call()
    assert info.value.code == code
    return info.value


@pytest.mark.tc("TC-M04-001")
def test_requests_reach_the_configured_base_url(fake_trello: FakeTrello) -> None:
    client = make_client(fake_trello)
    assert client.get("/members/me")["username"] == "tester"
    client.get("/members/me", {"fields": "username"})
    assert [r.raw_path for r in fake_trello.requests] == ["/1/members/me", "/1/members/me"]
    assert fake_trello.requests[0].param("key") == fake_trello.api_key
    assert fake_trello.requests[0].param("token") == fake_trello.token


@pytest.mark.tc("TC-M04-002")
def test_server_error_is_api_error_with_a_single_request(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/members/me", 500, "boom")
    error = raises("API_ERROR", lambda: make_client(fake_trello).get("/members/me"))
    assert error.details["status"] == 500
    assert len(fake_trello.requests) == 1


@pytest.mark.parametrize(
    ("limit", "prefix", "headers"),
    [
        pytest.param(
            "API_TOKEN_LIMIT_EXCEEDED",
            "api-token",
            (100, 0, 10000),
            marks=pytest.mark.tc("TC-M04-003"),
        ),
        pytest.param(
            "API_KEY_LIMIT_EXCEEDED",
            "api-key",
            (300, 7, 20000),
            marks=pytest.mark.tc("TC-M04-004"),
        ),
        pytest.param(
            "API_TOKEN_DB_LIMIT_EXCEEDED",
            "api-token",
            (50, 3, 5500),
            marks=pytest.mark.tc("TC-M04-005"),
        ),
    ],
)
def test_rate_limit_details_come_from_the_headers(
    fake_trello: FakeTrello, limit: str, prefix: str, headers: tuple[int, int, int]
) -> None:
    maximum, remaining, interval = headers
    fake_trello.fail_next(
        "GET",
        "/members/me",
        429,
        limit,
        {
            f"x-rate-limit-{prefix}-max": str(maximum),
            f"x-rate-limit-{prefix}-remaining": str(remaining),
            f"x-rate-limit-{prefix}-interval-ms": str(interval),
        },
    )
    error = raises("RATE_LIMITED", lambda: make_client(fake_trello).get("/members/me"))
    assert error.message == "Too many requests"
    assert error.details["limit"] == limit
    assert error.details["remaining"] == remaining
    assert error.details["interval_ms"] == interval
    assert error.details["retry_in_seconds"] == -(-interval // 1000)
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M04-006")
def test_rate_limit_without_headers_leaves_details_null(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/members/me", 429, "API_TOKEN_LIMIT_EXCEEDED")
    error = raises("RATE_LIMITED", lambda: make_client(fake_trello).get("/members/me"))
    assert error.details["remaining"] is None
    assert error.details["interval_ms"] is None
    assert error.details["retry_in_seconds"] is None


@pytest.mark.tc("TC-M04-007")
def test_closed_port_is_network_error() -> None:
    client = TrelloClient(f"http://127.0.0.1:{closed_port()}/1", "k", "t", 5)
    raises("NETWORK_ERROR", lambda: client.get("/members/me"))


@pytest.mark.tc("TC-M04-008")
def test_dns_and_tls_failures_are_network_errors() -> None:
    dns = urllib.error.URLError(socket.gaierror(11001, "getaddrinfo failed"))
    tls = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    for failure, kind in ((dns, "network"), (tls, "tls")):
        stub = StubTransport(failure)
        client = TrelloClient("https://api.trello.com/1", "k", "t", 5, transport=stub)
        error = raises("NETWORK_ERROR", lambda client=client: client.get("/members/me"))
        assert error.details["kind"] == kind
        assert len(stub.requests) == 1


@pytest.mark.tc("TC-M04-009")
def test_slow_server_is_timeout(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/members/me", None, delay=3.0)
    client = make_client(fake_trello, timeout=1)
    raises("TIMEOUT", lambda: client.get("/members/me"))
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M04-010")
def test_generic_client_errors_are_api_errors(fake_trello: FakeTrello) -> None:
    for status in (400, 404, 422):
        fake_trello.fail_next("GET", "/members/me", status, "nope")
        error = raises("API_ERROR", lambda: make_client(fake_trello).get("/members/me"))
        assert error.details["status"] == status


@pytest.mark.tc("TC-M04-011")
def test_credential_rejections_are_auth_invalid(fake_trello: FakeTrello) -> None:
    bad_key = TrelloClient(fake_trello.base_url, "wrong", fake_trello.token, 5)
    assert raises("AUTH_INVALID", lambda: bad_key.get("/members/me")).details["upstream"] == (
        "invalid key"
    )
    bad_token = TrelloClient(fake_trello.base_url, fake_trello.api_key, "wrong", 5)
    assert raises("AUTH_INVALID", lambda: bad_token.get("/members/me")).details["upstream"] == (
        "invalid token"
    )
    fake_trello.fail_next("GET", "/members/me", 401, "unauthorized")
    raises("AUTH_INVALID", lambda: make_client(fake_trello).get("/members/me"))
    assert len(fake_trello.requests) == 3


@pytest.mark.tc("TC-M04-012")
def test_401_and_403_on_a_resource_are_api_errors(fake_trello: FakeTrello) -> None:
    for status, body in ((401, "unauthorized board permission requested"), (403, "forbidden")):
        fake_trello.fail_next("GET", "/boards/b1", status, body)
        error = raises("API_ERROR", lambda: make_client(fake_trello).get("/boards/b1"))
        assert error.details["status"] == status
    assert len(fake_trello.requests) == 2


@pytest.mark.tc("TC-M04-013")
def test_error_keeps_the_original_body(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/members/me", 400, json.dumps({"message": "bad", "error": "X"}))
    as_json = raises("API_ERROR", lambda: make_client(fake_trello).get("/members/me"))
    assert as_json.details["upstream"] == {"message": "bad", "error": "X"}
    fake_trello.fail_next("GET", "/members/me", 400, "invalid value for name")
    as_text = raises("API_ERROR", lambda: make_client(fake_trello).get("/members/me"))
    assert as_text.details["upstream"] == "invalid value for name"


@pytest.mark.tc("TC-M04-014")
def test_secrets_never_appear_in_errors_or_logs(fake_trello: FakeTrello, tmp_path: Path) -> None:
    configure_logging(
        tmp_path, level="debug", secrets=lambda: (fake_trello.api_key, fake_trello.token)
    )
    echoed = f"bad key {fake_trello.api_key} token {fake_trello.token}"
    outputs: list[str] = []
    for status, body in ((401, f"invalid token {echoed}"), (429, echoed), (500, echoed)):
        fake_trello.fail_next("GET", "/members/me", status, body)
        error = raises(
            {401: "AUTH_INVALID", 429: "RATE_LIMITED", 500: "API_ERROR"}[status],
            lambda: make_client(fake_trello).get("/members/me"),
        )
        outputs.append(json.dumps({"message": error.message, "details": error.details}))
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    outputs.append((tmp_path / LOG_FILE).read_text(encoding="utf-8"))
    assert "http GET /members/me" in outputs[-1]
    for text in outputs:
        assert fake_trello.api_key not in text
        assert fake_trello.token not in text


@pytest.mark.tc("TC-M04-015")
def test_debug_log_line_has_method_path_status_and_duration(
    fake_trello: FakeTrello, tmp_path: Path
) -> None:
    configure_logging(
        tmp_path, level="debug", secrets=lambda: (fake_trello.api_key, fake_trello.token)
    )
    make_client(fake_trello).get("/members/me")
    for handler in logging.getLogger(LOGGER_NAME).handlers:
        handler.flush()
    text = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert re.search(r"http GET /members/me status=200 duration_ms=\d+", text)
    assert fake_trello.token not in text
    assert "token=" not in text


@pytest.fixture
def attachment(fake_trello: FakeTrello) -> FakeTrello:
    fake_trello.state.attachments["a1"] = {"id": "a1", "content": b"file-bytes"}
    return fake_trello


@pytest.mark.tc("TC-M04-016")
def test_download_authenticates_with_the_oauth_header(attachment: FakeTrello) -> None:
    data = make_client(attachment).download(f"https://api.trello.com/1{DOWNLOAD}")
    assert data == b"file-bytes"
    (request,) = attachment.requests
    assert request.headers["authorization"] == (
        f'OAuth oauth_consumer_key="{attachment.api_key}", oauth_token="{attachment.token}"'
    )
    assert request.query == {}
    assert attachment.api_key not in request.raw_path
    assert attachment.token not in request.raw_path


@pytest.mark.tc("TC-M04-017")
def test_fake_rejects_downloads_with_query_credentials(attachment: FakeTrello) -> None:
    url = f"{attachment.base_url}{DOWNLOAD}?key={attachment.api_key}&token={attachment.token}"
    result = urllib_transport(TransportRequest("GET", url), 5)
    assert result.status == 401


@pytest.mark.tc("TC-M04-018")
def test_absolute_download_urls_are_rewritten_onto_base_url(attachment: FakeTrello) -> None:
    client = make_client(attachment)
    for url in (
        f"https://api.trello.com/1{DOWNLOAD}",
        f"https://trello.com/1{DOWNLOAD}",
        f"https://trello.com{DOWNLOAD}",
    ):
        assert client.rewrite_download_url(url) == f"{attachment.base_url}{DOWNLOAD}"
        assert client.download(url) == b"file-bytes"
    assert len(attachment.requests) == 3
    assert {r.raw_path for r in attachment.requests} == {f"/1{DOWNLOAD}"}
    raises("API_ERROR", lambda: client.download("https://example.com/x/y.png"))
    assert len(attachment.requests) == 3


@pytest.mark.tc("TC-M04-019")
def test_upload_sends_multipart_with_file_and_name(fake_trello: FakeTrello) -> None:
    result = make_client(fake_trello).upload(
        "/cards/c1/attachments", filename="report.txt", content=b"payload", name="Quarterly"
    )
    (request,) = fake_trello.requests
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'filename="report.txt"' in request.body
    assert b"payload" in request.body
    assert request.param("key") == fake_trello.api_key
    assert result["name"] == "Quarterly"
    assert result["fileName"] == "report.txt"
    assert result["isUpload"] is True
    assert fake_trello.state.attachments[result["id"]]["content"] == b"payload"


@pytest.mark.tc("TC-M04-020")
def test_invalid_json_on_success_is_api_error(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/members/me", 200, "<html>not json</html>")
    error = raises("API_ERROR", lambda: make_client(fake_trello).get("/members/me"))
    assert error.details["upstream"] == "<html>not json</html>"


@pytest.mark.tc("TC-M04-021")
def test_credentials_only_come_from_constructor_values() -> None:
    source = inspect.getsource(trello_client)
    tree = ast.parse(source)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert not imported & {"os", "sys", "argparse", "tomllib", "subprocess"}
    assert "environ" not in source
    assert "argv" not in source
    parameters = inspect.signature(TrelloClient.__init__).parameters
    assert {"api_key", "token"} <= set(parameters)
    stub = StubTransport(response(200, "{}"))
    TrelloClient("http://x/1", "KEY", "TOK", 5, transport=stub).get("/members/me")
    assert "key=KEY" in stub.requests[0].url
    assert "token=TOK" in stub.requests[0].url

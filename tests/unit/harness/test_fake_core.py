"""Unit tests of the fake Trello core and clock."""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello, Request
from tests.fakes.trello.core import State, closed_port, text_response


def test_state_ids_are_unique_hex() -> None:
    state = State()
    a, b = state.new_id(), state.new_id()
    assert a != b
    assert len(a) == 24
    int(a, 16)


def test_clock_advances() -> None:
    clock = FakeClock()
    start = clock.time()
    clock.advance(10)
    assert clock.time() - start == 10
    assert clock.monotonic() == clock.time()
    assert clock.now().tzinfo is not None


def test_routes_listed_and_handler_variants(fake_trello: FakeTrello) -> None:
    @fake_trello.router.route("GET", "/t")
    def text(req: Request) -> object:
        return "plain"

    @fake_trello.router.route("GET", "/r")
    def resp(req: Request) -> object:
        return text_response("x", 418)

    @fake_trello.router.route("GET", "/open", auth="none")
    def open_route(req: Request) -> object:
        return {"ok": True}

    assert ("GET", "/t") in fake_trello.routes
    base = fake_trello.base_url
    key = f"?key={fake_trello.api_key}&token={fake_trello.token}"
    assert urllib.request.urlopen(f"{base}/t{key}", timeout=5).read() == b"plain"
    assert urllib.request.urlopen(f"{base}/open", timeout=5).read() == b'{"ok": true}'
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"{base}/r{key}", timeout=5)
    assert exc.value.code == 418


def test_oauth_route_requires_header(fake_trello: FakeTrello) -> None:
    @fake_trello.router.route("GET", "/dl", auth="oauth")
    def dl(req: Request) -> object:
        return "data"

    base = fake_trello.base_url
    query = f"{base}/dl?key={fake_trello.api_key}&token={fake_trello.token}"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(query, timeout=5)
    assert exc.value.code == 401
    header = f'OAuth oauth_consumer_key="{fake_trello.api_key}", oauth_token="{fake_trello.token}"'
    req = urllib.request.Request(f"{base}/dl", headers={"Authorization": header})
    assert urllib.request.urlopen(req, timeout=5).read() == b"data"
    bad = urllib.request.Request(
        f"{base}/dl", headers={"Authorization": f'OAuth oauth_consumer_key="{fake_trello.api_key}"'}
    )
    with pytest.raises(urllib.error.HTTPError) as exc2:
        urllib.request.urlopen(bad, timeout=5)
    assert exc2.value.read() == b"invalid token"


def test_dropped_connection_and_closed_port(fake_trello: FakeTrello) -> None:
    fake_trello.fail_next("GET", "/x", drop=True)
    with pytest.raises(OSError):  # noqa: PT011
        urllib.request.urlopen(f"{fake_trello.base_url}/x", timeout=5)
    port = closed_port()
    with pytest.raises(OSError):  # noqa: PT011
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)

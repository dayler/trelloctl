"""Core of the fake Trello server: router, state, request log and fault injection.

Resource modules in this package (any module not starting with an underscore,
other than ``core``) define ``register(router: Router) -> None`` and add their
routes there. They are discovered automatically by :class:`FakeTrello`.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

API_PREFIX = "/1"
TEST_API_KEY = "test-key-0000000000000000"
TEST_TOKEN = "test-token-000000000000000000000000000000000000000000000000"
NOT_FOUND_BODY = "The requested resource was not found."


@dataclass
class Response:
    """What a route handler returns."""

    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "application/json; charset=utf-8"


def json_response(data: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(status, json.dumps(data).encode("utf-8"), dict(headers or {}))


def text_response(text: str, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    return Response(status, text.encode("utf-8"), dict(headers or {}), "text/plain; charset=utf-8")


@dataclass
class State:
    """In-memory Trello data, seeded per test."""

    boards: dict[str, dict[str, Any]] = field(default_factory=dict)
    lists: dict[str, dict[str, Any]] = field(default_factory=dict)
    labels: dict[str, dict[str, Any]] = field(default_factory=dict)
    members: dict[str, dict[str, Any]] = field(default_factory=dict)
    cards: dict[str, dict[str, Any]] = field(default_factory=dict)
    checklists: dict[str, dict[str, Any]] = field(default_factory=dict)
    attachments: dict[str, dict[str, Any]] = field(default_factory=dict)
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    _counter: int = 0

    def new_id(self) -> str:
        """Return a deterministic 24-hex-character id."""
        self._counter += 1
        return f"{self._counter:024x}"


@dataclass
class RecordedRequest:
    """One request received by the server, as seen on the wire."""

    method: str
    raw_path: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def param(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[-1] if values else default

    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> Any:
        return json.loads(self.body)


@dataclass
class Request(RecordedRequest):
    """A request handed to a route handler."""

    params: dict[str, str] = field(default_factory=dict)
    state: State = field(default_factory=State)


Handler = Callable[[Request], "Response | dict[str, Any] | list[Any] | str"]


def _compile(template: str) -> re.Pattern[str]:
    regex = re.sub(
        r"\{(\w+)\}", r"(?P<\1>[^/]+)", re.escape(template).replace(r"\{", "{").replace(r"\}", "}")
    )
    return re.compile(f"^{regex}$")


@dataclass
class Route:
    method: str
    template: str
    handler: Handler
    auth: str = "query"  # "query", "oauth" (header only) or "none"
    pattern: re.Pattern[str] = field(init=False)

    def __post_init__(self) -> None:
        self.pattern = _compile(self.template)


class Router:
    """Registry of routes by method and path template (``/boards/{id}``)."""

    def __init__(self) -> None:
        self.routes: list[Route] = []
        # Once the resource modules are loaded, routes added by a test go first so that a
        # test can override a route of a module (the first match wins).
        self.override = False

    def route(
        self, method: str, template: str, auth: str = "query"
    ) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            route = Route(method.upper(), template, handler, auth)
            if self.override:
                self.routes.insert(0, route)
            else:
                self.routes.append(route)
            return handler

        return decorator

    def match(self, method: str, path: str) -> tuple[Route, dict[str, str]] | None:
        for route in self.routes:
            found = route.pattern.match(path)
            if found and route.method == method:
                return route, found.groupdict()
        return None


@dataclass
class Fault:
    """A scheduled failure, applied once to the next matching request."""

    method: str
    pattern: re.Pattern[str]
    status: int | None = 500  # None: only delay, then serve normally
    body: bytes = b"error"
    headers: dict[str, str] = field(default_factory=dict)
    delay: float = 0.0
    drop: bool = False


def closed_port() -> int:
    """Return a loopback port with nothing listening on it."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _Server(ThreadingHTTPServer):
    daemon_threads = False
    fake: FakeTrello


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        self._dispatch()

    do_POST = do_PUT = do_DELETE = do_PATCH = do_GET

    def _dispatch(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        split = urlsplit(self.path)
        raw_path = split.path
        path = raw_path[len(API_PREFIX) :] if raw_path.startswith(API_PREFIX + "/") else raw_path
        recorded = RecordedRequest(
            self.command,
            raw_path,
            path,
            parse_qs(split.query, keep_blank_values=True),
            {k.lower(): v for k, v in self.headers.items()},
            body,
        )
        fake = self.server.fake
        result = fake.handle(recorded)
        if result is None:
            self.close_connection = True
            return
        self.send_response(result.status)
        self.send_header("Content-Type", result.content_type)
        self.send_header("Content-Length", str(len(result.body)))
        for key, value in result.headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(result.body)


class FakeTrello:
    """Fake Trello HTTP server on 127.0.0.1 with an ephemeral port."""

    def __init__(self, api_key: str = TEST_API_KEY, token: str = TEST_TOKEN) -> None:
        self.api_key = api_key
        self.token = token
        self.state = State()
        self.router = Router()
        self.requests: list[RecordedRequest] = []
        self._faults: list[Fault] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._load_modules()

    def _load_modules(self) -> None:
        package = importlib.import_module(__package__ or "tests.fakes.trello")
        for info in pkgutil.iter_modules(package.__path__):
            if info.name.startswith("_") or info.name == "core":
                continue
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            register = getattr(module, "register", None)
            if register is not None:
                register(self.router)
        self.router.override = True

    @property
    def routes(self) -> list[tuple[str, str]]:
        return [(r.method, r.template) for r in self.router.routes]

    @property
    def port(self) -> int:
        assert self._server is not None, "server not started"
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}{API_PREFIX}"

    def start(self) -> FakeTrello:
        server = _Server(("127.0.0.1", 0), _Handler)
        server.fake = self
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join()
        self._server = None
        self._thread = None

    def __enter__(self) -> FakeTrello:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # Fault injection -------------------------------------------------

    def fail_next(
        self,
        method: str,
        template: str,
        status: int | None = 500,
        body: str | bytes = "error",
        headers: dict[str, str] | None = None,
        delay: float = 0.0,
        drop: bool = False,
    ) -> Fault:
        """Schedule a failure for the next request matching method and path."""
        data = body.encode("utf-8") if isinstance(body, str) else body
        fault = Fault(
            method.upper(), _compile(template), status, data, dict(headers or {}), delay, drop
        )
        with self._lock:
            self._faults.append(fault)
        return fault

    def rate_limit(
        self, method: str, template: str, body: str = "API_TOKEN_LIMIT_EXCEEDED"
    ) -> Fault:
        headers = {
            "x-rate-limit-api-token-interval-ms": "10000",
            "x-rate-limit-api-token-max": "100",
            "x-rate-limit-api-token-remaining": "0",
        }
        return self.fail_next(method, template, 429, body, headers)

    # Request handling ------------------------------------------------

    def handle(self, rec: RecordedRequest) -> Response | None:
        """Process a request; None means drop the connection."""
        with self._lock:
            self.requests.append(rec)
            fault = next(
                (f for f in self._faults if f.method == rec.method and f.pattern.match(rec.path)),
                None,
            )
            if fault is not None:
                self._faults.remove(fault)
        if fault is not None:
            if fault.delay:
                self._stop.wait(fault.delay)
            if fault.drop:
                return None
        if fault is not None and fault.status is not None:
            return Response(fault.status, fault.body, fault.headers, "text/plain; charset=utf-8")
        found = self.router.match(rec.method, rec.path)
        if found is None:
            return text_response(NOT_FOUND_BODY, 404)
        route, params = found
        denied = self._check_auth(route, rec)
        if denied is not None:
            return denied
        request = Request(
            rec.method, rec.raw_path, rec.path, rec.query, rec.headers, rec.body, params, self.state
        )
        result = route.handler(request)
        if isinstance(result, Response):
            return result
        if isinstance(result, str):
            return text_response(result)
        return json_response(result)

    def _check_auth(self, route: Route, rec: RecordedRequest) -> Response | None:
        if route.auth == "none":
            return None
        if route.auth == "oauth":
            header = rec.headers.get("authorization", "")
            key = re.search(r'oauth_consumer_key="([^"]*)"', header)
            token = re.search(r'oauth_token="([^"]*)"', header)
            given_key = key.group(1) if key else None
            given_token = token.group(1) if token else None
        else:
            given_key = rec.param("key")
            given_token = rec.param("token")
        if given_key != self.api_key:
            return text_response("invalid key", 401)
        if given_token != self.token:
            return text_response("invalid token", 401)
        return None

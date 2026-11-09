"""HTTP client for the Trello REST API: one request per call, typed errors, no retries."""

from __future__ import annotations

import json
import logging
import math
import ssl
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.logging import LOGGER_NAME, mask_text

DEFAULT_BASE_URL = "https://api.trello.com/1"
TRELLO_HOSTS = frozenset({"api.trello.com", "trello.com", "www.trello.com"})
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
RATE_LIMIT_NAMES = (
    "API_TOKEN_LIMIT_EXCEEDED",
    "API_KEY_LIMIT_EXCEEDED",
    "API_TOKEN_DB_LIMIT_EXCEEDED",
)
_HEADER_PREFIXES = {
    "API_TOKEN_LIMIT_EXCEEDED": ("api-token",),
    "API_KEY_LIMIT_EXCEEDED": ("api-key",),
    "API_TOKEN_DB_LIMIT_EXCEEDED": ("api-token-db", "api-token"),
}
_ALL_PREFIXES = ("api-token", "api-key")

QueryValue = str | int | float | bool | None


@dataclass(frozen=True)
class TransportRequest:
    """A fully built HTTP request handed to the transport."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None


@dataclass(frozen=True)
class TransportResponse:
    """An HTTP response as returned by the transport; header names are lowercase."""

    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""


Transport = Callable[[TransportRequest, float], TransportResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def urllib_transport(request: TransportRequest, timeout: float) -> TransportResponse:
    """Real transport over `urllib` and the system certificate store.

    HTTP error statuses are returned as responses; connection, DNS, TLS and timeout
    failures propagate as the exceptions raised by the standard library.
    """
    host = (urlsplit(request.url).hostname or "").lower()
    handlers: list[Any] = [
        _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    ]
    if host in LOOPBACK_HOSTS:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(  # noqa: S310 - scheme comes from configuration
        request.url, data=request.body, headers=request.headers, method=request.method
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            return TransportResponse(
                response.status,
                {k.lower(): v for k, v in response.headers.items()},
                response.read(),
            )
    except urllib.error.HTTPError as exc:
        try:
            return TransportResponse(
                exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()
            )
        finally:
            exc.close()


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, TimeoutError)


class TrelloClient:
    """Trello API client. Credentials and settings arrive as constructor values."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        token: str,
        timeout_seconds: float,
        *,
        transport: Transport | None = None,
        logger: logging.Logger | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = api_key
        self._token = token
        self._timeout = float(timeout_seconds)
        self._transport: Transport = transport or urllib_transport
        self._logger = logger or logging.getLogger(LOGGER_NAME)
        self._monotonic = monotonic

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def secrets(self) -> tuple[str, ...]:
        return (self._api_key, self._token)

    # Public API ------------------------------------------------------

    def request(
        self, method: str, path: str, params: Mapping[str, QueryValue] | None = None
    ) -> Any:
        """Send one request with key and token in the query; return the decoded JSON body."""
        response = self._send(TransportRequest(method.upper(), self._url(path, params)), path)
        return self._decode(response, path)

    def get(self, path: str, params: Mapping[str, QueryValue] | None = None) -> Any:
        return self.request("GET", path, params)

    def post(self, path: str, params: Mapping[str, QueryValue] | None = None) -> Any:
        return self.request("POST", path, params)

    def put(self, path: str, params: Mapping[str, QueryValue] | None = None) -> Any:
        return self.request("PUT", path, params)

    def delete(self, path: str, params: Mapping[str, QueryValue] | None = None) -> Any:
        return self.request("DELETE", path, params)

    def download(self, url: str) -> bytes:
        """Download a file with the OAuth header; credentials never go in the query."""
        target = self.rewrite_download_url(url)
        path = urlsplit(target).path
        auth = f'OAuth oauth_consumer_key="{self._api_key}", oauth_token="{self._token}"'
        response = self._send(TransportRequest("GET", target, {"Authorization": auth}), path)
        if not 200 <= response.status < 300:
            self._raise_for_status(response, path)
        return response.body

    def upload(
        self,
        path: str,
        *,
        filename: str,
        content: bytes,
        name: str | None = None,
        params: Mapping[str, QueryValue] | None = None,
        content_type: str = "application/octet-stream",
    ) -> Any:
        """POST a multipart/form-data body with one file (and an optional `name` field)."""
        boundary = f"trelloctl-{uuid.uuid4().hex}"
        parts: list[bytes] = []
        if name is not None:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="name"\r\n\r\n'.encode()
                + name.encode("utf-8")
                + b"\r\n"
            )
        safe_name = quote(filename, safe=" ._-()")
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        request = TransportRequest("POST", self._url(path, params), headers, b"".join(parts))
        return self._decode(self._send(request, path), path)

    def rewrite_download_url(self, url: str) -> str:
        """Rewrite an absolute Trello download URL onto `base_url` (RNF-8)."""
        split = urlsplit(url)
        host = (split.hostname or "").lower()
        if host not in TRELLO_HOSTS:
            raise TrelloctlError(
                "API_ERROR",
                "Refusing to send credentials to a host that is not Trello.",
                {"host": host},
            )
        path = split.path
        if path.startswith("/1/"):
            path = path[2:]
        target = f"{self._base_url}/{path.lstrip('/')}"
        return f"{target}?{split.query}" if split.query else target

    # Internals -------------------------------------------------------

    def _url(self, path: str, params: Mapping[str, QueryValue] | None) -> str:
        query = {"key": self._api_key, "token": self._token, **_clean(params)}
        return f"{self._base_url}/{path.lstrip('/')}?{urlencode(query)}"

    def _mask(self, text: str) -> str:
        return mask_text(text, self.secrets)

    def _send(self, request: TransportRequest, path: str) -> TransportResponse:
        started = self._monotonic()
        try:
            response = self._transport(request, self._timeout)
        except Exception as exc:
            self._log(request.method, path, "error", started)
            raise self._translate_failure(exc) from None
        self._log(request.method, path, str(response.status), started)
        return response

    def _log(self, method: str, path: str, status: str, started: float) -> None:
        duration_ms = int((self._monotonic() - started) * 1000)
        self._logger.debug(
            "%s", self._mask(f"http {method} {path} status={status} duration_ms={duration_ms}")
        )

    def _translate_failure(self, exc: Exception) -> TrelloctlError:
        if _is_timeout(exc):
            return TrelloctlError(
                "TIMEOUT",
                f"Trello did not respond within {self._timeout:g} seconds.",
                {"timeout_seconds": self._timeout},
            )
        reason: object = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        kind = "tls" if isinstance(reason, ssl.SSLError) else "network"
        return TrelloctlError(
            "NETWORK_ERROR",
            "Could not reach Trello.",
            {"reason": self._mask(str(reason)), "kind": kind},
        )

    def _decode(self, response: TransportResponse, path: str) -> Any:
        if not 200 <= response.status < 300:
            self._raise_for_status(response, path)
        if not response.body.strip():
            return None
        try:
            return json.loads(response.body)
        except ValueError:
            raise TrelloctlError(
                "API_ERROR",
                "Trello returned a response that is not valid JSON.",
                {"status": response.status, "upstream": self._upstream(response.body)},
            ) from None

    def _upstream(self, body: bytes) -> Any:
        text = self._mask(body.decode("utf-8", errors="replace"))
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _raise_for_status(self, response: TransportResponse, path: str) -> None:
        status = response.status
        text = self._mask(response.body.decode("utf-8", errors="replace"))
        upstream = self._upstream(response.body)
        details: dict[str, Any] = {"status": status, "upstream": upstream}
        if status == 429:
            raise _rate_limited(text, response.headers, upstream)
        if status == 401:
            lowered = text.lower()
            if (
                "invalid key" in lowered
                or "invalid token" in lowered
                or path.rstrip("/").endswith("/members/me")
            ):
                raise TrelloctlError("AUTH_INVALID", "Trello rejected the credentials.", details)
        raise TrelloctlError("API_ERROR", f"Trello API returned HTTP {status}.", details)


def _clean(params: Mapping[str, QueryValue] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        out[key] = ("true" if value else "false") if isinstance(value, bool) else str(value)
    return out


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _rate_limited(body: str, headers: Mapping[str, str], upstream: Any) -> TrelloctlError:
    limit = next((name for name in RATE_LIMIT_NAMES if name in body), None)
    prefixes = _HEADER_PREFIXES.get(limit or "", _ALL_PREFIXES)
    prefix = next(
        (p for p in prefixes if any(k.startswith(f"x-rate-limit-{p}-") for k in headers)),
        prefixes[0],
    )
    interval_ms = _int_header(headers, f"x-rate-limit-{prefix}-interval-ms")
    details: dict[str, Any] = {
        "status": 429,
        "limit": limit,
        "max": _int_header(headers, f"x-rate-limit-{prefix}-max"),
        "remaining": _int_header(headers, f"x-rate-limit-{prefix}-remaining"),
        "interval_ms": interval_ms,
        "retry_in_seconds": None if interval_ms is None else math.ceil(interval_ms / 1000),
        "upstream": upstream,
    }
    return TrelloctlError("RATE_LIMITED", "Too many requests", details)

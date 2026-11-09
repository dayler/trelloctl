"""Transport stub for unit tests: programmed responses or errors, no sockets."""

from __future__ import annotations

from trelloctl.infra.trello_client import TransportRequest, TransportResponse


class StubTransport:
    """Callable transport that replays queued outcomes and records every request."""

    def __init__(self, *outcomes: TransportResponse | BaseException) -> None:
        self.outcomes: list[TransportResponse | BaseException] = list(outcomes)
        self.requests: list[TransportRequest] = []
        self.timeouts: list[float] = []

    def queue(self, outcome: TransportResponse | BaseException) -> StubTransport:
        self.outcomes.append(outcome)
        return self

    def __call__(self, request: TransportRequest, timeout: float) -> TransportResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if not self.outcomes:
            raise AssertionError("StubTransport has no programmed outcome left")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def response(
    status: int = 200, body: str | bytes = b"{}", headers: dict[str, str] | None = None
) -> TransportResponse:
    data = body.encode("utf-8") if isinstance(body, str) else body
    return TransportResponse(status, {k.lower(): v for k, v in (headers or {}).items()}, data)

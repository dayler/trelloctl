"""Injectable clock for TTL tests; never sleep for real."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def time(self) -> float:
        return self._now.timestamp()

    def monotonic(self) -> float:
        return self.time()

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)

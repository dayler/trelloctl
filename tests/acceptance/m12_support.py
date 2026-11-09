"""Shared helpers of the M12 acceptance tests: a fake account with an Inbox and a work board."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m09_support import (  # noqa: F401
    BOARD,
    add_label,
    add_list,
    cached,
    calls,
    card,
    close_caches,
    open_cache,
    request_params,
    run,
    seed_world,
    write_config,
    writes,
)
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import RecordedRequest
from tests.fakes.trello.inbox import INBOX_BOARD, hide_inbox, seed_inbox  # noqa: F401

START = datetime(2026, 11, 9, 10, 0, tzinfo=UTC)


def seed_account(fake: FakeTrello) -> dict[str, str]:
    """Board `Work` (lists Todo, Doing, Done and `To Do`) and the Inbox with one label."""
    ids = seed_world(fake)
    ids["to_do"] = add_list(fake, "To Do", 5)
    inbox_board, inbox_list = seed_inbox(fake.state)
    ids["inbox_board"], ids["inbox_list"] = inbox_board, inbox_list
    ids["inbox_label"] = add_label(fake, "idea", "green", inbox_board)
    return ids


def inbox_card(
    fake: FakeTrello, ids: dict[str, str], name: str, id_short: int, **kwargs: Any
) -> str:
    """A card of the Inbox list."""
    return card(fake, ids["inbox_list"], name, id_short, board_id=ids["inbox_board"], **kwargs)


def configure(home: Path, fake: FakeTrello, *, board: str | None = None) -> None:
    write_config(home, fake, board=board)


def freeze(monkeypatch: pytest.MonkeyPatch, start: datetime = START) -> FakeClock:
    """Make every TTL decision of the commands use a controllable clock."""
    clock = FakeClock(start)
    monkeypatch.setattr("trelloctl.app.resolver.utc_now", clock.now)
    return clock


def inbox_row(home: Path) -> dict[str, Any] | None:
    return open_cache(home).get_board(INBOX_BOARD)


def me_calls(fake: FakeTrello) -> list[RecordedRequest]:
    return calls(fake, "/members/me")


def forget(fake: FakeTrello) -> None:
    fake.requests.clear()

"""Shared helpers of the M08 acceptance tests: seeds of the board group and request inspection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    OTHER,
    SHORT,
    add_card,
    add_checklist,
    add_label,
    add_list,
    add_member,
    calls,
    close_caches,
    new_id,
    paths,
    prime,
    run_json,
    seed_board,
    seed_standard,
    write_config,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import RecordedRequest

from trelloctl.infra.cache import Cache

MEMBER = "b" * 24
BASE_DATE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


def ago(seconds: float) -> datetime:
    return now() - timedelta(seconds=seconds)


def configure(home: Path, fake: FakeTrello) -> None:
    """Credentials, the fake as base URL and the default board."""
    write_config(home, fake, board=BOARD)


def seed_lists(fake: FakeTrello, *names: str, board_id: str = BOARD) -> dict[str, str]:
    """Board `Work` (when missing) with the named lists at positions 1, 2, 3..."""
    if board_id not in fake.state.boards:
        seed_board(fake, board_id)
    return {name: add_list(fake, name, float(i), board_id) for i, name in enumerate(names, 1)}


def open_names(fake: FakeTrello, board_id: str = BOARD) -> list[str]:
    """Names of the open lists of the fake, by position."""
    items = [x for x in fake.state.lists.values() if x["idBoard"] == board_id and not x["closed"]]
    return [x["name"] for x in sorted(items, key=lambda x: (x["pos"], x["id"]))]


def cached_names(home: Path, board_id: str = BOARD) -> list[str]:
    rows = [r for r in Cache.open(home).list_lists(board_id) if not r["closed"]]
    return [r["name"] for r in rows]


def writes(fake: FakeTrello) -> list[RecordedRequest]:
    """Every request that is not a GET."""
    return [r for r in fake.requests if r.method != "GET"]


def query(request: RecordedRequest) -> dict[str, str]:
    """Query of a request without the credentials."""
    return {k: v[-1] for k, v in request.query.items() if k not in ("key", "token")}


def add_action(
    fake: FakeTrello,
    kind: str,
    date: datetime,
    *,
    data: dict[str, Any] | None = None,
    board_id: str = BOARD,
    username: str = "ana",
) -> str:
    ident = new_id(fake)
    fake.state.actions[ident] = {
        "id": ident,
        "type": kind,
        "date": date.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "idMemberCreator": MEMBER,
        "memberCreator": {"id": MEMBER, "username": username, "fullName": username.title()},
        "data": data or {},
        "_board": board_id,
    }
    return ident


def add_actions(fake: FakeTrello, count: int, kind: str = "updateCard") -> list[str]:
    """`count` actions one minute apart; the last one is the newest."""
    return [
        add_action(fake, kind, BASE_DATE + timedelta(minutes=i), data={"text": f"n{i}"})
        for i in range(count)
    ]

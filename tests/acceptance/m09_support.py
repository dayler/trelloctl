"""Shared helpers of the M09 acceptance tests: a seeded board, cache priming and CLI runners."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    LONG_AGO,
    NEWER,
    OTHER,
    add_card,
    add_checklist,
    add_label,
    add_list,
    add_member,
    calls,
    close_caches,
    new_id,
    open_cache,
    paths,
    prime,
    run_json,
    seed_board,
    write_config,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import RecordedRequest

OLD = datetime(2020, 1, 1, tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


def seed_world(fake: FakeTrello) -> dict[str, str]:
    """Board `Work`: open lists Todo, Doing and Done, a closed list, two labels, two members."""
    seed_board(fake)
    ids = {
        "todo": add_list(fake, "Todo", 1),
        "doing": add_list(fake, "Doing", 2),
        "done": add_list(fake, "Done", 3),
        "old": add_list(fake, "Old", 4, closed=True),
        "bug": add_label(fake, "bug", "red"),
        "urgent": add_label(fake, "urgent", "yellow"),
        "ana": add_member(fake, "ana", "Ana Gomez"),
        "bob": add_member(fake, "bob", "Bob Ruiz"),
    }
    return ids


def card(
    fake: FakeTrello,
    list_id: str,
    name: str,
    id_short: int,
    *,
    board_id: str = BOARD,
    short_link: str | None = None,
    closed: bool = False,
    desc: str = "",
    labels: tuple[str, ...] = (),
    members: tuple[str, ...] = (),
    due: str | None = None,
    done: bool = False,
    pos: float | None = None,
) -> str:
    """A card with the optional fields the tests filter on."""
    ident = add_card(
        fake, list_id, name, id_short=id_short, board_id=board_id, short_link=short_link
    )
    item = fake.state.cards[ident]
    item.update(
        closed=closed,
        desc=desc,
        idLabels=list(labels),
        idMembers=list(members),
        due=due,
        dueComplete=done,
        badges={"comments": 0, "attachments": 0, "checkItems": 0, "checkItemsChecked": 0},
    )
    if pos is not None:
        item["pos"] = pos
    return ident


def comment(fake: FakeTrello, card_id: str, text: str, member: str | None = None) -> str:
    ident = new_id(fake)
    fake.state.actions[ident] = {
        "id": ident,
        "type": "commentCard",
        "idMemberCreator": member or "0" * 24,
        "date": "2026-03-01T10:00:00.000Z",
        "data": {"text": text, "card": {"id": card_id}},
    }
    return ident


def finish(
    home: Path,
    fake: FakeTrello,
    *,
    board: str | None = BOARD,
    when: datetime | None = None,
    archived: bool = False,
    extra: str = "",
    board_id: str = BOARD,
) -> None:
    """Write the configuration, prime the cache at `when` (now by default) and clear the log."""
    write_config(home, fake, board=board, extra=extra)
    prime(home, fake, when or now(), board_id, archived=archived)
    fake.requests.clear()


def run(
    capsys: pytest.CaptureFixture[str], *argv: str, stdin: bytes | None = None
) -> tuple[int, dict[str, Any]]:
    """Run the CLI in process, optionally with UTF-8 bytes on standard input."""
    if stdin is None:
        return run_json(capsys, *argv)
    real = sys.stdin
    sys.stdin = io.TextIOWrapper(io.BytesIO(stdin), encoding="utf-8")
    try:
        return run_json(capsys, *argv)
    finally:
        sys.stdin = real


def request_params(request: RecordedRequest) -> dict[str, str]:
    """The query of a request without the credentials."""
    return {k: v[-1] for k, v in request.query.items() if k not in ("key", "token")}


def writes(fake: FakeTrello) -> list[RecordedRequest]:
    return [r for r in fake.requests if r.method in ("POST", "PUT", "DELETE")]


def cached(home: Path, card_id: str, board_id: str = BOARD) -> dict[str, Any] | None:
    cache = open_cache(home)
    return cache.get_card(board_id, card_id)


def names(envelope: dict[str, Any]) -> list[str]:
    return [item["name"] for item in envelope["data"]["cards"]]

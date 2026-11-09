"""Shared helpers of the M07 acceptance tests: fake Trello seeding, contexts and CLI runners."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import RecordedRequest

from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.app.resolver import Resolver
from trelloctl.cli import main
from trelloctl.infra import cache as cache_mod
from trelloctl.infra.cache import Cache
from trelloctl.registry import Operation, Registry, default_registry

BOARD = "5f1b2c3d4e5f6a7b8c9d0e1f"
OTHER = "6a1b2c3d4e5f6a7b8c9d0e2a"
SHORT = "abcd1234"
URL = f"https://trello.com/b/{SHORT}/work"
LONG_AGO = "2020-01-01T00:00:00.000Z"
NEWER = "2030-01-01T00:00:00.000Z"


@pytest.fixture(autouse=True)
def close_caches() -> Iterator[None]:
    """Close the process-wide cache connections so temporary homes can be removed."""
    yield
    for cache in list(cache_mod._REGISTRY.values()):
        cache.close()


# --- fake Trello seeding ---------------------------------------------------------------


def new_id(fake: FakeTrello) -> str:
    """A deterministic 24-hex id that is never all digits (digits alone mean `idShort`)."""
    return "a" + fake.state.new_id()[1:]


def seed_board(
    fake: FakeTrello,
    board_id: str = BOARD,
    name: str = "Work",
    short_link: str = SHORT,
    *,
    closed: bool = False,
) -> dict[str, Any]:
    board = {
        "id": board_id,
        "name": name,
        "shortLink": short_link,
        "url": f"https://trello.com/b/{short_link}/{name.lower()}",
        "closed": closed,
        "_members": [],
    }
    fake.state.boards[board_id] = board
    return board


def add_list(
    fake: FakeTrello, name: str, pos: float = 1, board_id: str = BOARD, closed: bool = False
) -> str:
    ident = new_id(fake)
    fake.state.lists[ident] = {
        "id": ident,
        "idBoard": board_id,
        "name": name,
        "pos": pos,
        "closed": closed,
    }
    return ident


def add_label(fake: FakeTrello, name: str, color: str | None, board_id: str = BOARD) -> str:
    ident = new_id(fake)
    fake.state.labels[ident] = {"id": ident, "idBoard": board_id, "name": name, "color": color}
    return ident


def add_member(
    fake: FakeTrello, username: str, full_name: str | None = None, board_id: str = BOARD
) -> str:
    ident = new_id(fake)
    fake.state.members[ident] = {
        "id": ident,
        "username": username,
        "fullName": full_name or username.title(),
    }
    fake.state.boards[board_id]["_members"].append(ident)
    return ident


def add_card(
    fake: FakeTrello,
    list_id: str,
    name: str,
    *,
    id_short: int,
    board_id: str = BOARD,
    short_link: str | None = None,
    closed: bool = False,
    changed: str = LONG_AGO,
) -> str:
    ident = new_id(fake)
    fake.state.cards[ident] = {
        "id": ident,
        "idBoard": board_id,
        "idList": list_id,
        "name": name,
        "closed": closed,
        "idShort": id_short,
        "shortLink": short_link or f"c{ident[-7:]}",
        "dateLastActivity": changed,
        "idLabels": [],
        "idMembers": [],
        "pos": id_short,
        "url": f"https://trello.com/c/{short_link or 'x'}",
    }
    return ident


def add_checklist(
    fake: FakeTrello, card_id: str, name: str, items: tuple[str, ...] = (), pos: float = 1
) -> str:
    ident = new_id(fake)
    fake.state.checklists[ident] = {
        "id": ident,
        "idCard": card_id,
        "name": name,
        "pos": pos,
        "checkItems": [
            {"id": new_id(fake), "name": text, "state": "incomplete", "pos": index}
            for index, text in enumerate(items, 1)
        ],
    }
    return ident


def seed_standard(fake: FakeTrello) -> dict[str, str]:
    """Board `Work` with lists Todo/Doing, a label, a member and two cards."""
    seed_board(fake)
    ids = {
        "todo": add_list(fake, "Todo", 1),
        "doing": add_list(fake, "Doing", 2),
        "bug": add_label(fake, "bug", "red"),
        "ana": add_member(fake, "ana", "Ana Gomez"),
    }
    ids["c1"] = add_card(fake, ids["todo"], "First card", id_short=1, short_link="cardAAA1")
    ids["c2"] = add_card(fake, ids["doing"], "Second card", id_short=2, short_link="cardBBB2")
    return ids


# --- request inspection ----------------------------------------------------------------


def calls(
    fake: FakeTrello, path: str | None = None, method: str = "GET", prefix: bool = False
) -> list[RecordedRequest]:
    """Recorded requests with the method, and the path (exact, or a prefix when asked)."""
    return [
        r
        for r in fake.requests
        if r.method == method
        and (path is None or (r.path.startswith(path) if prefix else r.path == path))
    ]


def paths(fake: FakeTrello, method: str | None = None) -> list[str]:
    return [r.path for r in fake.requests if method is None or r.method == method]


# --- configuration, contexts and runners -------------------------------------------------


def write_config(
    home: Path,
    fake: FakeTrello,
    *,
    board: str | None = None,
    extra: str = "",
    auth: bool = True,
    timeout: int | None = None,
) -> None:
    key, token = (fake.api_key, fake.token) if auth else ("", "")
    lines = [
        "[auth]",
        f'api_key = "{key}"',
        f'token = "{token}"',
        "",
        "[http]",
        f'base_url = "{fake.base_url}"',
    ]
    if timeout is not None:
        lines.append(f"timeout_seconds = {timeout}")
    if board is not None:
        lines += ["", "[board]", f'default = "{board}"']
    text = "\n".join(lines) + "\n"
    (home / "config.toml").write_bytes((text + extra).encode("utf-8"))


def make_ctx(
    home: Path,
    fake: FakeTrello,
    *,
    board_flag: str | None = None,
    no_cache: bool = False,
    include_archived_boards: bool = False,
    board: str | None = None,
    extra: str = "",
    timeout: int | None = None,
) -> CommandContext:
    if not (home / "config.toml").exists():
        write_config(home, fake, board=board, extra=extra, timeout=timeout)
    settings, error = load_settings_lenient({}, home)
    return CommandContext(
        settings=settings,
        config_error=error,
        environ={},
        board_flag=board_flag,
        no_cache=no_cache,
        include_archived_boards=include_archived_boards,
    )


def make_resolver(
    home: Path, fake: FakeTrello, clock: FakeClock | None = None, **kwargs: Any
) -> Resolver:
    ctx = make_ctx(home, fake, **kwargs)
    return Resolver(ctx, clock=clock.now if clock else None)


def prime(
    home: Path,
    fake: FakeTrello,
    when: datetime,
    board_id: str = BOARD,
    *,
    archived: bool = False,
) -> None:
    """Fill the cache from the fake as of `when` (full reload, optionally archived cards)."""
    clock = FakeClock(when)
    resolver = make_resolver(home, fake, clock)
    resolver.reload_all(board_id)
    if archived:
        resolver.reload_archived(board_id)


def run_json(
    capsys: pytest.CaptureFixture[str], *argv: str, registry: Registry | None = None
) -> tuple[int, dict[str, Any]]:
    code = main(list(argv), registry=registry)
    return code, json.loads(capsys.readouterr().out)


def probe_registry(handler: Callable[[CommandContext, Mapping[str, Any]], Any]) -> Registry:
    operation = Operation(("probe", "read"), "probe", "trelloctl probe read", handler)
    return default_registry().extended(operation)


def open_cache(home: Path) -> Cache:
    return Cache.open(home)

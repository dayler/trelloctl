"""Unit tests of the `board list` commands."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    add_list,
    close_caches,
    make_ctx,
    prime,
    seed_board,
)
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import lists
from trelloctl.domain.errors import TrelloctlError

ROWS = [
    {"id": "a", "name": "A", "pos": 1, "closed": 0},
    {"id": "b", "name": "B", "pos": 2, "closed": 0},
    {"id": "c", "name": "C", "pos": 3, "closed": 0},
    {"id": "z", "name": "Z", "pos": 4, "closed": 1},
]


def fails(code: str, call: Any, *args: Any, **kwargs: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


def test_plan_of_a_shift_reports_clamping() -> None:
    ordered, start, end, clamped = lists._plan(ROWS, "b", "right", 1, False)
    assert ([x.id for x in ordered], start, end, clamped) == (["a", "b", "c"], 1, 2, False)
    assert lists._plan(ROWS, "b", "right", 5, True)[2:] == (2, True)
    assert lists._plan(ROWS, "b", "left", 5, True)[2:] == (0, True)
    assert fails("MOVE_OUT_OF_RANGE", lists._plan, ROWS, "b", "left", 2, False).details == {
        "from_index": 1,
        "to_index": -1,
        "list_count": 3,
    }
    fails("USAGE", lists._plan, ROWS, "b", "left", 0, False)


def test_plan_of_an_index_move() -> None:
    assert lists._plan(ROWS, "c", "to_index", 0, False)[1:] == (2, 0, False)
    assert lists._plan(ROWS, "a", "to_index", "2", False)[1:] == (0, 2, False)
    assert lists._plan(ROWS, "a", "to_index", 3, True)[1:] == (0, 2, True)
    assert fails("MOVE_OUT_OF_RANGE", lists._plan, ROWS, "a", "to_index", 3, False).details == {
        "from_index": 0,
        "to_index": 3,
        "list_count": 3,
    }
    fails("USAGE", lists._plan, ROWS, "a", "to_index", -1, False)


def test_an_archived_list_cannot_be_moved_by_index_or_by_shift() -> None:
    for kind in ("to_index", "right"):
        error = fails("MOVE_OUT_OF_RANGE", lists._plan, ROWS, "z", kind, 1, False)
        assert error.details["from_index"] is None


def test_exactly_one_target_is_required() -> None:
    assert lists._requested({"left": 2}) == ("left", 2)
    assert lists._requested({"to_index": 0, "left": None}) == ("to_index", 0)
    fails("USAGE", lists._requested, {})
    fails("USAGE", lists._requested, {"left": 1, "right": 1})


def test_names_must_not_be_blank() -> None:
    assert lists._name({"name": " A "}) == " A "
    for args in ({}, {"name": ""}, {"name": "  "}, {"name": 3}):
        fails("USAGE", lists._name, args)


def test_find_list_prefers_the_wanted_state_and_reports_ambiguity(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_board(fake_trello)
    open_one = add_list(fake_trello, "Dup", 1)
    shut = add_list(fake_trello, "Dup", 2, closed=True)
    add_list(fake_trello, "Twin", 3)
    add_list(fake_trello, "Twin", 4)
    only_shut = add_list(fake_trello, "Old", 5, closed=True)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    resolver = lists.Resolver(ctx)
    assert lists._find_list(resolver, BOARD, "dup", closed=False)["id"] == open_one
    assert lists._find_list(resolver, BOARD, "dup", closed=True)["id"] == shut
    assert lists._find_list(resolver, BOARD, "OLD", closed=False)["id"] == only_shut
    assert lists._find_list(resolver, BOARD, shut.upper(), closed=False)["id"] == shut
    ambiguous = fails("AMBIGUOUS_LIST", lists._find_list, resolver, BOARD, "twin", closed=False)
    assert len(ambiguous.details["candidates"]) == 2
    missing = fails("LIST_NOT_FOUND", lists._find_list, resolver, BOARD, "nope", closed=False)
    assert missing.details["list"] == "nope"


def test_create_with_an_unexpected_answer_is_an_api_error(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_board(fake_trello)
    add_list(fake_trello, "A", 1)
    fake_trello.fail_next("POST", "/lists", 200, "{}")
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    fails("API_ERROR", lists.create, ctx, {"name": "B"})


def test_a_list_that_vanishes_before_the_fresh_read_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_board(fake_trello)
    add_list(fake_trello, "A", 1)
    gone = add_list(fake_trello, "B", 2)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    prime(trelloctl_home, fake_trello, datetime.now(UTC))
    del fake_trello.state.lists[gone]
    error = fails("LIST_NOT_FOUND", lists.move, ctx, {"list": "B", "right": 1})
    assert error.details["list"] == "B"
    assert [r.method for r in fake_trello.requests if r.method != "GET"] == []

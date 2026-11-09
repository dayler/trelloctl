"""Unit tests of `app/commands/inbox.py`: argument handling, Inbox resolution and write-through."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import make_ctx
from tests.acceptance.m09_support import add_checklist
from tests.acceptance.m12_support import (
    BOARD,
    INBOX_BOARD,
    card,
    close_caches,  # noqa: F401
    forget,
    freeze,
    hide_inbox,
    inbox_card,
    inbox_row,
    open_cache,
    seed_account,
)
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import inbox
from trelloctl.domain.errors import TrelloctlError


def fails(code: str, call: Any, *args: Any, **kwargs: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as caught:
        call(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


@pytest.mark.parametrize(("raw", "expected"), [(None, 50), (0, None), (7, 7)])
def test_limit_defaults_to_50_and_zero_means_no_limit(raw: Any, expected: int | None) -> None:
    assert inbox._limit(raw) == expected


@pytest.mark.parametrize("raw", [-1, True, "3", 1.5])
def test_a_bad_limit_is_usage(raw: Any) -> None:
    fails("USAGE", inbox._limit, raw)


def test_fields_are_validated_against_the_card_fields() -> None:
    assert inbox._fields(None) is None
    assert inbox._fields(["id", " name "]) == ("id", "name")
    error = fails("USAGE", inbox._fields, ["id", "nope"])
    assert error.details["unknown"] == ["nope"]
    fails("USAGE", inbox._fields, [" "])


def test_pos_is_top_bottom_or_an_index() -> None:
    assert inbox._check_pos(None) is None
    assert inbox._check_pos("TOP") == "top"
    assert inbox._check_pos(" bottom ") == "bottom"
    assert inbox._check_pos("4") == "4"
    for raw in ("-1", "x", "1.5"):
        fails("USAGE", inbox._check_pos, raw)


def test_add_refuses_list_labels_and_members_and_blank_names(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello)
    for key in ("list", "labels", "members"):
        error = fails("USAGE", inbox.add, ctx, {"name": "X", key: ["a"]})
        assert error.details == {"option": "--" + key}
    fails("USAGE", inbox.add, ctx, {"name": "  "})
    fails("USAGE", inbox.add, ctx, {"name": "X", "due": "tomorrow"})
    fails("USAGE", inbox.add, ctx, {"name": "X", "desc": "a", "desc_file": "b"})
    assert fake_trello.requests == []


def test_move_checks_its_arguments_before_any_request(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    fails("USAGE", inbox.move, ctx, {"card": "X"})
    fails("USAGE", inbox.move, ctx, {"card": "X", "to": "  "})
    fails("USAGE", inbox.move, ctx, {"card": "X", "to": "Todo", "pos": "-3"})
    assert fake_trello.requests == []


@pytest.mark.parametrize(
    "state",
    [
        {"inbox": {"idBoard": "", "idList": "a" * 24}},
        {"inbox": {"idBoard": "a" * 24}},
        {"inbox": {"idBoard": 5, "idList": 6}},
    ],
)
def test_a_malformed_inbox_field_is_unavailable(
    state: dict[str, Any], trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    fake_trello.state.extra.update(state)
    ctx = make_ctx(trelloctl_home, fake_trello)
    error = fails("INBOX_UNAVAILABLE", inbox.list_cards, ctx, {})
    assert error.details["reason"]
    assert inbox_row(trelloctl_home) is None


@pytest.mark.parametrize("body", [["inbox"], {"inbox": "text"}, {"inbox": None}])
def test_a_response_without_an_inbox_object_is_unavailable(
    body: Any, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    fake_trello.router.route("GET", "/members/me")(lambda req: body)
    ctx = make_ctx(trelloctl_home, fake_trello)
    fails("INBOX_UNAVAILABLE", inbox.list_cards, ctx, {})


def test_an_unexpected_inbox_board_is_unavailable(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    fake_trello.router.route("GET", "/boards/{board_id}")(lambda req: ["not a board"])
    ctx = make_ctx(trelloctl_home, fake_trello)
    fails("INBOX_UNAVAILABLE", inbox.list_cards, ctx, {})


def test_a_cached_inbox_without_open_lists_is_resolved_again(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_account(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello)
    inbox.list_cards(ctx, {})
    fake_trello.state.lists[ids["inbox_list"]]["closed"] = True
    forget(fake_trello)
    inbox.list_cards(ctx, {"fresh": True})
    cache = open_cache(trelloctl_home)
    assert [x["closed"] for x in cache.list_lists(INBOX_BOARD)] == [1]
    fake_trello.state.lists[ids["inbox_list"]]["closed"] = False
    forget(fake_trello)
    envelope = inbox.add(ctx, {"name": "Back"})
    assert envelope["data"]["list"]["id"] == ids["inbox_list"]
    assert len([r for r in fake_trello.requests if r.path == "/members/me"]) == 1


def test_writes_use_the_cached_inbox_even_when_its_ttl_expired(
    trelloctl_home: Path, fake_trello: FakeTrello, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_account(fake_trello)
    clock = freeze(monkeypatch)
    ctx = make_ctx(trelloctl_home, fake_trello)
    inbox.list_cards(ctx, {})
    clock.advance(10_000)
    forget(fake_trello)
    envelope = inbox.add(ctx, {"name": "Late"})
    assert envelope["data"]["name"] == "Late"
    assert [r.path for r in fake_trello.requests] == ["/cards"]


def test_a_stale_read_serves_the_cache_and_reports_the_error(
    trelloctl_home: Path, fake_trello: FakeTrello, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    clock = freeze(monkeypatch)
    ctx = make_ctx(trelloctl_home, fake_trello)
    inbox.list_cards(ctx, {})
    # Metadata is still fresh; only the cards snapshot expired, and reloading it fails.
    clock.advance(301)
    fake_trello.fail_next("GET", f"/boards/{INBOX_BOARD}/cards/all", 503)
    envelope = inbox.list_cards(ctx, {})
    assert [c["name"] for c in envelope["data"]["cards"]] == ["One"]
    cache_meta = envelope["meta"]["cache"]
    assert cache_meta["stale"] is True
    assert cache_meta["hit"] is False
    assert cache_meta["error"]["code"] == "API_ERROR"


def test_the_response_of_a_create_must_be_a_card(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_account(fake_trello)
    fake_trello.router.route("POST", "/cards")(lambda req: [])
    ctx = make_ctx(trelloctl_home, fake_trello)
    fails("API_ERROR", inbox.add, ctx, {"name": "X"})


def test_the_response_of_a_move_must_be_a_card(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "X", 1)
    fake_trello.router.route("PUT", "/cards/{card_id}")(lambda req: [])
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    fails("API_ERROR", inbox.move, ctx, {"card": "X", "to": "To Do"})
    assert open_cache(trelloctl_home).get_card(BOARD, "x") is None


def test_a_move_keeps_checklists_attachments_and_the_other_cards_of_the_inbox(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_account(fake_trello)
    moved = inbox_card(fake_trello, ids, "Moved", 1)
    other = inbox_card(fake_trello, ids, "Other", 2, desc="keep me", due="2026-12-01T12:00:00.000Z")
    add_checklist(fake_trello, moved, "Steps", ("one", "two"))
    add_checklist(fake_trello, other, "Mine", ("a",))
    attachment = "a" + fake_trello.state.new_id()[1:]
    fake_trello.state.attachments[attachment] = {
        "id": attachment,
        "idCard": other,
        "name": "spec",
        "url": "https://example.com/spec",
        "isUpload": False,
    }
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    inbox.list_cards(ctx, {})
    envelope = inbox.move(ctx, {"card": moved, "to": "To Do"})
    assert envelope["meta"]["board_id"] == BOARD
    assert envelope["data"]["to"]["list"] == {"id": ids["to_do"], "name": "To Do"}
    cache = open_cache(trelloctl_home)
    assert [c["name"] for c in cache.list_checklists(BOARD, moved)] == ["Steps"]
    assert [i["name"] for i in cache.list_checklists(BOARD, moved)[0]["items"]] == ["one", "two"]
    assert cache.get_card(INBOX_BOARD, moved) is None
    kept = cache.get_card(INBOX_BOARD, other)
    assert kept is not None
    assert (kept["desc"], kept["due"]) == ("keep me", "2026-12-01T12:00:00.000Z")
    assert [c["name"] for c in cache.list_checklists(INBOX_BOARD, other)] == ["Mine"]
    assert [a["name"] for a in cache.list_attachments(INBOX_BOARD, other)] == ["spec"]
    assert not cache.is_card_deleted(INBOX_BOARD, moved)


def test_a_card_id_of_another_board_is_not_found_in_the_inbox(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_account(fake_trello)
    elsewhere = card(fake_trello, ids["todo"], "Work card", 1)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    fails("CARD_NOT_FOUND", inbox.move, ctx, {"card": elsewhere, "to": "To Do"})
    assert [r for r in fake_trello.requests if r.method == "PUT"] == []


def test_without_an_inbox_nothing_is_cached(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    seed_account(fake_trello)
    hide_inbox(fake_trello.state)
    ctx = make_ctx(trelloctl_home, fake_trello)
    fails("INBOX_UNAVAILABLE", inbox.list_cards, ctx, {})
    assert open_cache(trelloctl_home).list_boards() == []

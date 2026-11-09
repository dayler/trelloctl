"""Unit tests of the resolver service and the `cache` command handlers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    OTHER,
    SHORT,
    add_card,
    add_checklist,
    calls,
    close_caches,
    make_ctx,
    make_resolver,
    paths,
    prime,
    seed_board,
    seed_standard,
)
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello

from trelloctl.app import resolver as mod
from trelloctl.app.commands import cache as cache_cmd
from trelloctl.app.resolver import Resolver
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import Cache

T0 = datetime(2026, 3, 1, tzinfo=UTC)


def error(code: str, **details: Any) -> TrelloctlError:
    return TrelloctlError(code, "x", details)


def test_failure_classification() -> None:
    assert mod.is_transient(error("TIMEOUT"))
    assert mod.is_transient(error("API_ERROR", status=502))
    assert not mod.is_transient(error("API_ERROR", status=404))
    assert not mod.is_transient(error("AUTH_INVALID"))
    assert not mod.looks_stale(error("NETWORK_ERROR"))
    assert mod.looks_stale(error("API_ERROR", status=404))
    assert mod.looks_stale(error("API_ERROR", status=400, upstream="Invalid id"))
    assert not mod.looks_stale(error("API_ERROR", status=400, upstream="invalid value"))
    assert not mod.looks_stale(error("API_ERROR", status=500))


def test_label_reference_split() -> None:
    assert mod.split_label("bug:red") == ("bug", "red")
    assert mod.split_label("bug:Green_dark") == ("bug", "green_dark")
    assert mod.split_label("bug:unknown") is None
    assert mod.split_label("bug") is None
    assert mod.iso_utc(T0) == "2026-03-01T00:00:00.000Z"


def test_explicit_cache_and_client_are_used(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    seed_standard(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello)
    cache = Cache.memory()
    resolver = Resolver(ctx, cache=cache, client=ctx.client())
    assert resolver.cache is cache
    resolver.sync_metadata(BOARD)
    assert cache.list_lists(BOARD)
    resolver.close()  # not owned: left open
    assert cache.list_lists(BOARD)
    cache.close()


def test_cache_meta_of_member_boards_and_unknown_boards(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    clock = FakeClock(T0)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    assert resolver.cache_meta(None, "member_boards")["hit"] is False
    assert resolver.cache_meta("unknown")["hit"] is False
    prime(trelloctl_home, fake_trello, T0)
    clock.advance(5)
    again = make_resolver(trelloctl_home, fake_trello, clock)
    assert again.cache_meta(None, "member_boards") == {
        "hit": True,
        "age_seconds": 5,
        "stale": False,
    }


def test_board_reads_reject_unexpected_bodies_and_pass_other_errors(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello)
    fake_trello.fail_next("GET", f"/boards/{BOARD}", 200, "[]")
    with pytest.raises(TrelloctlError) as unexpected:
        resolver.sync_metadata(BOARD)
    assert unexpected.value.code == "API_ERROR"
    fake_trello.fail_next("GET", f"/boards/{BOARD}", 500, "boom")
    with pytest.raises(TrelloctlError) as server:
        resolver.sync_metadata(BOARD)
    assert server.value.details["status"] == 500


def test_incremental_reload_without_a_snapshot_is_a_full_load(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    assert resolver.sync_cards_incremental(BOARD) == 2
    assert paths(fake_trello) == [f"/boards/{BOARD}/cards/open"]


def test_single_card_fetch_errors(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    ids = seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    fake_trello.fail_next("GET", f"/cards/{ids['c1']}", 500, "boom")
    with pytest.raises(TrelloctlError) as server:
        resolver.sync_card(ids["c1"], board_id=BOARD)
    assert server.value.code == "API_ERROR"
    fake_trello.fail_next("GET", f"/cards/{ids['c1']}", 200, "{}")
    with pytest.raises(TrelloctlError) as unexpected:
        resolver.sync_card(ids["c1"])
    assert unexpected.value.code == "API_ERROR"
    missing = "b" * 24
    with pytest.raises(TrelloctlError) as gone:
        resolver.sync_card(missing, board_id=BOARD)
    assert gone.value.code == "CARD_NOT_FOUND"
    assert resolver.cache.is_card_deleted(BOARD, missing)
    with pytest.raises(TrelloctlError) as invalid:
        resolver.sync_card("not-valid", board_id=BOARD)
    assert invalid.value.code == "CARD_NOT_FOUND"


def test_archived_cards_follow_the_cards_ttl_and_fall_back_to_stale_data(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    add_card(fake_trello, ids["todo"], "Old", id_short=3, closed=True)
    clock = FakeClock(T0)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    prime(trelloctl_home, fake_trello, T0)
    assert resolver.ensure_archived(BOARD) is True  # never loaded
    assert resolver.ensure_archived(BOARD) is False  # fresh
    assert resolver.ensure_archived(BOARD, fresh=True) is True
    clock.advance(301)
    expired = make_resolver(trelloctl_home, fake_trello, clock)
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/closed", 503, "down")
    assert expired.ensure_archived(BOARD) is False
    assert expired.cache_meta(BOARD, "archived")["stale"] is True
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/closed", 503, "down")
    with pytest.raises(TrelloctlError):
        expired.ensure_archived(BOARD, fresh=True)


def test_metadata_and_user_boards_follow_the_stale_policy(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(2000)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    fake_trello.fail_next("GET", "/members/me/boards", 503, "down")
    fake_trello.fail_next("GET", f"/boards/{BOARD}", 503, "down")
    assert resolver.resolve_board("Work") == BOARD
    assert resolver.resolve_list(BOARD, "Todo")["name"] == "Todo"
    meta = resolver.cache_meta(BOARD, "metadata")
    assert meta["stale"] is True
    assert meta["error"]["code"] == "API_ERROR"


def test_reload_card_by_name_and_by_short_link(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0), board_flag=BOARD)
    board_id, data = resolver.reload_card("First card")
    assert (board_id, data["card"]["id"]) == (BOARD, ids["c1"])
    board_id, data = resolver.reload_card("https://trello.com/c/cardBBB2/second")
    assert (board_id, data["card"]["id"]) == (BOARD, ids["c2"])
    nobody = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    with pytest.raises(TrelloctlError) as info:
        nobody.reload_card("First card")
    assert info.value.code == "BOARD_NOT_CONFIGURED"


def test_invalid_selected_board_is_reported_not_swallowed(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, board_flag="No such board")
    with pytest.raises(TrelloctlError) as info:
        resolver.locate_card(ids["c1"])
    assert info.value.code == "BOARD_NOT_FOUND"


def test_board_resolution_is_memoized_and_uses_cached_short_links(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    assert resolver.resolve_board("work") == BOARD
    count = len(fake_trello.requests)
    assert resolver.resolve_board("work") == BOARD
    assert len(fake_trello.requests) == count
    # The mapping shortLink -> id comes from the cached user boards, then from board rows.
    by_url = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    assert by_url.resolve_board("https://trello.com/b/other001/x") == OTHER
    assert len(fake_trello.requests) == count
    by_url.cache.replace_metadata(OTHER, {"name": "Other", "shortLink": "other001"}, [], [], [], T0)
    by_url.cache.replace_member_boards([], T0)
    assert make_resolver(trelloctl_home, fake_trello).resolve_board("other001") == OTHER


def test_card_by_url_uses_the_cache_and_rejects_foreign_boards(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    prime(trelloctl_home, fake_trello, T0)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    fake_trello.requests.clear()
    assert resolver.resolve_card(BOARD, "https://trello.com/c/cardAAA1/x")["id"] == ids["c1"]
    assert fake_trello.requests == []
    assert resolver.resolve_card(BOARD, "cardAAA1")["id"] == ids["c1"]  # bare shortLink
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_card(OTHER, "https://trello.com/c/cardAAA1/x")
    assert info.value.code == "CARD_NOT_FOUND"


def test_ambiguous_short_link_candidates_are_reported(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    fake_trello.state.cards[ids["c2"]]["shortLink"] = "cardAAA1"
    prime(trelloctl_home, fake_trello, T0)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_card(BOARD, "cardAAA1")
    assert info.value.code == "AMBIGUOUS_CARD"


def test_locate_card_with_a_selected_board_finds_names_and_cached_ids(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0), board_flag=BOARD)
    fake_trello.requests.clear()
    assert resolver.locate_card("first card").card["id"] == ids["c1"]
    assert resolver.locate_card(ids["c2"]).board_id == BOARD
    assert resolver.locate_card("https://trello.com/c/cardBBB2/x").card["id"] == ids["c2"]
    assert fake_trello.requests == []


def test_checklist_and_item_reload_the_card_once(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    checklist = add_checklist(fake_trello, ids["c1"], "QA", ("one",))
    prime(trelloctl_home, fake_trello, T0)
    fake_trello.state.checklists[checklist]["checkItems"].append(
        {"id": "f" * 24, "name": "two", "state": "incomplete", "pos": 2}
    )
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    _, item = resolver.resolve_checkitem(BOARD, ids["c1"], "QA", "two")
    assert item["id"] == "f" * 24
    with pytest.raises(TrelloctlError) as missing:
        resolver.resolve_checkitem(BOARD, ids["c1"], "QA", "three")
    assert missing.value.code == "CHECKITEM_NOT_FOUND"
    assert len(calls(fake_trello, f"/cards/{ids['c1']}")) == 1


def test_guarded_write_errors(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))

    def boom(row: dict[str, Any]) -> None:
        raise error("API_ERROR", status=422)

    with pytest.raises(TrelloctlError) as plain:
        resolver.guarded_write("list", BOARD, "Todo", boom)
    assert plain.value.details["status"] == 422
    calls_made: list[str] = []

    def stale_then_other(row: dict[str, Any]) -> None:
        calls_made.append(row["id"])
        raise (
            error("API_ERROR", status=404)
            if len(calls_made) == 1
            else error("API_ERROR", status=422)
        )

    with pytest.raises(TrelloctlError) as other:
        resolver.guarded_write("list", BOARD, "Todo", stale_then_other)
    assert other.value.details["status"] == 422
    assert len(calls_made) == 2
    with pytest.raises(ValueError, match="card_id"):
        resolver.guarded_write("checklist", BOARD, "QA", boom)
    with pytest.raises(ValueError, match="checklist reference"):
        resolver.guarded_write("checkitem", BOARD, "one", boom, card_id=ids["c1"])
    fake_trello.fail_next("GET", f"/lists/{ids['todo']}", 500, "boom")

    def stale(row: dict[str, Any]) -> None:
        raise error("API_ERROR", status=404)

    with pytest.raises(TrelloctlError) as confirm:
        resolver.guarded_write("list", BOARD, ids["todo"], stale)
    assert confirm.value.details["status"] == 500


def test_reload_command_rejects_conflicting_scopes_and_cache_commands_in_memory(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ctx = make_ctx(trelloctl_home, fake_trello, no_cache=True)
    with pytest.raises(TrelloctlError) as info:
        cache_cmd.reload(ctx, {"list": "a", "archived": True})
    assert info.value.code == "USAGE"
    status = cache_cmd.status(ctx, {})["data"]
    assert status == {
        "boards": [],
        "member_boards": {"count": 0, "age_seconds": None},
        "attachments_bytes": 0,
        "database_bytes": 0,
    }
    cleared = cache_cmd.clear(ctx, {"attachments": True})["data"]
    assert cleared == {"cleared": True, "attachments_removed": 0, "attachments_kept": False}
    assert not (trelloctl_home / "cache.db").exists()


def test_status_reports_the_age_of_each_collection(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, datetime.now(UTC) - timedelta(seconds=30))
    ctx = make_ctx(trelloctl_home, fake_trello)
    data = cache_cmd.status(ctx, {})["data"]
    assert 29 <= data["member_boards"]["age_seconds"] <= 90
    [board] = data["boards"]
    assert board["cards"]["stale"] is False
    assert SHORT  # the shared constants stay importable for the other suites

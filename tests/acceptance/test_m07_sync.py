"""M07 acceptance tests: full, incremental and partial reloads, TTLs and the stale policy."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    add_card,
    add_checklist,
    add_list,
    calls,
    close_caches,
    make_resolver,
    new_id,
    paths,
    prime,
    probe_registry,
    run_json,
    seed_standard,
    write_config,
)
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver, iso_utc
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import Cache

T0 = datetime(2026, 3, 1, tzinfo=UTC)
PAST = datetime(2020, 1, 1, tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


def stamps(cache: Cache, board_id: str = BOARD) -> dict[str, Any]:
    row = cache.get_board(board_id)
    assert row is not None
    return row


# --- full reload (AC-22.6) ---


@pytest.mark.tc("TC-M07-001")
def test_full_reload_makes_three_reads_and_replaces_the_snapshot(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    old = Cache.open(trelloctl_home)
    gone = "e" * 24
    old.replace_snapshot(
        BOARD,
        {"name": "Old"},
        [{"id": ids["todo"], "name": "Todo", "pos": 1}],
        [],
        [],
        [{"id": gone, "name": "gone", "idList": ids["todo"]}],
        PAST,
    )
    old.replace_member_boards([{"id": BOARD, "name": "Old"}], PAST)
    code, envelope = run_json(capsys, "cache", "reload")
    assert (code, envelope["ok"]) == (0, True)
    assert paths(fake_trello) == [
        "/members/me/boards",
        f"/boards/{BOARD}",
        f"/boards/{BOARD}/cards/open",
    ]
    assert all(r.method == "GET" for r in fake_trello.requests)
    query = calls(fake_trello, f"/boards/{BOARD}/cards/open")[0].query
    assert query["attachments"] == ["true"]
    assert query["checklists"] == ["all"]
    assert query["limit"] == ["1000"]
    nested = calls(fake_trello, f"/boards/{BOARD}")[0].query
    assert (nested["lists"], nested["labels"], nested["members"]) == (["all"], ["all"], ["all"])
    assert envelope["meta"]["board_id"] == BOARD
    cache = Cache.open(trelloctl_home)
    assert {c["id"] for c in cache.list_cards(BOARD)} == {ids["c1"], ids["c2"]}
    assert cache.get_card(BOARD, gone) is None
    row = stamps(cache)
    floor = PAST.timestamp() + 1
    assert row["metadata_fetched_at"] > floor
    assert row["cards_fetched_at"] > floor
    assert min(r["fetched_at"] for r in cache.member_boards()) > floor
    assert cache.member_boards()[0]["name"] == "Work"


@pytest.mark.tc("TC-M07-002")
def test_a_board_of_2500_cards_is_read_in_three_pages_of_1000(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    for number in range(3, 2501):
        add_card(fake_trello, ids["todo"], f"card {number}", id_short=number)
    del fake_trello.state.cards[ids["c1"]], fake_trello.state.cards[ids["c2"]]
    write_config(trelloctl_home, fake_trello, board=BOARD)
    code, envelope = run_json(capsys, "cache", "reload")
    assert (code, envelope["ok"]) == (0, True)
    reads = calls(fake_trello, f"/boards/{BOARD}/cards/open")
    assert len(reads) == 3
    newest_first = sorted(fake_trello.state.cards, reverse=True)
    assert [r.query.get("before") for r in reads] == [
        None,
        [newest_first[999]],
        [newest_first[1999]],
    ]
    assert all(r.query["limit"] == ["1000"] for r in reads)
    assert len(Cache.open(trelloctl_home).list_cards(BOARD)) == 2498
    assert (envelope["data"]["cards"], envelope["data"]["pages"]) == (2498, 3)


@pytest.mark.tc("TC-M07-003")
def test_exactly_1000_cards_end_with_an_empty_second_page(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    del fake_trello.state.cards[ids["c2"]]
    for number in range(2, 1001):
        add_card(fake_trello, ids["todo"], f"card {number}", id_short=number)
    assert len(fake_trello.state.cards) == 1000
    write_config(trelloctl_home, fake_trello, board=BOARD)
    code, envelope = run_json(capsys, "cache", "reload")
    assert (code, envelope["ok"]) == (0, True)
    reads = calls(fake_trello, f"/boards/{BOARD}/cards/open")
    assert len(reads) == 2
    assert reads[1].query["before"] == [min(fake_trello.state.cards)]
    assert envelope["data"]["pages"] == 2
    assert len(Cache.open(trelloctl_home).list_cards(BOARD)) == 1000


# --- partial reloads ---


@pytest.mark.tc("TC-M07-004")
def test_reload_list_updates_only_that_list_and_keeps_the_snapshot_ttl(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    prime(trelloctl_home, fake_trello, now())
    cache = Cache.open(trelloctl_home)
    before = dict(stamps(cache))
    fake_trello.state.cards[ids["c1"]]["name"] = "First renamed"
    fake_trello.state.cards[ids["c2"]]["name"] = "Second renamed"
    fake_trello.requests.clear()
    code, envelope = run_json(capsys, "cache", "reload", "--list", "todo")
    assert (code, envelope["ok"]) == (0, True)
    assert paths(fake_trello) == [f"/lists/{ids['todo']}/cards"]
    assert envelope["data"]["list"] == {"id": ids["todo"], "name": "Todo"}
    assert cache.get_card(BOARD, ids["c1"])["name"] == "First renamed"  # type: ignore[index]
    assert cache.get_card(BOARD, ids["c2"])["name"] == "Second card"  # type: ignore[index]
    after = stamps(cache)
    assert after["cards_fetched_at"] == before["cards_fetched_at"]
    assert after["metadata_fetched_at"] == before["metadata_fetched_at"]


@pytest.mark.tc("TC-M07-005")
def test_reload_card_stores_attachments_and_checklists_with_its_own_stamp(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    prime(trelloctl_home, fake_trello, PAST)
    cache = Cache.open(trelloctl_home)
    before = dict(stamps(cache))
    attachment = new_id(fake_trello)
    fake_trello.state.attachments[attachment] = {
        "id": attachment,
        "idCard": ids["c1"],
        "name": "spec.txt",
        "bytes": 12,
        "isUpload": True,
    }
    add_checklist(fake_trello, ids["c1"], "QA", ("unit", "e2e"))
    fake_trello.state.cards[ids["c1"]]["name"] = "First renamed"
    fake_trello.requests.clear()
    code, envelope = run_json(capsys, "cache", "reload", "--card", ids["c1"])
    assert (code, envelope["ok"]) == (0, True)
    assert paths(fake_trello) == [f"/cards/{ids['c1']}"]
    assert envelope["meta"]["board_id"] == BOARD
    row = cache.get_card(BOARD, ids["c1"])
    assert row is not None
    assert row["name"] == "First renamed"
    assert row["fetched_at"] > PAST.timestamp() + 1
    assert [a["name"] for a in cache.list_attachments(BOARD, ids["c1"])] == ["spec.txt"]
    [checklist] = cache.list_checklists(BOARD, ids["c1"])
    assert [i["name"] for i in checklist["items"]] == ["unit", "e2e"]
    after = stamps(cache)
    assert after["cards_fetched_at"] == before["cards_fetched_at"]
    assert after["metadata_fetched_at"] == before["metadata_fetched_at"]


@pytest.mark.tc("TC-M07-006")
def test_reload_archived_stores_closed_cards_as_archived(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_standard(fake_trello)
    old = add_card(fake_trello, ids["todo"], "Old one", id_short=3, closed=True)
    older = add_card(fake_trello, ids["doing"], "Older one", id_short=4, closed=True)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    code, envelope = run_json(capsys, "cache", "reload", "--archived")
    assert (code, envelope["ok"]) == (0, True)
    assert paths(fake_trello) == [f"/boards/{BOARD}/cards/closed"]
    assert envelope["data"] == {"scope": "archived", "cards": 2}
    cache = Cache.open(trelloctl_home)
    archived = {c["id"] for c in cache.list_cards(BOARD, include_archived=True) if c["archived"]}
    assert archived == {old, older}
    assert cache.list_cards(BOARD) == []
    row = stamps(cache)
    assert row["archived_fetched_at"] is not None
    assert row["cards_fetched_at"] is None


@pytest.mark.tc("TC-M07-007")
def test_reload_with_list_and_card_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    code, envelope = run_json(capsys, "cache", "reload", "--list", "X", "--card", "Y")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


# --- incremental reload and TTLs (AC-22.4, AC-22.5, AC-22.7) ---


@pytest.mark.tc("TC-M07-008")
def test_expired_card_ttl_reloads_incrementally_with_modified_since(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    changed = iso_utc(T0 + timedelta(seconds=100))
    fake_trello.state.cards[ids["c1"]].update(name="First renamed", dateLastActivity=changed)
    fake_trello.state.cards[ids["c2"]].update(closed=True, dateLastActivity=changed)
    c3 = add_card(fake_trello, ids["todo"], "Third", id_short=3, changed=changed)
    clock = FakeClock(T0)
    clock.advance(301)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    fake_trello.requests.clear()
    assert resolver.ensure_cards(BOARD) is True
    [request] = calls(fake_trello, f"/boards/{BOARD}/cards/all")
    assert request.query["modifiedSince"] == [iso_utc(T0)]
    assert paths(fake_trello) == [f"/boards/{BOARD}/cards/all"]
    cache = resolver.cache
    assert cache.get_card(BOARD, ids["c1"])["name"] == "First renamed"  # type: ignore[index]
    assert cache.get_card(BOARD, c3) is not None
    assert cache.get_card(BOARD, ids["c2"])["archived"] == 1  # type: ignore[index]
    assert {c["id"] for c in cache.list_cards(BOARD)} == {ids["c1"], c3}
    assert stamps(cache)["cards_fetched_at"] == clock.time()
    assert resolver.ensure_cards(BOARD) is False


@pytest.mark.tc("TC-M07-009")
def test_hard_deleted_cards_survive_incremental_reloads_but_not_a_full_reload(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    del fake_trello.state.cards[ids["c2"]]
    clock = FakeClock(T0)
    clock.advance(301)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    resolver.ensure_cards(BOARD)
    assert resolver.cache.get_card(BOARD, ids["c2"]) is not None
    resolver.reload_all(BOARD)
    assert resolver.cache.get_card(BOARD, ids["c2"]) is None
    assert resolver.cache.get_card(BOARD, ids["c1"]) is not None


@pytest.mark.tc("TC-M07-010")
def test_auto_reload_renews_the_ttl_but_a_write_through_row_does_not(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(400)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    assert resolver.ensure_cards(BOARD) is True
    reloaded = stamps(resolver.cache)["cards_fetched_at"]
    assert reloaded == clock.time()
    clock.advance(10)
    card = dict(fake_trello.state.cards[ids["c1"]], name="Written through")
    resolver.cache.upsert_card(BOARD, card, clock.now())
    assert stamps(resolver.cache)["cards_fetched_at"] == reloaded
    assert resolver.cache.get_card(BOARD, ids["c1"])["fetched_at"] == clock.time()  # type: ignore[index]
    clock.advance(289)  # 299 s after the reload: still fresh despite the later write
    assert resolver.cache.cards_fresh(BOARD, clock.now(), 300) is True
    clock.advance(1)  # A-8: age equal to the TTL expires
    assert resolver.cache.cards_fresh(BOARD, clock.now(), 300) is False


@pytest.mark.tc("TC-M07-011")
def test_a_snapshot_marked_stale_reloads_with_the_previous_stamp(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(10)  # well inside the TTL
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    assert resolver.ensure_cards(BOARD) is False
    resolver.cache.mark_cards_stale(BOARD)
    changed = iso_utc(T0 + timedelta(seconds=5))
    fake_trello.state.cards[ids["c1"]].update(name="Changed", dateLastActivity=changed)
    fake_trello.requests.clear()
    assert resolver.ensure_cards(BOARD) is True
    [request] = calls(fake_trello, f"/boards/{BOARD}/cards/all")
    assert request.query["modifiedSince"] == [iso_utc(T0)]
    assert resolver.cache.get_card(BOARD, ids["c1"])["name"] == "Changed"  # type: ignore[index]
    row = stamps(resolver.cache)
    assert row["cards_stale"] == 0
    assert row["cards_fetched_at"] == clock.time()


# --- stale policy (AC-22.10, A-10) ---


def read_probe(clock: FakeClock) -> Callable[[CommandContext, Mapping[str, Any]], Any]:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        with Resolver(ctx, clock=clock.now) as resolver:
            board = resolver.selected_board()
            resolver.ensure_cards(board)
            names = sorted(c["name"] for c in resolver.cache.list_cards(board))
            meta = {"cache": resolver.cache_meta(board, "cards")}
            return ctx.success({"cards": names}, board_id=board, meta=meta)

    return handler


def inject(fake: FakeTrello, kind: str) -> None:
    path = f"/boards/{BOARD}/cards/all"
    if kind == "NETWORK_ERROR":
        fake.fail_next("GET", path, drop=True)
    elif kind == "TIMEOUT":
        fake.fail_next("GET", path, None, delay=1.6)
    elif kind == "RATE_LIMITED":
        fake.rate_limit("GET", path)
    else:
        fake.fail_next("GET", path, 503, "unavailable")


@pytest.mark.tc("TC-M07-012")
@pytest.mark.parametrize("kind", ["NETWORK_ERROR", "TIMEOUT", "RATE_LIMITED", "API_ERROR"])
def test_expired_ttl_and_a_transient_failure_serve_stale_data_with_exit_9(
    kind: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD, timeout=1)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(301)
    inject(fake_trello, kind)
    code, envelope = run_json(capsys, "probe", "read", registry=probe_registry(read_probe(clock)))
    assert code == 9
    assert envelope["ok"] is True
    assert envelope["data"]["cards"] == ["First card", "Second card"]
    cache_meta = envelope["meta"]["cache"]
    assert cache_meta["stale"] is True
    assert cache_meta["hit"] is True
    assert cache_meta["age_seconds"] == 301
    assert cache_meta["error"]["code"] == kind
    if kind == "API_ERROR":
        assert cache_meta["error"]["details"]["status"] == 503


@pytest.mark.tc("TC-M07-013")
@pytest.mark.parametrize(
    ("status", "body", "code", "exit_code"),
    [(401, "invalid token", "AUTH_INVALID", 4), (400, "bad request", "API_ERROR", 7)],
)
def test_expired_ttl_and_a_non_transient_failure_propagate(
    status: int,
    body: str,
    code: str,
    exit_code: int,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(301)
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/all", status, body)
    result, envelope = run_json(capsys, "probe", "read", registry=probe_registry(read_probe(clock)))
    assert (result, envelope["ok"]) == (exit_code, False)
    assert envelope["error"]["code"] == code
    assert "data" not in envelope


@pytest.mark.tc("TC-M07-014")
def test_a_failed_reload_without_cached_data_returns_the_original_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/open", drop=True)
    clock = FakeClock(T0)
    code, envelope = run_json(capsys, "probe", "read", registry=probe_registry(read_probe(clock)))
    assert (code, envelope["ok"]) == (8, False)
    assert envelope["error"]["code"] == "NETWORK_ERROR"
    assert "CACHE_STALE" not in str(envelope)


@pytest.mark.tc("TC-M07-015")
def test_writes_never_use_stale_data_when_the_reload_fails(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(2000)  # metadata (1800 s) and cards (300 s) expired
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    fake_trello.fail_next("GET", f"/boards/{BOARD}", drop=True)
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_list(BOARD, "Todo", write=True)
    assert info.value.code == "NETWORK_ERROR"
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/all", 503, "down")
    with pytest.raises(TrelloctlError) as again:
        resolver.resolve_card(BOARD, "First card", write=True)
    assert again.value.code == "API_ERROR"
    assert again.value.details["status"] == 503
    # The same failures on reads are served stale.
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/all", 503, "down")
    assert resolver.ensure_cards(BOARD) is False
    assert resolver.resolve_card(BOARD, ids["c1"])["id"] == ids["c1"]


@pytest.mark.tc("TC-M07-016")
def test_meta_cache_reports_hit_age_and_stale(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(100)
    fresh = make_resolver(trelloctl_home, fake_trello, clock)
    fresh.ensure_cards(BOARD)
    assert fresh.cache_meta(BOARD) == {"hit": True, "age_seconds": 100, "stale": False}
    clock.advance(300)
    expired = make_resolver(trelloctl_home, fake_trello, clock)
    expired.ensure_cards(BOARD)
    assert expired.cache_meta(BOARD) == {"hit": False, "age_seconds": 0, "stale": False}
    assert expired.cache_meta(BOARD, "metadata")["hit"] is True


@pytest.mark.tc("TC-M07-016")
def test_no_cache_reports_no_hit_and_never_creates_cache_db(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, no_cache=True)
    with resolver:
        resolver.ensure_metadata(BOARD)
        resolver.ensure_cards(BOARD)
        assert resolver.cache_meta(BOARD) == {"hit": False, "age_seconds": 0, "stale": False}
        assert len(resolver.cache.list_cards(BOARD)) == 2
    assert not (trelloctl_home / "cache.db").exists()


# --- lists that disappear, fresh metadata and expired collections ---


@pytest.mark.tc("TC-M07-037")
def test_cards_of_a_vanished_list_leave_the_cache_and_are_fetched_on_demand(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    del fake_trello.state.lists[ids["doing"]]
    fake_trello.state.cards[ids["c2"]]["idList"] = ids["todo"]
    resolver = make_resolver(trelloctl_home, fake_trello, FakeClock(T0))
    resolver.sync_metadata(BOARD)
    assert resolver.cache.get_card(BOARD, ids["c2"]) is None
    assert resolver.cache.get_card(BOARD, ids["c1"]) is not None
    fake_trello.requests.clear()
    row = resolver.resolve_card(BOARD, ids["c2"])
    assert paths(fake_trello) == [f"/cards/{ids['c2']}"]
    assert row["id_list"] == ids["todo"]
    assert resolver.cache.get_card(BOARD, ids["c2"])["id_list"] == ids["todo"]  # type: ignore[index]


@pytest.mark.tc("TC-M07-038")
def test_fresh_metadata_makes_the_complete_call_even_within_the_ttl(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(10)
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    third = add_list(fake_trello, "Done", 3)
    fake_trello.requests.clear()
    lists = resolver.fresh_metadata(BOARD)
    assert paths(fake_trello) == [f"/boards/{BOARD}"]
    query = calls(fake_trello, f"/boards/{BOARD}")[0].query
    assert (query["lists"], query["labels"], query["members"]) == (["all"], ["all"], ["all"])
    assert [item["id"] for item in lists] == [ids["todo"], ids["doing"], third]
    assert stamps(resolver.cache)["metadata_fetched_at"] == clock.time()


@pytest.mark.tc("TC-M07-042")
def test_expired_metadata_and_user_boards_reload_with_their_full_call_before_resolving(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    clock = FakeClock(T0)
    clock.advance(1801)
    renamed = add_list(fake_trello, "Brand new", 3)
    fake_trello.state.boards[BOARD]["name"] = "Work renamed"
    resolver = make_resolver(trelloctl_home, fake_trello, clock)
    fake_trello.requests.clear()
    assert resolver.resolve_board("work renamed") == BOARD
    assert resolver.resolve_list(BOARD, "brand new")["id"] == renamed
    assert paths(fake_trello) == ["/members/me/boards", f"/boards/{BOARD}"]
    boards_query = calls(fake_trello, "/members/me/boards")[0].query
    assert boards_query["filter"] == ["all"]
    assert resolver.resolve_list(BOARD, "todo")["id"] == ids["todo"]
    assert len(fake_trello.requests) == 2

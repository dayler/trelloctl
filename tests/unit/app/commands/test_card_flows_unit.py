"""Unit tests of `app/commands/card.py` error and edge paths, run through the CLI and the fake."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.acceptance.m09_support import (
    BOARD,
    OLD,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    finish,
    run,
    seed_world,
)
from tests.fakes.trello import FakeTrello


def test_a_failed_reload_serves_the_cached_cards_as_stale(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello, when=OLD)
    fake_trello.fail_next("GET", f"/boards/{BOARD}", 500)
    fake_trello.fail_next("GET", f"/boards/{BOARD}/cards/all", 500)
    code, out = run(capsys, "card", "list")
    assert (code, out["ok"]) == (9, True)
    assert out["meta"]["cache"]["stale"] is True
    assert out["meta"]["cache"]["error"]["code"] == "API_ERROR"
    assert [c["name"] for c in out["data"]["cards"]] == ["X"]


def test_a_card_that_is_not_fresh_reloads_the_snapshot_incrementally(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "Before", 1)
    finish(trelloctl_home, fake_trello, when=OLD)
    fake_trello.state.cards[target].update(name="After", dateLastActivity="2030-01-01T00:00:00Z")
    code, out = run(capsys, "card", "get", target)
    assert (code, out["data"]["name"]) == (0, "After")
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/all")) == 1
    assert out["meta"]["cache"]["hit"] is False


def test_an_unexpected_write_response_is_an_api_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("PUT", "/cards/{id}", 200, "{}")
    code, out = run(capsys, "card", "done", target)
    assert (code, out["error"]["code"]) == (7, "API_ERROR")


def test_a_partial_write_response_keeps_the_cached_values(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "Keep me", 1, labels=(ids["bug"],), desc="text")
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next(
        "PUT", "/cards/{id}", 200, json.dumps({"id": target, "dueComplete": True})
    )
    code, out = run(capsys, "card", "done", target)
    assert (code, out["ok"]) == (0, True)
    assert (out["data"]["name"], out["data"]["desc"]) == ("Keep me", "text")
    assert [x["name"] for x in out["data"]["labels"]] == ["bug"]
    assert out["data"]["dueComplete"] is True
    full = {
        "id": target,
        "name": "Full",
        "attachments": [],
        "checklists": [],
        "idList": ids["todo"],
    }
    fake_trello.fail_next("PUT", "/cards/{id}", 200, json.dumps(full))
    code, out = run(capsys, "card", "undone", target)
    assert (code, out["data"]["name"]) == (0, "Full")
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["name"] == "Full"


def test_comments_of_a_card_ignore_malformed_items(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("GET", "/cards/{id}/actions", 200, '["junk", 3]')
    code, out = run(capsys, "card", "get", target, "--with", "comments")
    assert (code, out["data"]["comments"]) == (0, [])
    fake_trello.fail_next("GET", "/cards/{id}/actions", 200, '{"unexpected": true}')
    code, out = run(capsys, "card", "get", target, "--with", "comments")
    assert (code, out["data"]["comments"]) == (0, [])


def test_a_copy_whose_read_back_fails_is_still_reported(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    source = card(fake_trello, ids["todo"], "Original", 1)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("GET", "/cards/{id}", 500)
    code, out = run(capsys, "card", "copy", source, "--to", "Done")
    assert (code, out["ok"], out["data"]["name"]) == (0, True, "Original")
    assert cached(trelloctl_home, out["data"]["id"]) is not None


@pytest.mark.parametrize(
    "argv",
    [
        ["card", "search", " "],
        ["card", "create", "--list", "Todo", "--name", " "],
        ["card", "update", "X", "--name", " "],
        ["card", "update", "X"],
        ["card", "list", "--due-before", "tomorrow"],
        ["card", "list", "--due-after", "tomorrow"],
        ["card", "list", "--limit", "-3"],
        ["card", "move", "X", "--to", "Done", "--pos", "bad"],
    ],
)
def test_bad_arguments_fail_before_any_request(
    argv: list[str],
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, *argv)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


def test_move_to_the_same_edge_with_clamp_writes_nothing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    last = card(fake_trello, ids["done"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", "--right", "1", "--clamp")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["from"] == out["data"]["to"]
    assert [r for r in fake_trello.requests if r.method == "PUT"] == []
    assert fake_trello.state.cards[last]["idList"] == ids["done"]


def test_listing_filters_resolve_names_and_skip_cards_without_a_due_date(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "no due", 1)
    dated = card(fake_trello, ids["todo"], "dated", 2, due="2026-12-05T10:00:00.000Z")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "list", "--due-after", "2026-12-01")
    assert [c["id"] for c in out["data"]["cards"]] == [dated]
    code, out = run(capsys, "card", "list", "--due-before", "2026-12-01")
    assert out["data"]["cards"] == []
    code, out = run(capsys, "card", "list", "--label", "nothing")
    assert (code, out["error"]["code"]) == (5, "LABEL_NOT_FOUND")
    code, out = run(capsys, "card", "list", "--member", "nobody")
    assert (code, out["error"]["code"]) == (5, "MEMBER_NOT_FOUND")
    code, out = run(capsys, "card", "list", "--list", "Nowhere")
    assert (code, out["error"]["code"]) == (5, "LIST_NOT_FOUND")


def test_no_cache_mode_reads_everything_from_trello(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "--no-cache", "card", "get", target)
    assert (code, out["ok"], out["meta"]["cache"]["hit"]) == (0, True, False)
    code, out = run(capsys, "--no-cache", "card", "list")
    assert [c["id"] for c in out["data"]["cards"]] == [target]
    code, out = run(capsys, "--no-cache", "card", "done", "X")
    assert (code, out["ok"]) == (0, True)

"""M08 acceptance tests: `board show|lists|labels|members` and their cache behaviour (RF-8)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m08_support import (  # noqa: F401
    BOARD,
    OTHER,
    SHORT,
    add_card,
    add_label,
    add_list,
    add_member,
    ago,
    close_caches,
    configure,
    prime,
    run_json,
    seed_board,
    seed_standard,
    write_config,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

Capsys = pytest.CaptureFixture[str]


def requests_after(fake: FakeTrello, mark: int) -> list[str]:
    return [f"{r.method} {r.path}" for r in fake.requests[mark:]]


# --- board show (AC-8.1) ---


@pytest.mark.tc("TC-M08-001")
def test_board_show_of_the_default_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "show")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"] == {
        "id": BOARD,
        "short_link": SHORT,
        "name": "Work",
        "url": f"https://trello.com/b/{SHORT}/work",
        "is_default": True,
        "lists": 2,
        "cards": 2,
        "labels": 1,
    }
    assert envelope["meta"]["command"] == "board show"
    assert envelope["meta"]["board_id"] == BOARD


@pytest.mark.tc("TC-M08-002")
def test_board_show_of_another_board_is_not_the_default(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    add_list(fake_trello, "Only", 1, OTHER)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "--board", OTHER, "board", "show")
    assert code == 0
    assert envelope["data"]["id"] == OTHER
    assert envelope["data"]["is_default"] is False
    assert (envelope["data"]["lists"], envelope["data"]["cards"]) == (1, 0)
    code, envelope = run_json(capsys, "board", "show")
    assert (code, envelope["data"]["is_default"]) == (0, True)


# --- board lists, labels and members (AC-8.2, AC-8.3) ---


@pytest.mark.tc("TC-M08-003")
def test_board_lists_are_open_lists_by_position_with_index_and_card_count(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_board(fake_trello)
    second = add_list(fake_trello, "Second", 20)
    first = add_list(fake_trello, "First", 10)
    third = add_list(fake_trello, "Third", 30)
    add_list(fake_trello, "Gone", 15, closed=True)
    for number, list_id in enumerate((first, first, third), 1):
        add_card(fake_trello, list_id, f"card {number}", id_short=number)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "lists")
    assert code == 0
    assert envelope["data"]["lists"] == [
        {"index": 0, "id": first, "name": "First", "closed": False, "cards": 2},
        {"index": 1, "id": second, "name": "Second", "closed": False, "cards": 0},
        {"index": 2, "id": third, "name": "Third", "closed": False, "cards": 1},
    ]


@pytest.mark.tc("TC-M08-004")
def test_board_lists_all_includes_the_archived_lists(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_board(fake_trello)
    first = add_list(fake_trello, "First", 10)
    gone = add_list(fake_trello, "Gone", 15, closed=True)
    last = add_list(fake_trello, "Last", 20)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "lists", "--all")
    assert code == 0
    assert [(x["id"], x["closed"], x["index"]) for x in envelope["data"]["lists"]] == [
        (first, False, 0),
        (gone, True, None),
        (last, False, 1),
    ]


@pytest.mark.tc("TC-M08-005")
def test_board_labels_and_members(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_board(fake_trello)
    named = add_label(fake_trello, "bug", "red")
    unnamed = add_label(fake_trello, "", "green")
    plain = add_label(fake_trello, "", None)
    ana = add_member(fake_trello, "ana", "Ana Gomez")
    bob = add_member(fake_trello, "bob", "Bob Ruiz")
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "labels")
    assert code == 0
    assert {(x["id"], x["name"], x["color"]) for x in envelope["data"]["labels"]} == {
        (named, "bug", "red"),
        (unnamed, "", "green"),
        (plain, "", None),
    }
    code, envelope = run_json(capsys, "board", "members")
    assert code == 0
    assert envelope["data"]["members"] == [
        {"id": ana, "username": "ana", "full_name": "Ana Gomez"},
        {"id": bob, "username": "bob", "full_name": "Bob Ruiz"},
    ]


# --- cache behaviour (AC-8.4, AC-13.6) ---


@pytest.mark.tc("TC-M08-006")
@pytest.mark.parametrize("command", ["show", "lists", "labels", "members"])
def test_a_second_read_within_the_ttl_makes_no_request(
    command: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, first = run_json(capsys, "board", command)
    assert (code, first["meta"]["cache"]["hit"]) == (0, False)
    mark = len(fake_trello.requests)
    code, second = run_json(capsys, "board", command)
    assert code == 0
    assert len(fake_trello.requests) == mark
    assert second["meta"]["cache"]["hit"] is True
    assert second["meta"]["cache"]["stale"] is False
    assert second["data"] == first["data"]


@pytest.mark.tc("TC-M08-007")
def test_fresh_metadata_with_an_expired_card_snapshot_reloads_only_the_cards(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(600))  # metadata TTL 1800 s, cards TTL 300 s
    add_card(fake_trello, ids["todo"], "Third card", id_short=3, changed="2099-01-01T00:00:00.000Z")
    mark = len(fake_trello.requests)
    code, envelope = run_json(capsys, "board", "lists")
    assert code == 0
    assert requests_after(fake_trello, mark) == [f"GET /boards/{BOARD}/cards/all"]
    assert [x["cards"] for x in envelope["data"]["lists"]] == [2, 1]
    assert envelope["meta"]["cache"]["hit"] is False


@pytest.mark.tc("TC-M08-008")
@pytest.mark.parametrize("command", ["show", "lists", "labels", "members"])
def test_fresh_reads_from_trello_and_renews_the_ttl(
    command: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(100))
    before = Cache.open(trelloctl_home).get_board(BOARD)
    assert before is not None
    add_label(fake_trello, "late", "blue")
    add_member(fake_trello, "bob", "Bob Ruiz")
    add_list(fake_trello, "Late", 3)
    add_card(fake_trello, ids["todo"], "Late card", id_short=3, changed="2099-01-01T00:00:00.000Z")
    mark = len(fake_trello.requests)
    code, envelope = run_json(capsys, "board", command, "--fresh")
    assert (code, envelope["meta"]["cache"]["hit"]) == (0, False)
    issued = requests_after(fake_trello, mark)
    assert f"GET /boards/{BOARD}" in issued
    assert (f"GET /boards/{BOARD}/cards/all" in issued) is (command in ("show", "lists"))
    after = Cache.open(trelloctl_home).get_board(BOARD)
    assert after is not None
    assert after["metadata_fetched_at"] > before["metadata_fetched_at"] + 50
    if command in ("show", "lists"):
        assert after["cards_fetched_at"] > before["cards_fetched_at"] + 50
    data: dict[str, Any] = envelope["data"]
    if command == "show":
        assert (data["lists"], data["cards"], data["labels"]) == (3, 3, 2)
    elif command == "lists":
        assert [x["name"] for x in data["lists"]] == ["Todo", "Doing", "Late"]
    elif command == "labels":
        assert {x["name"] for x in data["labels"]} == {"bug", "late"}
    else:
        assert {x["username"] for x in data["members"]} == {"ana", "bob"}


@pytest.mark.tc("TC-M08-009")
def test_init_credentials_and_board_show_create_the_working_directory(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: Capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_standard(fake_trello)
    code, envelope = run_json(capsys, "config", "init")
    assert (code, envelope["ok"]) == (0, True)
    config = trelloctl_home / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8") + f'\n[http]\nbase_url = "{fake_trello.base_url}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRELLO_API_KEY", fake_trello.api_key)
    monkeypatch.setenv("TRELLO_TOKEN", fake_trello.token)
    code, envelope = run_json(capsys, "--board", BOARD, "board", "show")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["name"] == "Work"
    assert config.is_file()
    assert (trelloctl_home / "logs").is_dir()
    assert (trelloctl_home / "cache.db").is_file()
    assert (trelloctl_home / "attachments").is_dir()


# --- stale fallback and the unknown `list delete` (AC-22.10, AC-9.3) ---


@pytest.mark.tc("TC-M08-040")
def test_an_expired_ttl_with_trello_down_serves_the_cache_as_stale(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(7200))
    fake_trello.stop()
    code, envelope = run_json(capsys, "board", "lists")
    assert (code, envelope["ok"]) == (9, True)
    assert [x["name"] for x in envelope["data"]["lists"]] == ["Todo", "Doing"]
    assert [x["cards"] for x in envelope["data"]["lists"]] == [1, 1]
    cache = envelope["meta"]["cache"]
    assert cache["stale"] is True
    assert cache["error"]["code"] == "NETWORK_ERROR"


@pytest.mark.tc("TC-M08-039")
def test_board_list_delete_does_not_exist(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "delete", "Doing")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []

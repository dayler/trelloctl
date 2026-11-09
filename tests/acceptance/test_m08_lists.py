"""M08 acceptance tests: `board list` commands (RF-9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m08_support import (  # noqa: F401
    BOARD,
    add_card,
    ago,
    cached_names,
    close_caches,
    configure,
    open_names,
    prime,
    query,
    run_json,
    seed_lists,
    writes,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

Capsys = pytest.CaptureFixture[str]
FOUR = ("Todo", "Doing", "Done", "Extra")


def setup_lists(home: Path, fake: FakeTrello, *names: str) -> dict[str, str]:
    ids = seed_lists(fake, *(names or ("Todo", "Doing", "Done")))
    configure(home, fake)
    return ids


# --- create (AC-9.1, A-3) ---


@pytest.mark.tc("TC-M08-010")
def test_list_create_at_the_end_at_the_top_and_at_an_index(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup_lists(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "create", "--name", "A")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["list"]["index"] == 3
    assert open_names(fake_trello) == ["Todo", "Doing", "Done", "A"]
    code, envelope = run_json(capsys, "board", "list", "create", "--name", "B", "--pos", "top")
    assert (code, envelope["data"]["list"]["index"]) == (0, 0)
    assert open_names(fake_trello) == ["B", "Todo", "Doing", "Done", "A"]
    code, envelope = run_json(capsys, "board", "list", "create", "--name", "C", "--pos", "1")
    assert (code, envelope["data"]["list"]["index"]) == (0, 1)
    assert open_names(fake_trello) == ["B", "C", "Todo", "Doing", "Done", "A"]
    posts = writes(fake_trello)
    assert [r.path for r in posts] == ["/lists"] * 3
    assert [query(r)["pos"] for r in posts[:2]] == ["bottom", "top"]
    assert all(query(r)["idBoard"] == BOARD for r in posts)
    assert cached_names(trelloctl_home) == open_names(fake_trello)


@pytest.mark.tc("TC-M08-011")
@pytest.mark.parametrize("pos", ["9", "5", "-1", "middle"])
def test_list_create_with_an_invalid_index_is_a_usage_error_without_writes(
    pos: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup_lists(trelloctl_home, fake_trello, *FOUR)
    code, envelope = run_json(capsys, "board", "list", "create", "--name", "X", "--pos", pos)
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []
    assert open_names(fake_trello) == list(FOUR)
    code, envelope = run_json(capsys, "board", "list", "create", "--name", "Y", "--pos", "4")
    assert (code, envelope["ok"]) == (0, True)  # N equals bottom
    assert open_names(fake_trello)[-1] == "Y"


# --- rename, archive and unarchive (AC-9.2, AC-9.3, RNF-6) ---


@pytest.mark.tc("TC-M08-012")
def test_list_rename_updates_trello_and_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello, "To Do", "Doing")
    prime(trelloctl_home, fake_trello, ago(100))
    stamp = Cache.open(trelloctl_home).get_board(BOARD)["metadata_fetched_at"]  # type: ignore[index]
    code, envelope = run_json(capsys, "board", "list", "rename", "to do", "--name", "Backlog")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["list"]["name"] == "Backlog"
    put = writes(fake_trello)
    assert [(r.method, r.path) for r in put] == [("PUT", f"/lists/{ids['To Do']}")]
    assert query(put[0]) == {"name": "Backlog"}
    assert fake_trello.state.lists[ids["To Do"]]["name"] == "Backlog"
    assert cached_names(trelloctl_home) == ["Backlog", "Doing"]
    cache = Cache.open(trelloctl_home)
    assert cache.get_board(BOARD)["metadata_fetched_at"] == stamp  # type: ignore[index]
    code, envelope = run_json(capsys, "board", "lists")
    assert [x["name"] for x in envelope["data"]["lists"]] == ["Backlog", "Doing"]
    assert envelope["meta"]["cache"]["hit"] is True


@pytest.mark.tc("TC-M08-013")
def test_list_archive_and_unarchive_leave_the_cards_alone(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello)
    card = add_card(fake_trello, ids["Doing"], "Work item", id_short=1)
    code, envelope = run_json(capsys, "board", "list", "archive", "Doing")
    assert (code, envelope["data"]["list"]["closed"]) == (0, True)
    put = writes(fake_trello)
    assert [(r.method, r.path) for r in put] == [("PUT", f"/lists/{ids['Doing']}/closed")]
    assert query(put[0]) == {"value": "true"}
    assert fake_trello.state.lists[ids["Doing"]]["closed"] is True
    assert fake_trello.state.cards[card]["closed"] is False
    assert cached_names(trelloctl_home) == ["Todo", "Done"]
    code, envelope = run_json(capsys, "board", "list", "unarchive", "Doing")
    assert (code, envelope["data"]["list"]["closed"]) == (0, False)
    assert query(writes(fake_trello)[1]) == {"value": "false"}
    assert fake_trello.state.lists[ids["Doing"]]["closed"] is False
    assert fake_trello.state.cards[card]["closed"] is False
    assert cached_names(trelloctl_home) == ["Todo", "Doing", "Done"]


@pytest.mark.tc("TC-M08-014")
@pytest.mark.parametrize("by_id", [False, True])
def test_archiving_an_archived_list_is_not_an_error(
    by_id: bool, trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello)
    fake_trello.state.lists[ids["Doing"]]["closed"] = True
    ref = ids["Doing"] if by_id else "Doing"
    code, envelope = run_json(capsys, "board", "list", "archive", ref)
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["list"] == {"id": ids["Doing"], "name": "Doing", "closed": True}
    assert fake_trello.state.lists[ids["Doing"]]["closed"] is True


@pytest.mark.tc("TC-M08-038")
def test_renaming_an_unknown_list_reloads_the_metadata_then_fails(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup_lists(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(100))
    mark = len(fake_trello.requests)
    code, envelope = run_json(capsys, "board", "list", "rename", "Inexistente", "--name", "X")
    assert (code, envelope["error"]["code"]) == (5, "LIST_NOT_FOUND")
    assert envelope["error"]["details"]["list"] == "Inexistente"
    issued = [(r.method, r.path) for r in fake_trello.requests[mark:]]
    assert issued == [("GET", f"/boards/{BOARD}")]
    assert writes(fake_trello) == []


# --- move (AC-9.4, AC-14.2 to AC-14.5, AC-22.11, A-22) ---


@pytest.mark.tc("TC-M08-015")
def test_list_move_right_reads_fresh_metadata_once_and_writes_once(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(100))
    before = Cache.open(trelloctl_home).get_board(BOARD)["metadata_fetched_at"]  # type: ignore[index]
    mark = len(fake_trello.requests)
    code, envelope = run_json(capsys, "board", "list", "move", "Doing", "--right", "1")
    assert (code, envelope["ok"]) == (0, True)
    issued = [(r.method, r.path) for r in fake_trello.requests[mark:]]
    assert issued == [("GET", f"/boards/{BOARD}"), ("PUT", f"/lists/{ids['Doing']}")]
    data = envelope["data"]
    assert (data["from_index"], data["to_index"], data["list_count"]) == (1, 2, 3)
    assert (data["moved"], data["dry_run"]) == (True, False)
    assert data["list"] == {"id": ids["Doing"], "name": "Doing"}
    assert data["target_list"]["name"] == "Done"
    assert open_names(fake_trello) == ["Todo", "Done", "Doing"]
    assert cached_names(trelloctl_home) == ["Todo", "Done", "Doing"]
    after = Cache.open(trelloctl_home).get_board(BOARD)["metadata_fetched_at"]  # type: ignore[index]
    assert after > before + 50  # the fresh read renewed the metadata TTL


@pytest.mark.tc("TC-M08-016")
def test_list_move_to_index_zero_makes_it_the_first(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move", "Done", "--to-index", "0")
    assert (code, envelope["ok"]) == (0, True)
    assert (envelope["data"]["from_index"], envelope["data"]["to_index"]) == (2, 0)
    assert open_names(fake_trello) == ["Done", "Todo", "Doing"]
    put = writes(fake_trello)
    assert [(r.method, r.path) for r in put] == [("PUT", f"/lists/{ids['Done']}")]
    assert query(put[0])["pos"] == "top"
    code, envelope = run_json(capsys, "board", "list", "move", "Done", "--to-index", "1")
    assert code == 0
    assert open_names(fake_trello) == ["Todo", "Done", "Doing"]


@pytest.mark.tc("TC-M08-017")
@pytest.mark.parametrize(
    ("option", "value", "expected"),
    [("--right", "5", 3), ("--left", "5", 0), ("--to-index", "9", 3)],
)
def test_list_move_out_of_range_fails_without_clamp_and_stops_at_the_end_with_it(
    option: str,
    value: str,
    expected: int,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: Capsys,
) -> None:
    setup_lists(trelloctl_home, fake_trello, *FOUR)
    start = ["board", "list", "move", "Doing", option, value]
    code, envelope = run_json(capsys, *start)
    assert (code, envelope["error"]["code"]) == (6, "MOVE_OUT_OF_RANGE")
    assert envelope["error"]["details"]["from_index"] == 1
    assert envelope["error"]["details"]["list_count"] == 4
    assert writes(fake_trello) == []
    code, envelope = run_json(capsys, *start, "--clamp")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["to_index"] == expected
    assert envelope["data"]["clamped"] is True
    names = open_names(fake_trello)
    assert names.index("Doing") == expected


@pytest.mark.tc("TC-M08-018")
@pytest.mark.parametrize(
    "options",
    [
        ("--to-index", "1", "--left", "1"),
        ("--left", "1", "--right", "1"),
        (),
    ],
)
def test_list_move_requires_exactly_one_of_the_three_targets(
    options: tuple[str, ...], trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup_lists(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move", "Doing", *options)
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M08-019")
def test_list_move_dry_run_reports_the_move_without_writing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = setup_lists(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move", "Doing", "--right", "1", "--dry-run")
    assert (code, envelope["ok"]) == (0, True)
    data = envelope["data"]
    assert (data["dry_run"], data["moved"]) == (True, False)
    assert (data["from_index"], data["to_index"], data["list_count"]) == (1, 2, 3)
    assert data["list"] == {"id": ids["Doing"], "name": "Doing"}
    assert data["target_list"] == {"id": ids["Done"], "name": "Done"}
    assert writes(fake_trello) == []
    assert open_names(fake_trello) == ["Todo", "Doing", "Done"]


@pytest.mark.tc("TC-M08-041")
def test_list_move_of_the_last_list_right_with_clamp_does_not_write(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup_lists(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move", "Done", "--right", "1", "--clamp")
    assert (code, envelope["ok"]) == (0, True)
    data = envelope["data"]
    assert data["from_index"] == data["to_index"] == 2
    assert data["moved"] is False
    assert writes(fake_trello) == []


# --- bulk card operations (AC-9.5, AC-22.7) ---


def bulk_setup(home: Path, fake: FakeTrello) -> dict[str, str]:
    ids = setup_lists(home, fake, "A", "B")
    add_card(fake, ids["A"], "a1", id_short=1)
    add_card(fake, ids["A"], "a2", id_short=2)
    add_card(fake, ids["A"], "a-old", id_short=3, closed=True)
    add_card(fake, ids["B"], "b1", id_short=4)
    prime(home, fake, ago(30))
    return ids


@pytest.mark.tc("TC-M08-020")
def test_move_cards_returns_how_many_and_leaves_the_snapshot_stale(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = bulk_setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move-cards", "A", "--to", "B")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["moved"] == 2
    assert envelope["data"]["from"]["id"] == ids["A"]
    assert envelope["data"]["to"]["id"] == ids["B"]
    post = writes(fake_trello)
    assert [(r.method, r.path) for r in post] == [("POST", f"/lists/{ids['A']}/moveAllCards")]
    assert query(post[0]) == {"idBoard": BOARD, "idList": ids["B"]}
    on_b = [c for c in fake_trello.state.cards.values() if c["idList"] == ids["B"]]
    assert len(on_b) == 3
    row = Cache.open(trelloctl_home).get_board(BOARD)
    assert row is not None
    assert row["cards_stale"] == 1
    code, envelope = run_json(capsys, "board", "lists")
    assert [x["cards"] for x in envelope["data"]["lists"]] == [0, 3]
    assert envelope["meta"]["cache"]["hit"] is False  # the stale snapshot reloaded


@pytest.mark.tc("TC-M08-021")
def test_archive_cards_archives_every_open_card_of_the_list(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = bulk_setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "archive-cards", "A")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["archived"] == 2
    post = writes(fake_trello)
    assert [(r.method, r.path) for r in post] == [("POST", f"/lists/{ids['A']}/archiveAllCards")]
    in_a = [c for c in fake_trello.state.cards.values() if c["idList"] == ids["A"]]
    assert len(in_a) == 3
    assert all(c["closed"] for c in in_a)
    row = Cache.open(trelloctl_home).get_board(BOARD)
    assert row is not None
    assert row["cards_stale"] == 1
    code, envelope = run_json(capsys, "board", "lists")
    assert [x["cards"] for x in envelope["data"]["lists"]] == [0, 1]


@pytest.mark.tc("TC-M08-022")
def test_move_cards_to_an_unknown_list_fails_without_writing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    bulk_setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "list", "move-cards", "A", "--to", "Nowhere")
    assert (code, envelope["error"]["code"]) == (5, "LIST_NOT_FOUND")
    assert envelope["error"]["details"]["list"] == "Nowhere"
    assert writes(fake_trello) == []
    code, envelope = run_json(capsys, "board", "list", "move-cards", "A", "--to", "A")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []

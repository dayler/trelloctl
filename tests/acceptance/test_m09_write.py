"""M09 acceptance tests: move, copy, update, done, archive and write-through (RF-14 to RF-16)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from tests.acceptance.m09_support import (
    BOARD,
    OLD,
    add_checklist,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    finish,
    now,
    open_cache,
    request_params,
    run,
    seed_world,
    writes,
)
from tests.fakes.trello import FakeTrello

FIXTURE = Path(__file__).parent.parent / "fixtures" / "m09_markdown_description.md"


def order(fake: FakeTrello, list_id: str) -> list[str]:
    cards = [c for c in fake.state.cards.values() if c["idList"] == list_id and not c["closed"]]
    return [c["name"] for c in sorted(cards, key=lambda c: c["pos"])]


def puts(fake: FakeTrello) -> list[dict[str, str]]:
    return [request_params(r) for r in calls(fake, None, "PUT", prefix=True)]


# --- card move ---


@pytest.mark.tc("TC-M09-037")
def test_move_to_a_list_goes_to_the_end_or_to_the_top(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["done"], "d1", 1)
    card(fake_trello, ids["done"], "d2", 2)
    first = card(fake_trello, ids["todo"], "X", 3)
    second = card(fake_trello, ids["todo"], "Y", 4)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", "--to", "Done")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["card"]["id"] == first
    assert out["data"]["to"]["name"] == "Done"
    assert order(fake_trello, ids["done"]) == ["d1", "d2", "X"]
    code, out = run(capsys, "card", "move", "Y", "--to", "Done", "--pos", "top")
    assert (code, out["ok"]) == (0, True)
    assert order(fake_trello, ids["done"]) == ["Y", "d1", "d2", "X"]
    assert [p["pos"] for p in puts(fake_trello)] == ["bottom", "top"]
    row = cached(trelloctl_home, second)
    assert row is not None
    assert row["id_list"] == ids["done"]


@pytest.mark.tc("TC-M09-038")
def test_move_right_reads_fresh_metadata_once_and_writes_once(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    moving = card(fake_trello, ids["todo"], "X", 1)
    before = now() - timedelta(seconds=60)
    finish(trelloctl_home, fake_trello, when=before)
    code, out = run(capsys, "card", "move", "X", "--right", "2")
    assert (code, out["ok"]) == (0, True)
    assert len(calls(fake_trello, f"/boards/{BOARD}")) == 1
    assert len(writes(fake_trello)) == 1
    assert len(fake_trello.requests) == 2
    (put,) = calls(fake_trello, f"/cards/{moving}", "PUT")
    assert request_params(put) == {"idList": ids["done"], "pos": "bottom"}
    data = out["data"]
    assert data["from"] == {"id": ids["todo"], "name": "Todo", "index": 0}
    assert data["to"] == {"id": ids["done"], "name": "Done", "index": 2}
    assert data["dry_run"] is False
    assert data["card"]["id"] == moving
    assert data["card"]["list"]["name"] == "Done"
    board = open_cache(trelloctl_home).get_board(BOARD)
    assert board is not None
    assert board["metadata_fetched_at"] > before.timestamp()
    assert fake_trello.state.cards[moving]["idList"] == ids["done"]


@pytest.mark.tc("TC-M09-039")
def test_move_left_goes_one_list_to_the_left(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    moving = card(fake_trello, ids["doing"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", "--left", "1")
    assert (code, out["ok"]) == (0, True)
    assert (out["data"]["from"]["name"], out["data"]["to"]["name"]) == ("Doing", "Todo")
    assert fake_trello.state.cards[moving]["idList"] == ids["todo"]


@pytest.mark.tc("TC-M09-040")
def test_move_out_of_range_fails_unless_clamped(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    moving = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", "--right", "5")
    assert (code, out["error"]["code"]) == (6, "MOVE_OUT_OF_RANGE")
    assert out["error"]["details"] == {"from_index": 0, "to_index": 5, "list_count": 3}
    assert writes(fake_trello) == []
    code, out = run(capsys, "card", "move", "X", "--right", "5", "--clamp")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["to"] == {"id": ids["done"], "name": "Done", "index": 2}
    assert fake_trello.state.cards[moving]["idList"] == ids["done"]
    code, out = run(capsys, "card", "move", "X", "--left", "0")
    assert (code, out["error"]["code"]) == (2, "USAGE")


@pytest.mark.tc("TC-M09-041")
@pytest.mark.parametrize(
    "extra", [["--to", "Done", "--right", "1"], ["--left", "1", "--right", "1"], []]
)
def test_move_targets_are_mutually_exclusive(
    extra: list[str],
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", *extra)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-042")
def test_move_dry_run_reports_origin_and_destination_without_writing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    moving = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "X", "--right", "1", "--dry-run")
    assert (code, out["ok"]) == (0, True)
    data = out["data"]
    assert data["dry_run"] is True
    assert data["from"] == {"id": ids["todo"], "name": "Todo", "index": 0}
    assert data["to"] == {"id": ids["doing"], "name": "Doing", "index": 1}
    assert writes(fake_trello) == []
    assert fake_trello.state.cards[moving]["idList"] == ids["todo"]
    row = cached(trelloctl_home, moving)
    assert row is not None
    assert row["id_list"] == ids["todo"]


@pytest.mark.tc("TC-M09-060")
def test_move_to_the_own_list_at_index_zero_and_from_an_archived_list(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "A", 1)
    card(fake_trello, ids["todo"], "B", 2)
    moving = card(fake_trello, ids["todo"], "X", 3)
    stuck = card(fake_trello, ids["old"], "Stuck", 4)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", moving, "--to", "Todo", "--pos", "0")
    assert (code, out["ok"]) == (0, True)
    assert order(fake_trello, ids["todo"]) == ["X", "A", "B"]
    code, out = run(capsys, "card", "move", "X", "--to", "Todo", "--pos", "3")
    assert (code, out["error"]["code"]) == (2, "USAGE")
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "move", stuck, "--right", "1")
    assert (code, out["error"]["code"]) == (6, "MOVE_OUT_OF_RANGE")
    assert out["error"]["details"]["from_index"] is None
    assert writes(fake_trello) == []


# --- card copy ---


@pytest.mark.tc("TC-M09-043")
def test_copy_creates_a_card_from_the_source(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["done"], "existing", 1)
    source = card(fake_trello, ids["todo"], "Original", 2)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "copy", "Original", "--to", "Done")
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, "/cards", "POST")
    assert request_params(post)["idCardSource"] == source
    assert request_params(post)["idList"] == ids["done"]
    assert "name" not in request_params(post)
    assert out["data"]["name"] == "Original"
    assert out["data"]["id"] != source
    assert order(fake_trello, ids["done"]) == ["existing", "Original"]
    code, out = run(capsys, "card", "copy", source, "--to", "Done", "--name", "Y", "--pos", "top")
    assert (code, out["data"]["name"]) == (0, "Y")
    assert order(fake_trello, ids["done"])[0] == "Y"
    assert fake_trello.state.cards[source]["idList"] == ids["todo"]


@pytest.mark.tc("TC-M09-044")
def test_copy_keep_controls_what_is_copied(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    source = card(fake_trello, ids["todo"], "Original", 1, labels=(ids["bug"],))
    finish(trelloctl_home, fake_trello)
    for keep, sent in (
        (["--keep", "none"], "none"),
        (["--keep", "checklists,labels"], "checklists,labels"),
        ([], "all"),
    ):
        fake_trello.requests.clear()
        code, out = run(capsys, "card", "copy", source, "--to", "Done", *keep)
        assert (code, out["ok"]) == (0, True)
        (post,) = calls(fake_trello, "/cards", "POST")
        assert request_params(post)["keepFromSource"] == sent
        assert bool(out["data"]["labels"]) is (sent != "none")


@pytest.mark.tc("TC-M09-045")
@pytest.mark.parametrize("keep", ["votes", "all,none", "labels,votes"])
def test_copy_keep_with_an_unknown_part_is_a_usage_error(
    keep: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "Original", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "copy", "Original", "--to", "Done", "--keep", keep)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-046")
def test_the_copy_is_complete_in_the_response_and_in_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    source = card(fake_trello, ids["todo"], "Original", 1, desc="body", labels=(ids["bug"],))
    add_checklist(fake_trello, source, "QA", ("one", "two"))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "copy", "Original", "--to", "Done")
    assert (code, out["ok"]) == (0, True)
    new_id = out["data"]["id"]
    assert (out["data"]["desc"], [x["name"] for x in out["data"]["labels"]]) == ("body", ["bug"])
    assert out["data"]["list"]["name"] == "Done"
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "get", new_id, "--with", "checklists")
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.requests == []
    assert [i["name"] for i in out["data"]["checklists"][0]["items"]] == ["one", "two"]
    assert out["data"]["name"] == "Original"


# --- card update, done, undone, archive ---


@pytest.mark.tc("TC-M09-047")
def test_update_sends_only_the_given_field_and_replaces_the_sets(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1, labels=(ids["bug"],), members=(ids["ana"],))
    card(fake_trello, ids["todo"], "Y", 2)
    finish(trelloctl_home, fake_trello)
    cases: list[tuple[list[str], dict[str, str]]] = [
        (["--name", "Renamed"], {"name": "Renamed"}),
        (["--desc", "new text"], {"desc": "new text"}),
        (["--labels", "urgent"], {"idLabels": ids["urgent"]}),
        (["--members", "bob,ana"], {"idMembers": f"{ids['bob']},{ids['ana']}"}),
        (["--due", "2026-12-24"], {"due": "2026-12-24T12:00:00.000Z"}),
        (["--done"], {"dueComplete": "true"}),
        (["--not-done"], {"dueComplete": "false"}),
        (["--pos", "top"], {"pos": "top"}),
    ]
    for flags, expected in cases:
        fake_trello.requests.clear()
        code, out = run(capsys, "card", "update", target, *flags)
        assert (code, out["ok"]) == (0, True), flags
        assert puts(fake_trello) == [expected], flags
    stored = fake_trello.state.cards[target]
    assert (stored["name"], stored["desc"]) == ("Renamed", "new text")
    assert stored["idLabels"] == [ids["urgent"]]
    assert stored["idMembers"] == [ids["bob"], ids["ana"]]
    assert order(fake_trello, ids["todo"])[0] == "Renamed"
    row = cached(trelloctl_home, target)
    assert row is not None
    assert (row["name"], row["id_labels"], row["due"]) == (
        "Renamed",
        [ids["urgent"]],
        "2026-12-24T12:00:00.000Z",
    )
    code, out = run(capsys, "card", "update", target, "--labels", "")
    assert (code, out["data"]["labels"]) == (0, [])
    code, out = run(capsys, "card", "update", target)
    assert (code, out["error"]["code"]) == (2, "USAGE")


@pytest.mark.tc("TC-M09-048")
@pytest.mark.parametrize("flags", [["--due", "2026-12-01", "--no-due"], ["--done", "--not-done"]])
def test_update_exclusive_options_are_usage_errors(
    flags: list[str],
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "update", target, *flags)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-049")
def test_archive_and_unarchive_restore_the_card_in_its_list(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["doing"], "X", 1)
    gone = card(fake_trello, ids["done"], "Old news", 2, closed=True)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "archive", "X")
    assert (code, out["data"]["archived"]) == (0, True)
    assert fake_trello.state.cards[target]["closed"] is True
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["archived"] == 1
    code, out = run(capsys, "card", "unarchive", target)
    assert (code, out["data"]["archived"], out["data"]["list"]["name"]) == (0, False, "Doing")
    assert fake_trello.state.cards[target]["closed"] is False
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["archived"] == 0
    assert cached(trelloctl_home, gone) is None
    code, out = run(capsys, "card", "unarchive", gone)
    assert (code, out["ok"], out["data"]["list"]["name"]) == (0, True, "Done")
    assert fake_trello.state.cards[gone]["closed"] is False
    row = cached(trelloctl_home, gone)
    assert row is not None
    assert (row["archived"], row["id_list"]) == (0, ids["done"])


@pytest.mark.tc("TC-M09-050")
def test_no_due_clears_the_due_date_in_trello_and_in_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1, due="2026-12-01T10:00:00.000Z")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "update", target, "--no-due")
    assert (code, out["ok"], out["data"]["due"]) == (0, True, None)
    assert puts(fake_trello) == [{"due": "null"}]
    assert fake_trello.state.cards[target]["due"] is None
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["due"] is None
    code, out = run(capsys, "card", "get", target)
    assert out["data"]["due"] is None


@pytest.mark.tc("TC-M09-051")
def test_done_and_undone_do_not_need_a_due_date(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "done", "X")
    assert (code, out["data"]["dueComplete"], out["data"]["due"]) == (0, True, None)
    assert fake_trello.state.cards[target]["dueComplete"] is True
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["due_complete"] == 1
    code, out = run(capsys, "card", "undone", "X")
    assert (code, out["data"]["dueComplete"]) == (0, False)
    assert fake_trello.state.cards[target]["dueComplete"] is False
    assert puts(fake_trello) == [{"dueComplete": "true"}, {"dueComplete": "false"}]


@pytest.mark.tc("TC-M09-052")
def test_repeating_archive_or_done_is_not_an_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    archived = card(fake_trello, ids["todo"], "Archived", 1, closed=True)
    completed = card(fake_trello, ids["todo"], "Completed", 2, done=True)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "archive", archived)
    assert (code, out["ok"], out["data"]["archived"]) == (0, True, True)
    code, out = run(capsys, "card", "done", completed)
    assert (code, out["ok"], out["data"]["dueComplete"]) == (0, True, True)


@pytest.mark.tc("TC-M09-053")
def test_there_is_no_card_delete_command(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "delete", target)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []
    assert target in fake_trello.state.cards


@pytest.mark.tc("TC-M09-054")
def test_updating_a_card_deleted_in_trello_marks_it_deleted(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    del fake_trello.state.cards[target]
    code, out = run(capsys, "card", "update", target, "--name", "Y")
    assert (code, out["error"]["code"]) == (5, "CARD_NOT_FOUND")
    assert open_cache(trelloctl_home).is_card_deleted(BOARD, target) is True
    assert len(calls(fake_trello, f"/cards/{target}", "PUT")) == 1
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "update", target, "--name", "Y")
    assert (code, out["error"]["code"]) == (5, "CARD_NOT_FOUND")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-055")
def test_a_write_ignores_the_ttl_and_updates_only_the_row(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    card(fake_trello, ids["todo"], "Other", 2)
    finish(trelloctl_home, fake_trello, when=OLD)
    board = open_cache(trelloctl_home).get_board(BOARD)
    assert board is not None
    snapshot_stamp = board["cards_fetched_at"]
    started = now().timestamp()
    code, out = run(capsys, "card", "update", target, "--name", "Y")
    assert (code, out["ok"], out["data"]["name"]) == (0, True, "Y")
    assert [(r.method, r.path) for r in fake_trello.requests] == [("PUT", f"/cards/{target}")]
    board = open_cache(trelloctl_home).get_board(BOARD)
    assert board is not None
    assert board["cards_fetched_at"] == snapshot_stamp
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["name"] == "Y"
    assert row["fetched_at"] >= started


@pytest.mark.tc("TC-M09-056")
def test_markdown_descriptions_travel_byte_for_byte(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    original = FIXTURE.read_bytes().decode("utf-8")
    assert "@ana" in original
    assert "**before**" in original
    code, out = run(
        capsys,
        "card",
        "create",
        "--list",
        "Todo",
        "--name",
        "MD",
        "--desc-file",
        str(FIXTURE),
    )
    assert (code, out["ok"]) == (0, True)
    target = out["data"]["id"]
    assert fake_trello.state.cards[target]["desc"] == original
    assert request_params(calls(fake_trello, "/cards", "POST")[0])["desc"] == original
    code, out = run(capsys, "card", "get", target)
    assert out["data"]["desc"] == original
    code, out = run(capsys, "card", "get", target, "--fresh")
    assert out["data"]["desc"] == original
    edited = original.replace("**before**", "**after**") + "\n¡Última línea! ☕\n"
    code, out = run(capsys, "card", "update", target, "--desc", "-", stdin=edited.encode("utf-8"))
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.state.cards[target]["desc"] == edited
    assert request_params(calls(fake_trello, f"/cards/{target}", "PUT")[0])["desc"] == edited
    for extra in ([], ["--fresh"]):
        code, out = run(capsys, "card", "get", target, *extra)
        assert out["data"]["desc"] == edited

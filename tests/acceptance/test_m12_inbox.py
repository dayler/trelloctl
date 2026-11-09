"""M12 acceptance tests: `inbox list`, `inbox add`, `inbox move` and the Inbox in `card`."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m12_support import (
    BOARD,
    INBOX_BOARD,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    configure,
    forget,
    freeze,
    hide_inbox,
    inbox_card,
    inbox_row,
    me_calls,
    open_cache,
    request_params,
    run,
    seed_account,
    writes,
)
from tests.fakes.trello import FakeTrello

MAX_DESC = 16384
FIXTURE = Path(__file__).parent.parent / "fixtures" / "m09_markdown_description.md"


def names(out: dict[str, Any]) -> list[str]:
    return [c["name"] for c in out["data"]["cards"]]


@pytest.mark.tc("TC-M12-001")
def test_first_list_resolves_the_inbox_and_caches_it_as_kind_inbox(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "Call back", 1)
    configure(trelloctl_home, fake_trello)
    code, out = run(capsys, "inbox", "list")
    assert (code, out["ok"]) == (0, True)
    assert names(out) == ["Call back"]
    assert out["meta"]["board_id"] == INBOX_BOARD
    (me,) = me_calls(fake_trello)
    assert request_params(me) == {"fields": "inbox"}
    row = inbox_row(trelloctl_home)
    assert row is not None
    assert (row["kind"], row["name"]) == ("inbox", "Inbox")
    assert open_cache(trelloctl_home).get_board(BOARD) is None


@pytest.mark.tc("TC-M12-002")
def test_cache_status_after_inbox_list_includes_the_inbox(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    inbox_card(fake_trello, ids, "Two", 2)
    configure(trelloctl_home, fake_trello)
    assert run(capsys, "inbox", "list")[0] == 0
    code, out = run(capsys, "cache", "status")
    assert (code, out["ok"]) == (0, True)
    (entry,) = [b for b in out["data"]["boards"] if b["board_id"] == INBOX_BOARD]
    assert entry["kind"] == "inbox"
    assert entry["metadata"]["lists"] == 1
    assert entry["cards"]["count"] == 2


@pytest.mark.tc("TC-M12-003")
def test_a_response_without_inbox_is_unavailable_in_list_add_and_move(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    hide_inbox(fake_trello.state)
    configure(trelloctl_home, fake_trello, board=BOARD)
    for argv in (
        ("inbox", "list"),
        ("inbox", "add", "--name", "X"),
        ("inbox", "move", "One", "--to", "Todo"),
    ):
        forget(fake_trello)
        code, out = run(capsys, *argv)
        assert (code, out["ok"], out["error"]["code"]) == (5, False, "INBOX_UNAVAILABLE")
        assert writes(fake_trello) == []
    assert inbox_row(trelloctl_home) is None


@pytest.mark.tc("TC-M12-003")
def test_an_inbox_that_disappears_after_being_cached_is_unavailable_on_reload(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_account(fake_trello)
    configure(trelloctl_home, fake_trello)
    assert run(capsys, "inbox", "list")[0] == 0
    hide_inbox(fake_trello.state)
    code, out = run(capsys, "inbox", "list", "--fresh")
    assert (code, out["error"]["code"]) == (5, "INBOX_UNAVAILABLE")


@pytest.mark.tc("TC-M12-004")
def test_an_expired_inbox_and_fresh_reload_it(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    configure(trelloctl_home, fake_trello)
    clock = freeze(monkeypatch)
    assert run(capsys, "inbox", "list")[0] == 0
    forget(fake_trello)
    clock.advance(1801)
    code, out = run(capsys, "inbox", "list")
    assert (code, names(out)) == (0, ["One"])
    assert len(me_calls(fake_trello)) == 1
    assert calls(fake_trello, f"/boards/{INBOX_BOARD}")
    assert out["meta"]["cache"]["hit"] is False
    forget(fake_trello)
    clock.advance(10)
    code, out = run(capsys, "inbox", "list", "--fresh")
    assert code == 0
    assert len(me_calls(fake_trello)) == 1
    assert calls(fake_trello, f"/boards/{INBOX_BOARD}")
    assert calls(fake_trello, f"/boards/{INBOX_BOARD}/cards", prefix=True)
    assert out["meta"]["cache"]["hit"] is False


@pytest.mark.tc("TC-M12-005")
def test_list_limits_to_50_then_no_limit_then_selected_fields(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    for number in range(1, 61):
        inbox_card(
            fake_trello,
            ids,
            f"Card {number:02d}",
            number,
            desc=f"text {number}",
            due="2026-12-01T12:00:00.000Z",
        )
    configure(trelloctl_home, fake_trello)
    code, out = run(capsys, "inbox", "list")
    assert code == 0
    cards = out["data"]["cards"]
    assert len(cards) == 50
    assert out["meta"]["truncated"] is True
    first = cards[0]
    assert first["id"]
    assert (first["idShort"], first["name"]) == (1, "Card 01")
    assert (first["desc"], first["due"]) == ("text 1", "2026-12-01T12:00:00.000Z")
    assert set(first["badges"]) == {"comments", "attachments", "checkItems", "checkItemsChecked"}
    code, out = run(capsys, "inbox", "list", "--limit", "0")
    assert (code, len(out["data"]["cards"]), out["meta"]["truncated"]) == (0, 60, False)
    code, out = run(capsys, "inbox", "list", "--fields", "id,name")
    assert code == 0
    assert len(out["data"]["cards"]) == 50
    assert all(set(c) == {"id", "name"} for c in out["data"]["cards"])
    code, out = run(capsys, "inbox", "list", "--fields", "nope")
    assert (code, out["error"]["code"]) == (2, "USAGE")


@pytest.mark.tc("TC-M12-006")
def test_a_second_list_inside_the_ttl_makes_no_requests(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    configure(trelloctl_home, fake_trello)
    assert run(capsys, "inbox", "list")[0] == 0
    forget(fake_trello)
    code, out = run(capsys, "inbox", "list")
    assert (code, names(out)) == (0, ["One"])
    assert fake_trello.requests == []
    assert out["meta"]["cache"]["hit"] is True


@pytest.mark.tc("TC-M12-007")
def test_list_and_add_need_no_selected_board(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "One", 1)
    monkeypatch.delenv("TRELLO_BOARD", raising=False)
    configure(trelloctl_home, fake_trello, board=None)
    assert "[board]" not in (trelloctl_home / "config.toml").read_text(encoding="utf-8")
    code, out = run(capsys, "inbox", "list")
    assert (code, names(out)) == (0, ["One"])
    code, out = run(capsys, "inbox", "add", "--name", "Two")
    assert (code, out["ok"], out["data"]["name"]) == (0, True, "Two")


@pytest.mark.tc("TC-M12-008")
@pytest.mark.parametrize("source", ["desc", "desc-file", "stdin"])
def test_add_posts_to_the_inbox_list_and_returns_the_card(
    source: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_account(fake_trello)
    configure(trelloctl_home, fake_trello)
    argv = ["inbox", "add", "--name", "X", "--due", "2026-11-15"]
    if source == "desc":
        argv += ["--desc", "from desc"]
    elif source == "desc-file":
        path = trelloctl_home / "desc.txt"
        path.write_bytes(b"from desc-file")
        argv += ["--desc-file", str(path)]
    else:
        monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"from stdin"), "utf-8"))
        argv += ["--desc", "-"]
    code, out = run(capsys, *argv)
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, "/cards", "POST")
    params = request_params(post)
    assert params == {
        "idList": ids["inbox_list"],
        "name": "X",
        "due": "2026-11-15T12:00:00.000Z",
        "desc": f"from {source}",
    }
    data = out["data"]
    assert (data["name"], data["desc"], data["due"]) == ("X", f"from {source}", params["due"])
    assert data["list"]["id"] == ids["inbox_list"]
    assert fake_trello.state.cards[data["id"]]["idBoard"] == INBOX_BOARD
    row = cached(trelloctl_home, data["id"], INBOX_BOARD)
    assert row is not None
    assert row["id_list"] == ids["inbox_list"]


@pytest.mark.tc("TC-M12-009")
@pytest.mark.parametrize("extra", [("--list", "A"), ("--labels", "bug"), ("--members", "ana")])
def test_add_rejects_list_labels_and_members(
    extra: tuple[str, str],
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_account(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, out = run(capsys, "inbox", "add", "--name", "X", *extra)
    assert (code, out["ok"], out["error"]["code"]) == (2, False, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M12-010")
def test_add_with_a_description_over_the_limit_is_usage_without_calling_the_api(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_account(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, out = run(capsys, "inbox", "add", "--name", "X", "--desc", "a" * (MAX_DESC + 1))
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []
    code, out = run(capsys, "inbox", "add", "--name", "X", "--desc", "a" * MAX_DESC)
    assert (code, out["ok"]) == (0, True)
    assert len(out["data"]["desc"]) == MAX_DESC


@pytest.mark.tc("TC-M12-011")
def test_move_sends_idboard_and_idlist_and_moves_the_card_between_caches(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    moved = inbox_card(fake_trello, ids, "X", 1)
    keep = inbox_card(fake_trello, ids, "Stays", 2)
    configure(trelloctl_home, fake_trello, board=BOARD)
    assert run(capsys, "inbox", "list")[0] == 0
    forget(fake_trello)
    code, out = run(capsys, "inbox", "move", "X", "--to", "To Do")
    assert (code, out["ok"]) == (0, True)
    (put,) = writes(fake_trello)
    assert (put.method, put.path) == ("PUT", f"/cards/{moved}")
    params = request_params(put)
    assert (params["idBoard"], params["idList"]) == (BOARD, ids["to_do"])
    stored = fake_trello.state.cards[moved]
    assert (stored["idBoard"], stored["idList"]) == (BOARD, ids["to_do"])
    assert out["meta"]["board_id"] == BOARD
    assert out["data"]["card"]["id"] == moved
    assert out["data"]["card"]["list"] == {"id": ids["to_do"], "name": "To Do"}
    assert cached(trelloctl_home, moved, INBOX_BOARD) is None
    row = cached(trelloctl_home, moved, BOARD)
    assert row is not None
    assert (row["id_list"], row["name"]) == (ids["to_do"], "X")
    assert cached(trelloctl_home, keep, INBOX_BOARD) is not None
    forget(fake_trello)
    code, out = run(capsys, "inbox", "list")
    assert (code, names(out)) == (0, ["Stays"])
    assert fake_trello.requests == []
    code, out = run(capsys, "card", "get", moved)
    assert (code, out["meta"]["board_id"]) == (0, BOARD)


@pytest.mark.tc("TC-M12-012")
@pytest.mark.parametrize("pos", ["top", "0"])
def test_move_with_top_or_index_zero_puts_the_card_first(
    pos: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = seed_account(fake_trello)
    card(fake_trello, ids["to_do"], "First", 1, pos=1000)
    card(fake_trello, ids["to_do"], "Second", 2, pos=2000)
    moved = inbox_card(fake_trello, ids, "X", 1)
    configure(trelloctl_home, fake_trello, board=BOARD)
    code, out = run(capsys, "inbox", "move", "X", "--to", "To Do", "--pos", pos)
    assert (code, out["ok"]) == (0, True)
    (put,) = writes(fake_trello)
    sent = request_params(put)["pos"]
    assert sent == "top" or (pos == "0" and float(sent) < 1000)
    in_list = [
        c
        for c in sorted(fake_trello.state.cards.values(), key=lambda c: c["pos"])
        if c["idList"] == ids["to_do"]
    ]
    assert [c["name"] for c in in_list] == ["X", "First", "Second"]
    assert in_list[0]["id"] == moved
    row = cached(trelloctl_home, moved, BOARD)
    assert row is not None
    assert row["pos"] < 1000


@pytest.mark.tc("TC-M12-013")
def test_move_without_to_is_usage(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "X", 1)
    configure(trelloctl_home, fake_trello, board=BOARD)
    code, out = run(capsys, "inbox", "move", "X")
    assert (code, out["ok"], out["error"]["code"]) == (2, False, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M12-014")
def test_move_without_a_selected_board_is_board_not_configured(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "X", 1)
    monkeypatch.delenv("TRELLO_BOARD", raising=False)
    configure(trelloctl_home, fake_trello, board=None)
    code, out = run(capsys, "inbox", "move", "X", "--to", "To Do")
    assert (code, out["error"]["code"]) == (3, "BOARD_NOT_CONFIGURED")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M12-015")
def test_move_resolves_the_card_only_against_the_inbox(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    card(fake_trello, ids["todo"], "On the board", 1)
    inbox_card(fake_trello, ids, "In the inbox", 1)
    configure(trelloctl_home, fake_trello, board=BOARD)
    code, out = run(capsys, "inbox", "move", "On the board", "--to", "To Do")
    assert (code, out["error"]["code"]) == (5, "CARD_NOT_FOUND")
    assert writes(fake_trello) == []
    assert out["error"]["details"]["board"] == INBOX_BOARD


@pytest.mark.tc("TC-M12-016")
def test_move_to_an_unknown_list_writes_nothing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    inbox_card(fake_trello, ids, "X", 1)
    configure(trelloctl_home, fake_trello, board=BOARD)
    code, out = run(capsys, "inbox", "move", "X", "--to", "Nowhere")
    assert (code, out["error"]["code"]) == (5, "LIST_NOT_FOUND")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M12-017")
def test_move_with_an_index_beyond_the_list_is_usage_without_writes(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_account(fake_trello)
    for number in range(1, 4):
        card(fake_trello, ids["to_do"], f"Card {number}", number)
    inbox_card(fake_trello, ids, "X", 1)
    configure(trelloctl_home, fake_trello, board=BOARD)
    code, out = run(capsys, "inbox", "move", "X", "--to", "To Do", "--pos", "9")
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []
    code, out = run(capsys, "inbox", "move", "X", "--to", "To Do", "--pos", "3")
    assert (code, out["ok"]) == (0, True)


@pytest.mark.tc("TC-M12-018")
def test_card_commands_accept_an_inbox_card_id_without_a_board(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = seed_account(fake_trello)
    ident = inbox_card(fake_trello, ids, "Idea", 1)
    monkeypatch.delenv("TRELLO_BOARD", raising=False)
    configure(trelloctl_home, fake_trello, board=None)
    code, out = run(capsys, "card", "get", ident)
    assert (code, out["ok"], out["meta"]["board_id"]) == (0, True, INBOX_BOARD)
    assert out["data"]["name"] == "Idea"
    code, out = run(capsys, "card", "update", ident, "--labels", "idea")
    assert (code, out["ok"], out["meta"]["board_id"]) == (0, True, INBOX_BOARD)
    assert [x["name"] for x in out["data"]["labels"]] == ["idea"]
    assert fake_trello.state.cards[ident]["idLabels"] == [ids["inbox_label"]]
    code, out = run(capsys, "card", "done", ident)
    assert (code, out["ok"], out["data"]["dueComplete"]) == (0, True, True)
    assert fake_trello.state.cards[ident]["dueComplete"] is True


@pytest.mark.tc("TC-M12-019")
def test_markdown_description_travels_byte_for_byte_through_inbox_add(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_account(fake_trello)
    configure(trelloctl_home, fake_trello)
    text = FIXTURE.read_bytes().decode("utf-8")
    for marker in ("**", "@ana", "]("):
        assert marker in text
    code, out = run(capsys, "inbox", "add", "--name", "Md", "--desc-file", str(FIXTURE))
    assert (code, out["ok"]) == (0, True)
    ident = out["data"]["id"]
    (post,) = calls(fake_trello, "/cards", "POST")
    assert request_params(post)["desc"] == text
    assert fake_trello.state.cards[ident]["desc"] == text
    assert out["data"]["desc"] == text
    code, out = run(capsys, "card", "get", ident, "--fresh")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["desc"] == text
    assert out["meta"]["board_id"] == INBOX_BOARD

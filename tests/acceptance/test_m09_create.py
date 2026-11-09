"""M09 acceptance tests: `card create` (RF-12) and the description rules (A-13)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m09_support import (
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    finish,
    request_params,
    run,
    seed_world,
    writes,
)
from tests.fakes.trello import FakeTrello

MAX_DESC = 16384


def sorted_names(fake: FakeTrello, list_id: str) -> list[str]:
    cards = [c for c in fake.state.cards.values() if c["idList"] == list_id and not c["closed"]]
    return [c["name"] for c in sorted(cards, key=lambda c: c["pos"])]


@pytest.mark.tc("TC-M09-001")
def test_create_puts_the_card_at_the_end_and_inserts_it_in_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "Existing", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "create", "--list", "Todo", "--name", "X")
    assert (code, out["ok"]) == (0, True)
    data = out["data"]
    assert data["name"] == "X"
    assert data["list"] == {"id": ids["todo"], "name": "Todo"}
    for key in ("id", "idShort", "shortLink", "url", "badges", "labels", "members", "due"):
        assert key in data
    assert sorted_names(fake_trello, ids["todo"]) == ["Existing", "X"]
    (post,) = calls(fake_trello, "/cards", "POST")
    assert request_params(post)["pos"] == "bottom"
    row = cached(trelloctl_home, data["id"])
    assert row is not None
    assert (row["name"], row["id_list"], row["archived"]) == ("X", ids["todo"], 0)


@pytest.mark.tc("TC-M09-002")
def test_create_sends_every_field_with_resolved_ids(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    for index, name in enumerate(("a", "b", "c"), 1):
        card(fake_trello, ids["todo"], name, index)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys,
        "card",
        "create",
        "--list",
        "Todo",
        "--name",
        "Full",
        "--desc",
        "details",
        "--due",
        "2026-12-01T09:30:00Z",
        "--labels",
        "bug,urgent",
        "--members",
        "ana",
        "--pos",
        "top",
    )
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, "/cards", "POST")
    assert request_params(post) == {
        "idList": ids["todo"],
        "name": "Full",
        "desc": "details",
        "due": "2026-12-01T09:30:00.000Z",
        "idLabels": f"{ids['bug']},{ids['urgent']}",
        "idMembers": ids["ana"],
        "pos": "top",
    }
    assert sorted_names(fake_trello, ids["todo"])[0] == "Full"
    assert [m["username"] for m in out["data"]["members"]] == ["ana"]
    assert [x["name"] for x in out["data"]["labels"]] == ["bug", "urgent"]
    code, out = run(capsys, "card", "create", "--list", "Todo", "--name", "Third", "--pos", "2")
    assert (code, out["ok"]) == (0, True)
    assert sorted_names(fake_trello, ids["todo"]) == ["Full", "a", "Third", "b", "c"]


@pytest.mark.tc("TC-M09-003")
@pytest.mark.parametrize("pos", ["7", "-1", "abc"])
def test_create_with_an_index_out_of_range_is_a_usage_error(
    pos: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    for index, name in enumerate(("a", "b", "c"), 1):
        card(fake_trello, ids["todo"], name, index)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "create", "--list", "Todo", "--name", "X", "--pos", pos)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M09-004")
def test_description_limit_is_16384_characters(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    existing = card(fake_trello, ids["todo"], "Existing", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "Big", "--desc", "a" * MAX_DESC
    )
    assert (code, out["ok"]) == (0, True)
    assert len(out["data"]["desc"]) == MAX_DESC
    before = len(fake_trello.requests)
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "Big2", "--desc", "a" * (MAX_DESC + 1)
    )
    assert (code, out["error"]["code"]) == (2, "USAGE")
    code, out = run(capsys, "card", "update", existing, "--desc", "b" * (MAX_DESC + 1))
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert len(fake_trello.requests) == before


@pytest.mark.tc("TC-M09-005")
def test_markdown_and_html_are_sent_literally(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    text = "# Title\n\n**bold** and <b>html</b> &amp; <script>x()</script>\n- item"
    code, out = run(capsys, "card", "create", "--list", "Todo", "--name", "MD", "--desc", text)
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, "/cards", "POST")
    assert request_params(post)["desc"] == text
    assert fake_trello.state.cards[out["data"]["id"]]["desc"] == text
    assert out["data"]["desc"] == text


@pytest.mark.tc("TC-M09-006")
def test_description_from_a_file_and_from_stdin_is_exact_utf8(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    text = "Árbol ñandú — café ☕ 日本語\nsecond line\n"
    path = trelloctl_home.parent / "desc.txt"
    path.write_bytes(text.encode("utf-8"))
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "F", "--desc-file", str(path)
    )
    assert (code, out["ok"]) == (0, True)
    code, out2 = run(
        capsys,
        "card",
        "create",
        "--list",
        "Todo",
        "--name",
        "S",
        "--desc",
        "-",
        stdin=text.encode("utf-8"),
    )
    assert (code, out2["ok"]) == (0, True)
    posts = calls(fake_trello, "/cards", "POST")
    assert [request_params(p)["desc"] for p in posts] == [text, text]
    assert out["data"]["desc"] == out2["data"]["desc"] == text


@pytest.mark.tc("TC-M09-007")
def test_desc_and_desc_file_together_are_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    path = trelloctl_home.parent / "d.txt"
    path.write_text("x", encoding="utf-8")
    code, out = run(
        capsys,
        "card",
        "create",
        "--list",
        "Todo",
        "--name",
        "X",
        "--desc",
        "a",
        "--desc-file",
        str(path),
    )
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M09-008")
def test_a_missing_description_file_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    missing = str(trelloctl_home.parent / "nothing.txt")
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "X", "--desc-file", missing
    )
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M09-009")
def test_an_unknown_label_or_member_creates_nothing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "X", "--labels", "bug,nothing"
    )
    assert (code, out["error"]["code"]) == (5, "LABEL_NOT_FOUND")
    code, out = run(
        capsys, "card", "create", "--list", "Todo", "--name", "X", "--members", "ana,nobody"
    )
    assert (code, out["error"]["code"]) == (5, "MEMBER_NOT_FOUND")
    assert calls(fake_trello, "/cards", "POST") == []
    assert [c for c in fake_trello.state.cards.values() if c["name"] == "X"] == []


@pytest.mark.tc("TC-M09-010")
def test_an_unknown_list_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "create", "--list", "Nowhere", "--name", "X")
    assert (code, out["error"]["code"]) == (5, "LIST_NOT_FOUND")
    assert out["error"]["details"]["list"] == "Nowhere"
    assert calls(fake_trello, "/cards", "POST") == []


@pytest.mark.tc("TC-M09-011")
@pytest.mark.parametrize("due", ["mañana", "next friday", "2026-13-45", ""])
def test_an_invalid_due_date_is_a_usage_error(
    due: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "create", "--list", "Todo", "--name", "X", "--due", due)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert writes(fake_trello) == []

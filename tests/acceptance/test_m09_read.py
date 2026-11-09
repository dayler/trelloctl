"""M09 acceptance tests: `card get` and card resolution (RF-13, AC-7.3, AC-7.4, AC-22.9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m09_support import (
    BOARD,
    OTHER,
    add_checklist,
    add_list,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    comment,
    finish,
    open_cache,
    paths,
    run,
    seed_board,
    seed_world,
    write_config,
)
from tests.fakes.trello import FakeTrello


def seed_detailed(fake: FakeTrello) -> dict[str, str]:
    ids = seed_world(fake)
    ids["c1"] = card(
        fake,
        ids["todo"],
        "First card",
        1,
        short_link="cardAAA1",
        desc="the description",
        labels=(ids["bug"],),
        members=(ids["ana"],),
        due="2026-12-01T10:00:00.000Z",
        done=True,
    )
    ids["c2"] = card(fake, ids["doing"], "Second card", 2, short_link="cardBBB2")
    fake.state.cards[ids["c1"]]["badges"] = {
        "comments": 2,
        "attachments": 1,
        "checkItems": 2,
        "checkItemsChecked": 0,
    }
    ids["qa"] = add_checklist(fake, ids["c1"], "QA", ("unit tests", "e2e tests"))
    attachment = fake.state.new_id()
    fake.state.attachments[attachment] = {
        "id": attachment,
        "idCard": ids["c1"],
        "name": "spec.pdf",
        "bytes": 10,
        "isUpload": True,
        "mimeType": "application/pdf",
        "date": "2026-03-01T00:00:00.000Z",
        "url": "https://trello.com/1/cards/x/attachments/y/download/spec.pdf",
    }
    return ids


@pytest.mark.tc("TC-M09-012")
def test_get_returns_the_card_without_requests_when_the_cache_is_fresh(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_detailed(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", ids["c1"])
    assert (code, out["ok"]) == (0, True)
    data = out["data"]
    assert data["list"] == {"id": ids["todo"], "name": "Todo"}
    assert [(x["name"], x["color"]) for x in data["labels"]] == [("bug", "red")]
    assert [m["username"] for m in data["members"]] == ["ana"]
    assert data["due"] == "2026-12-01T10:00:00.000Z"
    assert data["dueComplete"] is True
    assert data["url"] == fake_trello.state.cards[ids["c1"]]["url"]
    assert data["dateLastActivity"]
    assert data["badges"] == {
        "comments": 2,
        "attachments": 1,
        "checkItems": 2,
        "checkItemsChecked": 0,
    }
    assert "checklists" not in data
    assert fake_trello.requests == []
    assert out["meta"]["cache"]["hit"] is True
    assert out["meta"]["board_id"] == BOARD


@pytest.mark.tc("TC-M09-013")
def test_get_full_adds_checklists_attachments_and_comments_with_one_request(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_detailed(fake_trello)
    said = comment(fake_trello, ids["c1"], "looks good @ana", ids["ana"])
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", ids["c1"], "--full")
    assert (code, out["ok"]) == (0, True)
    data = out["data"]
    assert [c["name"] for c in data["checklists"]] == ["QA"]
    assert [i["name"] for i in data["checklists"][0]["items"]] == ["unit tests", "e2e tests"]
    assert [a["name"] for a in data["attachments"]] == ["spec.pdf"]
    assert [(c["id"], c["text"], c["member"]) for c in data["comments"]] == [
        (said, "looks good @ana", "ana")
    ]
    assert paths(fake_trello) == [f"/cards/{ids['c1']}/actions"]


@pytest.mark.tc("TC-M09-014")
def test_get_with_chooses_the_sections(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_detailed(fake_trello)
    comment(fake_trello, ids["c1"], "a comment")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", ids["c1"], "--with", "checklists")
    assert (code, out["ok"]) == (0, True)
    assert set(out["data"]) >= {"checklists"}
    assert "comments" not in out["data"]
    assert "attachments" not in out["data"]
    assert fake_trello.requests == []
    code, out = run(capsys, "card", "get", ids["c1"], "--with", "comments")
    assert (code, out["ok"]) == (0, True)
    assert [c["text"] for c in out["data"]["comments"]] == ["a comment"]
    assert "checklists" not in out["data"]
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M09-015")
@pytest.mark.parametrize(
    "extra", [["--full", "--with", "comments"], ["--with", "foo"], ["--with", ""]]
)
def test_get_full_with_and_unknown_parts_are_usage_errors(
    extra: list[str],
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ids = seed_detailed(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", ids["c1"], *extra)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-016")
def test_a_card_is_found_by_id_short_link_url_number_and_name(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_detailed(fake_trello)
    finish(trelloctl_home, fake_trello)
    for ref in (
        ids["c1"],
        ids["c1"].upper(),
        "cardAAA1",
        "https://trello.com/c/cardAAA1/1-first-card",
        "1",
        "First card",
        "first CARD",
    ):
        code, out = run(capsys, "card", "get", ref)
        assert (code, out["ok"]) == (0, True), ref
        assert out["data"]["id"] == ids["c1"], ref
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-017")
def test_a_numeric_value_is_the_number_and_search_finds_the_numeric_name(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    named = card(fake_trello, ids["todo"], "123", 5)
    numbered = card(fake_trello, ids["todo"], "Other", 123)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", "123")
    assert (code, out["data"]["id"]) == (0, numbered)
    code, out = run(capsys, "card", "search", "123")
    assert (code, [c["id"] for c in out["data"]["cards"]]) == (0, [named])


@pytest.mark.tc("TC-M09-018")
def test_a_card_missing_from_the_cache_is_read_once_and_inserted(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_detailed(fake_trello)
    finish(trelloctl_home, fake_trello)
    late = card(fake_trello, ids["doing"], "Late card", 9)
    assert cached(trelloctl_home, late) is None
    code, out = run(capsys, "card", "get", late)
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["name"] == "Late card"
    assert paths(fake_trello) == [f"/cards/{late}"]
    row = cached(trelloctl_home, late)
    assert row is not None
    assert row["name"] == "Late card"
    assert out["meta"]["cache"]["hit"] is False
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "get", late)
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-019")
def test_an_unknown_number_reloads_incrementally_and_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_detailed(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", "999")
    assert (code, out["error"]["code"]) == (5, "CARD_NOT_FOUND")
    assert out["error"]["details"]["card"] == "999"
    (reload,) = calls(fake_trello, f"/boards/{BOARD}/cards/all")
    assert "modifiedSince" in reload.query
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M09-020")
def test_a_repeated_name_is_ambiguous_and_nothing_is_written(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    first = card(fake_trello, ids["todo"], "Fix", 1)
    second = card(fake_trello, ids["doing"], "fix", 2)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "move", "Fix", "--right", "1")
    assert (code, out["error"]["code"]) == (5, "AMBIGUOUS_CARD")
    candidates = out["error"]["details"]["candidates"]
    assert {(c["id"], c["list"]) for c in candidates} == {(first, "Todo"), (second, "Doing")}
    assert [r for r in fake_trello.requests if r.method != "GET"] == []
    assert calls(fake_trello, f"/boards/{BOARD}") == []


@pytest.mark.tc("TC-M09-059")
def test_cards_of_another_board_are_located_without_a_selected_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    seed_board(fake_trello, OTHER, "Elsewhere", "other001")
    elsewhere = add_list(fake_trello, "Backlog", 1, board_id=OTHER)
    far = card(fake_trello, elsewhere, "Far card", 12, board_id=OTHER, short_link="farCard1")
    card(fake_trello, elsewhere, "Refactor", 13, board_id=OTHER)
    write_config(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", far)
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["list"] == {"id": elsewhere, "name": "Backlog"}
    assert out["meta"]["board_id"] == OTHER
    code, out = run(capsys, "card", "get", "https://trello.com/c/farCard1/12-far-card")
    assert (code, out["data"]["id"]) == (0, far)
    for ref in ("12", "Refactor"):
        code, out = run(capsys, "card", "get", ref)
        assert (code, out["error"]["code"]) == (3, "BOARD_NOT_CONFIGURED")
    assert open_cache(trelloctl_home).get_card(OTHER, far) is not None
    assert calls(fake_trello, f"/cards/{far}")


@pytest.mark.tc("TC-M09-061")
def test_a_name_wins_over_an_equal_short_link(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    named = card(fake_trello, ids["todo"], "Refactor", 1, short_link="namedAAA")
    card(fake_trello, ids["doing"], "Something else", 2, short_link="Refactor")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", "Refactor")
    assert (code, out["data"]["id"]) == (0, named)
    assert fake_trello.requests == []

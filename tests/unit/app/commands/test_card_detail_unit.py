"""Unit tests of the comment, label and member commands of `app/commands/card.py`."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m10_support import (
    ME,
    FakeTrello,
    calls,
    card,
    close_caches,  # noqa: F401
    comment,
    error_code,
    finish,
    run,
    seed_world,
    writes,
)

Capsys = pytest.CaptureFixture[str]


def world(fake: FakeTrello) -> tuple[dict[str, str], str]:
    ids = seed_world(fake)
    return ids, card(fake, ids["todo"], "X", 1)


def test_comment_text_must_not_be_empty_or_too_long(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "add", target, "--text", "   ")
    assert (code, error_code(out)) == (2, "USAGE")
    code, out = run(capsys, "card", "comment", "add", target, "--text", "x" * 16385)
    assert (code, error_code(out)) == (2, "USAGE")
    assert out["error"]["details"] == {"length": 16385, "max": 16384}
    assert writes(fake_trello) == []


def test_comments_options_are_validated(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comments", target, "--limit", "-1")
    assert (code, error_code(out)) == (2, "USAGE")
    code, out = run(capsys, "card", "comments", target, "--fields", "id,nope")
    assert (code, error_code(out)) == (2, "USAGE")
    assert fake_trello.requests == []


def test_comments_name_their_authors_from_the_board_members(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids, target = world(fake_trello)
    comment(fake_trello, target, "by ana", member=ids["ana"])
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comments", target)
    assert (code, out["ok"]) == (0, True)
    (only,) = out["data"]["comments"]
    assert (only["member"], only["memberId"]) == ("ana", ids["ana"])


def test_comments_stop_paging_at_the_limit_boundary(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    for index in range(1001):
        comment(fake_trello, target, str(index))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comments", target, "--limit", "1000")
    assert (code, out["ok"], len(out["data"]["comments"])) == (0, True, 1000)
    assert out["meta"]["truncated"] is True
    assert len(calls(fake_trello, f"/cards/{target}/actions")) == 2
    code, out = run(capsys, "card", "comments", target, "--limit", "1001")
    assert len(out["data"]["comments"]) == 1001
    assert out["meta"]["truncated"] is False


def test_an_unexpected_comment_response_is_an_api_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("POST", "/cards/{id}/actions/comments", 200, "{}")
    code, out = run(capsys, "card", "comment", "add", target, "--text", "hi")
    assert (code, error_code(out)) == (7, "API_ERROR")


def test_editing_or_removing_a_missing_comment_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    ghost = "a" * 24
    code, out = run(capsys, "card", "comment", "edit", target, ghost, "--text", "x")
    assert (code, error_code(out)) == (5, "COMMENT_NOT_FOUND")
    code, out = run(capsys, "card", "comment", "remove", target, ghost, "--yes")
    assert (code, error_code(out)) == (5, "COMMENT_NOT_FOUND")


def test_other_failures_of_comment_writes_are_kept(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    own = comment(fake_trello, target, "mine", member=ME)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("PUT", "/cards/{id}/actions/{a}/comments", 500)
    code, out = run(capsys, "card", "comment", "edit", target, own, "--text", "x")
    assert (code, error_code(out)) == (7, "API_ERROR")
    fake_trello.fail_next("DELETE", "/cards/{id}/actions/{a}/comments", 500)
    code, out = run(capsys, "card", "comment", "remove", target, own, "--yes")
    assert (code, error_code(out)) == (7, "API_ERROR")
    assert own in fake_trello.state.actions


def test_an_edit_without_a_usable_response_keeps_the_text_sent(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    own = comment(fake_trello, target, "mine", member=ME)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("PUT", "/cards/{id}/actions/{a}/comments", 200, "{}")
    code, out = run(capsys, "card", "comment", "edit", target, own, "--text", "new")
    assert (code, out["ok"]) == (0, True)
    assert (out["data"]["id"], out["data"]["text"]) == (own, "new")


def test_a_label_repeated_in_the_list_is_written_once(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "label", "add", target, f"bug,{ids['bug']},bug")
    assert (code, out["ok"]) == (0, True)
    assert len(writes(fake_trello)) == 1
    assert [x["name"] for x in out["data"]["labels"]] == ["bug"]


def test_removing_nothing_present_writes_nothing(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "member", "remove", target, "ana")
    assert (code, out["ok"]) == (0, True)
    assert writes(fake_trello) == []
    assert out["data"]["members"] == []

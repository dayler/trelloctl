"""Unit tests of `app/commands/checklists.py` error and edge paths, run through the CLI."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m07_support import make_ctx
from tests.acceptance.m10_support import (
    FakeTrello,
    add_checklist,
    calls,
    card,
    close_caches,  # noqa: F401
    error_code,
    finish,
    item_id,
    request_params,
    run,
    seed_world,
    writes,
)

from trelloctl.app.commands import checklists
from trelloctl.domain.errors import TrelloctlError

Capsys = pytest.CaptureFixture[str]


def world(fake: FakeTrello) -> tuple[str, str]:
    ids = seed_world(fake)
    target = card(fake, ids["todo"], "X", 1)
    return target, add_checklist(fake, target, "QA", ("a", "b"))


@pytest.mark.parametrize(
    "argv",
    [
        ("checklist", "add", "X", "--name", "  "),
        ("checklist", "rename", "X", "QA", "--name", " "),
        ("checklist", "item", "add", "X", "QA", "--name", ""),
        ("checklist", "item", "rename", "X", "QA", "a", "--name", " "),
    ],
)
def test_an_empty_name_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys, argv: tuple[str, ...]
) -> None:
    target, _ = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", *(target if a == "X" else a for a in argv))
    assert (code, error_code(out)) == (2, "USAGE")
    assert writes(fake_trello) == []


def test_check_needs_items_or_all(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA")
    assert (code, error_code(out)) == (2, "USAGE")


def test_an_item_remove_without_items_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    target, _ = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board=None)
    args = {"card": target, "checklist": "QA", "items": []}
    with pytest.raises(TrelloctlError) as raised:
        checklists.item_remove(ctx, args)
    assert raised.value.code == "USAGE"


def test_add_at_an_index_places_the_checklist_among_the_others(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "add", target, "--name", "New", "--pos", "0")
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, f"/cards/{target}/checklists", "POST")
    assert request_params(post)["pos"] == "top"
    code, out = run(capsys, "card", "checklist", "add", target, "--name", "Far", "--pos", "5")
    assert (code, error_code(out)) == (2, "USAGE")
    assert len(calls(fake_trello, f"/cards/{target}/checklists", "POST")) == 1


def test_add_in_the_middle_uses_a_numeric_position(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    add_checklist(fake_trello, target, "One", pos=10)
    add_checklist(fake_trello, target, "Two", pos=20)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "add", target, "--name", "Mid", "--pos", "1")
    assert (code, out["ok"]) == (0, True)
    (post,) = calls(fake_trello, f"/cards/{target}/checklists", "POST")
    assert float(request_params(post)["pos"]) == 15


def test_an_unexpected_checklist_response_is_an_api_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("POST", "/cards/{id}/checklists", 200, "{}")
    code, out = run(capsys, "card", "checklist", "add", target, "--name", "New")
    assert (code, error_code(out)) == (7, "API_ERROR")


def test_a_failed_initial_item_keeps_what_was_created(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    fake_trello.fail_next("POST", "/checklists/{id}/checkItems", 500)
    code, out = run(capsys, "card", "checklist", "add", target, "--name", "Two", "--items", "x,y")
    assert (code, error_code(out)) == (7, "API_ERROR")
    code, out = run(capsys, "card", "checklist", "list", target)
    assert [c["name"] for c in out["data"]["checklists"]] == ["Two"]
    assert [c["total"] for c in out["data"]["checklists"]] == [0]


def test_a_checklist_deleted_in_trello_is_reported_when_written_by_name(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    del fake_trello.state.checklists[checklist]
    code, out = run(capsys, "card", "checklist", "rename", target, "QA", "--name", "New")
    assert (code, error_code(out)) == (5, "CHECKLIST_NOT_FOUND")


def test_an_item_deleted_in_trello_is_reported_by_name(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    fake_trello.state.checklists[checklist]["checkItems"].pop(0)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "a")
    assert (code, error_code(out)) == (5, "CHECKITEM_NOT_FOUND")


def test_an_item_deleted_in_trello_is_reported_by_id(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    gone = item_id(fake_trello, checklist, "a")
    fake_trello.state.checklists[checklist]["checkItems"].pop(0)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", gone)
    assert (code, error_code(out)) == (5, "CHECKITEM_NOT_FOUND")


def test_the_write_error_is_kept_when_the_item_still_exists(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    target_item = item_id(fake_trello, checklist, "a")
    fake_trello.fail_next("PUT", "/cards/{id}/checkItem/{item}", 404, "gone")
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", target_item)
    assert (code, error_code(out)) == (7, "API_ERROR")
    assert out["error"]["details"]["upstream"] == "gone"

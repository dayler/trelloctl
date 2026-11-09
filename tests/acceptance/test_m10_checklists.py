"""M10 acceptance tests: checklists and their items (RF-20)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m10_support import (
    FakeTrello,
    add_checklist,
    calls,
    card,
    checklist_names,
    checklist_state,
    close_caches,  # noqa: F401
    error_code,
    finish,
    item_counts,
    item_id,
    request_params,
    run,
    seed_world,
    writes,
)

Capsys = pytest.CaptureFixture[str]


def qa_card(fake: FakeTrello, items: tuple[str, ...] = ("a", "b", "c")) -> tuple[str, str]:
    """A card with a checklist `QA`; returns `(card id, checklist id)`."""
    ids = seed_world(fake)
    target = card(fake, ids["todo"], "X", 1)
    return target, add_checklist(fake, target, "QA", items)


def get_checklists(
    capsys: Capsys, target: str
) -> tuple[dict[str, object], list[dict[str, object]]]:
    code, out = run(capsys, "card", "get", target, "--with", "checklists")
    assert (code, out["ok"]) == (0, True)
    return out["data"]["badges"], out["data"]["checklists"]


# --- checklist list ---


@pytest.mark.tc("TC-M10-021")
def test_list_shows_progress_from_the_cache_and_reads_with_fresh(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    fake_trello.state.checklists[checklist]["checkItems"][0]["state"] = "complete"
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "list", target)
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.requests == []
    (only,) = out["data"]["checklists"]
    assert (only["id"], only["name"], only["pos"]) == (checklist, "QA", 1)
    assert (only["progress"], only["checked"], only["total"]) == ("1/3", 1, 3)
    assert [(i["name"], i["state"]) for i in only["items"]] == [
        ("a", "complete"),
        ("b", "incomplete"),
        ("c", "incomplete"),
    ]
    assert set(only["items"][0]) == {"id", "name", "state", "pos"}
    fake_trello.state.checklists[checklist]["checkItems"][1]["state"] = "complete"
    code, out = run(capsys, "card", "checklist", "list", target, "--fresh")
    assert (code, out["ok"]) == (0, True)
    assert [r.path for r in fake_trello.requests] == [f"/cards/{target}"]
    assert out["data"]["checklists"][0]["progress"] == "2/3"


# --- checklist add / rename / remove ---


@pytest.mark.tc("TC-M10-022")
def test_add_creates_the_checklist_first_and_then_its_items(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    add_checklist(fake_trello, target, "Other", pos=100)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys,
        "card",
        "checklist",
        "add",
        target,
        "--name",
        "QA",
        "--items",
        "a,b,c",
        "--pos",
        "top",
    )
    assert (code, out["ok"]) == (0, True)
    sent = writes(fake_trello)
    assert [(r.method, r.path.rsplit("/", 2)[-1]) for r in sent] == [
        ("POST", "checklists"),
        *[("POST", "checkItems")] * 3,
    ]
    assert sent[0].path == f"/cards/{target}/checklists"
    assert request_params(sent[0]) == {"name": "QA", "pos": "top"}
    assert [request_params(r) for r in sent[1:]] == [{"name": n} for n in "abc"]
    assert checklist_names(fake_trello, target) == ["QA", "Other"]
    data = out["data"]
    assert [i["name"] for i in data["items"]] == ["a", "b", "c"]
    assert data["progress"] == "0/3"
    assert data["id"] in fake_trello.state.checklists


@pytest.mark.tc("TC-M10-023")
def test_add_reads_the_items_from_a_file_one_per_line(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys, tmp_path: Path
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    path = tmp_path / "items.txt"
    path.write_bytes("first ñ\nsecond item\nthird".encode())
    code, out = run(
        capsys, "card", "checklist", "add", target, "--name", "QA", "--items-file", str(path)
    )
    assert (code, out["ok"]) == (0, True)
    assert [i["name"] for i in out["data"]["items"]] == ["first ñ", "second item", "third"]


@pytest.mark.tc("TC-M10-024")
def test_add_ignores_empty_lines_and_carriage_returns(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys, tmp_path: Path
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    path = tmp_path / "items.txt"
    path.write_bytes(b"one\r\n\r\ntwo\r\n   \r\nthree\r\n\r\n")
    code, out = run(
        capsys, "card", "checklist", "add", target, "--name", "QA", "--items-file", str(path)
    )
    assert (code, out["ok"]) == (0, True)
    posts = [request_params(r)["name"] for r in calls(fake_trello, None, "POST", prefix=True)][1:]
    assert posts == ["one", "two", "three"]
    assert not any(n.endswith("\r") for n in posts)
    assert [i["name"] for i in out["data"]["items"]] == ["one", "two", "three"]


@pytest.mark.tc("TC-M10-025")
def test_add_rejects_items_and_items_file_together(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys, tmp_path: Path
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    path = tmp_path / "items.txt"
    path.write_text("a")
    code, out = run(
        capsys,
        "card",
        "checklist",
        "add",
        target,
        "--name",
        "QA",
        "--items",
        "a",
        "--items-file",
        str(path),
    )
    assert (code, error_code(out)) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-026")
def test_rename_and_remove_a_checklist(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "rename", target, "QA", "--name", "Pruebas")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["name"] == "Pruebas"
    assert fake_trello.state.checklists[checklist]["name"] == "Pruebas"
    (put,) = calls(fake_trello, f"/checklists/{checklist}", "PUT")
    assert request_params(put) == {"name": "Pruebas"}
    code, out = run(capsys, "card", "checklist", "remove", target, "Pruebas", "--yes")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["deleted"] == checklist
    assert checklist not in fake_trello.state.checklists
    badges, lists = get_checklists(capsys, target)
    assert lists == []
    assert (badges["checkItems"], badges["checkItemsChecked"]) == (0, 0)


@pytest.mark.tc("TC-M10-027")
def test_checklist_remove_without_yes_asks_for_confirmation(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "remove", target, "QA")
    assert error_code(out) == "CONFIRMATION_REQUIRED"
    assert code != 0
    assert writes(fake_trello) == []
    assert checklist in fake_trello.state.checklists


# --- items ---


@pytest.mark.tc("TC-M10-028")
def test_item_add_at_the_top_and_checked(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys,
        "card",
        "checklist",
        "item",
        "add",
        target,
        "QA",
        "--name",
        "e2e",
        "--pos",
        "0",
        "--checked",
    )
    assert (code, out["ok"]) == (0, True)
    (post,) = writes(fake_trello)
    assert post.path == f"/checklists/{checklist}/checkItems"
    assert request_params(post) == {"name": "e2e", "pos": "top", "checked": "true"}
    names = [i["name"] for i in fake_trello.state.checklists[checklist]["checkItems"]]
    assert names == ["e2e", "a", "b", "c"]
    assert checklist_state(fake_trello, checklist)["e2e"] == "complete"
    first_item = out["data"]["items"][0]
    assert (first_item["name"], first_item["state"]) == ("e2e", "complete")


@pytest.mark.tc("TC-M10-029")
def test_item_add_with_an_index_out_of_range_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys, "card", "checklist", "item", "add", target, "QA", "--name", "x", "--pos", "9"
    )
    assert (code, error_code(out)) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-030")
def test_check_skips_the_items_already_checked(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    fake_trello.state.checklists[checklist]["checkItems"][0]["state"] = "complete"
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "a,b")
    assert (code, out["ok"]) == (0, True)
    (put,) = writes(fake_trello)
    assert (put.method, put.path) == (
        "PUT",
        f"/cards/{target}/checkItem/{item_id(fake_trello, checklist, 'b')}",
    )
    assert request_params(put) == {"state": "complete"}
    assert out["data"]["changed"] == [item_id(fake_trello, checklist, "b")]
    assert checklist_state(fake_trello, checklist) == {
        "a": "complete",
        "b": "complete",
        "c": "incomplete",
    }


@pytest.mark.tc("TC-M10-031")
def test_check_and_uncheck_all_only_touch_the_items_that_change(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    fake_trello.state.checklists[checklist]["checkItems"][0]["state"] = "complete"
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "--all")
    assert (code, out["ok"]) == (0, True)
    assert len(writes(fake_trello)) == 2
    assert set(checklist_state(fake_trello, checklist).values()) == {"complete"}
    assert out["data"]["progress"] == "3/3"
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "checklist", "item", "uncheck", target, "QA", "--all")
    assert (code, out["ok"]) == (0, True)
    assert len(writes(fake_trello)) == 3
    assert set(checklist_state(fake_trello, checklist).values()) == {"incomplete"}
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "checklist", "item", "uncheck", target, "QA", "--all")
    assert (code, out["ok"]) == (0, True)
    assert writes(fake_trello) == []
    assert out["data"]["changed"] == []


@pytest.mark.tc("TC-M10-032")
def test_items_and_all_together_are_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "a", "--all")
    assert (code, error_code(out)) == (2, "USAGE")
    code, out = run(capsys, "card", "checklist", "item", "uncheck", target, "QA", "a,b", "--all")
    assert (code, error_code(out)) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-033")
def test_item_rename_and_remove(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys, "card", "checklist", "item", "rename", target, "QA", "a", "--name", "a2"
    )
    assert (code, out["ok"]) == (0, True)
    assert [i["name"] for i in out["data"]["items"]] == ["a2", "b", "c"]
    a2, b = item_id(fake_trello, checklist, "a2"), item_id(fake_trello, checklist, "b")
    (put,) = calls(fake_trello, f"/cards/{target}/checkItem/{a2}", "PUT")
    assert request_params(put) == {"name": "a2"}
    code, out = run(capsys, "card", "checklist", "item", "remove", target, "QA", "a2,b", "--yes")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["deleted"] == [a2, b]
    assert [i["name"] for i in fake_trello.state.checklists[checklist]["checkItems"]] == ["c"]
    deletes = [r.path for r in calls(fake_trello, None, "DELETE", prefix=True)]
    assert deletes == [
        f"/checklists/{checklist}/checkItems/{a2}",
        f"/checklists/{checklist}/checkItems/{b}",
    ]


@pytest.mark.tc("TC-M10-034")
def test_item_remove_without_yes_asks_for_confirmation(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, checklist = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "remove", target, "QA", "a")
    assert error_code(out) == "CONFIRMATION_REQUIRED"
    assert code != 0
    assert writes(fake_trello) == []
    assert len(fake_trello.state.checklists[checklist]["checkItems"]) == 3


@pytest.mark.tc("TC-M10-035")
def test_every_write_returns_the_checklist_and_updates_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    steps = [
        ("checklist", "add", target, "--name", "QA", "--items", "a,b,c"),
        ("checklist", "item", "add", target, "QA", "--name", "d", "--checked"),
        ("checklist", "item", "check", target, "QA", "a,b"),
        ("checklist", "item", "uncheck", target, "QA", "b"),
        ("checklist", "item", "rename", target, "QA", "c", "--name", "c2"),
        ("checklist", "item", "remove", target, "QA", "a", "--yes"),
        ("checklist", "rename", target, "QA", "--name", "Tests"),
    ]
    for step in steps:
        fake_trello.requests.clear()
        code, out = run(capsys, "card", *step)
        assert (code, out["ok"]) == (0, True), step
        (checklist,) = fake_trello.state.checklists.values()
        assert out["data"]["id"] == checklist["id"], step
        assert [(i["name"], i["state"]) for i in out["data"]["items"]] == [
            (i["name"], i["state"]) for i in checklist["checkItems"]
        ], step
        total, checked = item_counts(fake_trello, target)
        assert (out["data"]["total"], out["data"]["checked"]) == (total, checked), step
        fake_trello.requests.clear()
        badges, lists = get_checklists(capsys, target)
        assert fake_trello.requests == []
        assert (badges["checkItems"], badges["checkItemsChecked"]) == (total, checked), step
        assert [(i["name"], i["state"]) for i in lists[0]["items"]] == [
            (i["name"], i["state"]) for i in checklist["checkItems"]
        ], step
        assert lists[0]["name"] == checklist["name"]


@pytest.mark.tc("TC-M10-036")
def test_repeated_names_are_ambiguous_with_candidates(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = qa_card(fake_trello)
    second = add_checklist(fake_trello, target, "QA", ("x",), pos=2)
    other = add_checklist(fake_trello, target, "Dup", ("e2e", "e2e"), pos=3)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "rename", target, "QA", "--name", "New")
    assert error_code(out) == "AMBIGUOUS_CHECKLIST"
    assert code != 0
    candidates = out["error"]["details"]["candidates"]
    assert len(candidates) == 2
    assert second in {c["id"] for c in candidates}
    code, out = run(capsys, "card", "checklist", "item", "check", target, "Dup", "e2e")
    assert error_code(out) == "AMBIGUOUS_CHECKITEM"
    ambiguous = out["error"]["details"]["candidates"]
    assert len(ambiguous) == 2
    assert {c["id"] for c in ambiguous} == {
        i["id"] for i in fake_trello.state.checklists[other]["checkItems"]
    }
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-037")
def test_missing_checklists_and_items_are_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    target, _ = qa_card(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "rename", target, "Nope", "--name", "x")
    assert error_code(out) == "CHECKLIST_NOT_FOUND"
    assert code != 0
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "zzz")
    assert error_code(out) == "CHECKITEM_NOT_FOUND"
    assert code != 0
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-038")
def test_an_item_name_is_resolved_inside_its_own_checklist(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    qa = add_checklist(fake_trello, target, "QA", ("e2e",), pos=1)
    release = add_checklist(fake_trello, target, "Release", ("e2e",), pos=2)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "checklist", "item", "check", target, "QA", "e2e")
    assert (code, out["ok"]) == (0, True)
    assert checklist_state(fake_trello, qa) == {"e2e": "complete"}
    assert checklist_state(fake_trello, release) == {"e2e": "incomplete"}
    assert len(writes(fake_trello)) == 1

"""M10 acceptance tests: labels and members of a card (RF-18, RF-19)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m10_support import (
    FakeTrello,
    add_label,
    add_member,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    error_code,
    finish,
    request_params,
    run,
    seed_world,
    writes,
)


def label_names(fake: FakeTrello, target: str) -> list[str]:
    return [fake.state.labels[i]["name"] for i in fake.state.cards[target]["idLabels"]]


def member_names(fake: FakeTrello, target: str) -> list[str]:
    return [fake.state.members[i]["username"] for i in fake.state.cards[target]["idMembers"]]


@pytest.mark.tc("TC-M10-012")
def test_label_add_ignores_the_labels_already_present(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1, labels=(ids["bug"],))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "label", "add", target, "bug,urgent")
    assert (code, out["ok"]) == (0, True)
    posts = calls(fake_trello, f"/cards/{target}/idLabels", "POST")
    assert [request_params(p) for p in posts] == [{"value": ids["urgent"]}]
    assert len(writes(fake_trello)) == 1
    assert label_names(fake_trello, target) == ["bug", "urgent"]
    row = cached(trelloctl_home, target)
    assert row is not None
    assert row["id_labels"] == [ids["bug"], ids["urgent"]]


@pytest.mark.tc("TC-M10-013")
def test_label_remove_ignores_the_labels_that_are_absent(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    add_label(fake_trello, "ux", "blue")
    target = card(fake_trello, ids["todo"], "X", 1, labels=(ids["bug"], ids["urgent"]))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "label", "remove", target, "bug,ux")
    assert (code, out["ok"]) == (0, True)
    assert [r.path for r in writes(fake_trello)] == [f"/cards/{target}/idLabels/{ids['bug']}"]
    assert [r.method for r in writes(fake_trello)] == ["DELETE"]
    assert label_names(fake_trello, target) == ["urgent"]


@pytest.mark.tc("TC-M10-014")
def test_label_set_is_the_same_write_as_update_labels(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    first = card(fake_trello, ids["todo"], "X", 1)
    second = card(fake_trello, ids["todo"], "Y", 2)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "label", "set", first, "bug,urgent")
    assert (code, out["ok"]) == (0, True)
    code, out = run(capsys, "card", "update", second, "--labels", "bug,urgent")
    assert (code, out["ok"]) == (0, True)
    (put_set,) = calls(fake_trello, f"/cards/{first}", "PUT")
    (put_update,) = calls(fake_trello, f"/cards/{second}", "PUT")
    assert request_params(put_set) == request_params(put_update)
    assert request_params(put_set) == {"idLabels": f"{ids['bug']},{ids['urgent']}"}
    assert label_names(fake_trello, first) == label_names(fake_trello, second)


@pytest.mark.tc("TC-M10-015")
def test_label_operations_return_the_final_set(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1, labels=(ids["bug"],))
    finish(trelloctl_home, fake_trello)
    expected = [
        (("add", "urgent"), ["bug", "urgent"]),
        (("remove", "bug"), ["urgent"]),
        (("set", "bug"), ["bug"]),
    ]
    for (action, value), names in expected:
        code, out = run(capsys, "card", "label", action, target, value)
        assert (code, out["ok"]) == (0, True)
        assert [x["name"] for x in out["data"]["labels"]] == names
        assert out["data"]["id"] == target
        assert label_names(fake_trello, target) == names


@pytest.mark.tc("TC-M10-016")
def test_an_unknown_label_aborts_before_any_write(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "label", "add", target, "bug,nope")
    assert error_code(out) == "LABEL_NOT_FOUND"
    assert code != 0
    assert writes(fake_trello) == []
    assert label_names(fake_trello, target) == []


@pytest.mark.tc("TC-M10-017")
def test_member_add_ignores_the_members_already_assigned(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    luis = add_member(fake_trello, "luis", "Luis Mora")
    target = card(fake_trello, ids["todo"], "X", 1, members=(ids["ana"],))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "member", "add", target, "ana,luis")
    assert (code, out["ok"]) == (0, True)
    posts = calls(fake_trello, f"/cards/{target}/idMembers", "POST")
    assert [request_params(p) for p in posts] == [{"value": luis}]
    assert len(writes(fake_trello)) == 1
    assert member_names(fake_trello, target) == ["ana", "luis"]


@pytest.mark.tc("TC-M10-018")
def test_member_remove_ignores_the_absent_and_set_equals_update(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    add_member(fake_trello, "pepe", "Pepe Sanz")
    luis = add_member(fake_trello, "luis", "Luis Mora")
    target = card(fake_trello, ids["todo"], "X", 1, members=(ids["ana"],))
    other = card(fake_trello, ids["todo"], "Y", 2, members=(ids["ana"],))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "member", "remove", target, "ana,pepe")
    assert (code, out["ok"]) == (0, True)
    assert [(r.method, r.path) for r in writes(fake_trello)] == [
        ("DELETE", f"/cards/{target}/idMembers/{ids['ana']}")
    ]
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "member", "set", target, "luis")
    assert (code, out["ok"]) == (0, True)
    code, out = run(capsys, "card", "update", other, "--members", "luis")
    assert (code, out["ok"]) == (0, True)
    (put_set,) = calls(fake_trello, f"/cards/{target}", "PUT")
    (put_update,) = calls(fake_trello, f"/cards/{other}", "PUT")
    assert request_params(put_set) == request_params(put_update) == {"idMembers": luis}


@pytest.mark.tc("TC-M10-019")
def test_member_operations_return_the_final_set(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1, members=(ids["ana"],))
    finish(trelloctl_home, fake_trello)
    expected = [
        (("add", "bob"), ["ana", "bob"]),
        (("remove", "ana"), ["bob"]),
        (("set", "ana"), ["ana"]),
    ]
    for (action, value), names in expected:
        code, out = run(capsys, "card", "member", action, target, value)
        assert (code, out["ok"]) == (0, True)
        assert [x["username"] for x in out["data"]["members"]] == names
        assert member_names(fake_trello, target) == names


@pytest.mark.tc("TC-M10-020")
def test_an_unknown_member_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    target = card(fake_trello, ids["todo"], "X", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "member", "add", target, "desconocido")
    assert error_code(out) == "MEMBER_NOT_FOUND"
    assert code != 0
    assert writes(fake_trello) == []

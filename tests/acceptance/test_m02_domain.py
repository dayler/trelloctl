"""M02 acceptance tests: domain models, mover and identifier classification."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import (
    Candidate,
    Card,
    TrelloList,
    classify_board_ref,
    classify_card_ref,
)
from trelloctl.domain.mover import (
    compute_shift,
    list_position_for_index,
    to_trello_pos,
)

DOMAIN = Path(__file__).resolve().parents[2] / "src" / "trelloctl" / "domain"


def make_lists(count: int = 5, archived: tuple[int, ...] = ()) -> list[TrelloList]:
    return [
        TrelloList(id=f"l{i}", name=f"List {i}", pos=float(65536 * (i + 1)), closed=i in archived)
        for i in range(count)
    ]


@pytest.mark.tc("TC-M02-001")
def test_right_one_from_index_one() -> None:
    result = compute_shift(make_lists(5), "l1", "right", 1)
    assert (result.from_index, result.to_index, result.target_list_id) == (1, 2, "l2")


@pytest.mark.tc("TC-M02-002")
def test_left_two_from_index_three() -> None:
    result = compute_shift(make_lists(5), "l3", "left", 2)
    assert (result.from_index, result.to_index, result.target_list_id) == (3, 1, "l1")


@pytest.mark.tc("TC-M02-003")
def test_archived_list_does_not_count() -> None:
    result = compute_shift(make_lists(5, archived=(2,)), "l1", "right", 1)
    assert result.target_list_id == "l3"
    assert (result.from_index, result.to_index, result.list_count) == (1, 2, 4)


@pytest.mark.tc("TC-M02-004")
def test_shift_default_n_is_one() -> None:
    assert compute_shift(make_lists(5), "l1", "right").to_index == 2
    assert compute_shift(make_lists(5), "l1", "left").to_index == 0


@pytest.mark.tc("TC-M02-005")
@pytest.mark.parametrize("n", [0, -1, -7])
def test_shift_n_not_positive_is_usage(n: int) -> None:
    with pytest.raises(TrelloctlError) as info:
        compute_shift(make_lists(5), "l1", "right", n)
    assert info.value.code == "USAGE"


@pytest.mark.tc("TC-M02-006")
def test_shift_out_of_range_details() -> None:
    with pytest.raises(TrelloctlError) as info:
        compute_shift(make_lists(5), "l3", "right", 3)
    assert info.value.code == "MOVE_OUT_OF_RANGE"
    assert info.value.details == {"from_index": 3, "to_index": 6, "list_count": 5}


@pytest.mark.tc("TC-M02-007")
def test_shift_clamp() -> None:
    last = compute_shift(make_lists(5), "l3", "right", 3, clamp=True)
    assert (last.to_index, last.target_list_id) == (4, "l4")
    first = compute_shift(make_lists(5), "l3", "left", 9, clamp=True)
    assert (first.to_index, first.target_list_id) == (0, "l0")


@pytest.mark.tc("TC-M02-008")
def test_shift_clamp_at_edge_is_unchanged() -> None:
    result = compute_shift(make_lists(5), "l4", "right", 1, clamp=True)
    assert result.from_index == result.to_index == 4
    assert result.unchanged is True
    assert compute_shift(make_lists(5), "l1", "right", 1).unchanged is False


@pytest.mark.tc("TC-M02-009")
def test_list_to_index_between_open_neighbours() -> None:
    lists = make_lists(4, archived=(1,))  # open: l0=65536, l2=196608, l3=262144
    assert list_position_for_index(lists, "l3", 0) == "top"
    # l3 to final index 1: between l0 and l2
    assert list_position_for_index(lists, "l3", 1) == (65536 + 196608) / 2
    # l0 to final index 2: after l2, before... l3 is last -> between l2 and l3
    assert list_position_for_index(lists, "l0", 2) == "bottom"
    pos = list_position_for_index(make_lists(5), "l0", 2)
    assert pos == (196608 + 262144) / 2  # between l2 and l3 once l0 is removed


@pytest.mark.tc("TC-M02-010")
@pytest.mark.parametrize("index", [5, 6, 100])
def test_list_to_index_out_of_range(index: int) -> None:
    with pytest.raises(TrelloctlError) as info:
        list_position_for_index(make_lists(5), "l0", index)
    assert info.value.code == "MOVE_OUT_OF_RANGE"
    assert info.value.details["list_count"] == 5


@pytest.mark.tc("TC-M02-011")
def test_pos_top_bottom_and_intermediate() -> None:
    siblings = [100.0, 200.0, 400.0]
    assert to_trello_pos("top", siblings) == "top"
    assert to_trello_pos("bottom", siblings) == "bottom"
    assert to_trello_pos("2", siblings) == 300.0
    assert to_trello_pos(1, siblings) == 150.0
    assert to_trello_pos(None, siblings) == "bottom"


@pytest.mark.tc("TC-M02-012")
def test_pos_index_equal_to_count_is_bottom() -> None:
    assert to_trello_pos("3", [1.0, 2.0, 3.0]) == "bottom"


@pytest.mark.tc("TC-M02-013")
@pytest.mark.parametrize("value", ["4", "-1", "abc", "1.5", ""])
def test_pos_invalid_is_usage(value: str) -> None:
    with pytest.raises(TrelloctlError) as info:
        to_trello_pos(value, [1.0, 2.0, 3.0])
    assert info.value.code == "USAGE"


@pytest.mark.tc("TC-M02-014")
def test_pos_empty_container() -> None:
    assert to_trello_pos("0", []) == "bottom"
    with pytest.raises(TrelloctlError) as info:
        to_trello_pos("1", [])
    assert info.value.code == "USAGE"


@pytest.mark.tc("TC-M02-015")
def test_classify_card_ref_kinds() -> None:
    hex_id = "0123456789abcdef01234567"
    assert classify_card_ref(hex_id) == [Candidate("id", hex_id)]
    assert classify_card_ref("Ab3dE6gH") == [
        Candidate("name", "Ab3dE6gH"),
        Candidate("short_link", "Ab3dE6gH"),
    ]
    assert classify_card_ref("https://trello.com/c/Ab3dE6gH/12-my-card") == [
        Candidate("short_link", "Ab3dE6gH")
    ]
    assert classify_card_ref("42") == [Candidate("id_short", "42")]
    assert classify_card_ref("Fix the login") == [Candidate("name", "Fix the login")]


@pytest.mark.tc("TC-M02-016")
def test_classify_card_ref_boundaries() -> None:
    assert classify_card_ref("12345678") == [Candidate("id_short", "12345678")]
    not_hex = "g" * 24
    assert classify_card_ref(not_hex) == [Candidate("name", not_hex)]


@pytest.mark.tc("TC-M02-017")
def test_classify_board_ref() -> None:
    hex_id = "0123456789abcdef01234567"
    assert classify_board_ref(hex_id) == [Candidate("id", hex_id)]
    assert classify_board_ref("Ab3dE6gH") == [
        Candidate("name", "Ab3dE6gH"),
        Candidate("short_link", "Ab3dE6gH"),
    ]
    assert classify_board_ref("https://trello.com/b/Ab3dE6gH/slug?x=1") == [
        Candidate("short_link", "Ab3dE6gH")
    ]
    assert classify_board_ref("My board") == [Candidate("name", "My board")]


FULL_CARD = {
    "id": "0123456789abcdef01234567",
    "shortLink": "Ab3dE6gH",
    "idShort": 12,
    "name": "Card",
    "desc": "Body",
    "idList": "list1",
    "idLabels": ["lab1"],
    "idMembers": ["mem1"],
    "labels": [{"id": "lab1", "name": "Bug", "color": "red"}],
    "members": [{"id": "mem1", "username": "ana", "fullName": "Ana B"}],
    "due": "2026-11-09T10:00:00.000Z",
    "dueComplete": True,
    "pos": 16384,
    "closed": False,
    "url": "https://trello.com/c/Ab3dE6gH/12-card",
    "dateLastActivity": "2026-11-08T10:00:00.000Z",
    "badges": {"comments": 2, "attachments": 1, "checkItems": 4, "checkItemsChecked": 3},
}


@pytest.mark.tc("TC-M02-018")
def test_card_from_full_json() -> None:
    card = Card.from_json(FULL_CARD)
    assert card.list_id == "list1"
    assert [label.name for label in card.labels] == ["Bug"]
    assert [member.username for member in card.members] == ["ana"]
    assert card.due == "2026-11-09T10:00:00.000Z"
    assert card.due_complete is True
    assert card.url == "https://trello.com/c/Ab3dE6gH/12-card"
    assert card.date_last_activity == "2026-11-08T10:00:00.000Z"
    assert (
        card.badges.comments,
        card.badges.attachments,
        card.badges.check_items,
        card.badges.check_items_checked,
    ) == (2, 1, 4, 3)


@pytest.mark.tc("TC-M02-019")
def test_card_from_minimal_json() -> None:
    card = Card.from_json({"id": "c1", "name": "Bare"})
    assert card.due is None
    assert card.desc == ""
    assert card.due_complete is False
    assert card.badges.comments == 0
    assert card.badges.check_items == 0
    assert card.labels == ()
    assert card.members == ()


@pytest.mark.tc("TC-M02-020")
def test_domain_imports_are_pure() -> None:
    forbidden = {"trelloctl.infra", "trelloctl.app", "sqlite3", "urllib", "socket", "tomllib"}
    files = sorted(DOMAIN.glob("*.py"))
    assert files
    for path in files:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.level:
                    names = ["trelloctl." + (node.module or "")]
            for name in names:
                assert not any(name == bad or name.startswith(bad + ".") for bad in forbidden), (
                    f"{path.name} imports {name}"
                )


@pytest.mark.tc("TC-M02-021")
def test_classify_refactor_word() -> None:
    assert classify_card_ref("Refactor") == [
        Candidate("name", "Refactor"),
        Candidate("short_link", "Refactor"),
    ]


@pytest.mark.tc("TC-M02-022")
def test_negative_index_same_list_and_archived_origin() -> None:
    with pytest.raises(TrelloctlError) as negative:
        list_position_for_index(make_lists(5), "l2", -1)
    assert negative.value.code == "USAGE"
    # Moving to final position 0 inside the same list.
    assert to_trello_pos("0", [196608.0, 262144.0, 327680.0, 65536.0]) == "top"
    assert list_position_for_index(make_lists(5), "l2", 0) == "top"
    # Shift from an archived list.
    with pytest.raises(TrelloctlError) as archived:
        compute_shift(make_lists(5, archived=(2,)), "l2", "right", 1)
    assert archived.value.code == "MOVE_OUT_OF_RANGE"
    assert archived.value.details["from_index"] is None

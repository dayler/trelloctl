"""Unit tests for the mover."""

from __future__ import annotations

import pytest

from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import TrelloList
from trelloctl.domain.mover import open_lists, parse_index, position_at_index, to_trello_pos


def test_open_lists_sorted_and_filtered() -> None:
    lists = [TrelloList("a", "A", 3.0), TrelloList("b", "B", 1.0, True), TrelloList("c", "C", 2.0)]
    assert [item.id for item in open_lists(lists)] == ["c", "a"]


def test_parse_index_accepts_ints_and_digit_strings() -> None:
    assert parse_index(3) == 3
    assert parse_index(" 4 ") == 4
    for bad in (True, -1, "x", "٣"):
        with pytest.raises(TrelloctlError):
            parse_index(bad)


def test_position_at_index_sorts_siblings() -> None:
    assert position_at_index([30.0, 10.0, 20.0], 1) == 15.0
    with pytest.raises(TrelloctlError) as info:
        position_at_index([1.0], 5)
    assert info.value.details == {"index": 5, "max": 1}


def test_to_trello_pos_integer_input() -> None:
    assert to_trello_pos(0, [5.0]) == "top"

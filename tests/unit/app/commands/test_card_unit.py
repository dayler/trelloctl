"""Unit tests of the argument handling and views of `app/commands/card.py`."""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from trelloctl.app.commands import card
from trelloctl.domain.errors import TrelloctlError


def usage(call: Any, *args: Any, **kwargs: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        call(*args, **kwargs)
    assert info.value.code == "USAGE"
    return info.value  # type: ignore[no-any-return]


@pytest.mark.parametrize(
    ("raw", "hour", "expected"),
    [
        ("2026-11-15", 12, datetime(2026, 11, 15, 12, tzinfo=UTC)),
        ("2026-11-15", 0, datetime(2026, 11, 15, 0, tzinfo=UTC)),
        ("2026-11-15T09:30:00", 12, datetime(2026, 11, 15, 9, 30, tzinfo=UTC)),
        ("2026-11-15T09:30:00Z", 12, datetime(2026, 11, 15, 9, 30, tzinfo=UTC)),
        ("2026-11-15T09:30:00+02:00", 12, datetime(2026, 11, 15, 7, 30, tzinfo=UTC)),
        (" 2026-11-15 ", 12, datetime(2026, 11, 15, 12, tzinfo=UTC)),
    ],
)
def test_parse_moment(raw: str, hour: int, expected: datetime) -> None:
    assert card.parse_moment(raw, hour=hour) == expected


@pytest.mark.parametrize("raw", ["mañana", "", "2026-13-01", "tomorrow"])
def test_parse_moment_rejects_non_iso_values(raw: str) -> None:
    usage(card.parse_moment, raw)


def test_description_comes_from_one_source_only(tmp_path: Path) -> None:
    assert card.read_description({}) is None
    assert card.read_description({"desc": "text"}) == "text"
    assert card.read_description({"desc": ""}) == ""
    path = tmp_path / "d.txt"
    path.write_bytes("ñ\r\nline".encode())
    assert card.read_description({"desc_file": str(path)}) == "ñ\r\nline"
    usage(card.read_description, {"desc": "a", "desc_file": str(path)})


def test_description_file_errors_are_usage(tmp_path: Path) -> None:
    usage(card.read_description, {"desc_file": str(tmp_path / "missing.txt")})
    usage(card.read_description, {"desc_file": str(tmp_path)})
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe\x00")
    usage(card.read_description, {"desc_file": str(bad)})


def test_description_limit_counts_characters() -> None:
    assert len(card.read_description({"desc": "ñ" * card.MAX_DESC}) or "") == card.MAX_DESC
    error = usage(card.read_description, {"desc": "ñ" * (card.MAX_DESC + 1)})
    assert error.details == {"length": card.MAX_DESC + 1, "max": card.MAX_DESC}


def test_description_from_standard_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO("Árbol\n".encode()), "utf-8"))
    assert card.read_description({"desc": "-"}) == "Árbol\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO("plain text"))
    assert card.read_description({"desc": "-"}) == "plain text"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b"\xff\xfe"), "utf-8"))
    usage(card.read_description, {"desc": "-"})
    monkeypatch.setattr(sys, "stdin", None)
    usage(card.read_description, {"desc": "-"})


def test_unreadable_standard_input_is_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def read(self) -> str:
            raise OSError("closed")

    monkeypatch.setattr(sys, "stdin", Broken())
    usage(card.read_description, {"desc": "-"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("top", "top"), (" BOTTOM ", "bottom"), ("0", "0"), (3, "3")],
)
def test_pos(raw: Any, expected: str | None) -> None:
    assert card._check_pos(raw) == expected


@pytest.mark.parametrize("raw", ["-1", "abc", "1.5", ""])
def test_pos_rejects_anything_else(raw: str) -> None:
    usage(card._check_pos, raw)


def test_limit() -> None:
    assert card._limit(None) == card.DEFAULT_LIMIT
    assert card._limit(0) is None
    assert card._limit(7) == 7
    for bad in (-1, True, "3", 1.5):
        usage(card._limit, bad)


def test_fields() -> None:
    assert card._fields(None, card.CARD_FIELDS) is None
    assert card._fields(["id", " name "], card.CARD_FIELDS) == ("id", "name")
    for bad in (["nope"], [], [" "]):
        usage(card._fields, bad, card.CARD_FIELDS)


def test_details() -> None:
    assert card._details({}) == ()
    assert card._details({"full": True}) == card.DETAILS
    assert card._details({"with": ["comments", "checklists"]}) == ("checklists", "comments")
    usage(card._details, {"full": True, "with": ["comments"]})
    usage(card._details, {"with": ["labels"]})
    usage(card._details, {"with": []})


def test_keep() -> None:
    assert card._keep(None) == "all"
    assert card._keep(["none"]) == "none"
    assert card._keep(["Labels", "due", "labels"]) == "labels,due"
    for bad in (["votes"], [], ["all", "labels"], ["none", "due"]):
        usage(card._keep, bad)


def test_exclusive_options() -> None:
    assert card._exclusive({"a": None, "b": False}, "a", "b") == []
    assert card._exclusive({"a": 1, "b": None}, "a", "b") == ["a"]
    assert card._exclusive({"a": 0}, "a") == ["a"]
    error = usage(card._exclusive, {"to_x": "x", "left": 1}, "to_x", "left")
    assert error.details == {"options": ["to_x", "left"]}


def row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "c1",
        "id_short": 4,
        "short_link": "abcd1234",
        "name": "Card",
        "desc": None,
        "url": "https://trello.com/c/abcd1234",
        "id_list": "l1",
        "id_labels": ["lb1", "ghost"],
        "id_members": ["m1", "ghost"],
        "due": None,
        "due_complete": 0,
        "pos": 1.5,
        "archived": 0,
        "date_last_activity": "2026-03-01T00:00:00.000Z",
        "badge_comments": None,
        "badge_attachments": 2,
        "badge_check_items": None,
        "badge_check_items_checked": None,
    }
    return {**base, **overrides}


def catalog() -> card.Catalog:
    return card.Catalog(
        {"l1": {"id": "l1", "name": "Todo", "pos": 1, "closed": 0}},
        {"lb1": {"id": "lb1", "name": "bug", "color": "red"}},
        {"m1": {"id": "m1", "username": "ana", "full_name": "Ana Gomez"}},
    )


def test_card_view_resolves_names_and_keeps_unknown_ids() -> None:
    view = card.card_view(row(), catalog())
    assert view["desc"] == ""
    assert view["list"] == {"id": "l1", "name": "Todo"}
    assert view["labels"] == [{"id": "lb1", "name": "bug", "color": "red"}, {"id": "ghost"}]
    assert view["members"] == [
        {"id": "m1", "username": "ana", "fullName": "Ana Gomez"},
        {"id": "ghost"},
    ]
    assert view["badges"] == {
        "comments": 0,
        "attachments": 2,
        "checkItems": 0,
        "checkItemsChecked": 0,
    }
    assert set(view) == set(card.CARD_FIELDS)
    assert card.card_view(row(id_list="gone"), catalog())["list"]["name"] is None


def test_project_keeps_only_the_given_fields_in_order() -> None:
    view = {"id": 1, "name": "n", "desc": "d"}
    assert card._project(view, None) == view
    assert list(card._project(view, ["name", "id", "missing"])) == ["name", "id"]


def test_due_of_tolerates_missing_and_invalid_dates() -> None:
    assert card._due_of(row()) is None
    assert card._due_of(row(due="not a date")) is None
    assert card._due_of(row(due="2026-12-01T10:00:00.000Z")) == datetime(
        2026, 12, 1, 10, tzinfo=UTC
    )
    naive = card._due_of(row(due="2026-12-01T10:00:00"))
    assert naive is not None
    assert naive.tzinfo is not None


def test_page_truncates_only_beyond_the_limit() -> None:
    items = [{"i": n} for n in range(5)]
    assert card._page(items, None) == (items, False)
    assert card._page(items, 5) == (items, False)
    assert card._page(items, 4) == (items[:4], True)


def test_ordered_follows_the_open_lists_then_the_position() -> None:
    cat = card.Catalog(
        {
            "l1": {"id": "l1", "name": "A", "pos": 2, "closed": 0},
            "l2": {"id": "l2", "name": "B", "pos": 1, "closed": 0},
            "l3": {"id": "l3", "name": "Old", "pos": 0, "closed": 1},
        },
        {},
        {},
    )
    rows = [
        row(id="a", id_list="l1", pos=1),
        row(id="b", id_list="l2", pos=2),
        row(id="c", id_list="l2", pos=1),
        row(id="d", id_list="l3", pos=1),
        row(id="e", id_list=None, pos=1),
    ]
    assert [r["id"] for r in card._ordered(rows, cat)] == ["c", "b", "a", "e", "d"]
    assert card._index_of(cat, "l2") == 0
    assert card._index_of(cat, "l3") is None
    assert card._list_ref(cat, "l1", 1) == {"id": "l1", "name": "A", "index": 1}
    assert card._list_ref(cat, None, None) == {"id": None, "name": None, "index": None}

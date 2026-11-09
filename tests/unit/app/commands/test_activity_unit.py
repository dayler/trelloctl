"""Unit tests of the `board activity` helpers and handler checks."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from trelloctl.app.commands import activity as mod
from trelloctl.domain.errors import TrelloctlError


def usage(call: Any, *args: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as caught:
        call(*args)
    assert caught.value.code == "USAGE"
    return caught.value


def test_instants_are_normalized_to_utc() -> None:
    assert mod._instant(None, "since") is None
    assert mod._instant("2026-03-02", "since") == "2026-03-02T00:00:00Z"
    assert mod._instant("2026-03-02T10:00:00+02:00", "since") == "2026-03-02T08:00:00Z"
    assert usage(mod._instant, "yesterday", "before").details["value"] == "yesterday"


def test_types_accept_qualified_names_and_reject_unknown_ones() -> None:
    assert mod._types(None) == []
    assert mod._types(["updateCard:closed", "createCard"]) == ["updateCard:closed", "createCard"]
    assert usage(mod._types, ["createCard", "nope"]).details["unknown"] == ["nope"]


def test_fields_and_limit_validation() -> None:
    assert mod._fields(None) is None
    assert mod._fields(["id", "data"]) == ["id", "data"]
    usage(mod._fields, [])
    usage(mod._fields, ["id", "nope"])
    assert mod._limit(None) == 50
    assert mod._limit(0) is None
    assert mod._limit(7) == 7
    for bad in (-1, True, "3"):
        usage(mod._limit, bad)


def test_normalize_minimal_and_creator_only_actions() -> None:
    assert mod.normalize({"id": "a", "type": "x", "date": "d"}) == {
        "id": "a",
        "type": "x",
        "date": "d",
        "data": {},
    }
    item = mod.normalize({"id": "a", "type": "x", "memberCreator": {"id": "m", "username": "u"}})
    assert item["member"] == {"id": "m", "username": "u"}
    assert mod.normalize({"id": "a", "data": "odd", "memberCreator": "odd"})["data"] == {}


def test_summary_covers_text_lists_changes_creation_and_members() -> None:
    summary = mod._summary(
        "updateCard",
        {
            "card": {"id": "c", "name": "Now"},
            "old": {"name": "Before"},
            "text": "hello",
            "idMember": "m1",
        },
    )
    assert summary == {
        "text": "hello",
        "changes": {"name": {"from": "Before", "to": "Now"}},
        "member_id": "m1",
    }
    assert mod._summary("createList", {"list": {"id": "l", "name": "New"}}) == {"name": "New"}
    assert mod._summary("deleteCard", {}) == {}


def client_returning(*pages: Any) -> SimpleNamespace:
    queue = list(pages)
    sent: list[dict[str, Any]] = []

    def get(path: str, params: dict[str, Any]) -> Any:
        sent.append(params)
        return queue.pop(0)

    return SimpleNamespace(client=SimpleNamespace(get=get), sent=sent)


def test_fetch_stops_on_a_short_page_and_ignores_garbage() -> None:
    resolver = client_returning([{"id": "2"}, "junk", {"id": "1"}])
    items, truncated = mod._fetch(resolver, "b", {"before": None}, 5)  # type: ignore[arg-type]
    assert [i["id"] for i in items] == ["2", "1"]
    assert truncated is False
    assert resolver.sent[0]["limit"] == 6
    resolver = client_returning({"unexpected": True})
    assert mod._fetch(resolver, "b", {}, None) == ([], False)  # type: ignore[arg-type]


def test_fetch_truncates_and_stops_on_an_action_without_id() -> None:
    resolver = client_returning([{"id": "3"}, {"id": "2"}, {"id": "1"}])
    items, truncated = mod._fetch(resolver, "b", {}, 2)  # type: ignore[arg-type]
    assert ([i["id"] for i in items], truncated) == (["3", "2"], True)
    resolver = client_returning([{"id": "3"}, {"nope": 1}])
    items, truncated = mod._fetch(resolver, "b", {}, None)  # type: ignore[arg-type]
    assert len(items) == 2
    assert truncated is False

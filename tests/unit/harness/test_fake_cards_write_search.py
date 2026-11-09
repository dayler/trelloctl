"""Unit tests for the fake Trello card writes, comment reads and search (M09)."""

from __future__ import annotations

from typing import Any

import pytest
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.cards_write import position

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.trello_client import TrelloClient

BOARD = "5f1b2c3d4e5f6a7b8c9d0e1f"


def client(fake: FakeTrello) -> TrelloClient:
    return TrelloClient(fake.base_url, fake.api_key, fake.token, 5)


def seed(fake: FakeTrello) -> dict[str, str]:
    state = fake.state
    state.boards[BOARD] = {"id": BOARD, "name": "Work", "shortLink": "abcd1234"}
    state.lists["l1"] = {"id": "l1", "idBoard": BOARD, "name": "Todo", "pos": 1, "closed": False}
    state.lists["l2"] = {"id": "l2", "idBoard": BOARD, "name": "Done", "pos": 2, "closed": False}
    state.labels["lb1"] = {"id": "lb1", "idBoard": BOARD, "name": "bug", "color": "red"}
    src = "a" * 24
    state.cards[src] = {
        "id": src,
        "idBoard": BOARD,
        "idList": "l1",
        "name": "Source",
        "desc": "body",
        "closed": False,
        "idShort": 1,
        "shortLink": "srcLink1",
        "pos": 10,
        "due": "2026-12-01T10:00:00.000Z",
        "dueComplete": False,
        "idLabels": ["lb1"],
        "idMembers": ["m1"],
    }
    state.checklists["k1"] = {
        "id": "k1",
        "idCard": src,
        "name": "QA",
        "pos": 1,
        "checkItems": [{"id": "i1", "name": "one", "state": "complete", "pos": 1}],
    }
    state.attachments["t1"] = {"id": "t1", "idCard": src, "name": "f.txt"}
    state.actions["x1"] = {
        "id": "x1",
        "type": "commentCard",
        "data": {"text": "Deploy soon", "card": {"id": src}},
    }
    state.actions["x2"] = {
        "id": "x2",
        "type": "commentCard",
        "data": {"text": "second comment", "card": {"id": src}},
    }
    return {"src": src}


def test_create_assigns_ids_positions_and_children(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    c = client(fake_trello)
    first = c.post("/cards", {"idList": "l2", "name": "N", "idLabels": "lb1", "pos": "bottom"})
    assert not first["id"].isdigit()
    assert (first["idShort"], first["idList"], first["labels"][0]["name"]) == (2, "l2", "bug")
    assert first["badges"] == {
        "comments": 0,
        "attachments": 0,
        "checkItems": 0,
        "checkItemsChecked": 0,
    }
    top = c.post("/cards", {"idList": "l2", "name": "T", "pos": "top"})
    assert top["pos"] < first["pos"]
    numeric = c.post("/cards", {"idList": "l2", "name": "Z", "pos": "5", "idMembers": "m1"})
    assert (numeric["pos"], numeric["idMembers"]) == (5.0, ["m1"])
    copy = c.post("/cards", {"idList": "l2", "idCardSource": ids["src"], "keepFromSource": "all"})
    assert (copy["name"], copy["desc"], copy["due"], copy["idLabels"]) == (
        "Source",
        "body",
        "2026-12-01T10:00:00.000Z",
        ["lb1"],
    )
    assert copy["badges"] == {
        "comments": 2,
        "attachments": 1,
        "checkItems": 1,
        "checkItemsChecked": 1,
    }
    bare = c.post(
        "/cards",
        {"idList": "l2", "idCardSource": ids["src"], "keepFromSource": "none", "name": "B"},
    )
    assert (bare["desc"], bare["due"], bare["idLabels"], bare["badges"]["comments"]) == (
        "body",
        None,
        [],
        0,
    )
    some = c.post(
        "/cards", {"idList": "l2", "idCardSource": ids["src"], "keepFromSource": "labels,due"}
    )
    assert (some["idLabels"], some["badges"]["checkItems"]) == (["lb1"], 0)


@pytest.mark.parametrize(
    "params",
    [{}, {"idList": "nope", "name": "x"}, {"idList": "l1"}, {"idList": "l1", "idCardSource": "zz"}],
)
def test_create_rejects_bad_requests(fake_trello: FakeTrello, params: dict[str, str]) -> None:
    seed(fake_trello)
    with pytest.raises(TrelloctlError) as info:
        client(fake_trello).post("/cards", params)
    assert info.value.details["status"] in (400, 404)


def test_update_changes_only_the_given_fields(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    c = client(fake_trello)
    out = c.put(
        f"/cards/{ids['src']}",
        {
            "name": "New",
            "desc": "",
            "due": "null",
            "dueComplete": True,
            "closed": True,
            "idList": "l2",
            "idLabels": "",
            "idMembers": "m2,m3",
            "pos": "top",
        },
    )
    assert (out["name"], out["desc"], out["due"]) == ("New", "", None)
    assert (out["dueComplete"], out["closed"], out["idList"]) == (True, True, "l2")
    assert (out["idLabels"], out["idMembers"]) == ([], ["m2", "m3"])
    untouched = c.put(f"/cards/{ids['src']}", {"closed": False})
    assert untouched["name"] == "New"
    assert c.put("/cards/srcLink1", {"due": ""})["due"] is None


def test_update_of_a_missing_card_or_list_fails(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    c = client(fake_trello)
    with pytest.raises(TrelloctlError) as info:
        c.put("/cards/" + "f" * 24, {"name": "x"})
    assert info.value.details["status"] == 404
    with pytest.raises(TrelloctlError) as info:
        c.put(f"/cards/{ids['src']}", {"idList": "nope"})
    assert info.value.details["status"] in (400, 404)


def test_positions() -> None:
    from tests.fakes.trello.core import State

    state = State()
    assert position(state, "l", None) == 16384.0
    assert position(state, "l", "top") == 8192.0
    state.cards["c"] = {"id": "c", "idList": "l", "pos": 4, "closed": False}
    assert position(state, "l", "bottom") == 4 + 16384.0
    assert position(state, "l", "top") == 2.0
    assert position(state, "l", "7") == 7.0
    assert position(state, "l", "bottom", exclude="c") == 16384.0


def test_card_actions_filter_and_paginate(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    c = client(fake_trello)
    both = c.get(f"/cards/{ids['src']}/actions", {"filter": "commentCard"})
    assert [a["id"] for a in both] == ["x2", "x1"]
    older = c.get(f"/cards/{ids['src']}/actions", {"before": "x2", "limit": 1000})
    assert [a["id"] for a in older] == ["x1"]
    assert c.get(f"/cards/{ids['src']}/actions", {"filter": "updateCard"}) == []
    assert len(c.get(f"/cards/{ids['src']}/actions", {"limit": 1})) == 1
    with pytest.raises(TrelloctlError) as info:
        c.get("/cards/" + "f" * 24 + "/actions")
    assert info.value.details["status"] == 404


def search(fake: FakeTrello, **params: Any) -> dict[str, Any]:
    result = client(fake).get("/search", params)
    assert isinstance(result, dict)
    return result


def test_search_by_comment_partial_or_whole_word(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    both = {"query": "comment:Depl", "idBoards": BOARD, "modelTypes": "cards,actions"}
    assert search(fake_trello, **both)["actions"] == []
    found = search(fake_trello, **both, partial=True)
    assert [a["id"] for a in found["actions"]] == ["x1"]
    assert [c["id"] for c in found["cards"]] == [ids["src"]]
    whole = search(fake_trello, query="comment:deploy", modelTypes="actions")
    assert [a["id"] for a in whole["actions"]] == ["x1"]
    assert "cards" not in whole
    phrase = search(fake_trello, query='comment:"second comment"', card_fields="name")
    assert phrase["cards"] == [{"id": ids["src"], "name": "Source"}]
    assert search(fake_trello, query="comment:deploy", idBoards="other")["cards"] == []


def test_search_without_operator_matches_card_text(fake_trello: FakeTrello) -> None:
    ids = seed(fake_trello)
    assert [c["id"] for c in search(fake_trello, query="body")["cards"]] == [ids["src"]]
    assert search(fake_trello, query="absent")["cards"] == []
    with pytest.raises(TrelloctlError) as info:
        client(fake_trello).get("/search", {"query": " "})
    assert info.value.details["status"] == 400


def test_a_route_with_the_same_shape_replaces_the_earlier_one(fake_trello: FakeTrello) -> None:
    seed(fake_trello)
    fake_trello.router.route("PUT", "/cards/{card_id}")(lambda req: {"id": "overridden"})
    assert client(fake_trello).put("/cards/anything", {"name": "x"}) == {"id": "overridden"}

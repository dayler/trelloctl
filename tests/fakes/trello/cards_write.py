"""Fake Trello routes that write cards: ``POST /cards`` (create and copy) and ``PUT /cards/{id}``.

Parameters arrive in the query string, as the client sends them. State conventions are those of
``cards_read``; a created card also carries ``idLabels``, ``idMembers``, ``labels`` and
``badges``. Ids of cards created here start with a letter so they are never all digits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.fakes.trello.cards_read import missing
from tests.fakes.trello.core import Request, Response, Router, State, text_response

KEEP_ALL = ("attachments", "checklists", "comments", "due", "labels", "members", "stickers")
POS_STEP = 16384.0


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def card_id(state: State) -> str:
    return "b" + state.new_id()[1:]


def find_card(state: State, ident: str) -> dict[str, Any] | None:
    for card in state.cards.values():
        if ident in (card["id"], card.get("shortLink")):
            return card
    return None


def csv(value: str | None) -> list[str]:
    return [item for item in (value or "").split(",") if item]


def position(state: State, list_id: str, raw: str | None, exclude: str | None = None) -> float:
    """Numeric position for ``top``, ``bottom`` or a number among the open cards of the list."""
    siblings = [
        float(c.get("pos", 0))
        for c in state.cards.values()
        if c.get("idList") == list_id and not c.get("closed") and c["id"] != exclude
    ]
    if raw in (None, "", "bottom"):
        return max(siblings) + POS_STEP if siblings else POS_STEP
    if raw == "top":
        return min(siblings) / 2 if siblings and min(siblings) > 0 else POS_STEP / 2
    return float(raw)


def badges(state: State, card: dict[str, Any]) -> dict[str, int]:
    cid = card["id"]
    items = [
        i for c in state.checklists.values() if c.get("idCard") == cid for i in c["checkItems"]
    ]
    return {
        "comments": sum(
            1
            for a in state.actions.values()
            if a.get("type") == "commentCard" and a["data"]["card"]["id"] == cid
        ),
        "attachments": sum(1 for a in state.attachments.values() if a.get("idCard") == cid),
        "checkItems": len(items),
        "checkItemsChecked": sum(1 for i in items if i.get("state") == "complete"),
    }


def set_labels(state: State, card: dict[str, Any], ids: list[str]) -> None:
    card["idLabels"] = ids
    card["labels"] = [
        {k: v for k, v in state.labels[i].items() if not k.startswith("_")}
        for i in ids
        if i in state.labels
    ]


def _slug(name: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in name.lower()).split())


def _new_card(state: State, list_row: dict[str, Any], name: str) -> dict[str, Any]:
    cid = card_id(state)
    board_id = list_row["idBoard"]
    short = "s" + cid[-7:]
    id_short = 1 + max(
        (int(c.get("idShort", 0)) for c in state.cards.values() if c.get("idBoard") == board_id),
        default=0,
    )
    card: dict[str, Any] = {
        "id": cid,
        "idBoard": board_id,
        "idList": list_row["id"],
        "name": name,
        "desc": "",
        "closed": False,
        "idShort": id_short,
        "shortLink": short,
        "url": f"https://trello.com/c/{short}/{id_short}-{_slug(name)}",
        "dateLastActivity": now_iso(),
        "due": None,
        "dueComplete": False,
        "idLabels": [],
        "idMembers": [],
        "labels": [],
        "pos": POS_STEP,
    }
    state.cards[cid] = card
    return card


def _copy_children(
    state: State, source: dict[str, Any], card: dict[str, Any], keep: set[str]
) -> None:
    sid, cid = source["id"], card["id"]
    if "labels" in keep:
        set_labels(state, card, list(source.get("idLabels", [])))
    if "members" in keep:
        card["idMembers"] = list(source.get("idMembers", []))
    if "due" in keep:
        card["due"] = source.get("due")
        card["dueComplete"] = source.get("dueComplete", False)
    if "checklists" in keep:
        for checklist in [c for c in state.checklists.values() if c.get("idCard") == sid]:
            new_id = state.new_id()
            state.checklists[new_id] = {
                **checklist,
                "id": new_id,
                "idCard": cid,
                "checkItems": [{**i, "id": state.new_id()} for i in checklist["checkItems"]],
            }
    if "attachments" in keep:
        for attachment in [a for a in state.attachments.values() if a.get("idCard") == sid]:
            new_id = state.new_id()
            state.attachments[new_id] = {**attachment, "id": new_id, "idCard": cid}
    if "comments" in keep:
        for action in [
            a
            for a in state.actions.values()
            if a.get("type") == "commentCard" and a["data"]["card"]["id"] == sid
        ]:
            new_id = state.new_id()
            data = {**action["data"], "card": {**action["data"]["card"], "id": cid}}
            state.actions[new_id] = {**action, "id": new_id, "data": data}


def _keep_set(raw: str | None) -> set[str]:
    if raw in (None, "", "all"):
        return set(KEEP_ALL)
    if raw == "none":
        return set()
    return set(csv(raw))


def public(card: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in card.items() if not k.startswith("_")}


def create_card(req: Request) -> Response | dict[str, Any]:
    state = req.state
    list_id = req.param("idList")
    if not list_id:
        return text_response("invalid value for idList", 400)
    list_row = state.lists.get(list_id)
    if list_row is None:
        return missing(list_id)
    source_ref = req.param("idCardSource")
    source = None
    if source_ref:
        source = find_card(state, source_ref)
        if source is None:
            return missing(source_ref)
    name = req.param("name") or (source["name"] if source else "")
    if not name:
        return text_response("invalid value for name", 400)
    card = _new_card(state, list_row, name)
    card["pos"] = position(state, list_id, req.param("pos"), card["id"])
    if source is not None:
        card["desc"] = source.get("desc", "")
        _copy_children(state, source, card, _keep_set(req.param("keepFromSource")))
    if req.param("desc") is not None:
        card["desc"] = req.param("desc")
    if req.param("due"):
        card["due"] = req.param("due")
    if req.param("idLabels") is not None:
        set_labels(state, card, csv(req.param("idLabels")))
    if req.param("idMembers") is not None:
        card["idMembers"] = csv(req.param("idMembers"))
    card["badges"] = badges(state, card)
    return public(card)


def move_to_board(
    state: State, card: dict[str, Any], board_id: str, list_id: str | None
) -> Response | None:
    """Move a card to another board: the list defaults to the first open one.

    Like Trello, the card gets the next ``idShort`` of the new board and keeps only the labels
    that belong to it.
    """
    if board_id not in state.boards:
        return missing(board_id)
    if list_id is None:
        lists = sorted(
            (x for x in state.lists.values() if x["idBoard"] == board_id and not x.get("closed")),
            key=lambda x: (x.get("pos", 0), x["id"]),
        )
        if not lists:
            return text_response("invalid value for idList", 400)
        list_id = lists[0]["id"]
    list_row = state.lists.get(list_id)
    if list_row is None:
        return missing(list_id)
    if list_row["idBoard"] != board_id:
        return text_response("invalid value for idList", 400)
    if card.get("idBoard") != board_id:
        card["idShort"] = 1 + max(
            (
                int(c.get("idShort", 0))
                for c in state.cards.values()
                if c.get("idBoard") == board_id
            ),
            default=0,
        )
        set_labels(
            state,
            card,
            [
                i
                for i in card.get("idLabels", [])
                if state.labels.get(i, {}).get("idBoard") == board_id
            ],
        )
    card["idBoard"] = board_id
    card["idList"] = list_id
    return None


def update_card(req: Request) -> Response | dict[str, Any]:
    state = req.state
    ident = req.params["card_id"]
    card = find_card(state, ident)
    if card is None:
        return missing(ident)
    for key in ("name", "desc"):
        if req.param(key) is not None:
            card[key] = req.param(key)
    if req.param("due") is not None:
        card["due"] = None if req.param("due") in ("", "null") else req.param("due")
    for key in ("dueComplete", "closed"):
        if req.param(key) is not None:
            card[key] = req.param(key) == "true"
    new_board = req.param("idBoard")
    new_list = req.param("idList")
    if new_board is not None:
        moved = move_to_board(state, card, new_board, new_list)
        if moved is not None:
            return moved
    elif new_list is not None:
        list_row = state.lists.get(new_list)
        if list_row is None:
            return missing(new_list)
        card["idList"] = new_list
        card["idBoard"] = list_row["idBoard"]
    if req.param("idLabels") is not None:
        set_labels(state, card, csv(req.param("idLabels")))
    if req.param("idMembers") is not None:
        card["idMembers"] = csv(req.param("idMembers"))
    if req.param("pos") is not None:
        card["pos"] = position(state, card["idList"], req.param("pos"), card["id"])
    card["dateLastActivity"] = now_iso()
    card["badges"] = badges(state, card)
    return public(card)


def register(router: Router) -> None:
    router.route("POST", "/cards")(create_card)
    router.route("PUT", "/cards/{card_id}")(update_card)

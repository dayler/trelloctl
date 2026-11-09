"""Fake Trello routes for lists: create, update, archive and the bulk card operations.

State conventions: ``state.lists[id]`` holds ``{"id", "idBoard", "name", "pos", "closed"}``.
``pos`` accepts a number, ``top`` or ``bottom`` (placed relative to the open lists of the
board, as Trello does).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.fakes.trello.boards import find_board, public
from tests.fakes.trello.core import Request, Response, Router, State, text_response

_ID_CHARS = frozenset("0123456789abcdefABCDEF")


def missing(ident: str) -> Response:
    """404 for a well-formed id, 400 ``invalid id`` for anything else (as Trello does)."""
    if len(ident) == 24 and set(ident) <= _ID_CHARS:
        return text_response("The requested resource was not found.", 404)
    return text_response("invalid id", 400)


def new_object_id(state: State) -> str:
    """A 24-hex id that is never all digits."""
    return "a" + state.new_id()[1:]


def truthy(value: str | None) -> bool:
    return (value or "").lower() == "true"


def resolve_pos(
    state: State,
    board_id: str,
    raw: str | None,
    current: str | None = None,
    default: str = "bottom",
) -> float:
    """Numeric position for a ``pos`` parameter among the open lists of a board."""
    others = [
        float(item["pos"])
        for item in state.lists.values()
        if item["idBoard"] == board_id and not item.get("closed") and item["id"] != current
    ]
    value = raw or default
    if value == "top":
        return (min(others) - 1) if others else 1.0
    if value == "bottom":
        return (max(others) + 1) if others else 1.0
    return float(value)


def _touch(card: dict[str, Any]) -> None:
    card["dateLastActivity"] = datetime.now(UTC).isoformat(timespec="milliseconds")


def register(router: Router) -> None:
    @router.route("POST", "/lists")
    def create(req: Request) -> Response | dict[str, Any]:
        board = find_board(req.state, req.param("idBoard") or "")
        name = req.param("name")
        if board is None or not name:
            return text_response("invalid value for idBoard or name", 400)
        ident = new_object_id(req.state)
        item = {
            "id": ident,
            "idBoard": board["id"],
            "name": name,
            "pos": resolve_pos(req.state, board["id"], req.param("pos")),
            "closed": False,
        }
        req.state.lists[ident] = item
        return public(item)

    @router.route("GET", "/lists/{list_id}")
    def get_list(req: Request) -> Response | dict[str, Any]:
        item = req.state.lists.get(req.params["list_id"])
        return missing(req.params["list_id"]) if item is None else public(item)

    @router.route("PUT", "/lists/{list_id}")
    def update(req: Request) -> Response | dict[str, Any]:
        item = req.state.lists.get(req.params["list_id"])
        if item is None:
            return missing(req.params["list_id"])
        if req.param("name") is not None:
            item["name"] = req.param("name")
        if req.param("closed") is not None:
            item["closed"] = truthy(req.param("closed"))
        if req.param("pos") is not None:
            item["pos"] = resolve_pos(
                req.state, item["idBoard"], req.param("pos"), current=item["id"]
            )
        return public(item)

    @router.route("PUT", "/lists/{list_id}/closed")
    def closed(req: Request) -> Response | dict[str, Any]:
        item = req.state.lists.get(req.params["list_id"])
        if item is None:
            return missing(req.params["list_id"])
        item["closed"] = truthy(req.param("value"))
        return public(item)

    @router.route("POST", "/lists/{list_id}/moveAllCards")
    def move_all(req: Request) -> Response | list[dict[str, Any]]:
        source = req.state.lists.get(req.params["list_id"])
        target = req.state.lists.get(req.param("idList") or "")
        if source is None:
            return missing(req.params["list_id"])
        if target is None or req.param("idBoard") != target["idBoard"]:
            return text_response("invalid value for idList", 400)
        moved = [
            card
            for card in req.state.cards.values()
            if card.get("idList") == source["id"] and not card.get("closed")
        ]
        for card in moved:
            card["idList"] = target["id"]
            card["idBoard"] = target["idBoard"]
            _touch(card)
        return [{"id": card["id"]} for card in moved]

    @router.route("POST", "/lists/{list_id}/archiveAllCards")
    def archive_all(req: Request) -> Response | list[dict[str, Any]]:
        source = req.state.lists.get(req.params["list_id"])
        if source is None:
            return missing(req.params["list_id"])
        archived = [
            card
            for card in req.state.cards.values()
            if card.get("idList") == source["id"] and not card.get("closed")
        ]
        for card in archived:
            card["closed"] = True
            _touch(card)
        return [{"id": card["id"]} for card in archived]

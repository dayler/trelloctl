"""Fake Trello routes for board labels: create, update and delete.

State conventions: ``state.labels[id]`` holds ``{"id", "idBoard", "name", "color"}`` and cards
reference labels through ``idLabels``. Deleting a label removes it from every card.
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.boards import find_board, public
from tests.fakes.trello.core import Request, Response, Router, text_response
from tests.fakes.trello.lists import missing, new_object_id

_BASE = ("green", "yellow", "orange", "red", "purple", "blue", "sky", "lime", "pink", "black")
COLORS = frozenset(
    [*_BASE, *(f"{c}_{s}" for c in _BASE for s in ("light", "dark")), "null"],
)


def _color(raw: str | None) -> tuple[bool, str | None]:
    """Whether the value is valid, and the stored color (``null`` means no color)."""
    if raw is None or raw == "":
        return True, None
    if raw not in COLORS:
        return False, None
    return True, None if raw == "null" else raw


def register(router: Router) -> None:
    @router.route("POST", "/labels")
    def create(req: Request) -> Response | dict[str, Any]:
        board = find_board(req.state, req.param("idBoard") or "")
        valid, color = _color(req.param("color"))
        if board is None or not valid:
            return text_response("invalid value for idBoard or color", 400)
        ident = new_object_id(req.state)
        item = {
            "id": ident,
            "idBoard": board["id"],
            "name": req.param("name") or "",
            "color": color,
        }
        req.state.labels[ident] = item
        return public(item)

    @router.route("GET", "/labels/{label_id}")
    def get_label(req: Request) -> Response | dict[str, Any]:
        item = req.state.labels.get(req.params["label_id"])
        return missing(req.params["label_id"]) if item is None else public(item)

    @router.route("PUT", "/labels/{label_id}")
    def update(req: Request) -> Response | dict[str, Any]:
        item = req.state.labels.get(req.params["label_id"])
        if item is None:
            return missing(req.params["label_id"])
        if req.param("color") is not None:
            valid, color = _color(req.param("color"))
            if not valid:
                return text_response("invalid value for color", 400)
            item["color"] = color
        if req.param("name") is not None:
            item["name"] = req.param("name")
        return public(item)

    @router.route("DELETE", "/labels/{label_id}")
    def delete(req: Request) -> Response | dict[str, Any]:
        ident = req.params["label_id"]
        if ident not in req.state.labels:
            return missing(ident)
        del req.state.labels[ident]
        for card in req.state.cards.values():
            card["idLabels"] = [x for x in card.get("idLabels", []) if x != ident]
        return {}

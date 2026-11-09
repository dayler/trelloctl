"""Fake Trello routes that write checklists and their items.

``state.checklists[id]`` holds ``{"id", "idCard", "name", "pos", "checkItems": [{"id", "name",
"state", "pos", "idChecklist"}]}``. Items are ordered by ``pos``; ``top``, ``bottom`` and
numbers are accepted for ``pos``. Ids created here start with a letter.
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.cards_read import missing, not_found
from tests.fakes.trello.cards_write import POS_STEP, find_card
from tests.fakes.trello.core import Request, Response, Router, State, text_response


def _new_id(state: State) -> str:
    return "a" + state.new_id()[1:]


def place(siblings: list[dict[str, Any]], raw: str | None) -> float:
    positions = [float(s.get("pos", 0)) for s in siblings]
    if raw in (None, "", "bottom"):
        return max(positions) + POS_STEP if positions else POS_STEP
    if raw == "top":
        return min(positions) / 2 if positions and min(positions) > 0 else POS_STEP / 2
    return float(raw)


def _checklist(req: Request) -> dict[str, Any] | None:
    return req.state.checklists.get(req.params["checklist_id"])


def _item(checklist: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    return next((i for i in checklist["checkItems"] if i["id"] == item_id), None)


def _sorted(checklist: dict[str, Any]) -> dict[str, Any]:
    checklist["checkItems"].sort(key=lambda i: (float(i["pos"]), i["id"]))
    return checklist


def register(router: Router) -> None:
    @router.route("POST", "/cards/{card_id}/checklists")
    def create(req: Request) -> Response | dict[str, Any]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        name = req.param("name")
        if not name:
            return text_response("invalid value for name", 400)
        siblings = [c for c in req.state.checklists.values() if c["idCard"] == card["id"]]
        ident = _new_id(req.state)
        req.state.checklists[ident] = {
            "id": ident,
            "idCard": card["id"],
            "idBoard": card["idBoard"],
            "name": name,
            "pos": place(siblings, req.param("pos")),
            "checkItems": [],
        }
        return req.state.checklists[ident]

    @router.route("GET", "/checklists/{checklist_id}")
    def read(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        return not_found() if checklist is None else _sorted(checklist)

    @router.route("PUT", "/checklists/{checklist_id}")
    def rename(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        if checklist is None:
            return not_found()
        name = req.param("name")
        if name is not None:
            if not name:
                return text_response("invalid value for name", 400)
            checklist["name"] = name
        if req.param("pos") is not None:
            others = [
                c
                for c in req.state.checklists.values()
                if c["idCard"] == checklist["idCard"] and c is not checklist
            ]
            checklist["pos"] = place(others, req.param("pos"))
        return checklist

    @router.route("DELETE", "/checklists/{checklist_id}")
    def remove(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        if checklist is None:
            return not_found()
        del req.state.checklists[checklist["id"]]
        return {}

    @router.route("POST", "/checklists/{checklist_id}/checkItems")
    def add_item(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        if checklist is None:
            return not_found()
        name = req.param("name")
        if not name:
            return text_response("invalid value for name", 400)
        item = {
            "id": _new_id(req.state),
            "idChecklist": checklist["id"],
            "name": name,
            "state": "complete" if req.param("checked") == "true" else "incomplete",
            "pos": place(checklist["checkItems"], req.param("pos")),
        }
        checklist["checkItems"].append(item)
        _sorted(checklist)
        return item

    @router.route("GET", "/checklists/{checklist_id}/checkItems/{item_id}")
    def read_item(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        item = None if checklist is None else _item(checklist, req.params["item_id"])
        return not_found() if item is None else item

    @router.route("DELETE", "/checklists/{checklist_id}/checkItems/{item_id}")
    def remove_item(req: Request) -> Response | dict[str, Any]:
        checklist = _checklist(req)
        item = None if checklist is None else _item(checklist, req.params["item_id"])
        if checklist is None or item is None:
            return not_found()
        checklist["checkItems"].remove(item)
        return {}

    @router.route("PUT", "/cards/{card_id}/checkItem/{item_id}")
    def update_item(req: Request) -> Response | dict[str, Any]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        for checklist in req.state.checklists.values():
            item = _item(checklist, req.params["item_id"])
            if item is None or checklist["idCard"] != card["id"]:
                continue
            state = req.param("state")
            if state is not None:
                if state not in ("complete", "incomplete"):
                    return text_response("invalid value for state", 400)
                item["state"] = state
            if req.param("name"):
                item["name"] = req.param("name")
            if req.param("pos") is not None:
                others = [i for i in checklist["checkItems"] if i is not item]
                item["pos"] = place(others, req.param("pos"))
                _sorted(checklist)
            return item
        return not_found()

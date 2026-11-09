"""Fake Trello routes for boards: the user's boards and ``GET /boards/{id}`` with nested data.

State conventions (seeded by the tests):

- ``state.boards[id]``: ``{"id", "name", "shortLink", "url", "closed", ...}``. Keys starting
  with ``_`` are internal and never serialized; ``_members`` lists the member ids of the board.
- ``state.lists[id]``: ``{"id", "idBoard", "name", "pos", "closed"}``.
- ``state.labels[id]``: ``{"id", "idBoard", "name", "color"}``.
- ``state.members[id]``: ``{"id", "username", "fullName"}`` (the key ``"me"`` is the user).
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.core import Request, Response, Router, State, text_response


def public(item: dict[str, Any]) -> dict[str, Any]:
    """The serializable view of a stored object (internal ``_`` keys removed)."""
    return {k: v for k, v in item.items() if not k.startswith("_")}


def select(item: dict[str, Any], fields: str | None) -> dict[str, Any]:
    """Apply a Trello ``fields`` parameter (comma-separated names or ``all``)."""
    data = public(item)
    if not fields or fields == "all":
        return data
    wanted = {name.strip() for name in fields.split(",")}
    return {k: v for k, v in data.items() if k in wanted or k == "id"}


def find_board(state: State, ident: str) -> dict[str, Any] | None:
    for board in state.boards.values():
        if ident in (board.get("id"), board.get("shortLink")):
            return board
    return None


def _wanted(closed: bool, mode: str) -> bool:
    if mode == "all":
        return True
    return closed if mode == "closed" else not closed


def _nested(req: Request, board: dict[str, Any]) -> dict[str, Any]:
    state = req.state
    out = select(board, req.param("fields"))
    mode = req.param("lists", "none") or "none"
    if mode != "none":
        out["lists"] = [
            select(item, req.param("list_fields"))
            for item in sorted(state.lists.values(), key=lambda x: (x.get("pos", 0), x["id"]))
            if item.get("idBoard") == board["id"] and _wanted(bool(item.get("closed")), mode)
        ]
    if req.param("labels") == "all":
        out["labels"] = [
            select(item, req.param("label_fields"))
            for item in state.labels.values()
            if item.get("idBoard") == board["id"]
        ]
    if req.param("members") == "all":
        out["members"] = [
            select(state.members[ident], req.param("member_fields"))
            for ident in board.get("_members", [])
            if ident in state.members
        ]
    return out


def register(router: Router) -> None:
    @router.route("GET", "/members/me/boards")
    def my_boards(req: Request) -> list[dict[str, Any]]:
        mode = req.param("filter", "all") or "all"
        return [
            select(board, req.param("fields"))
            for board in req.state.boards.values()
            if _wanted(bool(board.get("closed")), mode)
        ]

    @router.route("GET", "/boards/{board_id}")
    def get_board(req: Request) -> Response | dict[str, Any]:
        board = find_board(req.state, req.params["board_id"])
        if board is None:
            return text_response("The requested resource was not found.", 404)
        return _nested(req, board)

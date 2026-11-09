"""Fake Trello routes for boards (only ``GET /boards/{id}`` so far)."""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.core import Request, Response, Router, text_response


def _select(board: dict[str, Any], fields: str | None) -> dict[str, Any]:
    if not fields or fields == "all":
        return dict(board)
    wanted = {name.strip() for name in fields.split(",")}
    return {k: v for k, v in board.items() if k in wanted or k == "id"}


def register(router: Router) -> None:
    @router.route("GET", "/boards/{board_id}")
    def get_board(req: Request) -> Response | dict[str, Any]:
        ident = req.params["board_id"]
        for board in req.state.boards.values():
            if ident in (board.get("id"), board.get("shortLink")):
                return _select(board, req.param("fields"))
        return text_response("The requested resource was not found.", 404)

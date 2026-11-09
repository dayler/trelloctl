"""Fake Trello route that reads the comments of a card: ``GET /cards/{id}/actions``.

State conventions: ``state.actions[id]`` holds ``{"id", "type": "commentCard",
"idMemberCreator", "date", "data": {"text", "card": {"id": <card id>}}}``. Actions are served
newest first (by id) and paginated with ``before`` (an action id) and ``limit``.
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.cards_read import missing
from tests.fakes.trello.cards_write import csv, find_card
from tests.fakes.trello.core import Request, Response, Router

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000


def card_actions(req: Request) -> Response | list[dict[str, Any]]:
    ident = req.params["card_id"]
    card = find_card(req.state, ident)
    if card is None:
        return missing(ident)
    types = csv(req.param("filter"))
    actions = [
        a
        for a in req.state.actions.values()
        if a["data"].get("card", {}).get("id") == card["id"] and (not types or a["type"] in types)
    ]
    actions.sort(key=lambda a: a["id"], reverse=True)
    before = req.param("before")
    if before:
        actions = [a for a in actions if a["id"] < before]
    limit = min(int(req.param("limit") or DEFAULT_LIMIT), MAX_LIMIT)
    return actions[:limit]


def register(router: Router) -> None:
    router.route("GET", "/cards/{card_id}/actions")(card_actions)

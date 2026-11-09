"""Fake Trello routes that read cards: by board, by list and by id or shortLink.

State conventions: ``state.cards[id]`` holds ``{"id", "idBoard", "idList", "name", "closed",
"shortLink", "idShort", "dateLastActivity", ...}`` (ids sort in creation order);
``state.checklists[id]`` holds ``{"id", "idCard", "name", "pos", "checkItems": [...]}`` and
``state.attachments[id]`` holds ``{"id", "idCard", ...}``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from tests.fakes.trello.boards import find_board, public, select
from tests.fakes.trello.core import Request, Response, Router, State, text_response

_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_SHORT = re.compile(r"^[A-Za-z0-9]{8}$")
DEFAULT_LIMIT = 1000


def not_found() -> Response:
    return text_response("The requested resource was not found.", 404)


def missing(ident: str) -> Response:
    """404 for a well-formed id, 400 ``invalid id`` for anything else (as Trello does)."""
    if _ID.match(ident) or _SHORT.match(ident):
        return not_found()
    return text_response("invalid id", 400)


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def card_view(state: State, card: dict[str, Any], req: Request) -> dict[str, Any]:
    """A card as serialized by Trello, with the optional nested data of the request."""
    out = select(card, req.param("fields"))
    if (req.param("attachments") or "false") in ("true", "cover"):
        out["attachments"] = [
            {k: v for k, v in item.items() if k != "content"}
            for item in state.attachments.values()
            if item.get("idCard") == card["id"]
        ]
    if req.param("checklists") == "all":
        out["checklists"] = [
            public(item)
            for item in sorted(state.checklists.values(), key=lambda x: (x.get("pos", 0), x["id"]))
            if item.get("idCard") == card["id"]
        ]
    return out


def _changed_since(card: dict[str, Any], since: datetime) -> bool:
    changed = _moment(card.get("dateLastActivity"))
    return changed is None or changed > since


def _page(req: Request, cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest first (by id); ``before`` is a card id, ``modifiedSince`` an ISO date."""
    selected = sorted(cards, key=lambda c: c["id"], reverse=True)
    since = _moment(req.param("modifiedSince"))
    if since is not None:
        selected = [c for c in selected if _changed_since(c, since)]
    before = req.param("before")
    if before:
        selected = [c for c in selected if c["id"] < before]
    return selected[: int(req.param("limit") or DEFAULT_LIMIT)]


def register(router: Router) -> None:
    @router.route("GET", "/boards/{board_id}/cards/{mode}")
    def board_cards(req: Request) -> Response | list[dict[str, Any]]:
        board = find_board(req.state, req.params["board_id"])
        mode = req.params["mode"]
        if board is None or mode not in ("open", "closed", "all", "visible"):
            return not_found()
        cards = [
            c
            for c in req.state.cards.values()
            if c.get("idBoard") == board["id"]
            and (mode == "all" or (mode == "closed") == bool(c.get("closed")))
        ]
        return [card_view(req.state, c, req) for c in _page(req, cards)]

    @router.route("GET", "/lists/{list_id}/cards")
    def list_cards(req: Request) -> Response | list[dict[str, Any]]:
        if req.params["list_id"] not in req.state.lists:
            return missing(req.params["list_id"])
        cards = [
            c
            for c in req.state.cards.values()
            if c.get("idList") == req.params["list_id"] and not c.get("closed")
        ]
        return [card_view(req.state, c, req) for c in _page(req, cards)]

    @router.route("GET", "/cards/{card_id}")
    def get_card(req: Request) -> Response | dict[str, Any]:
        ident = req.params["card_id"]
        for card in req.state.cards.values():
            if ident in (card["id"], card.get("shortLink")):
                return card_view(req.state, card, req)
        return missing(ident)

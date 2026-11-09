"""Fake Trello routes that write comments: add, edit and delete.

``POST /cards/{id}/actions/comments`` creates a ``commentCard`` action authored by the member
of the configured token (``state.members["me"]``, or the default member); edit and delete work
only on comments of that member and answer 401 for any other, as Trello does. Ids of created
actions start with a letter and sort in creation order.
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.cards_read import missing
from tests.fakes.trello.cards_write import find_card, now_iso
from tests.fakes.trello.core import Request, Response, Router, State, text_response
from tests.fakes.trello.members import DEFAULT_ME

DENIED = "unauthorized comment permission requested"


def token_member(state: State) -> dict[str, Any]:
    return dict(state.members.get("me", DEFAULT_ME))


def _comment_of(req: Request) -> tuple[dict[str, Any] | None, Response | None]:
    ident = req.params["card_id"]
    card = find_card(req.state, ident)
    if card is None:
        return None, missing(ident)
    action = req.state.actions.get(req.params["action_id"])
    if (
        action is None
        or action.get("type") != "commentCard"
        or action["data"].get("card", {}).get("id") != card["id"]
    ):
        return None, text_response("The requested resource was not found.", 404)
    if action.get("idMemberCreator") != token_member(req.state)["id"]:
        return None, text_response(DENIED, 401)
    return action, None


def register(router: Router) -> None:
    @router.route("POST", "/cards/{card_id}/actions/comments")
    def add_comment(req: Request) -> Response | dict[str, Any]:
        ident = req.params["card_id"]
        card = find_card(req.state, ident)
        if card is None:
            return missing(ident)
        text = req.param("text")
        if not text:
            return text_response("invalid value for text", 400)
        me = token_member(req.state)
        action_id = "a" + req.state.new_id()[1:]
        action: dict[str, Any] = {
            "id": action_id,
            "type": "commentCard",
            "idMemberCreator": me["id"],
            "memberCreator": {k: me.get(k) for k in ("id", "username", "fullName")},
            "date": now_iso(),
            "data": {"text": text, "card": {"id": card["id"]}},
        }
        req.state.actions[action_id] = action
        return action

    @router.route("PUT", "/cards/{card_id}/actions/{action_id}/comments")
    def edit_comment(req: Request) -> Response | dict[str, Any]:
        action, error = _comment_of(req)
        if action is None:
            assert error is not None
            return error
        text = req.param("text")
        if not text:
            return text_response("invalid value for text", 400)
        action["data"]["text"] = text
        return action

    @router.route("DELETE", "/cards/{card_id}/actions/{action_id}/comments")
    def remove_comment(req: Request) -> Response | dict[str, Any]:
        action, error = _comment_of(req)
        if action is None:
            assert error is not None
            return error
        del req.state.actions[action["id"]]
        return {}

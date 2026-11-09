"""Fake Trello routes for the labels and members of a card.

``POST /cards/{id}/idLabels`` and ``POST /cards/{id}/idMembers`` take the id in ``value``;
adding one that is already present is a 400, as in Trello. ``DELETE`` removes one by id and
answers 404 when it is not on the card.
"""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.cards_read import missing, not_found
from tests.fakes.trello.cards_write import find_card, set_labels
from tests.fakes.trello.core import Request, Response, Router, text_response


def register(router: Router) -> None:
    @router.route("POST", "/cards/{card_id}/idLabels")
    def add_label(req: Request) -> Response | list[str]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        value = req.param("value") or ""
        if value not in req.state.labels:
            return text_response("invalid value for value", 400)
        if value in card["idLabels"]:
            return text_response("that label is already on the card", 400)
        set_labels(req.state, card, [*card["idLabels"], value])
        return list(card["idLabels"])

    @router.route("DELETE", "/cards/{card_id}/idLabels/{label_id}")
    def remove_label(req: Request) -> Response | dict[str, Any]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        label_id = req.params["label_id"]
        if label_id not in card["idLabels"]:
            return not_found()
        set_labels(req.state, card, [i for i in card["idLabels"] if i != label_id])
        return {}

    @router.route("POST", "/cards/{card_id}/idMembers")
    def add_member(req: Request) -> Response | list[dict[str, Any]]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        value = req.param("value") or ""
        if value not in req.state.members:
            return text_response("invalid value for value", 400)
        if value in card["idMembers"]:
            return text_response("member is already on the card", 400)
        card["idMembers"] = [*card["idMembers"], value]
        return [req.state.members[i] for i in card["idMembers"] if i in req.state.members]

    @router.route("DELETE", "/cards/{card_id}/idMembers/{member_id}")
    def remove_member(req: Request) -> Response | list[dict[str, Any]]:
        card = find_card(req.state, req.params["card_id"])
        if card is None:
            return missing(req.params["card_id"])
        member_id = req.params["member_id"]
        if member_id not in card["idMembers"]:
            return not_found()
        card["idMembers"] = [i for i in card["idMembers"] if i != member_id]
        return [req.state.members[i] for i in card["idMembers"] if i in req.state.members]

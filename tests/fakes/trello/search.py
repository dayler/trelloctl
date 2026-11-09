"""Fake Trello route for ``GET /search`` (comment search used by ``card search --in comments``).

Understands the ``comment:`` operator (a word, or a quoted phrase), ``idBoards``,
``modelTypes`` (``cards`` and ``actions``) and ``partial``. With ``partial=true`` a term matches
inside words; otherwise it must match a whole word. Terms without an operator match the
name and description of cards. All terms must match.
"""

from __future__ import annotations

import re
from typing import Any

from tests.fakes.trello.cards_write import csv
from tests.fakes.trello.core import Request, Response, Router, text_response

_TERM = re.compile(r'(?:(comment):)?(?:"([^"]*)"|(\S+))')


def parse_terms(query: str) -> list[tuple[str | None, str]]:
    """The terms of a query as ``(operator, text)``; the operator is ``comment`` or ``None``."""
    return [
        (found.group(1), found.group(2) if found.group(2) is not None else found.group(3))
        for found in _TERM.finditer(query)
    ]


def _found(text: str, term: str, partial: bool) -> bool:
    lowered, wanted = text.lower(), term.lower()
    if partial:
        return wanted in lowered
    return re.search(rf"\b{re.escape(wanted)}\b", lowered) is not None


def search(req: Request) -> Response | dict[str, Any]:
    state = req.state
    query = (req.param("query") or "").strip()
    if not query:
        return text_response("invalid query", 400)
    partial = req.param("partial") == "true"
    boards = csv(req.param("idBoards"))
    types = csv(req.param("modelTypes")) or ["cards"]
    terms = parse_terms(query)
    comment_terms = [t for op, t in terms if op == "comment"]
    plain_terms = [t for op, t in terms if op is None]
    actions = []
    for action in state.actions.values():
        card = state.cards.get(action["data"].get("card", {}).get("id", ""))
        if action.get("type") != "commentCard" or card is None:
            continue
        if boards and card["idBoard"] not in boards:
            continue
        if comment_terms and all(_found(action["data"]["text"], t, partial) for t in comment_terms):
            actions.append(action)
    card_ids = {a["data"]["card"]["id"] for a in actions}
    if not comment_terms:
        for card in state.cards.values():
            text = f"{card.get('name', '')} {card.get('desc', '')}"
            if (not boards or card["idBoard"] in boards) and all(
                _found(text, t, partial) for t in plain_terms
            ):
                card_ids.add(card["id"])
    out: dict[str, Any] = {"options": {"terms": [t for _, t in terms], "partial": partial}}
    if "actions" in types:
        out["actions"] = sorted(actions, key=lambda a: a["id"], reverse=True)
    if "cards" in types:
        fields = set(csv(req.param("card_fields")))
        out["cards"] = [
            {k: v for k, v in state.cards[i].items() if not fields or k in fields or k == "id"}
            for i in sorted(card_ids)
        ]
    return out


def register(router: Router) -> None:
    router.route("GET", "/search")(search)

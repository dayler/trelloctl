"""Fake Trello Inbox: the hidden board of the user, reached through ``/members/me``.

The Inbox is a board of ``type = "inbox"`` with a single list. ``seed_inbox`` creates both in
the state and sets the ``inbox`` field (``idBoard`` and ``idList``) that ``GET
/members/me?fields=inbox`` returns (see ``members``); ``hide_inbox`` removes that field, as
Trello does when the account has no Inbox. Cards, labels and members are seeded with the
helpers of the tests, using the returned ids. Routes for boards, lists and cards are the
generic ones; the move out of the Inbox is ``PUT /cards/{id}`` with ``idBoard`` (``cards_write``).
"""

from __future__ import annotations

from tests.fakes.trello.core import Router, State

INBOX_BOARD = "7a1b2c3d4e5f6a7b8c9d0e3b"
INBOX_SHORT = "inbx0001"


def seed_inbox(
    state: State, *, board_id: str = INBOX_BOARD, list_id: str = "7a1b2c3d4e5f6a7b8c9d0e3c"
) -> tuple[str, str]:
    """Create the Inbox board and its list; returns ``(board_id, list_id)``."""
    state.boards[board_id] = {
        "id": board_id,
        "name": "Inbox",
        "shortLink": INBOX_SHORT,
        "url": f"https://trello.com/b/{INBOX_SHORT}/inbox",
        "closed": False,
        "type": "inbox",
        "_members": [],
    }
    state.lists[list_id] = {
        "id": list_id,
        "idBoard": board_id,
        "name": "Inbox",
        "pos": 1,
        "closed": False,
    }
    state.extra["inbox"] = {"idBoard": board_id, "idList": list_id}
    return board_id, list_id


def hide_inbox(state: State) -> None:
    """The account has no Inbox: ``/members/me`` carries no ``inbox`` field."""
    state.extra.pop("inbox", None)


def register(router: Router) -> None:
    """The Inbox adds no routes of its own."""

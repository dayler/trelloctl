"""Fake Trello route for board activity: ``GET /boards/{id}/actions``.

State conventions: ``state.actions[id]`` holds ``{"id", "type", "date", "idMemberCreator",
"memberCreator", "data", "_board"}``; ``_board`` is the id of the board it belongs to and is
never serialized. Results are newest first; ``before`` and ``since`` accept a date or an
action id; ``limit`` defaults to 50 and is capped at 1000, as in Trello.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from tests.fakes.trello.boards import find_board, public
from tests.fakes.trello.core import Request, Response, Router, State
from tests.fakes.trello.lists import missing

DEFAULT_LIMIT = 50
MAX_LIMIT = 1000
_ID = re.compile(r"^[0-9a-fA-F]{24}$")


def _moment(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _key(action: dict[str, Any]) -> tuple[datetime, str]:
    return _moment(action["date"]), action["id"]


def _bound(state: State, raw: str) -> tuple[datetime, str]:
    """The ordering key of a date or of an action id."""
    if _ID.match(raw) and raw in state.actions:
        return _key(state.actions[raw])
    return _moment(raw), ""


def register(router: Router) -> None:
    @router.route("GET", "/boards/{board_id}/actions")
    def actions(req: Request) -> Response | list[dict[str, Any]]:
        board = find_board(req.state, req.params["board_id"])
        if board is None:
            return missing(req.params["board_id"])
        selected = [a for a in req.state.actions.values() if a.get("_board") == board["id"]]
        types = {t for t in (req.param("filter") or "all").split(",") if t}
        if "all" not in types:
            selected = [a for a in selected if a["type"] in types]
        selected.sort(key=_key, reverse=True)
        since = req.param("since")
        before = req.param("before")
        if since:
            floor = _bound(req.state, since)
            selected = [a for a in selected if _key(a) > floor]
        if before:
            ceiling = _bound(req.state, before)
            selected = [a for a in selected if _key(a) < ceiling]
        limit = min(int(req.param("limit") or DEFAULT_LIMIT), MAX_LIMIT)
        return [public(a) for a in selected[:limit]]

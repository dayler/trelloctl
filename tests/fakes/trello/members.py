"""Fake Trello routes for members (``/members/me`` and member lookup)."""

from __future__ import annotations

from typing import Any

from tests.fakes.trello.core import Request, Response, Router, text_response

DEFAULT_ME: dict[str, Any] = {
    "id": "0000000000000000000000aa",
    "username": "tester",
    "fullName": "Test User",
}


def _select(member: dict[str, Any], fields: str | None) -> dict[str, Any]:
    if not fields or fields == "all":
        return dict(member)
    wanted = {name.strip() for name in fields.split(",")}
    return {k: v for k, v in member.items() if k in wanted or k == "id"}


def _with_inbox(req: Request, member: dict[str, Any]) -> dict[str, Any]:
    """The ``inbox`` field of ``/members/me`` (``{"idBoard", "idList"}``), when one is seeded."""
    inbox = req.state.extra.get("inbox")
    return {**member, "inbox": dict(inbox)} if inbox else member


def register(router: Router) -> None:
    @router.route("GET", "/members/me")
    def get_me(req: Request) -> dict[str, Any]:
        member = _with_inbox(req, req.state.members.get("me", DEFAULT_ME))
        return _select(member, req.param("fields"))

    @router.route("GET", "/members/{ident}")
    def get_member(req: Request) -> Response | dict[str, Any]:
        ident = req.params["ident"]
        for member in req.state.members.values():
            if ident in (member.get("id"), member.get("username")):
                return _select(member, req.param("fields"))
        return text_response("The requested resource was not found.", 404)

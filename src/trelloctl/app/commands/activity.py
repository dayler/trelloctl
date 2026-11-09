"""`board activity`: the actions of the board, newest first, never cached (RF-11)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver
from trelloctl.domain.errors import TrelloctlError
from trelloctl.output import to_utc_iso

Row = dict[str, Any]

DEFAULT_LIMIT = 50
PAGE_SIZE = 1000
FIELDS = ("id", "type", "date", "member", "card", "list", "data")

# Action types of the Trello API accepted by `--type` (AC-11.3).
ACTION_TYPES = frozenset(
    {
        "acceptEnterpriseJoinRequest",
        "addAttachmentToCard",
        "addChecklistToCard",
        "addMemberToBoard",
        "addMemberToCard",
        "addMemberToOrganization",
        "addToOrganizationBoard",
        "commentCard",
        "convertToCardFromCheckItem",
        "copyBoard",
        "copyCard",
        "copyChecklist",
        "copyCommentCard",
        "createBoard",
        "createCard",
        "createChecklist",
        "createLabel",
        "createList",
        "createOrganization",
        "deleteAttachmentFromCard",
        "deleteBoardInvitation",
        "deleteCard",
        "deleteCheckItem",
        "deleteComment",
        "deleteLabel",
        "deleteOrganizationInvitation",
        "disablePlugin",
        "disablePowerUp",
        "emailCard",
        "enablePlugin",
        "enablePowerUp",
        "makeAdminOfBoard",
        "makeNormalMemberOfBoard",
        "makeObserverOfBoard",
        "memberJoinedTrello",
        "moveCardFromBoard",
        "moveCardToBoard",
        "moveListFromBoard",
        "moveListToBoard",
        "removeChecklistFromCard",
        "removeFromOrganizationBoard",
        "removeMemberFromBoard",
        "removeMemberFromCard",
        "removeMemberFromOrganization",
        "unconfirmedBoardInvitation",
        "unconfirmedOrganizationInvitation",
        "updateBoard",
        "updateCard",
        "updateCheckItem",
        "updateCheckItemStateOnCard",
        "updateChecklist",
        "updateComment",
        "updateCustomField",
        "updateCustomFieldItem",
        "updateLabel",
        "updateList",
        "updateMember",
        "updateOrganization",
        "voteOnCard",
    }
)


def _usage(message: str, **details: Any) -> TrelloctlError:
    return TrelloctlError("USAGE", message, details)


def _instant(value: Any, option: str) -> str | None:
    """An ISO-8601 date or datetime as the UTC string sent to Trello."""
    if value is None:
        return None
    try:
        return to_utc_iso(datetime.fromisoformat(str(value).strip()))
    except ValueError:
        raise _usage(f"--{option} expects an ISO-8601 date or datetime.", value=value) from None


def _types(value: Any) -> list[str]:
    types = [str(item) for item in value or []]
    unknown = [t for t in types if t.split(":", 1)[0] not in ACTION_TYPES]
    if unknown:
        raise _usage("Unknown action type.", unknown=unknown)
    return types


def _fields(value: Any) -> list[str] | None:
    if value is None:
        return None
    fields = [str(item) for item in value]
    unknown = [f for f in fields if f not in FIELDS]
    if unknown or not fields:
        raise _usage("Unknown field.", unknown=unknown, valid=list(FIELDS))
    return fields


def _limit(value: Any) -> int | None:
    """The number of actions wanted, or `None` for no limit (`--limit 0`)."""
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _usage("--limit expects an integer greater than or equal to 0.", value=value)
    return value or None


def _named(value: Any) -> Row | None:
    if isinstance(value, dict) and value.get("id"):
        return {"id": value["id"], "name": value.get("name")}
    return None


def _summary(kind: str, data: Mapping[str, Any]) -> Row:
    """The part of `data` that tells what the action did, by action type."""
    summary: Row = {}
    subject = data.get("card") if isinstance(data.get("card"), dict) else data.get("list")
    subject = subject if isinstance(subject, dict) else {}
    if isinstance(data.get("text"), str):
        summary["text"] = data["text"]
    before, after = data.get("listBefore"), data.get("listAfter")
    if isinstance(before, dict) and isinstance(after, dict):
        summary["from_list"] = before.get("name")
        summary["to_list"] = after.get("name")
    old = data.get("old")
    if isinstance(old, dict):
        summary["changes"] = {k: {"from": v, "to": subject.get(k)} for k, v in old.items()}
    if kind.startswith("create") and subject.get("name") is not None:
        summary["name"] = subject["name"]
    if data.get("idMember"):
        summary["member_id"] = data["idMember"]
    return summary


def normalize(action: Mapping[str, Any]) -> Row:
    """An action as `{id, type, date, member, card?, list?, data}` (AC-11.2)."""
    raw = action.get("data")
    data: Mapping[str, Any] = raw if isinstance(raw, dict) else {}
    item: Row = {"id": action.get("id"), "type": action.get("type"), "date": action.get("date")}
    creator = action.get("memberCreator")
    creator = creator if isinstance(creator, dict) else {}
    member_id = action.get("idMemberCreator") or creator.get("id")
    if member_id or creator:
        item["member"] = {"id": member_id, "username": creator.get("username")}
    card = _named(data.get("card"))
    if card:
        item["card"] = card
    target = _named(data.get("list")) or _named(data.get("listAfter"))
    if target:
        item["list"] = target
    item["data"] = _summary(str(action.get("type") or ""), data)
    return item


def _fetch(
    resolver: Resolver, board_id: str, query: Mapping[str, str | None], want: int | None
) -> tuple[list[Row], bool]:
    """Actions newest first, paginating with `before`; returns them and whether more exist."""
    collected: list[Row] = []
    before = query.get("before")
    while True:
        size = PAGE_SIZE if want is None else min(PAGE_SIZE, want + 1 - len(collected))
        page = resolver.client.get(
            f"/boards/{board_id}/actions", {**query, "before": before, "limit": size}
        )
        rows = [a for a in page if isinstance(a, dict)] if isinstance(page, list) else []
        collected.extend(rows)
        if len(rows) < size or not rows[-1].get("id"):
            break
        if want is not None and len(collected) > want:
            break
        before = str(rows[-1]["id"])
    if want is None:
        return collected, False
    return collected[:want], len(collected) > want


def _pick(item: Row, fields: Sequence[str] | None) -> Row:
    if fields is None:
        return item
    return {name: item[name] for name in fields if name in item}


def activity(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Board actions newest first, 50 by default (`--limit 0` for all), from Trello (AC-11.1)."""
    since = _instant(args.get("since"), "since")
    before = _instant(args.get("before"), "before")
    types = _types(args.get("type"))
    fields = _fields(args.get("fields"))
    want = _limit(args.get("limit"))
    query = {"filter": ",".join(types) or None, "since": since, "before": before}
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        actions, truncated = _fetch(resolver, board_id, query, want)
    items = [_pick(normalize(action), fields) for action in actions]
    meta = {
        "cache": {"hit": False, "age_seconds": 0, "stale": False},
        "truncated": truncated,
    }
    return ctx.success({"actions": items}, board_id=board_id, meta=meta)

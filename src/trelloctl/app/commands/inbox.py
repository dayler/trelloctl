"""`inbox` commands: list, add and move (RF-28, AC-28.1 to AC-28.4, AC-7.3, AC-13.4, A-3, A-13).

The Inbox is the hidden board of the user, resolved with `GET /members/me?fields=inbox` and
cached like any board with `boards.kind = "inbox"`. Once cached it is found again without any
request, so a read inside the TTL costs nothing. `inbox list` and `inbox add` need no selected
board; `inbox move` is the only operation that crosses boards and always starts in the Inbox.
Every function returns its envelope as data and never prints.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from trelloctl.app.commands.card import (
    CARD_FIELDS,
    DEFAULT_LIMIT,
    Catalog,
    card_view,
    catalog_of,
    parse_moment,
    read_description,
)
from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import BOARD_PARAMS, Resolver, iso_utc
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.mover import parse_index, to_trello_pos
from trelloctl.infra.cache import Cache
from trelloctl.infra.settings import require_credentials
from trelloctl.infra.trello_client import QueryValue

Row = dict[str, Any]

KIND = "inbox"
_FORBIDDEN_ADD = (("list", "--list"), ("labels", "--labels"), ("members", "--members"))


# --- argument handling (no network) ---------------------------------------------------------


def _usage(message: str, **details: Any) -> TrelloctlError:
    return TrelloctlError("USAGE", message, details)


def _limit(raw: Any) -> int | None:
    """`--limit`: 50 by default, 0 for no limit (`None`), negative is `USAGE` (AC-13.4)."""
    if raw is None:
        return DEFAULT_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise _usage("--limit must be an integer greater than or equal to 0.")
    return raw or None


def _fields(raw: Sequence[str] | None) -> tuple[str, ...] | None:
    """`--fields` validated against the fields of a card."""
    if raw is None:
        return None
    wanted = [item.strip() for item in raw if item.strip()]
    unknown = [item for item in wanted if item not in CARD_FIELDS]
    if unknown or not wanted:
        raise _usage(
            "--fields accepts only: " + ", ".join(CARD_FIELDS) + ".",
            unknown=unknown,
            allowed=list(CARD_FIELDS),
        )
    return tuple(wanted)


def _check_pos(raw: Any) -> str | None:
    """`--pos` as `top`, `bottom` or a non-negative index; anything else is `USAGE`."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("top", "bottom"):
        return text
    parse_index(text)
    return text


# --- session and Inbox resolution -----------------------------------------------------------


@dataclass
class _Inbox:
    board_id: str
    list_id: str
    reloaded: bool


@dataclass
class _Session:
    ctx: CommandContext
    resolver: Resolver

    @property
    def cache(self) -> Cache:
        return self.resolver.cache


@contextmanager
def _session(ctx: CommandContext) -> Iterator[_Session]:
    require_credentials(ctx.settings)
    with Resolver(ctx) as resolver:
        yield _Session(ctx, resolver)


def _unavailable(reason: str) -> TrelloctlError:
    return TrelloctlError(
        "INBOX_UNAVAILABLE",
        "The Trello Inbox is not available for this account.",
        {"reason": reason},
    )


def _fetch_ids(s: _Session) -> tuple[str, str]:
    """`inbox.idBoard` and `inbox.idList` of `GET /members/me?fields=inbox` (AC-28.1)."""
    data = s.resolver.client.get("/members/me", {"fields": "inbox"})
    inbox = data.get("inbox") if isinstance(data, Mapping) else None
    if not isinstance(inbox, Mapping):
        raise _unavailable("The response of Trello has no inbox.")
    board_id, list_id = inbox.get("idBoard"), inbox.get("idList")
    if not isinstance(board_id, str) or not board_id or not isinstance(list_id, str) or not list_id:
        raise _unavailable("The inbox of the response has no idBoard or idList.")
    return board_id.lower(), list_id.lower()


def _sync_metadata(s: _Session, board_id: str) -> None:
    """Load lists, labels and members of the Inbox board and mark it as `kind = "inbox"`."""
    board = s.resolver.client.get(f"/boards/{board_id}", BOARD_PARAMS)
    if not isinstance(board, dict) or not board.get("id"):
        raise _unavailable("Trello returned an unexpected Inbox board.")
    s.cache.replace_metadata(
        board_id,
        {**board, "kind": KIND},
        board.get("lists") or [],
        board.get("labels") or [],
        board.get("members") or [],
        s.resolver.now(),
    )


def _cached(s: _Session) -> tuple[str, str] | None:
    """The cached Inbox as `(board_id, list_id)`, or `None` when it is not cached."""
    for row in s.cache.list_boards():
        if row["kind"] != KIND:
            continue
        open_lists = [x for x in s.cache.list_lists(row["board_id"]) if not x["closed"]]
        if open_lists:
            return str(row["board_id"]), str(open_lists[0]["id"])
    return None


def _open_inbox(s: _Session, *, fresh: bool = False, write: bool = False) -> _Inbox:
    """The Inbox from the cache, reloaded when absent, expired (reads) or asked for (AC-28.1).

    Writes never check the TTL (AC-22.11); they use the cached Inbox when there is one.
    """
    found = None if fresh else _cached(s)
    if found is not None and not write:
        board_id = found[0]
        if not s.cache.metadata_fresh(board_id, s.resolver.now(), s.resolver.metadata_ttl):
            found = None
    if found is not None:
        return _Inbox(found[0], found[1], False)
    board_id, list_id = _fetch_ids(s)
    _sync_metadata(s, board_id)
    return _Inbox(board_id, list_id, True)


def _meta(s: _Session, inbox: _Inbox) -> dict[str, Any]:
    """`meta.cache` of a read of the Inbox."""
    cards = s.resolver.cache_meta(inbox.board_id, "cards")
    metadata = s.resolver.cache_meta(inbox.board_id, "metadata")
    meta: dict[str, Any] = {
        "hit": cards["hit"] and metadata["hit"] and not inbox.reloaded and not cards["stale"],
        "age_seconds": max(cards["age_seconds"], metadata["age_seconds"]),
        "stale": cards["stale"],
    }
    if meta["stale"]:
        meta["error"] = cards["error"]
    return meta


def _view(s: _Session, board_id: str, row: Row) -> dict[str, Any]:
    return card_view(row, catalog_of(s.cache, board_id))


def _stored(s: _Session, board_id: str, data: Any) -> Row:
    """Write the card returned by Trello to the cache of `board_id` and return its row."""
    if not isinstance(data, Mapping) or not data.get("id"):
        raise TrelloctlError("API_ERROR", "Trello returned an unexpected card.", {})
    s.cache.upsert_card(board_id, dict(data), s.resolver.now())
    row = s.cache.get_card(board_id, str(data["id"]))
    if row is None:  # pragma: no cover - upsert_card has just stored it
        raise TrelloctlError("API_ERROR", "The card could not be stored.", {})
    return row


# --- reads ------------------------------------------------------------------------------------


def list_cards(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`inbox list`: the open cards of the Inbox, from the cache (AC-28.2, AC-13.4)."""
    limit = _limit(args.get("limit"))
    fields = _fields(args.get("fields"))
    fresh = bool(args.get("fresh"))
    with _session(ctx) as s:
        inbox = _open_inbox(s, fresh=fresh)
        s.resolver.ensure_cards(inbox.board_id, fresh=fresh)
        rows = sorted(s.cache.list_cards(inbox.board_id), key=lambda r: (r["pos"] or 0, r["id"]))
        truncated = limit is not None and len(rows) > limit
        page = rows[:limit] if truncated else rows
        catalog = catalog_of(s.cache, inbox.board_id)
        cards = [_project(card_view(r, catalog), fields) for r in page]
        meta = {"cache": _meta(s, inbox), "truncated": truncated}
    return ctx.success({"cards": cards}, board_id=inbox.board_id, meta=meta)


def _project(view: Mapping[str, Any], fields: Sequence[str] | None) -> dict[str, Any]:
    if fields is None:
        return dict(view)
    return {name: view[name] for name in fields if name in view}


# --- writes -----------------------------------------------------------------------------------


def add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`inbox add`: a card in the single list of the Inbox (AC-28.3, A-13)."""
    for key, flag in _FORBIDDEN_ADD:
        if args.get(key) is not None:
            raise _usage(f"inbox add does not accept {flag}.", option=flag)
    desc = read_description(args)
    due = None if args.get("due") is None else iso_utc(parse_moment(args["due"]))
    name = str(args["name"])
    if not name.strip():
        raise _usage("--name must not be empty.")
    with _session(ctx) as s:
        inbox = _open_inbox(s, write=True)
        params: dict[str, QueryValue] = {
            "idList": inbox.list_id,
            "name": name,
            "desc": desc,
            "due": due,
        }
        data = s.resolver.client.post("/cards", params)
        if isinstance(data, Mapping) and "idList" not in data:
            data = {**data, "idList": inbox.list_id}
        view = _view(s, inbox.board_id, _stored(s, inbox.board_id, data))
    return ctx.success(view, board_id=inbox.board_id)


def _target_pos(s: _Session, board_id: str, list_id: str, raw: str | None) -> str | float:
    """Trello position for `--pos` in the target list (A-3): an index counts its open cards."""
    if raw is None:
        return "bottom"
    if raw in ("top", "bottom"):
        return raw
    s.resolver.ensure_cards(board_id, write=True)
    others = [float(r["pos"] or 0) for r in s.cache.list_cards(board_id) if r["id_list"] == list_id]
    return to_trello_pos(raw, others)


def _row_json(cache: Cache, board_id: str, row: Row) -> Row:
    """A cached card in the JSON shape that `Cache.upsert_card` stores."""
    return {
        "id": row["id"],
        "shortLink": row["short_link"],
        "idShort": row["id_short"],
        "name": row["name"],
        "desc": row["desc"],
        "idList": row["id_list"],
        "idLabels": row["id_labels"],
        "idMembers": row["id_members"],
        "due": row["due"],
        "dueComplete": bool(row["due_complete"]),
        "pos": row["pos"],
        "closed": bool(row["archived"]),
        "url": row["url"],
        "dateLastActivity": row["date_last_activity"],
        "badges": {
            "comments": row["badge_comments"],
            "attachments": row["badge_attachments"],
            "checkItems": row["badge_check_items"],
            "checkItemsChecked": row["badge_check_items_checked"],
        },
        "attachments": [
            {
                "id": a["id"],
                "name": a["name"],
                "bytes": a["bytes"],
                "mimeType": a["mime_type"],
                "date": a["date"],
                "url": a["url"],
                "isUpload": bool(a["is_upload"]),
            }
            for a in cache.list_attachments(board_id, row["id"])
        ],
        "checklists": [
            {
                "id": c["id"],
                "name": c["name"],
                "pos": c["pos"],
                "checkItems": [
                    {"id": i["id"], "name": i["name"], "state": i["state"], "pos": i["pos"]}
                    for i in c["items"]
                ],
            }
            for c in cache.list_checklists(board_id, row["id"])
        ],
    }


def _leave_inbox(s: _Session, inbox: _Inbox, row: Row) -> None:
    """Drop the moved card from the cache of the Inbox without leaving a tombstone.

    A tombstone would make the card unreachable by id in its new board, so the rest of the
    cards of its list are written back instead.
    """
    remaining = [
        _row_json(s.cache, inbox.board_id, r)
        for r in s.cache.list_cards(inbox.board_id)
        if r["id_list"] == row["id_list"] and r["id"] != row["id"]
    ]
    s.cache.replace_list_cards(inbox.board_id, row["id_list"], remaining, s.resolver.now())


def _list_ref(catalog: Catalog, list_id: str) -> dict[str, Any]:
    found = catalog.lists.get(list_id)
    return {"id": list_id, "name": found["name"] if found else None}


def move(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`inbox move`: a card of the Inbox to a list of the selected board (AC-28.4, A-3)."""
    pos = _check_pos(args.get("pos"))
    ref = str(args["card"])
    to_ref = args.get("to")
    if to_ref is None or not str(to_ref).strip():
        raise _usage("--to is required.")
    with _session(ctx) as s:
        inbox = _open_inbox(s, write=True)
        resolver = s.resolver
        row = resolver.resolve_card(inbox.board_id, ref, write=True)
        target_board = resolver.selected_board()
        target = resolver.resolve_list(target_board, str(to_ref), write=True)
        position = _target_pos(s, target_board, str(target["id"]), pos)
        params: dict[str, QueryValue] = {
            "idBoard": target_board,
            "idList": str(target["id"]),
            "pos": position,
        }
        data = resolver.guarded_write(
            "card",
            inbox.board_id,
            ref,
            lambda found: resolver.client.put(f"/cards/{found['id']}", params),
        )
        if not isinstance(data, Mapping) or not data.get("id"):
            raise TrelloctlError("API_ERROR", "Trello returned an unexpected card.", {})
        merged = {**_row_json(s.cache, inbox.board_id, row), **data}
        stored = _stored(s, target_board, merged)
        _leave_inbox(s, inbox, row)
        catalog = catalog_of(s.cache, target_board)
        board_row = s.cache.get_board(target_board) or {}
        result = {
            "from": {"board": inbox.board_id, "kind": KIND},
            "to": {
                "board": {"id": target_board, "name": board_row.get("name")},
                "list": _list_ref(catalog, str(target["id"])),
            },
            "card": card_view(stored, catalog),
        }
    return ctx.success(result, board_id=target_board)

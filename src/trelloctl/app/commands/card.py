"""`card` commands: create, get, list, search, move, copy, update, done, undone, archive,
unarchive, comments, labels and members (RF-12 to RF-19, AC-7.3, AC-7.4, AC-22.9, AC-22.11,
AC-22.13, A-3, A-13, A-15, A-20 to A-22).

Every function returns its envelope as data. Reads are served from the cache through the
`Resolver`; writes send ids, update the cache with the response of Trello (write-through,
AC-22.11) and never check a TTL, except when a numeric `--pos` needs the cards of the target
list. Cards given by id or URL are located without a selected board (A-15).
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trelloctl.app import search
from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver, iso_utc, looks_stale, not_found
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import Comment, TrelloList
from trelloctl.domain.mover import compute_shift, open_lists, parse_index, to_trello_pos
from trelloctl.infra.cache import Cache
from trelloctl.infra.settings import require_credentials
from trelloctl.infra.trello_client import QueryValue, TrelloClient

Row = dict[str, Any]

MAX_DESC = 16384
DEFAULT_LIMIT = 50
DETAILS = ("checklists", "attachments", "comments")
KEEP_VALUES = ("attachments", "checklists", "comments", "due", "labels", "members", "stickers")
CARD_FIELDS = (
    "id",
    "idShort",
    "shortLink",
    "name",
    "desc",
    "url",
    "list",
    "labels",
    "members",
    "due",
    "dueComplete",
    "pos",
    "archived",
    "dateLastActivity",
    "badges",
)
SEARCH_RESULT_FIELDS = (*CARD_FIELDS, "matched", "comment_ids")
COMMENTS_LIMIT = 1000
COMMENT_FIELDS = ("id", "text", "date", "memberId", "member")
MAX_COMMENT = 16384
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --- argument handling (no network) ---------------------------------------------------------


def _usage(message: str, **details: Any) -> TrelloctlError:
    return TrelloctlError("USAGE", message, details)


def _given(args: Mapping[str, Any], name: str) -> bool:
    value = args.get(name)
    return value is not None and value is not False


def _exclusive(args: Mapping[str, Any], *names: str) -> list[str]:
    """The options of `names` that were given; more than one is `USAGE` (AC-14.4, AC-16.1)."""
    chosen = [name for name in names if _given(args, name)]
    if len(chosen) > 1:
        flags = ", ".join("--" + name.replace("_", "-") for name in names)
        raise _usage(f"{flags} are mutually exclusive.", options=chosen)
    return chosen


def parse_moment(raw: Any, *, hour: int = 12) -> datetime:
    """An ISO-8601 date or date-time as an aware UTC moment; anything else is `USAGE`.

    A date without time takes `hour` o'clock UTC; a naive date-time is taken as UTC.
    """
    text = str(raw).strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise _usage(f"{text!r} is not an ISO-8601 date or date-time.", value=text) from None
    if _DATE_ONLY.match(text):
        moment = moment.replace(hour=hour)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _read_file(path: str) -> str:
    try:
        return Path(path).expanduser().read_bytes().decode("utf-8")
    except (OSError, ValueError) as exc:
        raise _usage(f"Cannot read the file {path!r} as UTF-8 text.", path=path) from exc


def _read_stdin() -> str:
    stream = sys.stdin
    if stream is None:
        raise _usage("Standard input is not available.")
    try:
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            return bytes(buffer.read()).decode("utf-8")
        return str(stream.read())
    except (OSError, ValueError) as exc:
        raise _usage("Cannot read UTF-8 text from standard input.") from exc


def read_description(args: Mapping[str, Any]) -> str | None:
    """`--desc`, `--desc-file` or `--desc -` (stdin), mutually exclusive, at most 16 384 chars.

    The text is sent as is (A-13); `None` means that no description was given.
    """
    _exclusive(args, "desc", "desc_file")
    text: str | None
    if args.get("desc_file") is not None:
        text = _read_file(str(args["desc_file"]))
    elif args.get("desc") == "-":
        text = _read_stdin()
    else:
        raw = args.get("desc")
        text = None if raw is None else str(raw)
    if text is not None and len(text) > MAX_DESC:
        raise _usage(
            f"The description has {len(text)} characters; the maximum is {MAX_DESC}.",
            length=len(text),
            max=MAX_DESC,
        )
    return text


def read_text(args: Mapping[str, Any]) -> str:
    """`--text`, `--text-file` or `--text -` (stdin), exactly one, sent as is (A-13)."""
    _exclusive(args, "text", "text_file")
    if args.get("text_file") is not None:
        text = _read_file(str(args["text_file"]))
    elif args.get("text") == "-":
        text = _read_stdin()
    elif args.get("text") is not None:
        text = str(args["text"])
    else:
        raise _usage("Give --text, --text-file or --text - (standard input).")
    if not text.strip():
        raise _usage("The comment text must not be empty.")
    if len(text) > MAX_COMMENT:
        raise _usage(
            f"The comment has {len(text)} characters; the maximum is {MAX_COMMENT}.",
            length=len(text),
            max=MAX_COMMENT,
        )
    return text


def _check_pos(raw: Any) -> str | None:
    """`--pos` as `top`, `bottom` or a non-negative index; anything else is `USAGE`."""
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if text in ("top", "bottom"):
        return text
    parse_index(text)
    return text


def _limit(raw: Any) -> int | None:
    """`--limit`: 50 by default, 0 for no limit (`None`), negative is `USAGE`."""
    if raw is None:
        return DEFAULT_LIMIT
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise _usage("--limit must be an integer greater than or equal to 0.")
    return raw or None


def _fields(raw: Sequence[str] | None, allowed: Sequence[str]) -> tuple[str, ...] | None:
    """`--fields` validated against the fields of the listing (A-5)."""
    if raw is None:
        return None
    wanted = [item.strip() for item in raw if item.strip()]
    unknown = [item for item in wanted if item not in allowed]
    if unknown or not wanted:
        raise _usage(
            "--fields accepts only: " + ", ".join(allowed) + ".",
            unknown=unknown,
            allowed=list(allowed),
        )
    return tuple(wanted)


def _details(args: Mapping[str, Any]) -> tuple[str, ...]:
    """The extra sections of `card get`: `--full` or `--with` (AC-13.1)."""
    _exclusive(args, "full", "with")
    raw = args.get("with")
    if raw is None:
        return DETAILS if args.get("full") else ()
    wanted = [item.strip().lower() for item in raw if item.strip()]
    unknown = [item for item in wanted if item not in DETAILS]
    if unknown or not wanted:
        raise _usage(
            "--with accepts only: " + ", ".join(DETAILS) + ".",
            unknown=unknown,
            allowed=list(DETAILS),
        )
    return tuple(name for name in DETAILS if name in wanted)


def _keep(raw: Sequence[str] | None) -> str:
    """`--keep all|none|<list>` as the `keepFromSource` value (AC-15.2)."""
    if raw is None:
        return "all"
    wanted = [item.strip().lower() for item in raw if item.strip()]
    allowed = ("all", "none", *KEEP_VALUES)
    unknown = [item for item in wanted if item not in allowed]
    if unknown or not wanted:
        raise _usage(
            "--keep accepts all, none or a list of: " + ", ".join(KEEP_VALUES) + ".",
            unknown=unknown,
            allowed=list(allowed),
        )
    for single in ("all", "none"):
        if single in wanted and len(wanted) > 1:
            raise _usage(f"--keep {single} cannot be combined with other values.")
    return ",".join(dict.fromkeys(wanted))


# --- session: resolver, request counter, catalog --------------------------------------------


class _CountingClient(TrelloClient):
    """Trello client that counts its requests, to tell a cache hit from a synchronization."""

    calls = 0

    def request(
        self, method: str, path: str, params: Mapping[str, QueryValue] | None = None
    ) -> Any:
        self.calls += 1
        return super().request(method, path, params)


@dataclass
class _Session:
    ctx: CommandContext
    resolver: Resolver
    client: _CountingClient

    @property
    def cache(self) -> Cache:
        return self.resolver.cache

    def read_meta(self, board_id: str, *collections: str) -> dict[str, Any]:
        """`meta.cache` over several collections; any request made makes it a miss."""
        metas = [self.resolver.cache_meta(board_id, name) for name in collections]
        meta: dict[str, Any] = {
            "hit": all(m["hit"] for m in metas) and self.client.calls == 0,
            "age_seconds": max(m["age_seconds"] for m in metas),
            "stale": metas[0]["stale"],
        }
        if meta["stale"]:
            meta["error"] = metas[0]["error"]
        return meta


@contextmanager
def _session(ctx: CommandContext) -> Iterator[_Session]:
    api_key, token = require_credentials(ctx.settings)
    client = _CountingClient(
        ctx.settings.base_url,
        api_key,
        token,
        ctx.settings.timeout_seconds,
        transport=ctx.transport,
        logger=ctx.logger,
    )
    with Resolver(ctx, client=client) as resolver:
        yield _Session(ctx, resolver, client)


@dataclass
class Catalog:
    lists: dict[str, Row]
    labels: dict[str, Row]
    members: dict[str, Row]


def catalog_of(cache: Cache, board_id: str) -> Catalog:
    return Catalog(
        {r["id"]: r for r in cache.list_lists(board_id)},
        {r["id"]: r for r in cache.list_labels(board_id)},
        {r["id"]: r for r in cache.list_members(board_id)},
    )


def card_view(row: Row, catalog: Catalog) -> dict[str, Any]:
    """The JSON of a cached card: list, labels, members, due, badges and the rest (AC-13.1)."""
    list_row = catalog.lists.get(row["id_list"])
    labels = [
        {k: found[k] for k in ("id", "name", "color")} if found else {"id": label_id}
        for label_id in row["id_labels"]
        for found in (catalog.labels.get(label_id),)
    ]
    members = [
        {"id": found["id"], "username": found["username"], "fullName": found["full_name"]}
        if found
        else {"id": member_id}
        for member_id in row["id_members"]
        for found in (catalog.members.get(member_id),)
    ]
    return {
        "id": row["id"],
        "idShort": row["id_short"],
        "shortLink": row["short_link"],
        "name": row["name"],
        "desc": row["desc"] or "",
        "url": row["url"],
        "list": {"id": row["id_list"], "name": list_row["name"] if list_row else None},
        "labels": labels,
        "members": members,
        "due": row["due"],
        "dueComplete": bool(row["due_complete"]),
        "pos": row["pos"],
        "archived": bool(row["archived"]),
        "dateLastActivity": row["date_last_activity"],
        "badges": {
            "comments": row["badge_comments"] or 0,
            "attachments": row["badge_attachments"] or 0,
            "checkItems": row["badge_check_items"] or 0,
            "checkItemsChecked": row["badge_check_items_checked"] or 0,
        },
    }


def _project(view: Mapping[str, Any], fields: Sequence[str] | None) -> dict[str, Any]:
    if fields is None:
        return dict(view)
    return {name: view[name] for name in fields if name in view}


def _view_of(s: _Session, board_id: str, row: Row) -> dict[str, Any]:
    return card_view(row, catalog_of(s.cache, board_id))


# --- write-through --------------------------------------------------------------------------

_ROW_TO_JSON = {
    "name": "name",
    "desc": "desc",
    "id_list": "idList",
    "id_labels": "idLabels",
    "id_members": "idMembers",
    "due": "due",
    "due_complete": "dueComplete",
    "pos": "pos",
    "short_link": "shortLink",
    "id_short": "idShort",
    "url": "url",
    "date_last_activity": "dateLastActivity",
}


def _attachments_json(cache: Cache, board_id: str, card_id: str) -> list[Row]:
    return [
        {
            "id": a["id"],
            "name": a["name"],
            "bytes": a["bytes"],
            "mimeType": a["mime_type"],
            "date": a["date"],
            "url": a["url"],
            "isUpload": bool(a["is_upload"]),
        }
        for a in cache.list_attachments(board_id, card_id)
    ]


def _checklists_json(cache: Cache, board_id: str, card_id: str) -> list[Row]:
    return [
        {
            "id": c["id"],
            "name": c["name"],
            "pos": c["pos"],
            "checkItems": [
                {"id": i["id"], "name": i["name"], "state": i["state"], "pos": i["pos"]}
                for i in c["items"]
            ],
        }
        for c in cache.list_checklists(board_id, card_id)
    ]


def _store(s: _Session, board_id: str, data: Any, previous: Row | None = None) -> Row:
    """Write the card returned by a write to the cache and return its row.

    Fields and nested data that the response omits keep their cached values, so a `PUT` does
    not erase the checklists or attachments of the card.
    """
    if not isinstance(data, Mapping) or not data.get("id"):
        raise TrelloctlError("API_ERROR", "Trello returned an unexpected card.", {})
    card: Row = dict(data)
    card_id = str(card["id"])
    old = previous if previous is not None else s.cache.get_card(board_id, card_id)
    if old is not None:
        for column, key in _ROW_TO_JSON.items():
            if key not in card:
                card[key] = old[column]
        card.setdefault("closed", bool(old["archived"]))
    if "attachments" not in card:
        card["attachments"] = _attachments_json(s.cache, board_id, card_id)
    if "checklists" not in card:
        card["checklists"] = _checklists_json(s.cache, board_id, card_id)
    s.cache.upsert_card(board_id, card, s.resolver.now())
    stored = s.cache.get_card(board_id, card_id)
    if stored is None:  # pragma: no cover - upsert_card has just stored it
        raise TrelloctlError("API_ERROR", "The card could not be stored.", {"card": card_id})
    return stored


def _trello_pos(
    s: _Session, board_id: str, list_id: str, raw: str | None, exclude: str | None
) -> str | float:
    """Trello position for `--pos` in `list_id` (AC-12.2, A-3, A-21).

    `top` and `bottom` need no data; an index counts the open cards of the list without
    `exclude` (the moved card), reloading the snapshot when its TTL expired.
    """
    if raw is None:
        return "bottom"
    if raw in ("top", "bottom"):
        return raw
    s.resolver.ensure_cards(board_id, write=True)
    others = [
        float(r["pos"] or 0)
        for r in s.cache.list_cards(board_id)
        if r["id_list"] == list_id and r["id"] != exclude
    ]
    return to_trello_pos(raw, others)


def _resolve_ids(
    s: _Session, board_id: str, refs: Sequence[str] | None, kind: str
) -> list[str] | None:
    """Ids of labels or members; all must resolve before anything is written (AC-12.5)."""
    if refs is None:
        return None
    resolve = s.resolver.resolve_label if kind == "label" else s.resolver.resolve_member
    return [str(resolve(board_id, ref, write=True)["id"]) for ref in refs]


def _write_card(
    s: _Session,
    board_id: str,
    ref: str,
    params: Mapping[str, QueryValue],
) -> Row:
    """`PUT /cards/{id}` of a resolved card with the stale-reference handling of AC-22.13."""

    def call(row: Row) -> Any:
        return s.resolver.client.put(f"/cards/{row['id']}", params)

    data = s.resolver.guarded_write("card", board_id, ref, call)
    return _store(s, board_id, data)


# --- reads ------------------------------------------------------------------------------------


def _ensure_fresh_card(s: _Session, board_id: str, row: Row) -> Row:
    """Reload the snapshot when neither it nor the card itself is within its TTL (AC-22.3)."""
    now, ttl = s.resolver.now(), s.resolver.cards_ttl
    if s.cache.cards_fresh(board_id, now, ttl) or s.cache.card_fresh(board_id, row["id"], now, ttl):
        return row
    s.resolver.ensure_cards(board_id)
    return s.cache.get_card(board_id, row["id"]) or row


def _comment_json(item: Mapping[str, Any], catalog: Catalog) -> dict[str, Any]:
    comment = Comment.from_json(item)
    creator = item.get("memberCreator")
    username = creator.get("username") if isinstance(creator, Mapping) else None
    member = catalog.members.get(comment.member_id or "")
    return {
        "id": comment.id,
        "text": comment.text,
        "date": comment.date,
        "memberId": comment.member_id,
        "member": username or (member["username"] if member else None),
    }


def _comments_of(s: _Session, card_id: str, catalog: Catalog) -> list[dict[str, Any]]:
    data = s.resolver.client.get(
        f"/cards/{card_id}/actions", {"filter": "commentCard", "limit": COMMENTS_LIMIT}
    )
    items = data if isinstance(data, list) else []
    return [_comment_json(i, catalog) for i in items if isinstance(i, Mapping)]


def get(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card get`: one card with its lists, labels, members and, on request, more (RF-13)."""
    details = _details(args)
    fresh = bool(args.get("fresh"))
    with _session(ctx) as s:
        located = s.resolver.locate_card(str(args["card"]))
        board_id, row = located.board_id, located.card
        if fresh:
            data = s.resolver.sync_card(row["id"], board_id=board_id)
            board_id = str(data["idBoard"])
            row = s.cache.get_card(board_id, row["id"]) or row
        else:
            row = _ensure_fresh_card(s, board_id, row)
        s.resolver.ensure_metadata(board_id, fresh=fresh)
        catalog = catalog_of(s.cache, board_id)
        data_out = card_view(row, catalog)
        if "checklists" in details:
            data_out["checklists"] = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "items": [
                        {"id": i["id"], "name": i["name"], "state": i["state"]} for i in c["items"]
                    ],
                }
                for c in s.cache.list_checklists(board_id, row["id"])
            ]
        if "attachments" in details:
            data_out["attachments"] = [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "bytes": a["bytes"],
                    "mimeType": a["mime_type"],
                    "date": a["date"],
                    "url": a["url"],
                    "isUpload": bool(a["is_upload"]),
                    "localPath": a["local_path"],
                }
                for a in s.cache.list_attachments(board_id, row["id"])
            ]
        if "comments" in details:
            data_out["comments"] = _comments_of(s, row["id"], catalog)
        meta = {"cache": s.read_meta(board_id, "cards", "metadata")}
    return ctx.success(data_out, board_id=board_id, meta=meta)


def _snapshot(s: _Session, board_id: str, *, fresh: bool, archived: bool) -> list[Row]:
    """Metadata and the open (and archived) cards of the board, reloaded when expired."""
    s.resolver.ensure_metadata(board_id, fresh=fresh)
    s.resolver.ensure_cards(board_id, fresh=fresh)
    if archived:
        s.resolver.ensure_archived(board_id, fresh=fresh)
    return s.cache.list_cards(board_id, include_archived=archived)


def _ordered(rows: Sequence[Row], catalog: Catalog) -> list[Row]:
    """Cards in board order: open lists left to right, then by position."""
    order = {item.id: index for index, item in enumerate(open_lists(_models(catalog)))}
    unknown = len(order)
    return sorted(
        rows,
        key=lambda r: (
            order.get(r["id_list"], unknown),
            r["id_list"] or "",
            r["pos"] or 0,
            r["id"],
        ),
    )


def _models(catalog: Catalog) -> list[TrelloList]:
    return [
        TrelloList(r["id"], r["name"] or "", float(r["pos"] or 0), bool(r["closed"]))
        for r in catalog.lists.values()
    ]


def _due_of(row: Row) -> datetime | None:
    if not row["due"]:
        return None
    try:
        moment = datetime.fromisoformat(str(row["due"]))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _page(items: list[Row], limit: int | None) -> tuple[list[Row], bool]:
    if limit is None or len(items) <= limit:
        return items, False
    return items[:limit], True


def list_cards(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card list`: the open cards that satisfy every filter, from the cache (AC-13.2)."""
    _exclusive(args, "done", "not_done")
    limit = _limit(args.get("limit"))
    fields = _fields(args.get("fields"), CARD_FIELDS)
    before = None if args.get("due_before") is None else parse_moment(args["due_before"], hour=0)
    after = None if args.get("due_after") is None else parse_moment(args["due_after"], hour=0)
    archived = bool(args.get("include_archived"))
    with _session(ctx) as s:
        board_id = s.resolver.selected_board()
        rows = _snapshot(s, board_id, fresh=bool(args.get("fresh")), archived=archived)
        wanted_list = _id_of(s, board_id, args.get("list"), "list")
        wanted_label = _id_of(s, board_id, args.get("label"), "label")
        wanted_member = _id_of(s, board_id, args.get("member"), "member")
        catalog = catalog_of(s.cache, board_id)
        selected: list[Row] = []
        for row in _ordered(rows, catalog):
            if wanted_list and row["id_list"] != wanted_list:
                continue
            if wanted_label and wanted_label not in row["id_labels"]:
                continue
            if wanted_member and wanted_member not in row["id_members"]:
                continue
            if args.get("done") and not row["due_complete"]:
                continue
            if args.get("not_done") and row["due_complete"]:
                continue
            if before is not None or after is not None:
                due = _due_of(row)
                if due is None or (before is not None and due >= before):
                    continue
                if after is not None and due <= after:
                    continue
            selected.append(row)
        page, truncated = _page(selected, limit)
        cards = [_project(card_view(r, catalog), fields) for r in page]
        collections = ("cards", "metadata", "archived") if archived else ("cards", "metadata")
        meta = {"cache": s.read_meta(board_id, *collections), "truncated": truncated}
    return ctx.success({"cards": cards}, board_id=board_id, meta=meta)


def _id_of(s: _Session, board_id: str, ref: Any, kind: str) -> str | None:
    if ref is None:
        return None
    resolve = {
        "list": s.resolver.resolve_list,
        "label": s.resolver.resolve_label,
        "member": s.resolver.resolve_member,
    }[kind]
    return str(resolve(board_id, str(ref))["id"])


def find(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card search`: wildcard search in name, description, checklists and comments (RF-13)."""
    pattern = str(args["pattern"])
    if not pattern.strip():
        raise _usage("The search pattern must not be empty.")
    fields = search.parse_fields(args.get("in"))
    terms = search.require_comment_terms(pattern) if "comments" in fields else None
    limit = _limit(args.get("limit"))
    wanted = _fields(args.get("fields"), SEARCH_RESULT_FIELDS)
    archived = bool(args.get("include_archived"))
    warnings: list[str] = []
    if terms is not None and search.comment_terms(pattern)[1]:
        warnings.append(
            "Trello does not support wildcards or brackets in comment searches; "
            f"they were removed and {terms!r} was searched in comments."
        )
    with _session(ctx) as s:
        board_id = s.resolver.selected_board()
        rows = _snapshot(s, board_id, fresh=bool(args.get("fresh")), archived=archived)
        matched = search.local_matches(s.cache, board_id, rows, pattern, fields)
        comment_ids: dict[str, list[str]] = {}
        if terms is not None:
            comment_ids = search.comment_hits(s.resolver.client, board_id, terms)
            for card_id in comment_ids:
                matched.setdefault(card_id, [])
                matched[card_id].append("comments")
        catalog = catalog_of(s.cache, board_id)
        hits = [r for r in _ordered(rows, catalog) if r["id"] in matched]
        page, truncated = _page(hits, limit)
        cards = []
        for row in page:
            view = card_view(row, catalog)
            view["matched"] = [f for f in search.SEARCH_FIELDS if f in matched[row["id"]]]
            if "comments" in matched[row["id"]]:
                view["comment_ids"] = comment_ids.get(row["id"], [])
            cards.append(_project(view, wanted))
        collections = ("cards", "metadata", "archived") if archived else ("cards", "metadata")
        meta: dict[str, Any] = {
            "cache": s.read_meta(board_id, *collections),
            "truncated": truncated,
        }
        if warnings:
            meta["warnings"] = warnings
    return ctx.success({"cards": cards}, board_id=board_id, meta=meta)


# --- writes -----------------------------------------------------------------------------------


def create(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card create`: a card at the end (or `--pos`) of a list (RF-12)."""
    desc = read_description(args)
    due = None if args.get("due") is None else iso_utc(parse_moment(args["due"]))
    pos = _check_pos(args.get("pos"))
    name = str(args["name"])
    if not name.strip():
        raise _usage("--name must not be empty.")
    list_ref = str(args["list"])
    with _session(ctx) as s:
        board_id = s.resolver.selected_board()
        target = s.resolver.resolve_list(board_id, list_ref, write=True)
        labels = _resolve_ids(s, board_id, args.get("labels"), "label")
        members = _resolve_ids(s, board_id, args.get("members"), "member")
        position = _trello_pos(s, board_id, target["id"], pos, None)

        def call(row: Row) -> Any:
            params: dict[str, QueryValue] = {
                "idList": row["id"],
                "name": name,
                "desc": desc,
                "due": due,
                "idLabels": None if labels is None else ",".join(labels),
                "idMembers": None if members is None else ",".join(members),
                "pos": position,
            }
            return s.resolver.client.post("/cards", params)

        data = s.resolver.guarded_write("list", board_id, list_ref, call)
        row = _store(s, board_id, data)
        view = _view_of(s, board_id, row)
    return ctx.success(view, board_id=board_id)


def _list_ref(catalog: Catalog, list_id: str | None, index: int | None) -> dict[str, Any]:
    found = catalog.lists.get(list_id or "")
    return {"id": list_id, "name": found["name"] if found else None, "index": index}


def _index_of(catalog: Catalog, list_id: str | None) -> int | None:
    for index, item in enumerate(open_lists(_models(catalog))):
        if item.id == list_id:
            return index
    return None


def move(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card move`: to a list, or N lists to the left or right (RF-14, AC-22.11)."""
    chosen = _exclusive(args, "to", "left", "right")
    if not chosen:
        raise _usage("Give one of --to, --left or --right.")
    pos = _check_pos(args.get("pos"))
    ref = str(args["card"])
    clamp, dry_run = bool(args.get("clamp")), bool(args.get("dry_run"))
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id, row = located.board_id, located.card
        if chosen[0] == "to":
            target_id = str(s.resolver.resolve_list(board_id, str(args["to"]), write=True)["id"])
            unchanged = False
        else:
            s.resolver.fresh_metadata(board_id)
            shift = compute_shift(
                _models(catalog_of(s.cache, board_id)),
                str(row["id_list"]),
                "left" if chosen[0] == "left" else "right",
                int(args[chosen[0]]),
                clamp,
            )
            target_id, unchanged = shift.target_list_id, shift.unchanged
        catalog = catalog_of(s.cache, board_id)
        origin = _list_ref(catalog, row["id_list"], _index_of(catalog, row["id_list"]))
        destination = _list_ref(catalog, target_id, _index_of(catalog, target_id))
        position = None if unchanged else _trello_pos(s, board_id, target_id, pos, row["id"])
        if dry_run or unchanged:
            card = _view_of(s, board_id, row)
        else:
            params: dict[str, QueryValue] = {"idList": target_id, "pos": position}
            card = _view_of(s, board_id, _write_card(s, board_id, ref, params))
    data = {"dry_run": dry_run, "from": origin, "to": destination, "card": card}
    return ctx.success(data, board_id=board_id)


def copy(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card copy`: a copy in another list with `idCardSource` (RF-15)."""
    keep = _keep(args.get("keep"))
    pos = _check_pos(args.get("pos"))
    name = args.get("name")
    ref = str(args["card"])
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id = located.board_id
        target = s.resolver.resolve_list(board_id, str(args["to"]), write=True)
        position = _trello_pos(s, board_id, target["id"], pos, None)

        def call(row: Row) -> Any:
            params: dict[str, QueryValue] = {
                "idList": target["id"],
                "idCardSource": row["id"],
                "keepFromSource": keep,
                "name": None if name is None else str(name),
                "pos": position,
            }
            return s.resolver.client.post("/cards", params)

        data = s.resolver.guarded_write("card", board_id, ref, call)
        stored = _store(s, board_id, data)
        if keep == "all" or {"attachments", "checklists"} & set(keep.split(",")):
            stored = _complete_copy(s, board_id, stored)
        view = _view_of(s, board_id, stored)
    return ctx.success(view, board_id=board_id)


def _complete_copy(s: _Session, board_id: str, stored: Row) -> Row:
    """Read the copy back so that its checklists and attachments reach the cache."""
    try:
        s.resolver.sync_card(stored["id"], board_id=board_id)
    except TrelloctlError as exc:
        s.ctx.logger.warning("copy not completed in the cache: %s", exc.code)
        return stored
    return s.cache.get_card(board_id, stored["id"]) or stored


def update(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card update`: only the given fields; label and member sets are replaced (RF-16)."""
    desc = read_description(args)
    _exclusive(args, "due", "no_due")
    _exclusive(args, "done", "not_done")
    pos = _check_pos(args.get("pos"))
    due = None if args.get("due") is None else iso_utc(parse_moment(args["due"]))
    name = args.get("name")
    if name is not None and not str(name).strip():
        raise _usage("--name must not be empty.")
    given = [
        desc is not None,
        name is not None,
        due is not None,
        bool(args.get("no_due")),
        bool(args.get("done")),
        bool(args.get("not_done")),
        args.get("labels") is not None,
        args.get("members") is not None,
        pos is not None,
    ]
    if not any(given):
        raise _usage("Nothing to update: give at least one field to change.")
    ref = str(args["card"])
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id, row = located.board_id, located.card
        labels = _resolve_ids(s, board_id, args.get("labels"), "label")
        members = _resolve_ids(s, board_id, args.get("members"), "member")
        params: dict[str, QueryValue] = {
            "name": None if name is None else str(name),
            "desc": desc,
            "due": "null" if args.get("no_due") else due,
            "dueComplete": True if args.get("done") else (False if args.get("not_done") else None),
            "idLabels": None if labels is None else ",".join(labels),
            "idMembers": None if members is None else ",".join(members),
            "pos": None
            if pos is None
            else _trello_pos(s, board_id, row["id_list"], pos, row["id"]),
        }
        stored = _write_card(s, board_id, ref, params)
        view = _view_of(s, board_id, stored)
    return ctx.success(view, board_id=board_id)


def _simple(
    ctx: CommandContext, args: Mapping[str, Any], params: dict[str, QueryValue]
) -> dict[str, Any]:
    ref = str(args["card"])
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id = located.board_id
        stored = _write_card(s, board_id, ref, params)
        view = _view_of(s, board_id, stored)
    return ctx.success(view, board_id=board_id)


def done(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card done`: `dueComplete = true`; no due date is needed (AC-16.2)."""
    return _simple(ctx, args, {"dueComplete": True})


def undone(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card undone`: `dueComplete = false` (AC-16.2)."""
    return _simple(ctx, args, {"dueComplete": False})


def archive(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card archive`: `closed = true`; archiving an archived card is not an error (RNF-6)."""
    return _simple(ctx, args, {"closed": True})


def unarchive(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card unarchive`: `closed = false`; the card returns to its list (AC-16.3)."""
    return _simple(ctx, args, {"closed": False})


# --- comments (RF-17) -------------------------------------------------------------------------


def _fetch_comments(s: _Session, card_id: str, limit: int | None) -> tuple[list[Row], bool]:
    """Comments newest first, in pages of 1000; one more than `limit` tells it was truncated."""
    found: list[Row] = []
    before: str | None = None
    while True:
        want = COMMENTS_LIMIT if limit is None else min(limit + 1 - len(found), COMMENTS_LIMIT)
        params: dict[str, QueryValue] = {"filter": "commentCard", "limit": want, "before": before}
        data = s.resolver.client.get(f"/cards/{card_id}/actions", params)
        page: list[Row] = (
            [dict(i) for i in data if isinstance(i, Mapping)] if isinstance(data, list) else []
        )
        found.extend(page)
        if len(page) < want or not page[-1].get("id"):
            break
        if limit is not None and len(found) > limit:
            break
        before = str(page[-1]["id"])
    if limit is not None and len(found) > limit:
        return found[:limit], True
    return found, False


def comments(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card comments`: the comments of a card, never cached (AC-17.1, AC-13.4)."""
    limit = _limit(args.get("limit"))
    fields = _fields(args.get("fields"), COMMENT_FIELDS)
    with _session(ctx) as s:
        located = s.resolver.locate_card(str(args["card"]))
        board_id, row = located.board_id, located.card
        s.resolver.ensure_metadata(board_id)
        catalog = catalog_of(s.cache, board_id)
        items, truncated = _fetch_comments(s, row["id"], limit)
        out = [_project(_comment_json(i, catalog), fields) for i in items]
    return ctx.success({"comments": out}, board_id=board_id, meta={"truncated": truncated})


def _set_badges(s: _Session, board_id: str, card_id: str, **changes: int) -> None:
    row = s.cache.get_card(board_id, card_id)
    if row is None:  # pragma: no cover - the card was just located
        return
    badges = {
        "comments": row["badge_comments"] or 0,
        "attachments": row["badge_attachments"] or 0,
        "checkItems": row["badge_check_items"] or 0,
        "checkItemsChecked": row["badge_check_items_checked"] or 0,
    }
    badges.update(changes)
    _store(s, board_id, {"id": card_id, "badges": badges}, row)


def _comment_error(exc: TrelloctlError, ref: str) -> TrelloctlError:
    return not_found("comment", ref) if looks_stale(exc) else exc


def comment_add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card comment add`: a comment with the text as is; mentions are Trello's (AC-17.2)."""
    text = read_text(args)
    ref = str(args["card"])
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id = located.board_id

        def call(row: Row) -> Any:
            return s.resolver.client.post(f"/cards/{row['id']}/actions/comments", {"text": text})

        data = s.resolver.guarded_write("card", board_id, ref, call)
        if not isinstance(data, Mapping) or not data.get("id"):
            raise TrelloctlError("API_ERROR", "Trello returned an unexpected comment.", {})
        current = s.cache.get_card(board_id, located.card["id"]) or located.card
        _set_badges(s, board_id, current["id"], comments=(current["badge_comments"] or 0) + 1)
        view = _comment_json(data, catalog_of(s.cache, board_id))
    return ctx.success(view, board_id=board_id)


def comment_edit(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card comment edit`: replace the text of a comment of the token's member (AC-17.3)."""
    text = read_text(args)
    with _session(ctx) as s:
        located = s.resolver.locate_card(str(args["card"]), write=True)
        board_id, card_id = located.board_id, located.card["id"]
        comment_id = s.resolver.resolve_comment(str(args["comment"]))
        try:
            data = s.resolver.client.put(
                f"/cards/{card_id}/actions/{comment_id}/comments", {"text": text}
            )
        except TrelloctlError as exc:
            raise _comment_error(exc, str(args["comment"])) from exc
        item = data if isinstance(data, Mapping) and data.get("id") else {"id": comment_id}
        view = _comment_json(item, catalog_of(s.cache, board_id))
        view["text"] = text
    return ctx.success(view, board_id=board_id)


def comment_remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card comment remove`: delete a comment of the token's member (AC-17.3, AC-16.4)."""
    with _session(ctx) as s:
        located = s.resolver.locate_card(str(args["card"]), write=True)
        board_id, row = located.board_id, located.card
        comment_id = s.resolver.resolve_comment(str(args["comment"]))
        try:
            s.resolver.client.delete(f"/cards/{row['id']}/actions/{comment_id}/comments")
        except TrelloctlError as exc:
            raise _comment_error(exc, str(args["comment"])) from exc
        current = s.cache.get_card(board_id, row["id"]) or row
        _set_badges(s, board_id, row["id"], comments=max((current["badge_comments"] or 0) - 1, 0))
    return ctx.success({"deleted": comment_id, "card": row["id"]}, board_id=board_id)


# --- labels and members of a card (RF-18, RF-19) ----------------------------------------------

_RELATIONS = {
    "label": ("labels", "id_labels", "idLabels"),
    "member": ("members", "id_members", "idMembers"),
}


def _write_relation(
    s: _Session, board_id: str, card_id: str, kind: str, action: str, ref: str
) -> None:
    path = f"/cards/{card_id}/{_RELATIONS[kind][2]}"

    def call(entity: Row) -> Any:
        if action == "add":
            return s.resolver.client.post(path, {"value": entity["id"]})
        return s.resolver.client.delete(f"{path}/{entity['id']}")

    s.resolver.guarded_write(kind, board_id, ref, call)


def _relation(
    ctx: CommandContext, args: Mapping[str, Any], kind: str, action: str
) -> dict[str, Any]:
    """`card label|member add|remove|set`: idempotent; every value resolves before any write."""
    key, column, json_key = _RELATIONS[kind]
    refs = [str(r) for r in args[key]]
    ref = str(args["card"])
    with _session(ctx) as s:
        located = s.resolver.locate_card(ref, write=True)
        board_id, card_id = located.board_id, located.card["id"]
        ids = _resolve_ids(s, board_id, refs, kind) or []
        if action == "set":
            _write_card(s, board_id, ref, {json_key: ",".join(dict.fromkeys(ids))})
        else:
            current: list[str] = list(located.card[column])
            for item_id, item_ref in dict.fromkeys(zip(ids, refs, strict=True)):
                if (item_id in current) == (action == "add"):
                    continue
                _write_relation(s, board_id, card_id, kind, action, item_ref)
                if action == "add":
                    current = [*current, item_id]
                else:
                    current = [i for i in current if i != item_id]
                _store(s, board_id, {"id": card_id, json_key: current})
        row = s.cache.get_card(board_id, card_id) or located.card
        view = _view_of(s, board_id, row)
    data = {"id": view["id"], "name": view["name"], key: view[key]}
    return ctx.success(data, board_id=board_id)


def label_add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card label add`: labels not yet on the card (AC-18.1)."""
    return _relation(ctx, args, "label", "add")


def label_remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card label remove`: labels that are on the card (AC-18.1)."""
    return _relation(ctx, args, "label", "remove")


def label_set(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card label set`: exactly these labels, as `card update --labels` (AC-18.1)."""
    return _relation(ctx, args, "label", "set")


def member_add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card member add`: members not yet on the card (AC-19.1)."""
    return _relation(ctx, args, "member", "add")


def member_remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card member remove`: members that are on the card (AC-19.1)."""
    return _relation(ctx, args, "member", "remove")


def member_set(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card member set`: exactly these members, as `card update --members` (AC-19.1)."""
    return _relation(ctx, args, "member", "set")

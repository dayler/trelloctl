"""`board show|lists|labels|members` and the cache helpers shared by the `board` group (RF-8).

The reads are served from the cache (metadata for lists, labels and members; the card snapshot
for the card counts) with the TTL policy of the resolver. The write-through helpers keep the
cache in step with what Trello answered without renewing any TTL (AC-22.4, AC-22.11).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver
from trelloctl.domain.errors import TrelloctlError

Row = dict[str, Any]


# -- cache metadata of a read ---------------------------------------------------------------


def combined_cache_meta(
    resolver: Resolver, board_id: str, collections: Sequence[str]
) -> dict[str, Any]:
    """`meta.cache` of a read that used several collections: all hit, oldest age, any stale."""
    parts = [resolver.cache_meta(board_id, collection) for collection in collections]
    meta: dict[str, Any] = {
        "hit": all(part["hit"] for part in parts),
        "age_seconds": max(part["age_seconds"] for part in parts),
        "stale": any(part["stale"] for part in parts),
    }
    for part in parts:
        if "error" in part:
            meta["error"] = part["error"]
            break
    return meta


# -- write-through helpers (shared with lists.py and labels.py) --------------------------------


def list_json(row: Mapping[str, Any]) -> Row:
    return {
        "id": row["id"],
        "name": row["name"],
        "pos": row["pos"],
        "closed": bool(row["closed"]),
    }


def label_json(row: Mapping[str, Any]) -> Row:
    return {"id": row["id"], "name": row["name"], "color": row["color"]}


def _member_json(row: Mapping[str, Any]) -> Row:
    return {"id": row["id"], "username": row["username"], "fullName": row["full_name"]}


def store_metadata(
    resolver: Resolver,
    board_id: str,
    *,
    lists: Iterable[Mapping[str, Any]] | None = None,
    labels: Iterable[Mapping[str, Any]] | None = None,
) -> None:
    """Replace the cached lists and/or labels (Trello JSON) keeping the metadata TTL stamp."""
    cache = resolver.cache
    board = cache.get_board(board_id)
    if board is None or board["metadata_fetched_at"] is None:  # pragma: no cover - ensured earlier
        return
    stamp = datetime.fromtimestamp(float(board["metadata_fetched_at"]), UTC)
    cache.replace_metadata(
        board_id,
        {
            "name": board["name"],
            "url": board["url"],
            "shortLink": board["short_link"],
            "kind": board["kind"],
        },
        [list_json(r) for r in cache.list_lists(board_id)] if lists is None else list(lists),
        [label_json(r) for r in cache.list_labels(board_id)] if labels is None else list(labels),
        [_member_json(r) for r in cache.list_members(board_id)],
        stamp,
    )


def _card_json(
    cache_row: Mapping[str, Any], attachments: Sequence[Row], checklists: Sequence[Row]
) -> Row:
    return {
        "id": cache_row["id"],
        "shortLink": cache_row["short_link"],
        "idShort": cache_row["id_short"],
        "name": cache_row["name"],
        "desc": cache_row["desc"],
        "idList": cache_row["id_list"],
        "idLabels": list(cache_row["id_labels"]),
        "idMembers": list(cache_row["id_members"]),
        "due": cache_row["due"],
        "dueComplete": bool(cache_row["due_complete"]),
        "pos": cache_row["pos"],
        "closed": bool(cache_row["archived"]),
        "url": cache_row["url"],
        "dateLastActivity": cache_row["date_last_activity"],
        "badges": {
            "comments": cache_row["badge_comments"],
            "attachments": cache_row["badge_attachments"],
            "checkItems": cache_row["badge_check_items"],
            "checkItemsChecked": cache_row["badge_check_items_checked"],
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
            for a in attachments
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
            for c in checklists
        ],
    }


def strip_label_from_cards(resolver: Resolver, board_id: str, label_id: str) -> int:
    """Remove `label_id` from the cached cards that carry it; returns how many were rewritten.

    Each card is stored again with its own `fetched_at`, keeping its attachments, checklists
    and downloaded attachment paths.
    """
    cache = resolver.cache
    changed = 0
    for row in cache.list_cards(board_id, include_archived=True):
        if label_id not in row["id_labels"]:
            continue
        row["id_labels"] = [x for x in row["id_labels"] if x != label_id]
        card = _card_json(
            row,
            cache.list_attachments(board_id, row["id"]),
            cache.list_checklists(board_id, row["id"]),
        )
        cache.upsert_card(board_id, card, resolver.now())
        changed += 1
    return changed


# -- reads ------------------------------------------------------------------------------------


def _is_default(resolver: Resolver, board_id: str) -> bool:
    default = resolver.ctx.settings.board_default.strip()
    if not default:
        return False
    try:
        return resolver.resolve_board(default) == board_id
    except TrelloctlError:
        return False


def show(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Board id, shortLink, name, url, whether it is the default board, and the counts."""
    fresh = bool(args.get("fresh"))
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, fresh=fresh)
        resolver.ensure_cards(board_id, fresh=fresh)
        cache = resolver.cache
        row = cache.get_board(board_id) or {}
        data = {
            "id": board_id,
            "short_link": row.get("short_link"),
            "name": row.get("name"),
            "url": row.get("url"),
            "is_default": _is_default(resolver, board_id),
            "lists": sum(1 for r in cache.list_lists(board_id) if not r["closed"]),
            "cards": len(cache.list_cards(board_id)),
            "labels": len(cache.list_labels(board_id)),
        }
        meta = {"cache": combined_cache_meta(resolver, board_id, ("metadata", "cards"))}
    return ctx.success(data, board_id=board_id, meta=meta)


def lists(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Open lists by position with a 0-based index and their card count; `--all` adds archived."""
    fresh = bool(args.get("fresh"))
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, fresh=fresh)
        resolver.ensure_cards(board_id, fresh=fresh)
        counts = Counter(card["id_list"] for card in resolver.cache.list_cards(board_id))
        items: list[Row] = []
        index = 0
        for row in resolver.cache.list_lists(board_id):
            closed = bool(row["closed"])
            if closed and not args.get("all"):
                continue
            items.append(
                {
                    "index": None if closed else index,
                    "id": row["id"],
                    "name": row["name"],
                    "closed": closed,
                    "cards": counts.get(row["id"], 0),
                }
            )
            index += 0 if closed else 1
        meta = {"cache": combined_cache_meta(resolver, board_id, ("metadata", "cards"))}
    return ctx.success({"lists": items}, board_id=board_id, meta=meta)


def labels(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Every label of the board with its id, name (possibly empty) and color."""
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, fresh=bool(args.get("fresh")))
        items = [
            {"id": r["id"], "name": r["name"] or "", "color": r["color"]}
            for r in resolver.cache.list_labels(board_id)
        ]
        meta = {"cache": combined_cache_meta(resolver, board_id, ("metadata",))}
    return ctx.success({"labels": items}, board_id=board_id, meta=meta)


def members(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Every member of the board with its id, username and full name."""
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, fresh=bool(args.get("fresh")))
        items = [
            {"id": r["id"], "username": r["username"], "full_name": r["full_name"] or ""}
            for r in resolver.cache.list_members(board_id)
        ]
        meta = {"cache": combined_cache_meta(resolver, board_id, ("metadata",))}
    return ctx.success({"members": items}, board_id=board_id, meta=meta)

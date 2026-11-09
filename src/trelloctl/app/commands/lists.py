"""`board list` commands: create, rename, archive, move and the bulk card operations (RF-9)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trelloctl.app.commands.board import list_json, store_metadata
from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver, ambiguous, not_found
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import TrelloList
from trelloctl.domain.mover import (
    compute_shift,
    open_lists,
    parse_index,
    position_at_index,
    to_trello_pos,
)

Row = dict[str, Any]
_TARGETS = ("to_index", "left", "right")


def _name(args: Mapping[str, Any]) -> str:
    name = args.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TrelloctlError("USAGE", "The list name must not be empty.")
    return name


def _summary(row: Mapping[str, Any]) -> Row:
    return {"id": row["id"], "name": row["name"], "closed": bool(row["closed"])}


def _merge(resolver: Resolver, board_id: str, updated: Any, fallback_id: str) -> Row:
    """Write the list Trello returned into the cached lists; returns the merged list."""
    rows = [list_json(r) for r in resolver.cache.list_lists(board_id)]
    current = next((r for r in rows if r["id"] == fallback_id), None)
    base: Row = dict(current) if current else {"id": fallback_id}
    if isinstance(updated, dict):
        base.update({k: updated[k] for k in ("name", "pos", "closed") if k in updated})
        base["closed"] = bool(base.get("closed"))
    if current is None:
        rows.append(base)
    else:
        rows = [base if r["id"] == fallback_id else r for r in rows]
    store_metadata(resolver, board_id, lists=rows)
    return base


def _find_list(resolver: Resolver, board_id: str, ref: str, *, closed: bool) -> Row:
    """A list by id or name; for names, the ones in the wanted state come first (AC-7.2)."""
    value = ref.strip()
    synced = resolver.ensure_metadata(board_id, write=True)
    for attempt in (0, 1):
        rows = resolver.cache.list_lists(board_id)
        lowered = value.lower()
        by_id = [r for r in rows if r["id"] == lowered]
        named = [r for r in rows if (r["name"] or "").lower() == lowered]
        wanted = [r for r in named if bool(r["closed"]) == closed]
        matches = by_id or wanted or named
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise ambiguous("list", value, [_summary(m) for m in matches])
        if attempt == 0 and not synced:
            resolver.sync_metadata(board_id)
            synced = True
            continue
        break
    raise not_found("list", value, board=board_id)


def create(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Create a list, at the bottom by default or at a 0-based index among the open lists."""
    name = _name(args)
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, write=True)
        positions = [
            float(r["pos"]) for r in resolver.cache.list_lists(board_id) if not r["closed"]
        ]
        pos = to_trello_pos(args.get("pos"), positions)
        created = resolver.client.post("/lists", {"name": name, "idBoard": board_id, "pos": pos})
        if not isinstance(created, dict) or not created.get("id"):
            raise TrelloctlError(
                "API_ERROR", "Trello returned an unexpected list.", {"board": board_id}
            )
        merged = _merge(resolver, board_id, created, str(created["id"]))
        ordered = open_lists([_trello_list(r) for r in resolver.cache.list_lists(board_id)])
        index = next((i for i, item in enumerate(ordered) if item.id == merged["id"]), None)
    data = {**_summary(merged), "pos": merged.get("pos"), "index": index}
    return ctx.success({"list": data}, board_id=board_id)


def rename(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Rename a list (`PUT /lists/{id}`)."""
    name = _name(args)
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        row, updated = resolver.guarded_write(
            "list",
            board_id,
            str(args["list"]),
            lambda found: (found, resolver.client.put(f"/lists/{found['id']}", {"name": name})),
        )
        merged = _merge(resolver, board_id, updated, row["id"])
    return ctx.success({"list": _summary(merged)}, board_id=board_id)


def _set_closed(ctx: CommandContext, args: Mapping[str, Any], closed: bool) -> dict[str, Any]:
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        found = _find_list(resolver, board_id, str(args["list"]), closed=closed)
        updated = resolver.guarded_write(
            "list",
            board_id,
            found["id"],
            lambda row: resolver.client.put(
                f"/lists/{row['id']}/closed", {"value": "true" if closed else "false"}
            ),
        )
        merged = _merge(resolver, board_id, updated, found["id"])
        merged["closed"] = closed
    return ctx.success({"list": _summary(merged)}, board_id=board_id)


def archive(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Archive a list; its cards are not archived and Trello hides them with it (AC-9.3)."""
    return _set_closed(ctx, args, True)


def unarchive(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Restore an archived list."""
    return _set_closed(ctx, args, False)


def _trello_list(row: Mapping[str, Any]) -> TrelloList:
    return TrelloList(
        id=str(row["id"]),
        name=row["name"] or "",
        pos=float(row["pos"] or 0),
        closed=bool(row["closed"]),
    )


def _out_of_range(from_index: int | None, to_index: int, count: int) -> TrelloctlError:
    return TrelloctlError(
        "MOVE_OUT_OF_RANGE",
        "The target position is outside the open lists.",
        {"from_index": from_index, "to_index": to_index, "list_count": count},
    )


def _requested(args: Mapping[str, Any]) -> tuple[str, Any]:
    given = [key for key in _TARGETS if args.get(key) is not None]
    if len(given) != 1:
        raise TrelloctlError(
            "USAGE",
            "Give exactly one of --to-index, --left and --right.",
            {"options": given},
        )
    return given[0], args[given[0]]


def _plan(
    rows: list[Row], list_id: str, kind: str, value: Any, clamp: bool
) -> tuple[list[TrelloList], int, int, bool]:
    """`(open lists, from_index, to_index, clamped)` for the requested move (AC-14.2, A-21)."""
    lists = [_trello_list(r) for r in rows]
    if kind != "to_index":
        shift = compute_shift(lists, list_id, "left" if kind == "left" else "right", value, clamp)
        wanted = shift.from_index + (value if kind == "right" else -value)
        return open_lists(lists), shift.from_index, shift.to_index, wanted != shift.to_index
    index = parse_index(value)
    ordered = open_lists(lists)
    from_index = next((i for i, item in enumerate(ordered) if item.id == list_id), None)
    if from_index is None:
        raise _out_of_range(None, index, len(ordered))
    if index >= len(ordered):
        if not clamp:
            raise _out_of_range(from_index, index, len(ordered))
        return ordered, from_index, len(ordered) - 1, True
    return ordered, from_index, index, False


def move(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Move a list among the open lists: `--to-index N`, `--left N` or `--right N` (AC-9.4)."""
    kind, value = _requested(args)
    clamp = bool(args.get("clamp"))
    dry_run = bool(args.get("dry_run"))
    ref = str(args["list"])
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()

        def run(row: Row) -> Row:
            fresh = resolver.fresh_metadata(board_id)
            if not any(r["id"] == row["id"] for r in fresh):
                raise not_found("list", ref, board=board_id)
            ordered, from_index, to_index, clamped = _plan(fresh, row["id"], kind, value, clamp)
            result: Row = {
                "list": {"id": row["id"], "name": row["name"]},
                "from_index": from_index,
                "to_index": to_index,
                "list_count": len(ordered),
                "target_list": {"id": ordered[to_index].id, "name": ordered[to_index].name},
                "clamped": clamped,
                "dry_run": dry_run,
                "moved": False,
            }
            if dry_run or to_index == from_index:
                return result
            others = [item.pos for item in ordered if item.id != row["id"]]
            updated = resolver.client.put(
                f"/lists/{row['id']}", {"pos": position_at_index(others, to_index)}
            )
            _merge(resolver, board_id, updated, row["id"])
            result["moved"] = True
            return result

        if dry_run:
            result = run(resolver.resolve_list(board_id, ref))
        else:
            result = resolver.guarded_write("list", board_id, ref, run)
    return ctx.success(result, board_id=board_id)


def _open_cards_of(resolver: Resolver, board_id: str, list_id: str) -> int:
    return sum(1 for card in resolver.cache.list_cards(board_id) if card["id_list"] == list_id)


def move_cards(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Move every open card of a list to another list (`POST /lists/{id}/moveAllCards`)."""
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        target = resolver.resolve_list(board_id, str(args["to"]), write=True)
        resolver.ensure_cards(board_id, write=True)

        def run(source: Row) -> Row:
            if source["id"] == target["id"]:
                raise TrelloctlError(
                    "USAGE", "The source and the target list are the same.", {"list": source["id"]}
                )
            count = _open_cards_of(resolver, board_id, source["id"])
            resolver.client.post(
                f"/lists/{source['id']}/moveAllCards",
                {"idBoard": board_id, "idList": target["id"]},
            )
            resolver.cache.mark_cards_stale(board_id)
            return {
                "from": {"id": source["id"], "name": source["name"]},
                "to": {"id": target["id"], "name": target["name"]},
                "moved": count,
            }

        data = resolver.guarded_write("list", board_id, str(args["list"]), run)
    return ctx.success(data, board_id=board_id)


def archive_cards(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Archive every open card of a list (`POST /lists/{id}/archiveAllCards`)."""
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, write=True)
        resolver.ensure_cards(board_id, write=True)

        def run(source: Row) -> Row:
            count = _open_cards_of(resolver, board_id, source["id"])
            resolver.client.post(f"/lists/{source['id']}/archiveAllCards")
            resolver.cache.mark_cards_stale(board_id)
            return {"list": {"id": source["id"], "name": source["name"]}, "archived": count}

        data = resolver.guarded_write("list", board_id, str(args["list"]), run)
    return ctx.success(data, board_id=board_id)

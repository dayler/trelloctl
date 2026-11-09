"""`card checklist` commands: checklists and their items (RF-20, AC-7.2, AC-7.4, AC-7.5, A-3, A-22).

Checklists and items live in the cache with their card. Reads are served from it; every write
sends ids, applies the result to the cached card (checklists and the `badges.checkItems*`
counters) and returns the complete checklist, or the deleted ids (AC-20.7).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from trelloctl.app.commands.card import (
    Row,
    _check_pos,
    _checklists_json,
    _ensure_fresh_card,
    _exclusive,
    _read_file,
    _Session,
    _session,
    _store,
    _usage,
)
from trelloctl.app.commands.context import CommandContext
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.mover import to_trello_pos
from trelloctl.infra.trello_client import QueryValue

COMPLETE = "complete"
INCOMPLETE = "incomplete"


# --- argument handling (no network) ---------------------------------------------------------


def _clean_name(raw: Any, option: str = "--name") -> str:
    name = str(raw).strip()
    if not name:
        raise _usage(f"{option} must not be empty.")
    return name


def _initial_items(args: Mapping[str, Any]) -> list[str]:
    """`--items a,b,c` or `--items-file` (one per line); empty entries are ignored (A-22)."""
    _exclusive(args, "items", "items_file")
    if args.get("items_file") is not None:
        raw = _read_file(str(args["items_file"])).splitlines()
    else:
        raw = [str(i) for i in args.get("items") or []]
    return [text for text in (line.strip() for line in raw) if text]


# --- presentation and write-through ---------------------------------------------------------


def _present(checklist: Mapping[str, Any]) -> dict[str, Any]:
    items = [
        {"id": i["id"], "name": i["name"], "state": i["state"], "pos": i["pos"]}
        for i in sorted(checklist["checkItems"], key=lambda x: (float(x["pos"] or 0), x["id"]))
    ]
    checked = sum(1 for i in items if i["state"] == COMPLETE)
    return {
        "id": checklist["id"],
        "name": checklist["name"],
        "pos": checklist["pos"],
        "progress": f"{checked}/{len(items)}",
        "checked": checked,
        "total": len(items),
        "items": items,
    }


def _commit(
    s: _Session, board_id: str, card_id: str, change: Callable[[list[Row]], None]
) -> list[Row]:
    """Apply `change` to the cached checklists of the card and store them with new badges."""
    checklists = _checklists_json(s.cache, board_id, card_id)
    change(checklists)
    old = s.cache.get_card(board_id, card_id)
    if old is None:  # pragma: no cover - the card was just located
        raise TrelloctlError("API_ERROR", "The card is not in the cache.", {"card": card_id})
    items = [i for c in checklists for i in c["checkItems"]]
    badges = {
        "comments": old["badge_comments"] or 0,
        "attachments": old["badge_attachments"] or 0,
        "checkItems": len(items),
        "checkItemsChecked": sum(1 for i in items if i["state"] == COMPLETE),
    }
    _store(s, board_id, {"id": card_id, "checklists": checklists, "badges": badges}, old)
    return checklists


def _item_json(data: Any, default_name: str, default_state: str) -> Row:
    raw = data if isinstance(data, Mapping) else {}
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or default_name,
        "state": raw.get("state") or default_state,
        "pos": raw.get("pos"),
    }


def _find(checklists: list[Row], checklist_id: str) -> Row:
    return next(c for c in checklists if c["id"] == checklist_id)


def _find_item(checklists: list[Row], item_id: str) -> Row | None:
    return next((i for c in checklists for i in c["checkItems"] if i["id"] == item_id), None)


Change = Callable[[list[Row]], None]


def _append_checklist(created: Row) -> Change:
    def change(lists: list[Row]) -> None:
        lists.append(created)

    return change


def _drop_checklist(checklist_id: str) -> Change:
    def change(lists: list[Row]) -> None:
        lists.remove(_find(lists, checklist_id))

    return change


def _patch_checklist(checklist_id: str, **fields: Any) -> Change:
    def change(lists: list[Row]) -> None:
        _find(lists, checklist_id).update(fields)

    return change


def _append_item(checklist_id: str, item: Row) -> Change:
    def change(lists: list[Row]) -> None:
        _find(lists, checklist_id)["checkItems"].append(item)

    return change


def _patch_item(item_id: str, **fields: Any) -> Change:
    def change(lists: list[Row]) -> None:
        found = _find_item(lists, item_id)
        if found is not None:
            found.update(fields)

    return change


def _drop_item(item_id: str) -> Change:
    def change(lists: list[Row]) -> None:
        for checklist in lists:
            checklist["checkItems"] = [i for i in checklist["checkItems"] if i["id"] != item_id]

    return change


def _position(raw: str | None, siblings: Sequence[Row]) -> str | float | None:
    """Trello `pos` for `--pos`; an index outside 0..N is `USAGE` before any write (A-3)."""
    if raw is None or raw in ("top", "bottom"):
        return raw
    return to_trello_pos(raw, [float(r["pos"] or 0) for r in siblings])


class _Target:
    """The card of a write and its board, located once."""

    def __init__(self, s: _Session, ref: str, *, write: bool = True) -> None:
        located = s.resolver.locate_card(ref, write=write)
        self.board_id = located.board_id
        self.row: Row = located.card
        self.id: str = located.card["id"]


# --- reads ----------------------------------------------------------------------------------


def list_checklists(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist list`: checklists with progress and items, from the cache (AC-20.1)."""
    with _session(ctx) as s:
        located = s.resolver.locate_card(str(args["card"]))
        board_id, row = located.board_id, located.card
        if args.get("fresh"):
            data = s.resolver.sync_card(row["id"], board_id=board_id)
            board_id = str(data["idBoard"])
            row = s.cache.get_card(board_id, row["id"]) or row
        else:
            row = _ensure_fresh_card(s, board_id, row)
        checklists = [_present(c) for c in _checklists_json(s.cache, board_id, row["id"])]
        meta = {"cache": s.read_meta(board_id, "cards")}
    data_out = {"card": {"id": row["id"], "name": row["name"]}, "checklists": checklists}
    return ctx.success(data_out, board_id=board_id, meta=meta)


# --- checklist writes -----------------------------------------------------------------------


def add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist add`: a checklist, then its initial items one by one (AC-20.2)."""
    name = _clean_name(args["name"])
    items = _initial_items(args)
    pos = _check_pos(args.get("pos"))
    ref = str(args["card"])
    with _session(ctx) as s:
        target = _Target(s, ref)
        board_id = target.board_id
        if pos is not None and pos not in ("top", "bottom"):
            _ensure_fresh_card(s, board_id, target.row)
        position = _position(pos, s.cache.list_checklists(board_id, target.id))

        def create(card: Row) -> Any:
            params: dict[str, QueryValue] = {"name": name, "pos": position}
            return s.resolver.client.post(f"/cards/{card['id']}/checklists", params)

        data = s.resolver.guarded_write("card", board_id, ref, create)
        if not isinstance(data, Mapping) or not data.get("id"):
            raise TrelloctlError("API_ERROR", "Trello returned an unexpected checklist.", {})
        checklist_id = str(data["id"])
        created: Row = {
            "id": checklist_id,
            "name": data.get("name") or name,
            "pos": data.get("pos"),
            "checkItems": [],
        }
        _commit(s, board_id, target.id, _append_checklist(created))
        try:
            for text in items:
                posted = s.resolver.client.post(
                    f"/checklists/{checklist_id}/checkItems", {"name": text}
                )
                added = _item_json(posted, text, INCOMPLETE)
                _commit(s, board_id, target.id, _append_item(checklist_id, added))
        finally:
            final = _find(_checklists_json(s.cache, board_id, target.id), checklist_id)
    return ctx.success(_present(final), board_id=board_id)


def rename(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist rename`: a new name for the checklist (AC-20.3)."""
    name = _clean_name(args["name"])
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id

        def call(checklist: Row) -> Any:
            return s.resolver.client.put(f"/checklists/{checklist['id']}", {"name": name})

        resolved = s.resolver.resolve_checklist(board_id, target.id, str(args["checklist"]))
        s.resolver.guarded_write(
            "checklist", board_id, str(args["checklist"]), call, card_id=target.id
        )
        lists = _commit(s, board_id, target.id, _patch_checklist(resolved["id"], name=name))
    return ctx.success(_present(_find(lists, resolved["id"])), board_id=board_id)


def remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist remove`: delete the checklist with its items (AC-20.3, AC-16.4)."""
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id
        resolved = s.resolver.resolve_checklist(board_id, target.id, str(args["checklist"]))

        def call(checklist: Row) -> Any:
            return s.resolver.client.delete(f"/checklists/{checklist['id']}")

        s.resolver.guarded_write(
            "checklist", board_id, str(args["checklist"]), call, card_id=target.id
        )
        _commit(s, board_id, target.id, _drop_checklist(resolved["id"]))
    data = {"deleted": resolved["id"], "name": resolved["name"], "card": target.id}
    return ctx.success(data, board_id=board_id)


# --- item writes ----------------------------------------------------------------------------


def _checklist_result(
    ctx: CommandContext, board_id: str, lists: list[Row], checklist_id: str, **extra: Any
) -> dict[str, Any]:
    data = {**_present(_find(lists, checklist_id)), **extra}
    return ctx.success(data, board_id=board_id)


def item_add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist item add`: an item, optionally at `--pos` and already checked (AC-20.4)."""
    name = _clean_name(args["name"])
    pos = _check_pos(args.get("pos"))
    ref = str(args["checklist"])
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id
        if pos is not None and pos not in ("top", "bottom"):
            _ensure_fresh_card(s, board_id, target.row)
        resolved = s.resolver.resolve_checklist(board_id, target.id, ref)
        position = _position(pos, resolved["items"])
        checked = bool(args.get("checked"))

        def call(checklist: Row) -> Any:
            params: dict[str, QueryValue] = {
                "name": name,
                "pos": position,
                "checked": True if checked else None,
            }
            return s.resolver.client.post(f"/checklists/{checklist['id']}/checkItems", params)

        data = s.resolver.guarded_write("checklist", board_id, ref, call, card_id=target.id)
        added = _item_json(data, name, COMPLETE if checked else INCOMPLETE)
        lists = _commit(s, board_id, target.id, _append_item(resolved["id"], added))
    return _checklist_result(ctx, board_id, lists, resolved["id"], added=added["id"])


def _resolve_items(
    s: _Session, board_id: str, card_id: str, checklist_ref: str, refs: Sequence[str]
) -> tuple[Row, list[tuple[str, Row]]]:
    """The checklist and its items for `refs`, resolved before anything is written."""
    checklist: Row | None = None
    found: dict[str, tuple[str, Row]] = {}
    for item_ref in refs:
        checklist, item = s.resolver.resolve_checkitem(board_id, card_id, checklist_ref, item_ref)
        found.setdefault(item["id"], (item_ref, item))
    if checklist is None:
        raise _usage("Give at least one item.")
    return checklist, list(found.values())


def _set_state(ctx: CommandContext, args: Mapping[str, Any], state: str) -> dict[str, Any]:
    refs = [str(i) for i in args.get("items") or []]
    if refs and args.get("all"):
        raise _usage("Give items or --all, not both.")
    if not refs and not args.get("all"):
        raise _usage("Give the items to change, or --all.")
    checklist_ref = str(args["checklist"])
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id
        if args.get("all"):
            checklist = s.resolver.resolve_checklist(board_id, target.id, checklist_ref)
            chosen = [(str(i["id"]), i) for i in checklist["items"]]
        else:
            checklist, chosen = _resolve_items(s, board_id, target.id, checklist_ref, refs)
        changed: list[str] = []
        for item_ref, item in chosen:
            if item["state"] == state:
                continue

            def call(entity: Row) -> Any:
                return s.resolver.client.put(
                    f"/cards/{target.id}/checkItem/{entity['id']}", {"state": state}
                )

            s.resolver.guarded_write(
                "checkitem",
                board_id,
                item_ref,
                call,
                card_id=target.id,
                checklist=checklist_ref,
            )
            _commit(s, board_id, target.id, _patch_item(item["id"], state=state))
            changed.append(item["id"])
        lists = _checklists_json(s.cache, board_id, target.id)
    return _checklist_result(ctx, board_id, lists, checklist["id"], changed=changed)


def item_check(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist item check`: mark items (or `--all`) as complete (AC-20.5)."""
    return _set_state(ctx, args, COMPLETE)


def item_uncheck(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist item uncheck`: mark items (or `--all`) as incomplete (AC-20.5)."""
    return _set_state(ctx, args, INCOMPLETE)


def item_rename(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist item rename`: a new name for an item (AC-20.6)."""
    name = _clean_name(args["name"])
    checklist_ref, item_ref = str(args["checklist"]), str(args["item"])
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id
        checklist, item = s.resolver.resolve_checkitem(board_id, target.id, checklist_ref, item_ref)

        def call(entity: Row) -> Any:
            return s.resolver.client.put(
                f"/cards/{target.id}/checkItem/{entity['id']}", {"name": name}
            )

        s.resolver.guarded_write(
            "checkitem", board_id, item_ref, call, card_id=target.id, checklist=checklist_ref
        )
        lists = _commit(s, board_id, target.id, _patch_item(item["id"], name=name))
    return _checklist_result(ctx, board_id, lists, checklist["id"])


def item_remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """`card checklist item remove`: delete items; their ids are returned (AC-20.6, AC-16.4)."""
    refs = [str(i) for i in args["items"]]
    checklist_ref = str(args["checklist"])
    with _session(ctx) as s:
        target = _Target(s, str(args["card"]))
        board_id = target.board_id
        checklist, chosen = _resolve_items(s, board_id, target.id, checklist_ref, refs)
        deleted: list[str] = []
        for item_ref, item in chosen:

            def call(entity: Row) -> Any:
                return s.resolver.client.delete(
                    f"/checklists/{entity['checklist_id']}/checkItems/{entity['id']}"
                )

            s.resolver.guarded_write(
                "checkitem", board_id, item_ref, call, card_id=target.id, checklist=checklist_ref
            )
            _commit(s, board_id, target.id, _drop_item(item["id"]))
            deleted.append(item["id"])
        lists = _checklists_json(s.cache, board_id, target.id)
    return _checklist_result(ctx, board_id, lists, checklist["id"], deleted=deleted)

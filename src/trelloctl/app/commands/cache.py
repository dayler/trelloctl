"""`cache` commands: reload, status and clear (RF-22, AC-22.6, AC-22.15, A-4)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver, utc_now
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra import paths
from trelloctl.infra.cache import Cache


def _open_cache(ctx: CommandContext) -> Cache:
    return Cache.memory() if ctx.no_cache else Cache.open(ctx.settings.home)


def _age(value: float | None) -> int | None:
    return None if value is None else max(0, int(value))


def reload(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Reload the cache from Trello: the whole board, one list, one card or the archived cards."""
    chosen = [name for name in ("list", "card", "archived") if args.get(name)]
    if len(chosen) > 1:
        raise TrelloctlError(
            "USAGE",
            "cache reload accepts only one of --list, --card and --archived.",
            {"options": chosen},
        )
    with Resolver(ctx) as resolver:
        if args.get("card"):
            board_id, data = resolver.reload_card(str(args["card"]))
        else:
            board_id = resolver.selected_board()
            if args.get("list"):
                data = resolver.reload_list(board_id, str(args["list"]))
            elif args.get("archived"):
                data = resolver.reload_archived(board_id)
            else:
                data = resolver.reload_all(board_id)
    return ctx.success(data, board_id=board_id)


def _board_status(board: Mapping[str, Any]) -> dict[str, Any]:
    metadata = board["metadata"]
    return {
        "board_id": board["board_id"],
        "name": board["name"],
        "kind": board["kind"],
        "metadata": {
            "age_seconds": _age(metadata["age_seconds"]),
            "lists": metadata["lists"],
            "labels": metadata["labels"],
            "members": metadata["members"],
        },
        "cards": {
            "age_seconds": _age(board["cards"]["age_seconds"]),
            "stale": board["cards"]["stale"],
            "count": board["cards"]["count"],
        },
        "archived": {
            "age_seconds": _age(board["archived"]["age_seconds"]),
            "count": board["archived"]["count"],
        },
    }


def _database_bytes(ctx: CommandContext) -> int:
    if ctx.no_cache:
        return 0
    base = ctx.settings.home / paths.CACHE_DB
    names = (base, base.with_name(base.name + "-wal"), base.with_name(base.name + "-shm"))
    return sum(path.stat().st_size for path in names if path.is_file())


def status(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Age and size of every cached snapshot per board, and the space used by attachments."""
    cache = _open_cache(ctx)
    try:
        now = utc_now()
        stats = cache.stats(now)
        member_boards = cache.member_boards()
        stamps = [row["fetched_at"] for row in member_boards if row["fetched_at"] is not None]
        data = {
            "boards": [_board_status(board) for board in stats["boards"]],
            "member_boards": {
                "count": len(member_boards),
                "age_seconds": _age(now.timestamp() - min(stamps)) if stamps else None,
            },
            "attachments_bytes": stats["attachments_bytes"],
            "database_bytes": _database_bytes(ctx),
        }
    finally:
        if ctx.no_cache:
            cache.close()
    return ctx.success(data)


def clear(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Empty `cache.db`; downloaded attachments are kept unless `--attachments` is given."""
    with_attachments = bool(args.get("attachments"))
    cache = _open_cache(ctx)
    try:
        removed = cache.clear(attachments=with_attachments)
    finally:
        if ctx.no_cache:
            cache.close()
    return ctx.success(
        {
            "cleared": True,
            "attachments_removed": removed["attachments_removed"],
            "attachments_kept": not with_attachments,
        }
    )

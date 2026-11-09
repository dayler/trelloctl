"""`board label` commands: create, update and remove (RF-10)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from trelloctl.app.commands.board import label_json, store_metadata, strip_label_from_cards
from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver
from trelloctl.domain.errors import TrelloctlError

Row = dict[str, Any]

BASE_COLORS = (
    "green",
    "yellow",
    "orange",
    "red",
    "purple",
    "blue",
    "sky",
    "lime",
    "pink",
    "black",
)
COLORS: tuple[str, ...] = (
    *BASE_COLORS,
    *(f"{color}_{shade}" for color in BASE_COLORS for shade in ("light", "dark")),
    "none",
)


def api_color(value: Any) -> str:
    """The `color` parameter for Trello; `none` is sent as `null` (a label without color)."""
    if not isinstance(value, str) or value not in COLORS:
        raise TrelloctlError(
            "USAGE",
            "Invalid label color; use one of the Trello colors (with an optional _light or "
            "_dark suffix) or none.",
            {"color": value, "valid": list(COLORS)},
        )
    return "null" if value == "none" else value


def _stored(updated: Any, fallback: Mapping[str, Any]) -> Row:
    """The label as cached after a write: Trello's answer over the previous values."""
    base = label_json(fallback)
    if isinstance(updated, dict):
        base.update({k: updated[k] for k in ("name", "color") if k in updated})
    return base


def _replace(resolver: Resolver, board_id: str, label: Row) -> None:
    rows = [label_json(r) for r in resolver.cache.list_labels(board_id)]
    if any(r["id"] == label["id"] for r in rows):
        rows = [label if r["id"] == label["id"] else r for r in rows]
    else:
        rows.append(label)
    store_metadata(resolver, board_id, labels=rows)


def _shown(label: Mapping[str, Any]) -> Row:
    return {"id": label["id"], "name": label["name"] or "", "color": label["color"]}


def create(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Create a label with a name and a color (`POST /labels`)."""
    color = api_color(args.get("color"))
    name = args.get("name")
    if not isinstance(name, str):
        raise TrelloctlError("USAGE", "The label name is required.")
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_metadata(board_id, write=True)
        created = resolver.client.post(
            "/labels", {"name": name, "color": color, "idBoard": board_id}
        )
        if not isinstance(created, dict) or not created.get("id"):
            raise TrelloctlError(
                "API_ERROR", "Trello returned an unexpected label.", {"board": board_id}
            )
        label = _stored(created, {"id": created["id"], "name": name, "color": None})
        _replace(resolver, board_id, label)
    return ctx.success({"label": _shown(label)}, board_id=board_id)


def update(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Change only the fields given: `--name`, `--color` (`PUT /labels/{id}`)."""
    params: dict[str, str] = {}
    if args.get("color") is not None:
        params["color"] = api_color(args["color"])
    if args.get("name") is not None:
        params["name"] = str(args["name"])
    if not params:
        raise TrelloctlError("USAGE", "Give at least one of --name and --color.")
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        found, updated = resolver.guarded_write(
            "label",
            board_id,
            str(args["label"]),
            lambda row: (row, resolver.client.put(f"/labels/{row['id']}", params)),
        )
        label = _stored(updated, found)
        _replace(resolver, board_id, label)
    return ctx.success({"label": _shown(label)}, board_id=board_id)


def remove(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Delete a label from the board and from all its cards (`DELETE /labels/{id}`)."""
    with Resolver(ctx) as resolver:
        board_id = resolver.selected_board()
        resolver.ensure_cards(board_id, write=True)

        def run(row: Row) -> tuple[Row, int]:
            carrying = sum(
                1
                for card in resolver.cache.list_cards(board_id, include_archived=True)
                if row["id"] in card["id_labels"]
            )
            resolver.client.delete(f"/labels/{row['id']}")
            return row, carrying

        found, carrying = resolver.guarded_write("label", board_id, str(args["label"]), run)
        remaining = [
            label_json(r) for r in resolver.cache.list_labels(board_id) if r["id"] != found["id"]
        ]
        store_metadata(resolver, board_id, labels=remaining)
        strip_label_from_cards(resolver, board_id, found["id"])
    return ctx.success(
        {"label": _shown(label_json(found)), "removed": True, "cards_affected": carrying},
        board_id=board_id,
    )

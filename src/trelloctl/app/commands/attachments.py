"""`card attachments` commands: list, download, add and clear (RF-21, A-4, A-9, A-11, A-15).

Attachment metadata arrives with the card snapshot; files are downloaded only on request,
into `<HOME>/attachments/<board_id>/<card_id>/<attachment_id>_<name>`, and have no TTL.
Every file written or deleted here lives under `attachments/`; deletions go through
`paths.delete_owned_file`, so nothing outside that directory is ever removed.
"""

from __future__ import annotations

import mimetypes
import os
import re
import uuid
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver, not_found, utc_now
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import classify_card_ref
from trelloctl.infra import paths
from trelloctl.infra.cache import Cache
from trelloctl.infra.settings import select_board

Row = dict[str, Any]

_RESERVED = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_DOTS = re.compile(r"\.{2,}")
_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_MAX_STEM = 100
_MAX_SUFFIX = 16


# -- file names and paths ---------------------------------------------------------------------


def safe_filename(attachment_id: str, name: str | None) -> str:
    """`<attachment_id>_<name>` with separators, `..` and reserved characters replaced (A-9)."""
    cleaned = _RESERVED.sub("_", name or "")
    cleaned = _DOTS.sub("_", cleaned).rstrip(". ").strip()
    if not cleaned:
        cleaned = "attachment"
    stem, suffix = os.path.splitext(cleaned)
    cleaned = stem[:_MAX_STEM] + suffix[:_MAX_SUFFIX]
    return f"{attachment_id}_{cleaned}"


def card_directory(home: Path, board_id: str, card_id: str) -> Path:
    return home / paths.ATTACHMENTS_DIR / board_id / card_id


def _prune_empty(home: Path, directory: Path) -> None:
    """Remove `directory` and its parents while they are empty, never `attachments/` itself."""
    root = (home / paths.ATTACHMENTS_DIR).resolve()
    current = directory.resolve()
    while current != root and root in current.parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _delete_local(home: Path, local_path: str | None) -> bool:
    """Delete a downloaded file; paths outside `attachments/` are never touched."""
    if not local_path:
        return False
    target = Path(local_path)
    try:
        removed = paths.delete_owned_file(home, target)
    except (paths.PathNotAllowedError, OSError):
        return False
    with suppress(OSError):
        _prune_empty(home, target.parent)
    return removed


def _write_file(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{uuid.uuid4().hex}.part")
    try:
        partial.write_bytes(content)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


# -- views ------------------------------------------------------------------------------------


def _view(row: Mapping[str, Any]) -> Row:
    return {
        "id": row["id"],
        "name": row.get("name"),
        "bytes": row.get("bytes"),
        "mimeType": row.get("mime_type"),
        "date": row.get("date"),
        "url": row.get("url"),
        "isUpload": bool(row.get("is_upload")),
        "local_path": row.get("local_path"),
    }


def _view_json(att: Mapping[str, Any], local_path: str | None = None) -> Row:
    """An attachment as returned by Trello, in the shape of `_view`."""
    return {
        "id": att["id"],
        "name": att.get("name"),
        "bytes": att.get("bytes"),
        "mimeType": att.get("mimeType"),
        "date": att.get("date"),
        "url": att.get("url"),
        "isUpload": bool(att.get("isUpload")),
        "local_path": local_path,
    }


def _card_summary(row: Mapping[str, Any]) -> Row:
    return {"id": row["id"], "name": row.get("name"), "idShort": row.get("id_short")}


# -- argument checks --------------------------------------------------------------------------


def _text(args: Mapping[str, Any], name: str) -> str | None:
    value = args.get(name)
    return None if value is None or value == "" else str(value)


def _card_ref(args: Mapping[str, Any]) -> str:
    ref = _text(args, "card")
    if ref is None:
        raise TrelloctlError("USAGE", "A card (id, URL, shortLink, idShort or name) is required.")
    return ref


# -- list -------------------------------------------------------------------------------------


def list_attachments(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Attachments of a card from the cache; `--fresh` reads the card from Trello first."""
    ref = _card_ref(args)
    fresh = bool(args.get("fresh"))
    with Resolver(ctx) as resolver:
        located = resolver.locate_card(ref)
        board_id, card_id = located.board_id, str(located.card["id"])
        if fresh:
            resolver.sync_card(card_id, board_id=board_id)
            meta_cache: dict[str, Any] = {"hit": False, "age_seconds": 0, "stale": False}
        else:
            resolver.ensure_cards(board_id)
            meta_cache = resolver.cache_meta(board_id)
        card = resolver.cache.get_card(board_id, card_id) or located.card
        rows = resolver.cache.list_attachments(board_id, card_id)
    return ctx.success(
        {"card": _card_summary(card), "attachments": [_view(r) for r in rows]},
        board_id=board_id,
        meta={"cache": meta_cache},
    )


# -- download ---------------------------------------------------------------------------------


class _Report:
    """Accumulates the four categories of a download."""

    def __init__(self) -> None:
        self.downloaded: list[Row] = []
        self.kept: list[Row] = []
        self.skipped: list[Row] = []
        self.deleted: list[Row] = []

    def as_dict(self) -> dict[str, list[Row]]:
        return {
            "downloaded": self.downloaded,
            "kept": self.kept,
            "skipped": self.skipped,
            "deleted": self.deleted,
        }


def _current_copy(old: Mapping[str, Any] | None, new: Mapping[str, Any]) -> str | None:
    """Path of the local copy when it is current: bytes and date match and the file exists."""
    if old is None or not old.get("local_path"):
        return None
    if old.get("bytes") != new.get("bytes") or old.get("date") != new.get("date"):
        return None
    local = Path(str(old["local_path"]))
    if not local.is_file():
        return None
    size = new.get("bytes")
    if isinstance(size, int) and local.stat().st_size != size:
        return None
    return str(local)


def _fetch(
    resolver: Resolver, board_id: str, card_id: str, att: Mapping[str, Any], report: _Report
) -> None:
    url = str(att.get("url") or "")
    content = resolver.client.download(url)
    home = resolver.ctx.settings.home
    target = card_directory(home, board_id, card_id) / safe_filename(
        str(att["id"]), att.get("name")
    )
    _write_file(target, content)
    resolver.cache.set_attachment_local_path(
        board_id, card_id, str(att["id"]), str(target), utc_now()
    )
    report.downloaded.append(
        {
            "id": att["id"],
            "name": att.get("name"),
            "bytes": len(content),
            "local_path": str(target),
        }
    )


def _skip(att: Mapping[str, Any], report: _Report) -> None:
    report.skipped.append(
        {"id": att["id"], "name": att.get("name"), "url": att.get("url"), "reason": "link"}
    )


def _check_download_args(args: Mapping[str, Any]) -> str | None:
    attachment = _text(args, "id")
    everything = bool(args.get("all"))
    if everything == (attachment is not None):
        raise TrelloctlError(
            "USAGE",
            "Give exactly one of --all or --id.",
            {"options": ["--all", "--id"]},
        )
    return attachment


def download(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Download the files of a card (`--all`, synchronizing with Trello) or one (`--id`)."""
    wanted = _check_download_args(args)
    ref = _card_ref(args)
    report = _Report()
    with Resolver(ctx) as resolver:
        located = resolver.locate_card(ref)
        board_id, card_id = located.board_id, str(located.card["id"])
        old = {str(r["id"]): r for r in resolver.cache.list_attachments(board_id, card_id)}
        card = resolver.sync_card(card_id, board_id=board_id)
        current: list[Row] = [a for a in card.get("attachments") or [] if isinstance(a, dict)]
        by_id = {str(a["id"]): a for a in current}
        if wanted is not None:
            _download_one(resolver, board_id, card_id, wanted.lower(), old, by_id, report)
        else:
            _download_all(resolver, board_id, card_id, old, current, report)
    data: dict[str, Any] = {
        "card": {"id": card_id, "name": card.get("name"), "idShort": card.get("idShort")},
        **report.as_dict(),
    }
    return ctx.success(data, board_id=board_id)


def _download_one(
    resolver: Resolver,
    board_id: str,
    card_id: str,
    wanted: str,
    old: Mapping[str, Row],
    by_id: Mapping[str, Row],
    report: _Report,
) -> None:
    att = by_id.get(wanted)
    if att is None:
        previous = old.get(wanted)
        if previous is None:
            raise not_found_attachment(wanted, card_id)
        removed = _delete_local(resolver.ctx.settings.home, previous.get("local_path"))
        raise TrelloctlError(
            "ATTACHMENT_GONE",
            f"Attachment {wanted!r} no longer exists in Trello.",
            {"attachment": wanted, "card": card_id, "local_copy_deleted": removed},
        )
    if not att.get("isUpload"):
        _skip(att, report)
        return
    _fetch(resolver, board_id, card_id, att, report)


def not_found_attachment(value: str, card_id: str) -> TrelloctlError:
    return TrelloctlError(
        "ATTACHMENT_NOT_FOUND",
        f"Attachment {value!r} was not found on the card.",
        {"attachment": value, "card": card_id},
    )


def _download_all(
    resolver: Resolver,
    board_id: str,
    card_id: str,
    old: Mapping[str, Row],
    current: Sequence[Row],
    report: _Report,
) -> None:
    home = resolver.ctx.settings.home
    present = {str(a["id"]) for a in current}
    for att_id, previous in old.items():
        if att_id in present or not previous.get("local_path"):
            continue
        if _delete_local(home, previous["local_path"]):
            report.deleted.append({"id": att_id, "local_path": previous["local_path"]})
    for att in current:
        if not att.get("isUpload"):
            _skip(att, report)
            continue
        kept = _current_copy(old.get(str(att["id"])), att)
        if kept is not None:
            report.kept.append({"id": att["id"], "name": att.get("name"), "local_path": kept})
            continue
        try:
            _fetch(resolver, board_id, card_id, att, report)
        except TrelloctlError as exc:
            if exc.code == "RATE_LIMITED":
                exc.details["partial"] = report.as_dict()
            raise


# -- add --------------------------------------------------------------------------------------


def _read_upload(path_text: str, limit: int) -> tuple[Path, bytes]:
    path = Path(path_text).expanduser()
    try:
        if not path.is_file():
            raise TrelloctlError(
                "USAGE",
                f"--file {path_text!r} does not exist or is not a file.",
                {"file": path_text},
            )
        size = path.stat().st_size
        if size > limit:
            raise TrelloctlError(
                "ATTACHMENT_TOO_LARGE",
                f"{path.name} is {size} bytes; attachments.max_upload_bytes is {limit}.",
                {"file": path_text, "bytes": size, "max_upload_bytes": limit},
            )
        return path, path.read_bytes()
    except OSError as exc:
        raise TrelloctlError(
            "USAGE", f"--file {path_text!r} cannot be read: {exc.strerror}.", {"file": path_text}
        ) from exc


def add(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Upload a file (multipart) or attach a link to a card."""
    ref = _card_ref(args)
    file_arg, url_arg, name = _text(args, "file"), _text(args, "url"), _text(args, "name")
    if (file_arg is None) == (url_arg is None):
        raise TrelloctlError(
            "USAGE", "Give exactly one of --file or --url.", {"options": ["--file", "--url"]}
        )
    upload: tuple[Path, bytes] | None = None
    if file_arg is not None:
        upload = _read_upload(file_arg, int(ctx.settings.max_upload_bytes))
    with Resolver(ctx) as resolver:
        located = resolver.locate_card(ref, write=True)
        board_id = located.board_id

        def create(row: Row) -> Any:
            endpoint = f"/cards/{row['id']}/attachments"
            if upload is None:
                return resolver.client.post(endpoint, {"url": url_arg, "name": name})
            path, content = upload
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return resolver.client.upload(
                endpoint, filename=path.name, content=content, name=name, content_type=kind
            )

        created = resolver.guarded_write("card", board_id, ref, create)
        if not isinstance(created, dict) or not created.get("id"):
            raise TrelloctlError(
                "API_ERROR", "Trello returned an unexpected attachment.", {"card": ref}
            )
        card_id = str(created.get("idCard") or located.card["id"])
        resolver.sync_card(card_id, board_id=board_id)
        stored = next(
            (
                r
                for r in resolver.cache.list_attachments(board_id, card_id)
                if r["id"] == created["id"]
            ),
            None,
        )
        card = resolver.cache.get_card(board_id, card_id) or located.card
    view = _view(stored) if stored is not None else _view_json(created)
    return ctx.success({"card": _card_summary(card), "attachment": view}, board_id=board_id)


# -- clear ------------------------------------------------------------------------------------


def _locate_cached(ctx: CommandContext, cache: Cache, ref: str) -> tuple[str, Row]:
    """Find a card in the cache without calling Trello (A-4); `CARD_NOT_FOUND` if absent."""
    value = ref.strip()
    candidates = classify_card_ref(value)
    first = candidates[0]
    boards = [str(b["board_id"]) for b in cache.list_boards()]
    if len(candidates) == 1 and first.kind in ("id", "short_link"):
        for board_id in boards:
            if first.kind == "id":
                row = cache.get_card(board_id, first.value.lower())
            else:
                row = next(
                    (
                        r
                        for r in cache.list_cards(board_id, include_archived=True)
                        if r["short_link"] == first.value
                    ),
                    None,
                )
            if row is not None:
                return board_id, row
        raise not_found("card", value)
    selected = select_board(ctx.settings, ctx.board_flag)
    if _ID.match(selected):
        board_id = selected.lower()
    else:
        with Resolver(ctx, cache=cache) as resolver:
            board_id = resolver.resolve_board(selected)
    for candidate in candidates:
        for row in cache.list_cards(board_id, include_archived=True):
            if candidate.kind == "id_short" and row["id_short"] == int(candidate.value):
                return board_id, row
            if candidate.kind == "short_link" and row["short_link"] == candidate.value:
                return board_id, row
            if (
                candidate.kind == "name"
                and isinstance(row["name"], str)
                and row["name"].lower() == candidate.value.lower()
            ):
                return board_id, row
    raise not_found("card", value, board=board_id)


def clear(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Delete the downloaded files of a card (or one attachment); never calls Trello."""
    ref = _card_ref(args)
    wanted = _text(args, "id")
    cache = Cache.memory() if ctx.no_cache else Cache.open(ctx.settings.home)
    try:
        board_id, card = _locate_cached(ctx, cache, ref)
        card_id = str(card["id"])
        rows = cache.list_attachments(board_id, card_id)
        if wanted is not None:
            rows = [r for r in rows if r["id"] == wanted.lower()]
            if not rows:
                raise not_found_attachment(wanted, card_id)
        cleared: list[Row] = []
        home = ctx.settings.home
        for row in rows:
            local = row.get("local_path")
            if not local:
                continue
            _delete_local(home, local)
            cache.set_attachment_local_path(board_id, card_id, str(row["id"]), None, utc_now())
            cleared.append({"id": row["id"], "local_path": local})
        _prune_empty(home, card_directory(home, board_id, card_id))
    finally:
        if ctx.no_cache:
            cache.close()
    return ctx.success(
        {"card": _card_summary(card), "cleared": cleared, "count": len(cleared)},
        board_id=board_id,
    )

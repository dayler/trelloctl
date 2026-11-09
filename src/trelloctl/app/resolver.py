"""Synchronization with Trello and identifier resolution (PRD 10.3, 10.7, RF-7, RF-22).

`Resolver` is the application service that talks to both the cache (`infra/cache.py`) and
Trello (`infra/trello_client.py`). It owns:

- the three kinds of reload: complete (`reload_all`), incremental (`modifiedSince`) and
  partial (`reload_list`, `reload_card`, `reload_archived`), with the TTL rules of AC-22.4;
- the TTL policy of reads (`ensure_*`) including the stale fallback of AC-22.10;
- the resolution of every entity from an id or a human identifier, with one reload and one
  retry on a miss (AC-22.12), `AMBIGUOUS_*` candidates (AC-7.4) and `*_NOT_FOUND` (AC-7.5);
- the handling of stale references on writes (`guarded_write`, AC-22.13);
- the location of a card without a selected board (`locate_card`, A-15);
- `cache_meta`, the `meta.cache` object of every read (AC-22.8).

Commands never print: they call this service and return their envelope as data. With
`--no-cache` the service uses the in-memory backend, so `cache.db` is neither read nor written.
Every time-dependent decision uses the injectable `clock`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from trelloctl.app.commands.context import CommandContext
from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import Candidate, classify_board_ref, classify_card_ref
from trelloctl.infra.cache import Cache, is_expired
from trelloctl.infra.settings import select_board
from trelloctl.infra.trello_client import TrelloClient

T = TypeVar("T")
Row = dict[str, Any]

PAGE_SIZE = 1000
CARD_PARAMS: dict[str, str] = {"attachments": "true", "checklists": "all"}
BOARD_PARAMS: dict[str, str] = {
    "fields": "name,url,shortLink,closed",
    "lists": "all",
    "list_fields": "name,pos,closed",
    "labels": "all",
    "label_fields": "name,color",
    "members": "all",
    "member_fields": "username,fullName",
}
MEMBER_BOARDS_PARAMS: dict[str, str] = {"fields": "name,shortLink,closed", "filter": "all"}

_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_LABEL_COLORS = frozenset(
    {"green", "yellow", "orange", "red", "purple", "blue", "sky", "lime", "pink", "black"}
)
_COLOR_SUFFIX = re.compile(r"^(?P<base>[a-z]+)(?:_(?:dark|light))?$")
_NOT_FOUND_CODES = {
    "board": "BOARD_NOT_FOUND",
    "list": "LIST_NOT_FOUND",
    "card": "CARD_NOT_FOUND",
    "label": "LABEL_NOT_FOUND",
    "member": "MEMBER_NOT_FOUND",
    "checklist": "CHECKLIST_NOT_FOUND",
    "checkitem": "CHECKITEM_NOT_FOUND",
    "comment": "COMMENT_NOT_FOUND",
}
_AMBIGUOUS_CODES = {
    "board": "AMBIGUOUS_BOARD",
    "list": "AMBIGUOUS_LIST",
    "card": "AMBIGUOUS_CARD",
    "label": "AMBIGUOUS_LABEL",
    "member": "AMBIGUOUS_MEMBER",
    "checklist": "AMBIGUOUS_CHECKLIST",
    "checkitem": "AMBIGUOUS_CHECKITEM",
}
# Where each entity can be read to confirm that it still exists (AC-22.13).
_CONFIRM_PATHS = {
    "list": "/lists/{id}",
    "label": "/labels/{id}",
    "member": "/members/{id}",
    "card": "/cards/{id}",
    "checklist": "/checklists/{id}",
    "checkitem": "/checklists/{checklist_id}/checkItems/{id}",
}
_NOT_FOUND_STATUSES = (400, 401, 403, 404)


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(moment: datetime) -> str:
    """ISO-8601 UTC with milliseconds and a `Z` suffix, as sent in `modifiedSince`."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def not_found(entity: str, value: str, **details: Any) -> TrelloctlError:
    """`<ENTITY>_NOT_FOUND` carrying the value received."""
    return TrelloctlError(
        _NOT_FOUND_CODES[entity],
        f"{entity.capitalize()} {value!r} was not found.",
        {entity: value, **details},
    )


def ambiguous(entity: str, value: str, candidates: Sequence[Row]) -> TrelloctlError:
    """`AMBIGUOUS_<ENTITY>` with `details.candidates` (id, name and context)."""
    return TrelloctlError(
        _AMBIGUOUS_CODES[entity],
        f"{entity.capitalize()} {value!r} matches {len(candidates)} {entity}s; use the id.",
        {entity: value, "candidates": list(candidates)},
    )


def is_transient(exc: TrelloctlError) -> bool:
    """Failures after which a read may be served from stale data (AC-22.10)."""
    if exc.code in ("NETWORK_ERROR", "TIMEOUT", "RATE_LIMITED"):
        return True
    status = exc.details.get("status")
    return exc.code == "API_ERROR" and isinstance(status, int) and status >= 500


def looks_stale(exc: TrelloctlError) -> bool:
    """A 404, or a 400 `invalid id`, on an id: the entity may no longer exist (AC-22.13)."""
    if exc.code != "API_ERROR":
        return False
    status = exc.details.get("status")
    if status == 404:
        return True
    return status == 400 and "invalid id" in str(exc.details.get("upstream", "")).lower()


def split_label(ref: str) -> tuple[str, str] | None:
    """`name:color` as `(name, color)` when the suffix is a Trello color, else `None`."""
    name, sep, color = ref.rpartition(":")
    if not sep:
        return None
    found = _COLOR_SUFFIX.match(color.lower())
    if found is None or found.group("base") not in _LABEL_COLORS:
        return None
    return name, color.lower()


def _lower(value: Any) -> str:
    return value.lower() if isinstance(value, str) else ""


def _error_summary(exc: TrelloctlError) -> dict[str, Any]:
    return {"code": exc.code, "message": exc.message, "details": exc.details}


@dataclass(frozen=True)
class LocatedCard:
    """A card found without a prior board context: its board and its cached row."""

    board_id: str
    card: Row


class Resolver:
    """Cache-backed access to Trello data for one invocation (or one MCP session)."""

    def __init__(
        self,
        ctx: CommandContext,
        *,
        clock: Callable[[], datetime] | None = None,
        cache: Cache | None = None,
        client: TrelloClient | None = None,
    ) -> None:
        self.ctx = ctx
        self._clock = clock or utc_now
        self._owns_cache = cache is None and ctx.no_cache
        if cache is not None:
            self.cache = cache
        elif ctx.no_cache:
            self.cache = Cache.memory()
        else:
            self.cache = Cache.open(ctx.settings.home)
        self._client = client
        self._synced: set[tuple[str, str]] = set()
        self._stale: dict[tuple[str, str], dict[str, Any]] = {}
        self._boards: dict[str, str] = {}

    # -- plumbing ------------------------------------------------------------------------

    def close(self) -> None:
        """Release the in-memory backend of `--no-cache`; the disk cache is process-wide."""
        if self._owns_cache:
            self.cache.close()

    def __enter__(self) -> Resolver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def now(self) -> datetime:
        return self._clock()

    @property
    def client(self) -> TrelloClient:
        if self._client is None:
            self._client = self.ctx.client()
        return self._client

    @property
    def metadata_ttl(self) -> int:
        return int(self.ctx.settings.metadata_ttl_seconds)

    @property
    def cards_ttl(self) -> int:
        return int(self.ctx.settings.cards_ttl_seconds)

    def _expired(self, stamp: float | None, ttl: int) -> bool:
        return is_expired(stamp, self.now(), ttl)

    def _board_row(self, board_id: str) -> Row:
        return self.cache.get_board(board_id) or {}

    def _guarded(
        self,
        key: tuple[str, str],
        load: Callable[[], object],
        *,
        has_data: bool,
        strict: bool,
    ) -> bool:
        """Run a TTL-driven reload; a transient failure with cached data serves it stale.

        `strict` (writes and `--fresh`) never falls back to stale data, and without data the
        original error is raised (A-10). Returns whether the collection was synchronized.
        """
        try:
            load()
        except TrelloctlError as exc:
            if strict or not has_data or not is_transient(exc):
                raise
            self._stale[key] = _error_summary(exc)
            self.ctx.logger.warning("stale cache served: %s", exc.code)
            return False
        return True

    # -- meta.cache ----------------------------------------------------------------------

    def cache_meta(self, board_id: str | None = None, collection: str = "cards") -> dict[str, Any]:
        """`meta.cache` of a read: `hit`, `age_seconds`, `stale` and, if stale, `error`.

        `collection` is `cards`, `metadata`, `archived` or `member_boards`. `hit` is false when
        the read synchronized with Trello (or used no cache); `stale` is true when a reload
        failed and cached data was served (AC-22.10).
        """
        meta: dict[str, Any] = {"hit": False, "age_seconds": 0, "stale": False}
        if not self.ctx.no_cache:
            key = (board_id or "", collection)
            stamp = self._stamp(board_id, collection)
            if stamp is not None and key not in self._synced:
                meta["hit"] = True
                meta["age_seconds"] = max(0, int(self.now().timestamp() - stamp))
        if self._stale:
            meta["stale"] = True
            meta["error"] = next(iter(self._stale.values()))
        return meta

    def _stamp(self, board_id: str | None, collection: str) -> float | None:
        if collection == "member_boards":
            rows = self.cache.member_boards()
            return min((r["fetched_at"] for r in rows), default=None)
        if board_id is None:
            return None
        row = self._board_row(board_id)
        column = {
            "cards": "cards_fetched_at",
            "metadata": "metadata_fetched_at",
            "archived": "archived_fetched_at",
        }[collection]
        value = row.get(column)
        return None if value is None else float(value)

    # -- low-level synchronization (always talks to Trello) -------------------------------

    def _get_board_json(self, ref: str, params: Mapping[str, str]) -> Row:
        try:
            data = self.client.get(f"/boards/{ref}", params)
        except TrelloctlError as exc:
            if exc.code == "API_ERROR" and exc.details.get("status") in _NOT_FOUND_STATUSES:
                raise not_found("board", ref) from exc
            raise
        if not isinstance(data, dict) or not data.get("id"):
            raise TrelloctlError(
                "API_ERROR", "Trello returned an unexpected board.", {"board": ref}
            )
        return data

    def _paginate(self, path: str, params: Mapping[str, str]) -> tuple[list[Row], int]:
        """All pages of a card listing, `PAGE_SIZE` at a time, paginating with `before`."""
        cards: list[Row] = []
        before: str | None = None
        pages = 0
        while True:
            query: dict[str, str | int | None] = {**params, "limit": PAGE_SIZE, "before": before}
            data = self.client.get(path, query)
            page = [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
            pages += 1
            cards.extend(page)
            if len(page) < PAGE_SIZE:
                return cards, pages
            before = str(page[-1]["id"])

    def sync_member_boards(self) -> list[Row]:
        """`GET /members/me/boards` replacing `member_boards`; renews its TTL."""
        data = self.client.get("/members/me/boards", MEMBER_BOARDS_PARAMS)
        boards = [b for b in data if isinstance(b, dict) and b.get("id")]
        self.cache.replace_member_boards(boards, self.now())
        self._synced.add(("", "member_boards"))
        return boards

    def sync_metadata(self, board_ref: str) -> str:
        """The complete metadata call of AC-22.6 (lists, labels, members); returns the board id.

        Cards of lists that vanished are dropped from the cache (A-17).
        """
        board = self._get_board_json(board_ref, BOARD_PARAMS)
        board_id = str(board["id"])
        self.cache.replace_metadata(
            board_id,
            board,
            board.get("lists") or [],
            board.get("labels") or [],
            board.get("members") or [],
            self.now(),
        )
        self._synced.add((board_id, "metadata"))
        return board_id

    def sync_cards_full(self, board_id: str) -> int:
        """Replace the open-cards snapshot with `cards/open` (paginated); renews its TTL."""
        cards, _ = self._paginate(f"/boards/{board_id}/cards/open", CARD_PARAMS)
        self.cache.replace_cards(board_id, cards, self.now())
        self._synced.add((board_id, "cards"))
        return len(cards)

    def sync_cards_incremental(self, board_id: str) -> int:
        """Apply the cards changed since the last reload (`cards/all?modifiedSince`).

        Open and archived changes are applied; hard deletions are not detected (AC-22.5).
        Renews the snapshot TTL and clears the stale flag; `cards_fetched_at` becomes the
        moment the request started.
        """
        since = self._board_row(board_id).get("cards_fetched_at")
        if since is None:
            return self.sync_cards_full(board_id)
        started = self.now()
        params = {**CARD_PARAMS, "modifiedSince": iso_utc(datetime.fromtimestamp(since, UTC))}
        cards, _ = self._paginate(f"/boards/{board_id}/cards/all", params)
        for card in cards:
            self.cache.upsert_card(board_id, card, self.now())
        self._renew_cards_stamp(board_id, started)
        self._synced.add((board_id, "cards"))
        return len(cards)

    def _renew_cards_stamp(self, board_id: str, moment: datetime) -> None:
        with self.cache._tx() as conn:
            conn.execute(
                "UPDATE boards SET cards_fetched_at = ?, cards_stale = 0 WHERE board_id = ?",
                (moment.timestamp(), board_id),
            )

    def sync_archived(self, board_id: str) -> int:
        """Replace the archived cards with `cards/closed`; renews `archived_fetched_at`."""
        cards, _ = self._paginate(f"/boards/{board_id}/cards/closed", CARD_PARAMS)
        self.cache.replace_archived(board_id, cards, self.now())
        self._synced.add((board_id, "archived"))
        return len(cards)

    def sync_card(self, ident: str, *, board_id: str | None = None) -> Row:
        """`GET /cards/{id}` with attachments and checklists, stored with its own `fetched_at`.

        The card goes to the cache of its own board (`idBoard`). A 404 or `invalid id` becomes
        `CARD_NOT_FOUND`; the id is then marked deleted for `board_id` when it is known.
        """
        try:
            data = self.client.get(f"/cards/{ident}", CARD_PARAMS)
        except TrelloctlError as exc:
            if looks_stale(exc):
                if board_id is not None and _ID.match(ident):
                    self.cache.mark_card_deleted(board_id, ident.lower())
                raise not_found("card", ident) from exc
            raise
        if not isinstance(data, dict) or not data.get("id") or not data.get("idBoard"):
            raise TrelloctlError(
                "API_ERROR", "Trello returned an unexpected card.", {"card": ident}
            )
        self.cache.upsert_card(str(data["idBoard"]), data, self.now())
        return data

    # -- TTL-driven synchronization (reads) -----------------------------------------------

    def ensure_member_boards(self, *, write: bool = False, fresh: bool = False) -> bool:
        """Reload the user's boards when absent or expired; returns whether Trello was read."""
        if not fresh and self.cache.member_boards_fresh(self.now(), self.metadata_ttl):
            return False
        has_data = bool(self.cache.member_boards())
        return self._guarded(
            ("", "member_boards"),
            self.sync_member_boards,
            has_data=has_data,
            strict=write or fresh,
        )

    def ensure_metadata(self, board_id: str, *, write: bool = False, fresh: bool = False) -> bool:
        """Reload lists, labels and members when absent or expired (AC-22.5)."""
        row = self._board_row(board_id)
        stamp = row.get("metadata_fetched_at")
        if not fresh and not self._expired(stamp, self.metadata_ttl):
            return False
        return self._guarded(
            (board_id, "metadata"),
            lambda: self.sync_metadata(board_id),
            has_data=stamp is not None,
            strict=write or fresh,
        )

    def ensure_cards(self, board_id: str, *, write: bool = False, fresh: bool = False) -> bool:
        """Keep the open-cards snapshot within its TTL.

        The first load is complete; later reloads are incremental (`modifiedSince`). A snapshot
        flagged stale reloads even within its TTL (AC-22.7).
        """
        row = self._board_row(board_id)
        stamp = row.get("cards_fetched_at")
        if stamp is None:
            self.sync_cards_full(board_id)
            return True
        if not (fresh or row.get("cards_stale") or self._expired(stamp, self.cards_ttl)):
            return False
        return self._guarded(
            (board_id, "cards"),
            lambda: self.sync_cards_incremental(board_id),
            has_data=True,
            strict=write or fresh,
        )

    def ensure_archived(self, board_id: str, *, write: bool = False, fresh: bool = False) -> bool:
        """Keep the archived-cards snapshot within the cards TTL (only read with archived)."""
        stamp = self._board_row(board_id).get("archived_fetched_at")
        if not fresh and not self._expired(stamp, self.cards_ttl):
            return False
        return self._guarded(
            (board_id, "archived"),
            lambda: self.sync_archived(board_id),
            has_data=stamp is not None,
            strict=write or fresh,
        )

    def fresh_metadata(self, board_id: str) -> list[Row]:
        """Fresh metadata for `card move --left/--right` and `board list move` (AC-22.11).

        Always makes the complete call, even within the TTL, and renews it. Returns the lists.
        """
        self.sync_metadata(board_id)
        return self.cache.list_lists(board_id)

    # -- explicit reloads (`cache reload`) -------------------------------------------------

    def reload_all(self, board_id: str) -> dict[str, Any]:
        """`cache reload`: the user's boards, the metadata and the open cards (AC-22.6).

        The new snapshot replaces the previous one in a single transaction and renews the TTL
        of the three collections.
        """
        if ("", "member_boards") not in self._synced:
            self.sync_member_boards()
        board = self._get_board_json(board_id, BOARD_PARAMS)
        cards, pages = self._paginate(f"/boards/{board_id}/cards/open", CARD_PARAMS)
        self.cache.replace_snapshot(
            board_id,
            board,
            board.get("lists") or [],
            board.get("labels") or [],
            board.get("members") or [],
            cards,
            self.now(),
        )
        for collection in ("metadata", "cards"):
            self._synced.add((board_id, collection))
        return {
            "scope": "full",
            "board": {"id": board_id, "name": board.get("name")},
            "lists": len(board.get("lists") or []),
            "labels": len(board.get("labels") or []),
            "members": len(board.get("members") or []),
            "cards": len(cards),
            "pages": pages,
        }

    def reload_list(self, board_id: str, ref: str) -> dict[str, Any]:
        """`cache reload --list`: only the open cards of one list; no TTL is renewed."""
        found = self.resolve_list(board_id, ref)
        data = self.client.get(f"/lists/{found['id']}/cards", CARD_PARAMS)
        cards = [c for c in data if isinstance(c, dict)] if isinstance(data, list) else []
        self.cache.replace_list_cards(board_id, found["id"], cards, self.now())
        return {
            "scope": "list",
            "list": {"id": found["id"], "name": found["name"]},
            "cards": len(cards),
        }

    def reload_card(self, ref: str) -> tuple[str, dict[str, Any]]:
        """`cache reload --card`: one card with attachments and checklists; no TTL renewed."""
        candidates = classify_card_ref(ref)
        if candidates[0].kind in ("id", "short_link") and len(candidates) == 1:
            ident = candidates[0].value
            selected = self._selected_or_none()
            card = self.sync_card(ident, board_id=selected)
        else:
            board_id = self.selected_board()
            row = self.resolve_card(board_id, ref)
            card = self.sync_card(row["id"], board_id=board_id)
        board_id = str(card["idBoard"])
        return board_id, {
            "scope": "card",
            "card": {"id": card["id"], "name": card.get("name"), "idShort": card.get("idShort")},
        }

    def reload_archived(self, board_id: str) -> dict[str, Any]:
        """`cache reload --archived`: the closed cards, stored with `archived = true`."""
        count = self.sync_archived(board_id)
        return {"scope": "archived", "cards": count}

    # -- board selection and resolution ----------------------------------------------------

    def _selected_ref(self) -> str:
        return self.ctx.board_ref or select_board(self.ctx.settings, self.ctx.board_flag)

    def selected_board(self) -> str:
        """The board of the invocation (`--board` > `TRELLO_BOARD` > default) as an id."""
        return self.resolve_board(self._selected_ref())

    def _selected_or_none(self) -> str | None:
        try:
            return self.selected_board()
        except TrelloctlError as exc:
            if exc.code == "BOARD_NOT_CONFIGURED":
                return None
            raise

    def resolve_board(self, ref: str) -> str:
        """A board id from an id, shortLink, URL or name (AC-1.9, A-14, A-24).

        Ids and URLs make no call to `members/me/boards`; a value of 8 alphanumeric characters
        is searched by name and then by shortLink in the cached boards of the user.
        """
        value = ref.strip()
        if value in self._boards:
            return self._boards[value]
        board_id = self._resolve_board(value)
        self._boards[value] = board_id
        return board_id

    def _resolve_board(self, value: str) -> str:
        candidates = classify_board_ref(value)
        first = candidates[0]
        if first.kind == "id":
            return value.lower()
        if first.kind == "short_link":
            return self._board_id_by_short_link(first.value)
        return self._board_id_by_name(value, candidates)

    def _board_id_by_short_link(self, short_link: str) -> str:
        for row in self.cache.list_boards():
            if row["short_link"] == short_link:
                return str(row["board_id"])
        for member_board in self.cache.member_boards():
            if member_board["short_link"] == short_link:
                return str(member_board["id"])
        return self.sync_metadata(short_link)

    def _board_id_by_name(self, value: str, candidates: Sequence[Candidate]) -> str:
        loaded = self.ensure_member_boards()
        for attempt in (0, 1):
            boards = self.cache.member_boards()
            usable = [b for b in boards if self.ctx.include_archived_boards or not b["closed"]]
            by_name = [b for b in usable if _lower(b["name"]) == value.lower()]
            if len(by_name) > 1:
                raise ambiguous("board", value, [self._describe_board(b) for b in by_name])
            if by_name:
                return str(by_name[0]["id"])
            if any(c.kind == "short_link" for c in candidates):
                by_link = [b for b in boards if b["short_link"] == value]
                if by_link:
                    return str(by_link[0]["id"])
            if attempt == 0 and not loaded:
                self.sync_member_boards()
                loaded = True
                continue
            break
        raise not_found("board", value)

    @staticmethod
    def _describe_board(row: Row) -> Row:
        return {
            "id": row["id"],
            "name": row["name"],
            "context": "archived" if row["closed"] else "open",
        }

    # -- entities of the board metadata ----------------------------------------------------

    def resolve_list(self, board_id: str, ref: str, *, write: bool = False) -> Row:
        """A list (cache row) from an id or a name without case distinction (AC-7.2)."""
        return self._resolve_metadata("list", board_id, ref, write)

    def resolve_label(self, board_id: str, ref: str, *, write: bool = False) -> Row:
        """A label from an id, a name or `name:color`."""
        return self._resolve_metadata("label", board_id, ref, write)

    def resolve_member(self, board_id: str, ref: str, *, write: bool = False) -> Row:
        """A member from an id, a username or a full name."""
        return self._resolve_metadata("member", board_id, ref, write)

    @staticmethod
    def resolve_comment(ref: str) -> str:
        """Comments are identified only by their action id; text is never matched."""
        if not _ID.match(ref.strip()):
            raise not_found("comment", ref)
        return ref.strip().lower()

    def _resolve_metadata(self, entity: str, board_id: str, ref: str, write: bool) -> Row:
        value = ref.strip()
        synced = self.ensure_metadata(board_id, write=write)
        for attempt in (0, 1):
            matches = self._metadata_matches(entity, board_id, value)
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise ambiguous(
                    entity, value, [self._describe(entity, board_id, m) for m in matches]
                )
            if attempt == 0 and not synced and (board_id, "metadata") not in self._synced:
                self.sync_metadata(board_id)
                synced = True
                continue
            break
        raise not_found(entity, value, board=board_id)

    def _metadata_matches(self, entity: str, board_id: str, value: str) -> list[Row]:
        lowered = value.lower()
        if entity == "list":
            rows = self.cache.list_lists(board_id)
            by_id = [r for r in rows if r["id"] == lowered]
            return by_id or [r for r in rows if not r["closed"] and _lower(r["name"]) == lowered]
        if entity == "label":
            rows = self.cache.list_labels(board_id)
            by_id = [r for r in rows if r["id"] == lowered]
            if by_id:
                return by_id
            parts = split_label(value)
            if parts is not None:
                name, color = parts
                found = [
                    r for r in rows if _lower(r["name"]) == name.lower() and r["color"] == color
                ]
                if found:
                    return found
            return [r for r in rows if _lower(r["name"]) == lowered]
        rows = self.cache.list_members(board_id)
        by_id = [r for r in rows if r["id"] == lowered]
        if by_id:
            return by_id
        name = lowered.removeprefix("@")
        return [r for r in rows if name in (_lower(r["username"]), _lower(r["full_name"]))]

    def _describe(self, entity: str, board_id: str, row: Row) -> Row:
        if entity == "list":
            return {"id": row["id"], "name": row["name"], "closed": bool(row["closed"])}
        if entity == "label":
            return {"id": row["id"], "name": row["name"], "color": row["color"]}
        if entity == "member":
            return {
                "id": row["id"],
                "name": row["full_name"] or row["username"],
                "username": row["username"],
            }
        lists = {r["id"]: r["name"] for r in self.cache.list_lists(board_id)}
        return {"id": row["id"], "name": row["name"], "list": lists.get(row["id_list"])}

    # -- cards -------------------------------------------------------------------------------

    def resolve_card(self, board_id: str, ref: str, *, write: bool = False) -> Row:
        """A card of `board_id` from an id, shortLink, URL, `idShort` or name (AC-7.3, A-14).

        An id or a URL is looked up in the cache and then fetched with one call (AC-22.9); an
        `idShort`, a name or a bare shortLink needs the snapshot, which is reloaded
        incrementally once on a miss. Ids already marked deleted fail without any request.
        """
        value = ref.strip()
        candidates = classify_card_ref(value)
        first = candidates[0]
        if first.kind == "id":
            return self._card_by_id(board_id, value.lower(), value)
        if first.kind == "short_link":
            return self._card_by_short_link(board_id, first.value, value)
        return self._card_from_snapshot(board_id, candidates, value, write)

    def _card_by_id(self, board_id: str, card_id: str, ref: str) -> Row:
        if self.cache.is_card_deleted(board_id, card_id):
            raise not_found("card", ref)
        row = self.cache.get_card(board_id, card_id)
        if row is not None:
            return row
        return self._store_fetched(board_id, self.sync_card(card_id, board_id=board_id), ref)

    def _card_by_short_link(self, board_id: str, short_link: str, ref: str) -> Row:
        for row in self.cache.list_cards(board_id, include_archived=True):
            if row["short_link"] == short_link:
                return row
        return self._store_fetched(board_id, self.sync_card(short_link), ref)

    def _store_fetched(self, board_id: str, data: Row, ref: str) -> Row:
        if str(data["idBoard"]) != board_id:
            raise not_found("card", ref)
        row = self.cache.get_card(board_id, str(data["id"]))
        if row is None:  # pragma: no cover - upsert_card has just stored it
            raise not_found("card", ref)
        return row

    def _card_from_snapshot(
        self, board_id: str, candidates: Sequence[Candidate], ref: str, write: bool
    ) -> Row:
        synced = self.ensure_cards(board_id, write=write)
        for attempt in (0, 1):
            matches = self._card_matches(board_id, candidates)
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise ambiguous("card", ref, [self._describe("card", board_id, m) for m in matches])
            if attempt == 0 and (board_id, "cards") not in self._synced and not synced:
                self.sync_cards_incremental(board_id)
                synced = True
                continue
            break
        raise not_found("card", ref, board=board_id)

    def _card_matches(self, board_id: str, candidates: Sequence[Candidate]) -> list[Row]:
        for candidate in candidates:
            if candidate.kind == "id_short":
                rows = [
                    r
                    for r in self.cache.list_cards(board_id, include_archived=True)
                    if r["id_short"] == int(candidate.value)
                ]
            elif candidate.kind == "name":
                rows = [
                    r
                    for r in self.cache.list_cards(board_id)
                    if _lower(r["name"]) == candidate.value.lower()
                ]
            else:
                rows = [
                    r
                    for r in self.cache.list_cards(board_id, include_archived=True)
                    if r["short_link"] == candidate.value
                ]
            if rows:
                return rows
        return []

    def locate_card(self, ref: str, *, write: bool = False) -> LocatedCard:
        """Find a card without requiring a selected board (A-15).

        An id or a URL locates the card (cache first, then `GET /cards/{id}` or
        `GET /cards/{shortLink}`) and its board is the context. A bare shortLink, an `idShort`
        or a name are searched in the selected board (`BOARD_NOT_CONFIGURED` without one).
        """
        value = ref.strip()
        candidates = classify_card_ref(value)
        first = candidates[0]
        if len(candidates) == 1 and first.kind in ("id", "short_link"):
            return self._locate_direct(first, value)
        board_id = self.selected_board()
        return LocatedCard(board_id, self.resolve_card(board_id, value, write=write))

    def _locate_direct(self, candidate: Candidate, ref: str) -> LocatedCard:
        selected = self._selected_or_none()
        board_ids = [selected] if selected else []
        board_ids += [
            str(r["board_id"]) for r in self.cache.list_boards() if r["board_id"] != selected
        ]
        is_id = candidate.kind == "id"
        card_id = candidate.value.lower()
        if is_id and any(self.cache.is_card_deleted(b, card_id) for b in board_ids):
            raise not_found("card", ref)
        for board_id in board_ids:
            if is_id:
                row = self.cache.get_card(board_id, card_id)
            else:
                row = next(
                    (
                        r
                        for r in self.cache.list_cards(board_id, include_archived=True)
                        if r["short_link"] == candidate.value
                    ),
                    None,
                )
            if row is not None:
                return LocatedCard(board_id, row)
        data = self.sync_card(candidate.value if not is_id else card_id, board_id=selected)
        board_id = str(data["idBoard"])
        row = self.cache.get_card(board_id, str(data["id"]))
        if row is None:  # pragma: no cover - upsert_card has just stored it
            raise not_found("card", ref)
        return LocatedCard(board_id, row)

    # -- checklists and items ----------------------------------------------------------------

    def resolve_checklist(
        self, board_id: str, card_id: str, ref: str, *, write: bool = False
    ) -> Row:
        """A checklist of a card from an id or a name; a miss reloads that card once."""
        del write  # checklists live in the card rows; there is no TTL to check here
        value = ref.strip()
        for attempt in (0, 1):
            matches = self._checklist_matches(board_id, card_id, value)
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise ambiguous(
                    "checklist", value, [self._describe_check(m, None) for m in matches]
                )
            if attempt == 0 and not self._card_synced(board_id, card_id):
                self._reload_card(board_id, card_id)
                continue
            break
        raise not_found("checklist", value, card=card_id)

    def resolve_checkitem(
        self,
        board_id: str,
        card_id: str,
        checklist_ref: str,
        item_ref: str,
        *,
        write: bool = False,
    ) -> tuple[Row, Row]:
        """A checklist item by id or name inside its checklist; returns `(checklist, item)`."""
        checklist = self.resolve_checklist(board_id, card_id, checklist_ref, write=write)
        value = item_ref.strip()
        for attempt in (0, 1):
            matches = self._item_matches(checklist, value)
            if len(matches) == 1:
                return checklist, matches[0]
            if matches:
                described = [self._describe_check(m, checklist) for m in matches]
                raise ambiguous("checkitem", value, described)
            if attempt == 0 and not self._card_synced(board_id, card_id):
                self._reload_card(board_id, card_id)
                found = self._checklist_matches(board_id, card_id, checklist["id"])
                if found:
                    checklist = found[0]
                continue
            break
        raise not_found("checkitem", value, checklist=checklist["id"])

    def _card_synced(self, board_id: str, card_id: str) -> bool:
        return (board_id, f"card:{card_id}") in self._synced

    def _reload_card(self, board_id: str, card_id: str) -> None:
        self.sync_card(card_id, board_id=board_id)
        self._synced.add((board_id, f"card:{card_id}"))

    def _checklist_matches(self, board_id: str, card_id: str, value: str) -> list[Row]:
        rows = self.cache.list_checklists(board_id, card_id)
        lowered = value.lower()
        return [r for r in rows if r["id"] == lowered] or [
            r for r in rows if _lower(r["name"]) == lowered
        ]

    @staticmethod
    def _item_matches(checklist: Row, value: str) -> list[Row]:
        rows: list[Row] = checklist.get("items", [])
        lowered = value.lower()
        return [r for r in rows if r["id"] == lowered] or [
            r for r in rows if _lower(r["name"]) == lowered
        ]

    @staticmethod
    def _describe_check(row: Row, checklist: Row | None) -> Row:
        out: Row = {"id": row["id"], "name": row["name"]}
        if checklist is not None:
            out["checklist"] = checklist["name"]
        return out

    # -- stale references on writes (AC-22.13) -----------------------------------------------

    def guarded_write(
        self,
        entity: str,
        board_id: str,
        ref: str,
        call: Callable[[Row], T],
        *,
        card_id: str | None = None,
        checklist: str | None = None,
    ) -> T:
        """Resolve `ref` and run `call(row)`, handling a stale id on the write.

        `entity` is `list`, `label`, `member`, `card`, `checklist` or `checkitem` (the last two
        need `card_id`; `checkitem` also the `checklist` reference). When `call` fails with a
        404 or `invalid id`:

        - id obtained from a name: the collection is reloaded, the name resolved again and
          the write retried exactly once;
        - id given by the user: a confirmation read decides. If the entity is gone, the card
          is marked `deleted` and `<ENTITY>_NOT_FOUND` is raised without retrying; if the
          entity exists, the original error of the write is raised (A-22).
        """
        row = self._resolve_entity(entity, board_id, ref, card_id, checklist, write=True)
        try:
            return call(row)
        except TrelloctlError as exc:
            if not looks_stale(exc):
                raise
            if _ID.match(ref.strip()):
                if self._confirm_exists(entity, row):
                    raise
                self._mark_gone(entity, board_id, row)
                raise not_found(entity, ref) from exc
            self._reload_collection(entity, board_id, card_id)
            fresh = self._resolve_entity(entity, board_id, ref, card_id, checklist, write=True)
            try:
                return call(fresh)
            except TrelloctlError as again:
                if looks_stale(again):
                    self._mark_gone(entity, board_id, fresh)
                    raise not_found(entity, ref) from again
                raise

    def _resolve_entity(
        self,
        entity: str,
        board_id: str,
        ref: str,
        card_id: str | None,
        checklist: str | None,
        *,
        write: bool,
    ) -> Row:
        if entity == "card":
            return self.resolve_card(board_id, ref, write=write)
        if entity in ("checklist", "checkitem") and card_id is None:
            raise ValueError(f"{entity} needs card_id")
        if entity == "checklist":
            return self.resolve_checklist(board_id, str(card_id), ref, write=write)
        if entity == "checkitem":
            if checklist is None:
                raise ValueError("checkitem needs the checklist reference")
            _, item = self.resolve_checkitem(board_id, str(card_id), checklist, ref, write=write)
            return item
        return self._resolve_metadata(entity, board_id, ref, write)

    def _reload_collection(self, entity: str, board_id: str, card_id: str | None) -> None:
        if entity == "card":
            self.sync_cards_full(board_id)
        elif entity in ("checklist", "checkitem"):
            self._reload_card(board_id, str(card_id))
        else:
            self.sync_metadata(board_id)

    def _confirm_exists(self, entity: str, row: Row) -> bool:
        path = _CONFIRM_PATHS[entity].format(id=row["id"], checklist_id=row.get("checklist_id"))
        try:
            self.client.get(path, {"fields": "name"})
        except TrelloctlError as exc:
            if looks_stale(exc):
                return False
            raise
        return True

    def _mark_gone(self, entity: str, board_id: str, row: Row) -> None:
        if entity == "card":
            self.cache.mark_card_deleted(board_id, str(row["id"]))

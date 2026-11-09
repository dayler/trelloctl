"""SQLite cache of Trello data (`cache.db`) and its in-memory twin for `--no-cache`.

The store keeps rows and timestamps only. Reloading from Trello, name resolution and the
`cache *` commands live in upper layers. Every function that evaluates freshness receives the
current time as a parameter (injectable clock).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from trelloctl.infra import paths

SCHEMA_VERSION = "1"
BUSY_TIMEOUT_MS = 5000

_DDL = (
    "CREATE TABLE IF NOT EXISTS schema (key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE IF NOT EXISTS member_boards (
        id TEXT PRIMARY KEY, name TEXT, short_link TEXT, closed INTEGER, fetched_at REAL)""",
    """CREATE TABLE IF NOT EXISTS boards (
        board_id TEXT PRIMARY KEY, name TEXT, url TEXT, short_link TEXT,
        kind TEXT NOT NULL DEFAULT 'board',
        metadata_fetched_at REAL, cards_fetched_at REAL,
        cards_stale INTEGER NOT NULL DEFAULT 0, archived_fetched_at REAL)""",
    """CREATE TABLE IF NOT EXISTS lists (
        board_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT, pos REAL, closed INTEGER,
        PRIMARY KEY (board_id, id))""",
    """CREATE TABLE IF NOT EXISTS labels (
        board_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT, color TEXT,
        PRIMARY KEY (board_id, id))""",
    """CREATE TABLE IF NOT EXISTS members (
        board_id TEXT NOT NULL, id TEXT NOT NULL, username TEXT, full_name TEXT,
        PRIMARY KEY (board_id, id))""",
    """CREATE TABLE IF NOT EXISTS cards (
        board_id TEXT NOT NULL, id TEXT NOT NULL, short_link TEXT, id_short INTEGER,
        name TEXT, desc TEXT, id_list TEXT, id_labels TEXT, id_members TEXT, due TEXT,
        due_complete INTEGER, pos REAL, archived INTEGER NOT NULL DEFAULT 0,
        deleted INTEGER NOT NULL DEFAULT 0, url TEXT, date_last_activity TEXT,
        badge_comments INTEGER, badge_attachments INTEGER, badge_check_items INTEGER,
        badge_check_items_checked INTEGER, fetched_at REAL,
        PRIMARY KEY (board_id, id))""",
    """CREATE TABLE IF NOT EXISTS attachments (
        board_id TEXT NOT NULL, card_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT,
        bytes INTEGER, mime_type TEXT, date TEXT, url TEXT, is_upload INTEGER,
        local_path TEXT, downloaded_at REAL,
        PRIMARY KEY (board_id, card_id, id))""",
    """CREATE TABLE IF NOT EXISTS checklists (
        board_id TEXT NOT NULL, card_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT, pos REAL,
        PRIMARY KEY (board_id, card_id, id))""",
    """CREATE TABLE IF NOT EXISTS checkitems (
        board_id TEXT NOT NULL, checklist_id TEXT NOT NULL, id TEXT NOT NULL, name TEXT,
        state TEXT, pos REAL,
        PRIMARY KEY (board_id, checklist_id, id))""",
)

PER_BOARD_TABLES = (
    "lists",
    "labels",
    "members",
    "cards",
    "attachments",
    "checklists",
    "checkitems",
)

_REGISTRY: dict[Path, Cache] = {}
_REGISTRY_LOCK = threading.Lock()


def is_expired(fetched_at: float | None, now: datetime, ttl_seconds: int | float) -> bool:
    """A datum expires when its age is greater than or equal to its TTL (A-8)."""
    if fetched_at is None:
        return True
    return now.timestamp() - fetched_at >= ttl_seconds


def _unicode_lower(value: Any) -> Any:
    return value.lower() if isinstance(value, str) else value


def _flag(value: Any) -> int:
    return 1 if value else 0


def _dump(value: Any) -> str:
    return json.dumps(value if value is not None else [], ensure_ascii=False)


def _load_list(text: str | None) -> list[Any]:
    if not text:
        return []
    loaded = json.loads(text)
    return loaded if isinstance(loaded, list) else []


class Cache:
    """Cache store over one SQLite connection; `Cache.memory()` never touches disk."""

    def __init__(self, conn: sqlite3.Connection, home: Path | None) -> None:
        self._conn = conn
        self.home = home
        self._lock = threading.RLock()

    # -- opening ----------------------------------------------------------------------

    @staticmethod
    def _connect(target: str) -> sqlite3.Connection:
        conn = sqlite3.connect(
            target, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.create_function("lower", 1, _unicode_lower, deterministic=True)
        return conn

    @classmethod
    def open(cls, home: Path) -> Cache:
        """The single cache connection of this process for `<home>/cache.db`."""
        key = home.resolve()
        with _REGISTRY_LOCK:
            existing = _REGISTRY.get(key)
            if existing is not None:
                return existing
            home.mkdir(parents=True, exist_ok=True)
            conn = cls._connect(str(home / paths.CACHE_DB))
            conn.execute("PRAGMA journal_mode = WAL")
            cache = cls(conn, home)
            cache._init_schema()
            _REGISTRY[key] = cache
            return cache

    @classmethod
    def memory(cls) -> Cache:
        """In-memory backend with the same interface; creates no file."""
        cache = cls(cls._connect(":memory:"), None)
        cache._init_schema()
        return cache

    def close(self) -> None:
        with _REGISTRY_LOCK:
            for key, value in list(_REGISTRY.items()):
                if value is self:
                    del _REGISTRY[key]
        self._conn.close()

    def _init_schema(self) -> None:
        with self._tx():
            for statement in _DDL:
                self._conn.execute(statement)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema (key, value) VALUES ('version', ?)",
                (SCHEMA_VERSION,),
            )

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Short write transaction: commit on success, roll back on any error."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    # -- schema -----------------------------------------------------------------------

    def schema_version(self) -> str | None:
        row = self._one("SELECT value FROM schema WHERE key = 'version'")
        return None if row is None else str(row["value"])

    def integrity(self) -> tuple[bool, str]:
        """Result of `PRAGMA integrity_check` on this connection; never raises."""
        try:
            rows = self._query("PRAGMA integrity_check")
        except sqlite3.Error as exc:
            return False, str(exc)
        detail = "; ".join(str(next(iter(row.values()))) for row in rows)
        return detail == "ok", detail

    # -- member boards ----------------------------------------------------------------

    def replace_member_boards(self, boards: Iterable[Mapping[str, Any]], now: datetime) -> None:
        stamp = now.timestamp()
        with self._tx() as conn:
            conn.execute("DELETE FROM member_boards")
            conn.executemany(
                "INSERT INTO member_boards (id, name, short_link, closed, fetched_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [
                    (b["id"], b.get("name"), b.get("shortLink"), _flag(b.get("closed")), stamp)
                    for b in boards
                ],
            )

    def member_boards(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM member_boards ORDER BY name, id")

    def member_boards_fresh(self, now: datetime, ttl_seconds: int) -> bool:
        row = self._one("SELECT MIN(fetched_at) AS t, COUNT(*) AS n FROM member_boards")
        if row is None or not row["n"]:
            return False
        return not is_expired(row["t"], now, ttl_seconds)

    # -- board rows and freshness -----------------------------------------------------

    def get_board(self, board_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM boards WHERE board_id = ?", (board_id,))

    def list_boards(self) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM boards ORDER BY name, board_id")

    def _ensure_board(self, conn: sqlite3.Connection, board_id: str) -> None:
        conn.execute("INSERT OR IGNORE INTO boards (board_id) VALUES (?)", (board_id,))

    def metadata_fresh(self, board_id: str, now: datetime, ttl_seconds: int) -> bool:
        board = self.get_board(board_id)
        return board is not None and not is_expired(board["metadata_fetched_at"], now, ttl_seconds)

    def cards_fresh(self, board_id: str, now: datetime, ttl_seconds: int) -> bool:
        """Snapshot of open cards: fresh when not flagged stale and younger than the TTL."""
        board = self.get_board(board_id)
        if board is None or board["cards_stale"]:
            return False
        return not is_expired(board["cards_fetched_at"], now, ttl_seconds)

    def archived_fresh(self, board_id: str, now: datetime, ttl_seconds: int) -> bool:
        board = self.get_board(board_id)
        return board is not None and not is_expired(board["archived_fetched_at"], now, ttl_seconds)

    def card_fresh(self, board_id: str, card_id: str, now: datetime, ttl_seconds: int) -> bool:
        """A card uses its own `fetched_at`, independent of the snapshot."""
        card = self._one(
            "SELECT fetched_at FROM cards WHERE board_id = ? AND id = ?", (board_id, card_id)
        )
        return card is not None and not is_expired(card["fetched_at"], now, ttl_seconds)

    def cards_age_seconds(self, board_id: str, now: datetime) -> float | None:
        board = self.get_board(board_id)
        if board is None or board["cards_fetched_at"] is None:
            return None
        return now.timestamp() - float(board["cards_fetched_at"])

    def mark_cards_stale(self, board_id: str) -> None:
        """Force a reload on the next read; `cards_fetched_at` stays intact (modifiedSince)."""
        with self._tx() as conn:
            conn.execute("UPDATE boards SET cards_stale = 1 WHERE board_id = ?", (board_id,))

    def touch_cards(self, board_id: str, now: datetime) -> None:
        """Renew the cards freshness stamp after an incremental reload and clear the stale flag."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE boards SET cards_fetched_at = ?, cards_stale = 0 WHERE board_id = ?",
                (now.timestamp(), board_id),
            )

    # -- metadata ---------------------------------------------------------------------

    def replace_metadata(
        self,
        board_id: str,
        board: Mapping[str, Any],
        lists: Iterable[Mapping[str, Any]],
        labels: Iterable[Mapping[str, Any]],
        members: Iterable[Mapping[str, Any]],
        now: datetime,
    ) -> int:
        """Replace lists, labels and members of a board; drop cards of vanished lists (A-17).

        Returns the number of cards removed.
        """
        with self._tx() as conn:
            self._write_metadata(conn, board_id, board, lists, labels, members, now)
            return self._drop_orphan_cards(conn, board_id)

    def _write_metadata(
        self,
        conn: sqlite3.Connection,
        board_id: str,
        board: Mapping[str, Any],
        lists: Iterable[Mapping[str, Any]],
        labels: Iterable[Mapping[str, Any]],
        members: Iterable[Mapping[str, Any]],
        now: datetime,
    ) -> None:
        self._ensure_board(conn, board_id)
        conn.execute(
            "UPDATE boards SET name = ?, url = ?, short_link = ?, kind = ?,"
            " metadata_fetched_at = ? WHERE board_id = ?",
            (
                board.get("name"),
                board.get("url"),
                board.get("shortLink"),
                board.get("kind") or "board",
                now.timestamp(),
                board_id,
            ),
        )
        for table in ("lists", "labels", "members"):
            conn.execute(f"DELETE FROM {table} WHERE board_id = ?", (board_id,))  # noqa: S608
        conn.executemany(
            "INSERT INTO lists (board_id, id, name, pos, closed) VALUES (?, ?, ?, ?, ?)",
            [
                (board_id, x["id"], x.get("name"), x.get("pos"), _flag(x.get("closed")))
                for x in lists
            ],
        )
        conn.executemany(
            "INSERT INTO labels (board_id, id, name, color) VALUES (?, ?, ?, ?)",
            [(board_id, x["id"], x.get("name"), x.get("color")) for x in labels],
        )
        conn.executemany(
            "INSERT INTO members (board_id, id, username, full_name) VALUES (?, ?, ?, ?)",
            [(board_id, x["id"], x.get("username"), x.get("fullName")) for x in members],
        )

    def list_lists(self, board_id: str) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM lists WHERE board_id = ? ORDER BY pos, id", (board_id,))

    def list_labels(self, board_id: str) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM labels WHERE board_id = ? ORDER BY name, id", (board_id,))

    def list_members(self, board_id: str) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM members WHERE board_id = ? ORDER BY username, id", (board_id,)
        )

    # -- cards ------------------------------------------------------------------------

    def _drop_orphan_cards(self, conn: sqlite3.Connection, board_id: str) -> int:
        ids = [
            row["id"]
            for row in conn.execute(
                "SELECT id FROM cards WHERE board_id = ? AND id_list NOT IN"
                " (SELECT id FROM lists WHERE board_id = ?)",
                (board_id, board_id),
            ).fetchall()
        ]
        self._delete_cards(conn, board_id, ids)
        return len(ids)

    def delete_cards_of_missing_lists(self, board_id: str) -> int:
        """Remove cards whose list is no longer in the cached metadata (A-17)."""
        with self._tx() as conn:
            return self._drop_orphan_cards(conn, board_id)

    def _delete_cards(self, conn: sqlite3.Connection, board_id: str, ids: Sequence[str]) -> None:
        for card_id in ids:
            conn.execute(
                "DELETE FROM checkitems WHERE board_id = ? AND checklist_id IN"
                " (SELECT id FROM checklists WHERE board_id = ? AND card_id = ?)",
                (board_id, board_id, card_id),
            )
            for table in ("checklists", "attachments"):
                conn.execute(
                    f"DELETE FROM {table} WHERE board_id = ? AND card_id = ?",  # noqa: S608
                    (board_id, card_id),
                )
            conn.execute("DELETE FROM cards WHERE board_id = ? AND id = ?", (board_id, card_id))

    def _write_card(
        self,
        conn: sqlite3.Connection,
        board_id: str,
        card: Mapping[str, Any],
        now: datetime,
        archived: bool | None = None,
    ) -> None:
        card_id = card["id"]
        self._delete_cards(conn, board_id, [card_id])
        badges = card.get("badges") or {}
        is_archived = (
            bool(card.get("closed")) if archived is None else archived or bool(card.get("closed"))
        )
        conn.execute(
            "INSERT INTO cards (board_id, id, short_link, id_short, name, desc, id_list,"
            " id_labels, id_members, due, due_complete, pos, archived, deleted, url,"
            " date_last_activity, badge_comments, badge_attachments, badge_check_items,"
            " badge_check_items_checked, fetched_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)",
            (
                board_id,
                card_id,
                card.get("shortLink"),
                card.get("idShort"),
                card.get("name"),
                card.get("desc"),
                card.get("idList"),
                _dump(card.get("idLabels")),
                _dump(card.get("idMembers")),
                card.get("due"),
                _flag(card.get("dueComplete")),
                card.get("pos"),
                _flag(is_archived),
                card.get("url"),
                card.get("dateLastActivity"),
                badges.get("comments"),
                badges.get("attachments"),
                badges.get("checkItems"),
                badges.get("checkItemsChecked"),
                now.timestamp(),
            ),
        )
        for att in card.get("attachments") or []:
            conn.execute(
                "INSERT INTO attachments (board_id, card_id, id, name, bytes, mime_type, date,"
                " url, is_upload, local_path, downloaded_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    board_id,
                    card_id,
                    att["id"],
                    att.get("name"),
                    att.get("bytes"),
                    att.get("mimeType"),
                    att.get("date"),
                    att.get("url"),
                    _flag(att.get("isUpload")),
                    None,
                    None,
                ),
            )
        for chk in card.get("checklists") or []:
            conn.execute(
                "INSERT INTO checklists (board_id, card_id, id, name, pos) VALUES (?, ?, ?, ?, ?)",
                (board_id, card_id, chk["id"], chk.get("name"), chk.get("pos")),
            )
            for item in chk.get("checkItems") or []:
                conn.execute(
                    "INSERT INTO checkitems (board_id, checklist_id, id, name, state, pos)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        board_id,
                        chk["id"],
                        item["id"],
                        item.get("name"),
                        item.get("state"),
                        item.get("pos"),
                    ),
                )

    def _restore_local_paths(
        self, conn: sqlite3.Connection, saved: Sequence[Mapping[str, Any]]
    ) -> None:
        for row in saved:
            conn.execute(
                "UPDATE attachments SET local_path = ?, downloaded_at = ? WHERE board_id = ?"
                " AND card_id = ? AND id = ?",
                (
                    row["local_path"],
                    row["downloaded_at"],
                    row["board_id"],
                    row["card_id"],
                    row["id"],
                ),
            )

    def _saved_local_paths(self, conn: sqlite3.Connection, board_id: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT board_id, card_id, id, local_path, downloaded_at FROM attachments"
                " WHERE board_id = ? AND local_path IS NOT NULL",
                (board_id,),
            ).fetchall()
        ]

    def _replace_open(
        self,
        conn: sqlite3.Connection,
        board_id: str,
        cards: Iterable[Mapping[str, Any]],
        now: datetime,
    ) -> None:
        saved = self._saved_local_paths(conn, board_id)
        old = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM cards WHERE board_id = ? AND archived = 0 AND deleted = 0",
                (board_id,),
            ).fetchall()
        ]
        self._delete_cards(conn, board_id, old)
        for card in cards:
            self._write_card(conn, board_id, card, now)
        self._restore_local_paths(conn, saved)
        conn.execute(
            "UPDATE boards SET cards_fetched_at = ?, cards_stale = 0 WHERE board_id = ?",
            (now.timestamp(), board_id),
        )

    def replace_cards(
        self, board_id: str, cards: Iterable[Mapping[str, Any]], now: datetime
    ) -> None:
        """Replace the open-cards snapshot of a board in a single transaction.

        Archived cards and `deleted` tombstones are kept. Downloaded attachment paths survive.
        A failure anywhere rolls the whole replacement back.
        """
        with self._tx() as conn:
            self._ensure_board(conn, board_id)
            self._replace_open(conn, board_id, cards, now)

    def replace_snapshot(
        self,
        board_id: str,
        board: Mapping[str, Any],
        lists: Iterable[Mapping[str, Any]],
        labels: Iterable[Mapping[str, Any]],
        members: Iterable[Mapping[str, Any]],
        cards: Iterable[Mapping[str, Any]],
        now: datetime,
    ) -> None:
        """Full reload of one board (metadata and open cards) in a single transaction."""
        with self._tx() as conn:
            self._write_metadata(conn, board_id, board, lists, labels, members, now)
            self._replace_open(conn, board_id, cards, now)

    def replace_archived(
        self, board_id: str, cards: Iterable[Mapping[str, Any]], now: datetime
    ) -> None:
        """Replace the archived-cards snapshot; stored with `archived = true`."""
        with self._tx() as conn:
            self._ensure_board(conn, board_id)
            old = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM cards WHERE board_id = ? AND archived = 1", (board_id,)
                ).fetchall()
            ]
            self._delete_cards(conn, board_id, old)
            for card in cards:
                self._write_card(conn, board_id, card, now, archived=True)
            conn.execute(
                "UPDATE boards SET archived_fetched_at = ? WHERE board_id = ?",
                (now.timestamp(), board_id),
            )

    def upsert_card(self, board_id: str, card: Mapping[str, Any], now: datetime) -> None:
        """Store one card with its own `fetched_at`; `closed = true` becomes `archived`."""
        with self._tx() as conn:
            self._ensure_board(conn, board_id)
            saved = [
                r for r in self._saved_local_paths(conn, board_id) if r["card_id"] == card["id"]
            ]
            self._write_card(conn, board_id, card, now)
            self._restore_local_paths(conn, saved)

    def replace_list_cards(
        self, board_id: str, list_id: str, cards: Iterable[Mapping[str, Any]], now: datetime
    ) -> None:
        """Reload only the open cards of one list; each card gets its own `fetched_at`."""
        with self._tx() as conn:
            self._ensure_board(conn, board_id)
            saved = self._saved_local_paths(conn, board_id)
            old = [
                r["id"]
                for r in conn.execute(
                    "SELECT id FROM cards WHERE board_id = ? AND id_list = ? AND archived = 0"
                    " AND deleted = 0",
                    (board_id, list_id),
                ).fetchall()
            ]
            self._delete_cards(conn, board_id, old)
            for card in cards:
                self._write_card(conn, board_id, card, now)
            self._restore_local_paths(conn, saved)

    def mark_card_deleted(self, board_id: str, card_id: str) -> None:
        """Record that Trello confirmed the card does not exist; its rows are dropped."""
        with self._tx() as conn:
            self._ensure_board(conn, board_id)
            self._delete_cards(conn, board_id, [card_id])
            conn.execute(
                "INSERT INTO cards (board_id, id, deleted, archived) VALUES (?, ?, 1, 0)",
                (board_id, card_id),
            )

    def is_card_deleted(self, board_id: str, card_id: str) -> bool:
        row = self._one(
            "SELECT deleted FROM cards WHERE board_id = ? AND id = ?", (board_id, card_id)
        )
        return bool(row and row["deleted"])

    @staticmethod
    def _decode_card(row: dict[str, Any]) -> dict[str, Any]:
        row["id_labels"] = _load_list(row["id_labels"])
        row["id_members"] = _load_list(row["id_members"])
        return row

    def get_card(self, board_id: str, card_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT * FROM cards WHERE board_id = ? AND id = ? AND deleted = 0",
            (board_id, card_id),
        )
        return None if row is None else self._decode_card(row)

    def list_cards(self, board_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cards WHERE board_id = ? AND deleted = 0"
        if not include_archived:
            sql += " AND archived = 0"
        rows = self._query(sql + " ORDER BY id_list, pos, id", (board_id,))
        return [self._decode_card(r) for r in rows]

    def list_attachments(self, board_id: str, card_id: str | None = None) -> list[dict[str, Any]]:
        if card_id is None:
            return self._query(
                "SELECT * FROM attachments WHERE board_id = ? ORDER BY card_id, date, id",
                (board_id,),
            )
        return self._query(
            "SELECT * FROM attachments WHERE board_id = ? AND card_id = ? ORDER BY date, id",
            (board_id, card_id),
        )

    def set_attachment_local_path(
        self,
        board_id: str,
        card_id: str,
        attachment_id: str,
        local_path: str | None,
        now: datetime,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                "UPDATE attachments SET local_path = ?, downloaded_at = ? WHERE board_id = ?"
                " AND card_id = ? AND id = ?",
                (
                    local_path,
                    None if local_path is None else now.timestamp(),
                    board_id,
                    card_id,
                    attachment_id,
                ),
            )

    def list_checklists(self, board_id: str, card_id: str) -> list[dict[str, Any]]:
        """Checklists of a card, each with its `items`."""
        lists = self._query(
            "SELECT * FROM checklists WHERE board_id = ? AND card_id = ? ORDER BY pos, id",
            (board_id, card_id),
        )
        for chk in lists:
            chk["items"] = self._query(
                "SELECT * FROM checkitems WHERE board_id = ? AND checklist_id = ? ORDER BY pos, id",
                (board_id, chk["id"]),
            )
        return lists

    # -- statistics and clearing --------------------------------------------------------

    def stats(self, now: datetime) -> dict[str, Any]:
        """Per-board snapshot age and size, plus bytes under `attachments/`."""
        boards = []
        for board in self.list_boards():
            bid = board["board_id"]

            def age(stamp: float | None) -> float | None:
                return None if stamp is None else now.timestamp() - float(stamp)

            counts = self._one(
                "SELECT COUNT(*) AS n, COALESCE(SUM(archived), 0) AS a FROM cards"
                " WHERE board_id = ? AND deleted = 0",
                (bid,),
            ) or {"n": 0, "a": 0}
            boards.append(
                {
                    "board_id": bid,
                    "name": board["name"],
                    "kind": board["kind"],
                    "metadata": {
                        "age_seconds": age(board["metadata_fetched_at"]),
                        "lists": self._count("lists", bid),
                        "labels": self._count("labels", bid),
                        "members": self._count("members", bid),
                    },
                    "cards": {
                        "age_seconds": age(board["cards_fetched_at"]),
                        "stale": bool(board["cards_stale"]),
                        "count": counts["n"] - counts["a"],
                    },
                    "archived": {
                        "age_seconds": age(board["archived_fetched_at"]),
                        "count": counts["a"],
                    },
                }
            )
        return {"boards": boards, "attachments_bytes": self.attachments_bytes()}

    def _count(self, table: str, board_id: str) -> int:
        row = self._one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE board_id = ?",  # noqa: S608
            (board_id,),
        )
        return int(row["n"]) if row else 0

    def attachments_bytes(self) -> int:
        if self.home is None:
            return 0
        root = self.home / paths.ATTACHMENTS_DIR
        if not root.is_dir():
            return 0
        return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())

    def clear(self, attachments: bool = False) -> dict[str, int]:
        """Empty every cache table; downloaded attachments stay unless `attachments` is set.

        With `attachments`, files under `attachments/` and the directories left empty are
        deleted, keeping `attachments/` itself.
        """
        with self._tx() as conn:
            for table in ("member_boards", "boards", *PER_BOARD_TABLES):
                conn.execute(f"DELETE FROM {table}")  # noqa: S608
        removed = 0
        if attachments and self.home is not None:
            removed = self._clear_attachments(self.home)
        return {"attachments_removed": removed}

    @staticmethod
    def _clear_attachments(home: Path) -> int:
        root = home / paths.ATTACHMENTS_DIR
        if not root.is_dir():
            return 0
        removed = 0
        for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_file():
                paths.delete_owned_file(home, path)
                removed += 1
            elif path.is_dir():
                with suppress(OSError):
                    path.rmdir()
        return removed


def check_integrity(home: Path) -> tuple[bool, str]:
    """`PRAGMA integrity_check` and schema version of `<home>/cache.db`, without raising.

    Returns `(True, "ok")` when the file is absent (nothing to check) or healthy.
    """
    db = home / paths.CACHE_DB
    if not db.exists():
        return True, "ok"
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return False, str(exc)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        detail = "; ".join(str(r[0]) for r in rows)
        if detail != "ok":
            return False, detail
        version = conn.execute("SELECT value FROM schema WHERE key = 'version'").fetchone()
        if version is None or str(version[0]) != SCHEMA_VERSION:
            return False, "unsupported cache schema version"
        return True, "ok"
    except sqlite3.Error as exc:
        return False, str(exc)
    finally:
        conn.close()

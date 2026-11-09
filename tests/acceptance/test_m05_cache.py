"""M05 acceptance tests: SQLite cache store, TTL freshness and the in-memory backend."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.clock import FakeClock

from trelloctl.infra import cache as cache_mod
from trelloctl.infra.cache import Cache, check_integrity, is_expired

BOARD = {"name": "Work", "url": "https://trello.com/b/abc", "shortLink": "abc"}


def make_card(card_id: str, list_id: str = "l1", **extra: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "id": card_id,
        "name": f"card {card_id}",
        "idList": list_id,
        "idLabels": [],
        "idMembers": [],
        "closed": False,
    }
    card.update(extra)
    return card


@pytest.fixture
def store(trelloctl_home: Path) -> Iterator[Cache]:
    cache = Cache.open(trelloctl_home)
    yield cache
    cache.close()


def seed(cache: Cache, clock: FakeClock, board_id: str = "b1") -> None:
    cache.replace_snapshot(
        board_id,
        BOARD,
        lists=[{"id": "l1", "name": "Todo", "pos": 1}, {"id": "l2", "name": "Doing", "pos": 2}],
        labels=[{"id": "x1", "name": "bug", "color": "red"}],
        members=[{"id": "m1", "username": "ana", "fullName": "Ana"}],
        cards=[make_card("c1"), make_card("c2", "l2")],
        now=clock.now(),
    )


def columns(cache: Cache, table: str) -> list[str]:
    return [r["name"] for r in cache._query(f"PRAGMA table_info({table})")]


@pytest.mark.tc("TC-M05-001")
def test_open_first_time_wal_busy_timeout_single_connection(trelloctl_home: Path) -> None:
    db = trelloctl_home / "cache.db"
    assert not db.exists()
    cache = Cache.open(trelloctl_home)
    try:
        assert db.is_file()
        assert cache._one("PRAGMA journal_mode")["journal_mode"] == "wal"  # type: ignore[index]
        assert cache._one("PRAGMA busy_timeout")["timeout"] == 5000  # type: ignore[index]
        assert Cache.open(trelloctl_home) is cache
    finally:
        cache.close()


@pytest.mark.tc("TC-M05-002")
def test_per_board_tables_have_board_id_first_in_key(store: Cache) -> None:
    for table in cache_mod.PER_BOARD_TABLES:
        info = store._query(f"PRAGMA table_info({table})")
        key = sorted((r for r in info if r["pk"]), key=lambda r: r["pk"])
        assert key[0]["name"] == "board_id", table
    boards_key = [r["name"] for r in store._query("PRAGMA table_info(boards)") if r["pk"]]
    assert boards_key == ["board_id"]


@pytest.mark.tc("TC-M05-003")
def test_schema_matches_prd(store: Cache) -> None:
    expected = {
        "schema": ["key", "value"],
        "member_boards": ["id", "name", "short_link", "closed", "fetched_at"],
        "boards": [
            "board_id", "name", "url", "short_link", "kind", "metadata_fetched_at",
            "cards_fetched_at", "cards_stale", "archived_fetched_at",
        ],
        "lists": ["board_id", "id", "name", "pos", "closed"],
        "labels": ["board_id", "id", "name", "color"],
        "members": ["board_id", "id", "username", "full_name"],
        "cards": [
            "board_id", "id", "short_link", "id_short", "name", "desc", "id_list", "id_labels",
            "id_members", "due", "due_complete", "pos", "archived", "deleted", "url",
            "date_last_activity", "badge_comments", "badge_attachments", "badge_check_items",
            "badge_check_items_checked", "fetched_at",
        ],
        "attachments": [
            "board_id", "card_id", "id", "name", "bytes", "mime_type", "date", "url",
            "is_upload", "local_path", "downloaded_at",
        ],
        "checklists": ["board_id", "card_id", "id", "name", "pos"],
        "checkitems": ["board_id", "checklist_id", "id", "name", "state", "pos"],
    }  # fmt: skip
    tables = {r["name"] for r in store._query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == set(expected)
    for table, cols in expected.items():
        assert columns(store, table) == cols, table
    assert store.schema_version() == cache_mod.SCHEMA_VERSION


@pytest.mark.tc("TC-M05-004")
def test_no_comment_or_activity_tables(store: Cache) -> None:
    names = [r["name"].lower() for r in store._query("SELECT name FROM sqlite_master")]
    assert not [n for n in names if "comment" in n or "activity" in n.replace("last_activity", "")]
    for table in ("cards", "attachments", "checklists", "checkitems"):
        assert not [c for c in columns(store, table) if c in ("comment", "comments", "actions")]


@pytest.mark.tc("TC-M05-005")
def test_ttl_expiry_per_entity(store: Cache, clock: FakeClock) -> None:
    store.replace_member_boards([{"id": "b1", "name": "Work"}], clock.now())
    seed(store, clock)
    store.replace_archived("b1", [make_card("a1", closed=True)], clock.now())
    meta_ttl, cards_ttl = 1800, 300
    fresh = clock.now() + timedelta(seconds=299)
    assert store.member_boards_fresh(fresh, meta_ttl)
    assert store.metadata_fresh("b1", fresh, meta_ttl)
    assert store.cards_fresh("b1", fresh, cards_ttl)
    assert store.archived_fresh("b1", fresh, cards_ttl)
    assert store.card_fresh("b1", "c1", fresh, cards_ttl)
    # Past the cards TTL only: metadata-class data still fresh, card-class data expired.
    mid = clock.now() + timedelta(seconds=301)
    assert store.member_boards_fresh(mid, meta_ttl)
    assert store.metadata_fresh("b1", mid, meta_ttl)
    assert not store.cards_fresh("b1", mid, cards_ttl)
    assert not store.archived_fresh("b1", mid, cards_ttl)
    assert not store.card_fresh("b1", "c1", mid, cards_ttl)
    # Past the metadata TTL: everything expired.
    late = clock.now() + timedelta(seconds=1801)
    assert not store.member_boards_fresh(late, meta_ttl)
    assert not store.metadata_fresh("b1", late, meta_ttl)


@pytest.mark.tc("TC-M05-006")
def test_card_freshness_uses_its_own_fetched_at(store: Cache, clock: FakeClock) -> None:
    seed(store, clock)
    t0 = clock.now()
    later = t0 + timedelta(seconds=400)
    # Single card refreshed alone: fresher than the expired snapshot.
    store.upsert_card("b1", make_card("c1", name="renamed"), later)
    check = later + timedelta(seconds=10)
    assert not store.cards_fresh("b1", check, 300)
    assert store.card_fresh("b1", "c1", check, 300)
    assert not store.card_fresh("b1", "c2", check, 300)
    # And the reverse: a fresh snapshot with an old card row.
    store.replace_snapshot("b1", BOARD, [{"id": "l1"}], [], [], [make_card("c1")], t0)
    with store._tx() as conn:
        conn.execute("UPDATE cards SET fetched_at = ? WHERE id = 'c1'", (t0.timestamp() - 1000,))
    assert store.cards_fresh("b1", t0 + timedelta(seconds=10), 300)
    assert not store.card_fresh("b1", "c1", t0 + timedelta(seconds=10), 300)


@pytest.mark.tc("TC-M05-007")
def test_age_equal_to_ttl_is_expired(store: Cache, clock: FakeClock) -> None:
    seed(store, clock)
    edge = clock.now() + timedelta(seconds=300)
    assert is_expired(clock.now().timestamp(), edge, 300)
    assert not store.cards_fresh("b1", edge, 300)
    assert not store.card_fresh("b1", "c1", edge, 300)
    assert store.cards_fresh("b1", edge - timedelta(seconds=1), 300)
    assert not store.metadata_fresh("b1", clock.now() + timedelta(seconds=1800), 1800)


@pytest.mark.tc("TC-M05-008")
def test_mark_stale_keeps_fetched_at(store: Cache, clock: FakeClock) -> None:
    seed(store, clock)
    before = store.get_board("b1")
    assert before is not None
    assert before["cards_stale"] == 0
    store.mark_cards_stale("b1")
    after = store.get_board("b1")
    assert after is not None
    assert after["cards_stale"] == 1
    assert after["cards_fetched_at"] == before["cards_fetched_at"]
    assert not store.cards_fresh("b1", clock.now(), 300)
    store.replace_cards("b1", [make_card("c1")], clock.now())
    reloaded = store.get_board("b1")
    assert reloaded is not None
    assert reloaded["cards_stale"] == 0


@pytest.mark.tc("TC-M05-009")
def test_failed_snapshot_replacement_keeps_previous(store: Cache, clock: FakeClock) -> None:
    seed(store, clock)
    store.set_attachment_local_path("b1", "c1", "none", None, clock.now())
    old_cards = store.list_cards("b1")
    old_lists = store.list_lists("b1")
    old_board = store.get_board("b1")
    bad_cards = [make_card("n1"), make_card("n2", idLabels=object())]
    with pytest.raises(TypeError):
        store.replace_snapshot(
            "b1", {"name": "New"}, [{"id": "z"}], [], [], bad_cards, clock.now() + timedelta(days=1)
        )
    assert store.list_cards("b1") == old_cards
    assert store.list_lists("b1") == old_lists
    assert store.get_board("b1") == old_board
    with pytest.raises(TypeError):
        store.replace_cards("b1", bad_cards, clock.now())
    assert store.list_cards("b1") == old_cards


_WRITER = textwrap.dedent(
    """
    import sys
    from datetime import datetime, UTC
    from pathlib import Path
    from trelloctl.infra.cache import Cache

    home, tag = Path(sys.argv[1]), sys.argv[2]
    cache = Cache.open(home)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(40):
        cache.upsert_card(tag, {"id": f"{tag}-{i}", "idList": "l"}, now)
    cache.close()
    """
)


@pytest.mark.tc("TC-M05-010")
def test_two_processes_write_concurrently(trelloctl_home: Path, tmp_path: Path) -> None:
    Cache.open(trelloctl_home).close()
    script = tmp_path / "writer.py"
    script.write_text(_WRITER, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    procs = [
        subprocess.Popen(  # noqa: S603
            [sys.executable, str(script), str(trelloctl_home), tag],
            env=env,
            stderr=subprocess.PIPE,
            text=True,
        )
        for tag in ("p1", "p2")
    ]
    errors = [p.communicate(timeout=60)[1] for p in procs]
    assert [p.returncode for p in procs] == [0, 0], errors
    assert not any("locked" in e for e in errors)
    cache = Cache.open(trelloctl_home)
    try:
        for tag in ("p1", "p2"):
            assert len(cache.list_cards(tag)) == 40
    finally:
        cache.close()


@pytest.mark.tc("TC-M05-011")
def test_memory_backend_does_not_touch_cache_db(trelloctl_home: Path, clock: FakeClock) -> None:
    db = trelloctl_home / "cache.db"
    mem = Cache.memory()
    seed(mem, clock)
    assert mem.list_cards("b1")
    mem.clear()
    mem.close()
    assert not db.exists()
    assert list(trelloctl_home.glob("cache.db*")) == []
    real = Cache.open(trelloctl_home)
    seed(real, clock)
    real.close()
    snapshot = (db.read_bytes(), db.stat().st_mtime_ns)
    mem = Cache.memory()
    seed(mem, clock)
    mem.replace_member_boards([{"id": "x"}], clock.now())
    mem.close()
    assert (db.read_bytes(), db.stat().st_mtime_ns) == snapshot


@pytest.mark.tc("TC-M05-012")
def test_closed_card_is_stored_archived(store: Cache, clock: FakeClock) -> None:
    seed(store, clock)
    store.upsert_card("b1", make_card("c1", closed=True), clock.now())
    card = store.get_card("b1", "c1")
    assert card is not None
    assert card["archived"] == 1
    assert [c["id"] for c in store.list_cards("b1")] == ["c2"]
    assert {c["id"] for c in store.list_cards("b1", include_archived=True)} == {"c1", "c2"}
    store.upsert_card("b1", make_card("c1", closed=False), clock.now())
    reopened = store.get_card("b1", "c1")
    assert reopened is not None
    assert reopened["archived"] == 0


@pytest.mark.tc("TC-M05-013")
def test_cards_of_vanished_list_are_deleted(store: Cache, clock: FakeClock) -> None:
    detailed = make_card(
        "c2",
        "l2",
        attachments=[{"id": "a2", "name": "f.txt", "bytes": 3}],
        checklists=[{"id": "k2", "name": "todo", "checkItems": [{"id": "i2", "name": "x"}]}],
    )
    keep = make_card(
        "c1",
        attachments=[{"id": "a1", "name": "g.txt"}],
        checklists=[{"id": "k1", "name": "todo", "checkItems": [{"id": "i1", "name": "y"}]}],
    )
    store.replace_snapshot(
        "b1", BOARD, [{"id": "l1"}, {"id": "l2"}], [], [], [keep, detailed], clock.now()
    )
    store.replace_snapshot(
        "other", BOARD, [{"id": "l2"}], [], [], [make_card("c2", "l2")], clock.now()
    )
    removed = store.replace_metadata("b1", BOARD, [{"id": "l1"}], [], [], clock.now())
    assert removed == 1
    assert [c["id"] for c in store.list_cards("b1")] == ["c1"]
    assert store.list_attachments("b1", "c2") == []
    assert store.list_checklists("b1", "c2") == []
    assert store._query("SELECT * FROM checkitems WHERE id = 'i2'") == []
    assert len(store.list_attachments("b1", "c1")) == 1
    assert store.list_checklists("b1", "c1")[0]["items"][0]["id"] == "i1"
    assert [c["id"] for c in store.list_cards("other")] == ["c2"]
    assert store.delete_cards_of_missing_lists("b1") == 0


@pytest.mark.tc("TC-M05-014")
def test_stats_two_boards_with_attachments(
    store: Cache, trelloctl_home: Path, clock: FakeClock
) -> None:
    seed(store, clock, "b1")
    store.replace_snapshot(
        "b2",
        {"name": "Other"},
        [{"id": "l1"}],
        [],
        [],
        [make_card("d1"), make_card("d2"), make_card("d3")],
        clock.now() - timedelta(seconds=100),
    )
    att = trelloctl_home / "attachments" / "b1" / "c1"
    att.mkdir(parents=True)
    (att / "one.bin").write_bytes(b"x" * 10)
    (att / "two.bin").write_bytes(b"y" * 5)
    stats = store.stats(clock.now())
    by_id = {b["board_id"]: b for b in stats["boards"]}
    assert set(by_id) == {"b1", "b2"}
    assert by_id["b1"]["cards"]["count"] == 2
    assert by_id["b1"]["cards"]["age_seconds"] == 0
    assert by_id["b1"]["metadata"]["lists"] == 2
    assert by_id["b2"]["cards"]["count"] == 3
    assert by_id["b2"]["cards"]["age_seconds"] == 100
    assert by_id["b2"]["metadata"]["age_seconds"] == 100
    assert by_id["b1"]["archived"] == {"age_seconds": None, "count": 0}
    assert stats["attachments_bytes"] == 15


@pytest.mark.tc("TC-M05-015")
def test_clear_without_and_with_attachments(
    store: Cache, trelloctl_home: Path, clock: FakeClock
) -> None:
    seed(store, clock)
    store.replace_member_boards([{"id": "b1"}], clock.now())
    folder = trelloctl_home / "attachments" / "b1" / "c1"
    folder.mkdir(parents=True)
    (folder / "f.bin").write_bytes(b"abc")

    def empty() -> bool:
        tables = ["member_boards", "boards", *cache_mod.PER_BOARD_TABLES]
        return all(not store._query(f"SELECT * FROM {t}") for t in tables)  # noqa: S608

    assert not empty()
    store.clear()
    assert empty()
    assert (folder / "f.bin").read_bytes() == b"abc"
    seed(store, clock)
    result = store.clear(attachments=True)
    assert empty()
    assert result == {"attachments_removed": 1}
    assert not (folder / "f.bin").exists()
    assert not (trelloctl_home / "attachments" / "b1").exists()
    assert (trelloctl_home / "attachments").is_dir()


@pytest.mark.tc("TC-M05-016")
def test_corrupt_cache_reports_integrity_failure(trelloctl_home: Path) -> None:
    (trelloctl_home / "cache.db").write_bytes(b"this is not a sqlite database" * 200)
    ok, detail = check_integrity(trelloctl_home)
    assert ok is False
    assert detail
    # A damaged page inside a valid database is reported too.
    (trelloctl_home / "cache.db").unlink()
    cache = Cache.open(trelloctl_home)
    cache.close()
    with sqlite3.connect(trelloctl_home / "cache.db") as conn:
        conn.execute("PRAGMA journal_mode = DELETE")
    data = bytearray((trelloctl_home / "cache.db").read_bytes())
    data[100:140] = b"\xff" * 40
    (trelloctl_home / "cache.db").write_bytes(bytes(data))
    assert check_integrity(trelloctl_home)[0] is False


@pytest.mark.tc("TC-M05-017")
def test_lower_glob_matches_unicode_case_insensitively(store: Cache, clock: FakeClock) -> None:
    store.replace_snapshot(
        "b1",
        BOARD,
        [{"id": "l1"}],
        [],
        [],
        [make_card("c1", name="ÁRBOL grande"), make_card("c2", name="roble")],
        clock.now(),
    )
    for pattern in ("ÁRBOL*", "árbol*"):
        rows = store._query("SELECT id FROM cards WHERE lower(name) GLOB lower(?)", (pattern,))
        assert [r["id"] for r in rows] == ["c1"]

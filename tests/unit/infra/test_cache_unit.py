"""Unit tests for the cache store branches not covered by the acceptance cases."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trelloctl.infra.cache import Cache, check_integrity, is_expired

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_is_expired_none_and_boundary() -> None:
    assert is_expired(None, NOW, 10)
    assert is_expired(NOW.timestamp() - 10, NOW, 10)
    assert not is_expired(NOW.timestamp() - 9.9, NOW, 10)


def test_empty_cache_reads() -> None:
    cache = Cache.memory()
    assert cache.get_board("x") is None
    assert not cache.metadata_fresh("x", NOW, 10)
    assert not cache.cards_fresh("x", NOW, 10)
    assert not cache.archived_fresh("x", NOW, 10)
    assert not cache.card_fresh("x", "c", NOW, 10)
    assert not cache.member_boards_fresh(NOW, 10)
    assert cache.cards_age_seconds("x", NOW) is None
    assert cache.get_card("x", "c") is None
    assert cache.attachments_bytes() == 0
    assert cache.clear(attachments=True) == {"attachments_removed": 0}
    assert cache.integrity() == (True, "ok")
    cache.close()


def test_member_boards_roundtrip_and_ttl() -> None:
    cache = Cache.memory()
    cache.replace_member_boards([{"id": "b", "name": "B", "shortLink": "s", "closed": True}], NOW)
    rows = cache.member_boards()
    assert rows[0]["short_link"] == "s"
    assert rows[0]["closed"] == 1
    assert cache.member_boards_fresh(NOW + timedelta(seconds=5), 10)
    assert not cache.member_boards_fresh(NOW + timedelta(seconds=10), 10)
    cache.close()


def test_tombstone_survives_replace_and_hides_card() -> None:
    cache = Cache.memory()
    cache.replace_snapshot("b", {}, [{"id": "l"}], [], [], [{"id": "c", "idList": "l"}], NOW)
    cache.mark_card_deleted("b", "c")
    assert cache.is_card_deleted("b", "c")
    assert cache.get_card("b", "c") is None
    assert cache.list_cards("b") == []
    cache.replace_cards("b", [], NOW)
    assert cache.is_card_deleted("b", "c")
    cache.upsert_card("b", {"id": "c", "idList": "l"}, NOW)
    assert not cache.is_card_deleted("b", "c")
    assert not cache.is_card_deleted("b", "nope")
    cache.close()


def test_local_path_survives_reload_and_list_reload() -> None:
    cache = Cache.memory()
    card = {"id": "c", "idList": "l", "attachments": [{"id": "a", "name": "f"}]}
    cache.replace_snapshot("b", {}, [{"id": "l"}], [], [], [card], NOW)
    cache.set_attachment_local_path("b", "c", "a", "attachments/b/c/f", NOW)
    cache.replace_cards("b", [card], NOW)
    att = cache.list_attachments("b")[0]
    assert att["local_path"] == "attachments/b/c/f"
    assert att["downloaded_at"] == NOW.timestamp()
    cache.replace_list_cards("b", "l", [card], NOW)
    cache.upsert_card("b", card, NOW)
    assert cache.list_attachments("b", "c")[0]["local_path"] == "attachments/b/c/f"
    cache.set_attachment_local_path("b", "c", "a", None, NOW)
    assert cache.list_attachments("b", "c")[0]["downloaded_at"] is None
    cache.close()


def test_replace_list_cards_only_touches_that_list() -> None:
    cache = Cache.memory()
    cards = [{"id": "c1", "idList": "l1"}, {"id": "c2", "idList": "l2"}]
    cache.replace_snapshot("b", {}, [{"id": "l1"}, {"id": "l2"}], [], [], cards, NOW)
    cache.replace_list_cards("b", "l1", [{"id": "c3", "idList": "l1"}], NOW)
    assert {c["id"] for c in cache.list_cards("b")} == {"c2", "c3"}
    cache.close()


def test_replace_cards_keeps_archived_and_replace_archived_keeps_open() -> None:
    cache = Cache.memory()
    cache.replace_snapshot("b", {}, [{"id": "l"}], [], [], [{"id": "o", "idList": "l"}], NOW)
    cache.replace_archived("b", [{"id": "a", "idList": "l"}], NOW)
    cache.replace_cards("b", [{"id": "o2", "idList": "l"}], NOW)
    assert {c["id"] for c in cache.list_cards("b", include_archived=True)} == {"a", "o2"}
    cache.replace_archived("b", [], NOW)
    assert [c["id"] for c in cache.list_cards("b", include_archived=True)] == ["o2"]
    assert cache.archived_fresh("b", NOW, 10)
    cache.close()


def test_card_label_and_member_ids_decode() -> None:
    cache = Cache.memory()
    card = {"id": "c", "idList": "l", "idLabels": ["x"], "idMembers": ["m", "n"]}
    card["badges"] = {"comments": 2, "attachments": 1, "checkItems": 4, "checkItemsChecked": 3}
    cache.upsert_card("b", card, NOW)
    row = cache.get_card("b", "c")
    assert row is not None
    assert row["id_labels"] == ["x"]
    assert row["id_members"] == ["m", "n"]
    assert row["badge_check_items_checked"] == 3
    cache.close()


def test_metadata_roundtrip_and_age() -> None:
    cache = Cache.memory()
    cache.replace_metadata(
        "b",
        {"name": "N", "kind": "inbox"},
        [{"id": "l", "name": "L", "pos": 1, "closed": True}],
        [{"id": "x", "name": "bug", "color": "red"}],
        [{"id": "m", "username": "u", "fullName": "F"}],
        NOW,
    )
    assert cache.list_lists("b")[0]["closed"] == 1
    assert cache.list_labels("b")[0]["color"] == "red"
    assert cache.list_members("b")[0]["full_name"] == "F"
    board = cache.get_board("b")
    assert board is not None
    assert board["kind"] == "inbox"
    assert cache.cards_age_seconds("b", NOW) is None
    cache.replace_cards("b", [], NOW)
    assert cache.cards_age_seconds("b", NOW + timedelta(seconds=7)) == 7
    cache.close()


def test_integrity_and_schema_checks(tmp_path: Path) -> None:
    assert check_integrity(tmp_path) == (True, "ok")
    cache = Cache.open(tmp_path)
    assert cache.integrity() == (True, "ok")
    assert check_integrity(tmp_path) == (True, "ok")
    cache._conn.execute("UPDATE schema SET value = '99'")
    ok, detail = check_integrity(tmp_path)
    assert not ok
    assert "version" in detail
    cache.close()
    assert cache.integrity()[0] is False


def test_clear_attachments_ignores_missing_folder(tmp_path: Path) -> None:
    cache = Cache.open(tmp_path)
    assert cache.clear(attachments=True) == {"attachments_removed": 0}
    cache.close()


def test_touch_cards_renews_stamp_and_clears_stale() -> None:
    cache = Cache.memory()
    cache.replace_cards("b", [], NOW)
    cache.mark_cards_stale("b")
    later = NOW + timedelta(seconds=30)
    cache.touch_cards("b", later)
    assert cache.cards_age_seconds("b", later) == 0
    assert cache.cards_fresh("b", later, 10)
    cache.close()

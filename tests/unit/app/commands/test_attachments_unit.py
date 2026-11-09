"""Unit tests of the `card attachments` command helpers and error paths."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    SHORT,
    close_caches,
    make_ctx,
    new_id,
    prime,
    seed_standard,
)
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import attachments as mod
from trelloctl.app.commands.context import CommandContext
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import Cache

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def raises(code: str, func: Any, *args: Any, **kwargs: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        func(*args, **kwargs)
    assert info.value.code == code
    return info.value


def downloaded_card(
    home: Path, fake: FakeTrello, *, primed: bool = False
) -> tuple[Cache, dict[str, str], Path]:
    """A card of the cache with one attachment whose file exists on disk."""
    ids = seed_standard(fake)
    if primed:
        prime(home, fake, datetime.now(UTC))
    cache = Cache.open(home)
    card = dict(fake.state.cards[ids["c1"]])
    card["attachments"] = [{"id": "a" * 24, "name": "one.txt", "bytes": 3, "isUpload": True}]
    cache.upsert_card(BOARD, card, NOW)
    target = mod.card_directory(home, BOARD, ids["c1"]) / mod.safe_filename("a" * 24, "one.txt")
    target.parent.mkdir(parents=True)
    target.write_bytes(b"abc")
    cache.set_attachment_local_path(BOARD, ids["c1"], "a" * 24, str(target), NOW)
    return cache, ids, target


# --- names (A-9) ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", "id_report.pdf"),
        ("../../x", "id_____x"),
        ("a:b*?.txt", "id_a_b__.txt"),
        ("dir\\file", "id_dir_file"),
        ("", "id_attachment"),
        (None, "id_attachment"),
        ("...", "id__"),
        ("trailing. ", "id_trailing"),
        ("tab\tname", "id_tab_name"),
    ],
)
def test_safe_filename(name: str | None, expected: str) -> None:
    assert mod.safe_filename("id", name) == expected


def test_safe_filename_truncates_long_names_keeping_the_extension() -> None:
    result = mod.safe_filename("id", "n" * 300 + ".extension-that-is-too-long")
    stem, _, suffix = result.removeprefix("id_").partition(".")
    assert len(stem) == 100
    assert len(suffix) < 17


# --- local files ---


def test_prune_empty_stops_at_a_non_empty_directory_and_at_the_root(tmp_path: Path) -> None:
    root = tmp_path / "attachments"
    leaf = root / "b" / "c"
    leaf.mkdir(parents=True)
    (root / "b" / "other.txt").write_text("x", encoding="utf-8")
    mod._prune_empty(tmp_path, leaf)
    assert not leaf.exists()
    assert (root / "b").is_dir()
    mod._prune_empty(tmp_path, root / "missing")
    mod._prune_empty(tmp_path, root)
    assert root.is_dir()


def test_delete_local_refuses_paths_outside_attachments(tmp_path: Path) -> None:
    outside = tmp_path / "precious.txt"
    outside.write_text("keep", encoding="utf-8")
    (tmp_path / "attachments").mkdir()
    assert mod._delete_local(tmp_path, str(outside)) is False
    assert mod._delete_local(tmp_path, None) is False
    assert mod._delete_local(tmp_path, str(tmp_path / "attachments" / "missing")) is False
    assert outside.read_text(encoding="utf-8") == "keep"


def test_write_file_leaves_no_partial_file_when_the_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(mod.os, "replace", broken)
    target = tmp_path / "card" / "file.bin"
    with pytest.raises(OSError, match="disk full"):
        mod._write_file(target, b"data")
    assert list(target.parent.iterdir()) == []


def test_current_copy_requires_matching_metadata_and_the_file_on_disk(tmp_path: Path) -> None:
    local = tmp_path / "f"
    local.write_bytes(b"abc")
    old = {"local_path": str(local), "bytes": 3, "date": "d1"}
    assert mod._current_copy(old, {"bytes": 3, "date": "d1"}) == str(local)
    assert mod._current_copy(None, {"bytes": 3, "date": "d1"}) is None
    assert mod._current_copy({**old, "local_path": None}, {"bytes": 3, "date": "d1"}) is None
    assert mod._current_copy(old, {"bytes": 3, "date": "d2"}) is None
    assert mod._current_copy(old, {"bytes": 4, "date": "d1"}) is None
    local.write_bytes(b"abcdef")
    assert mod._current_copy(old, {"bytes": 3, "date": "d1"}) is None
    local.unlink()
    assert mod._current_copy(old, {"bytes": 3, "date": "d1"}) is None
    assert mod._current_copy({**old, "bytes": None}, {"bytes": None, "date": "d1"}) is None


# --- arguments ---


def test_card_is_required(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    ctx = make_ctx(trelloctl_home, fake_trello)
    for handler in (mod.list_attachments, mod.download, mod.add, mod.clear):
        raises("USAGE", handler, ctx, {"all": True, "file": "x"})


def test_download_and_add_validate_their_exclusive_options(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ctx = make_ctx(trelloctl_home, fake_trello)
    raises("USAGE", mod.download, ctx, {"card": "c"})
    raises("USAGE", mod.download, ctx, {"card": "c", "all": True, "id": "x"})
    raises("USAGE", mod.add, ctx, {"card": "c"})
    raises("USAGE", mod.add, ctx, {"card": "c", "file": "f", "url": "u"})


def test_read_upload_reports_unreadable_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "a.txt"
    target.write_bytes(b"abc")
    path, content = mod._read_upload(str(target), 3)
    assert (path, content) == (target, b"abc")
    raises("ATTACHMENT_TOO_LARGE", mod._read_upload, str(target), 2)

    def denied(self: Path) -> bytes:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "read_bytes", denied)
    raises("USAGE", mod._read_upload, str(target), 10)


# --- clear and the cache-only card lookup (A-4) ---


def test_clear_by_short_link_name_and_id_short(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    cache, ids, target = downloaded_card(trelloctl_home, fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    for ref in ("cardAAA1", "1", "First card"):
        cache.set_attachment_local_path(BOARD, ids["c1"], "a" * 24, str(target), NOW)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"abc")
        data = mod.clear(ctx, {"card": ref})["data"]
        assert data["count"] == 1
        assert not target.exists()


def test_clear_reports_unknown_cards_and_attachments(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    _, ids, _ = downloaded_card(trelloctl_home, fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    raises("CARD_NOT_FOUND", mod.clear, ctx, {"card": "f" * 24})
    raises("CARD_NOT_FOUND", mod.clear, ctx, {"card": "zzzzzzzz"})
    raises("CARD_NOT_FOUND", mod.clear, ctx, {"card": "99"})
    raises("ATTACHMENT_NOT_FOUND", mod.clear, ctx, {"card": ids["c1"], "id": "b" * 24})


def test_clear_without_a_board_for_a_name_is_not_configured(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    downloaded_card(trelloctl_home, fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello)
    raises("BOARD_NOT_CONFIGURED", mod.clear, ctx, {"card": "First card"})


def test_clear_resolves_a_board_given_by_short_link(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    downloaded_card(trelloctl_home, fake_trello, primed=True)
    ctx = make_ctx(trelloctl_home, fake_trello, board_flag=SHORT)
    assert mod.clear(ctx, {"card": "First card"})["data"]["count"] == 1


def test_clear_with_no_cache_has_nothing_to_delete(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    downloaded_card(trelloctl_home, fake_trello)
    ctx: CommandContext = make_ctx(trelloctl_home, fake_trello, no_cache=True, board=BOARD)
    raises("CARD_NOT_FOUND", mod.clear, ctx, {"card": new_id(fake_trello)})


def test_clear_skips_attachments_that_were_never_downloaded(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    cache, ids, target = downloaded_card(trelloctl_home, fake_trello)
    cache.set_attachment_local_path(BOARD, ids["c1"], "a" * 24, None, NOW)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    data = mod.clear(ctx, {"card": ids["c1"]})["data"]
    assert data["count"] == 0
    assert data["cleared"] == []
    assert target.is_file()


# --- download and add against the fake ---


def test_add_rejects_an_unexpected_response(
    trelloctl_home: Path, fake_trello: FakeTrello, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, datetime.now(UTC))
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    monkeypatch.setattr(
        "trelloctl.infra.trello_client.TrelloClient.post", lambda self, path, params=None: None
    )
    raises("API_ERROR", mod.add, ctx, {"card": ids["c1"], "url": "https://example.com"})


def test_add_falls_back_to_the_response_when_the_cache_has_no_row(
    trelloctl_home: Path, fake_trello: FakeTrello, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = seed_standard(fake_trello)
    prime(trelloctl_home, fake_trello, datetime.now(UTC))
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    monkeypatch.setattr(
        "trelloctl.app.resolver.Resolver.sync_card", lambda self, ident, board_id=None: {}
    )
    data = mod.add(ctx, {"card": ids["c1"], "url": "https://example.com", "name": "Link"})["data"]
    assert data["attachment"]["name"] == "Link"
    assert data["attachment"]["local_path"] is None


def test_download_of_a_failing_file_propagates_other_errors_without_partial(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    att = new_id(fake_trello)
    fake_trello.state.attachments[att] = {
        "id": att,
        "idCard": ids["c1"],
        "name": "a.txt",
        "bytes": 1,
        "date": "2026-01-01T00:00:00.000Z",
        "url": f"https://trello.com/1/cards/{ids['c1']}/attachments/{att}/download/a.txt",
        "isUpload": True,
    }
    prime(trelloctl_home, fake_trello, datetime.now(UTC))
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    fake_trello.fail_next("GET", f"/cards/{ids['c1']}/attachments/{att}/download/a.txt", 500)
    error = raises("API_ERROR", mod.download, ctx, {"card": ids["c1"], "all": True})
    assert "partial" not in error.details

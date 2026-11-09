"""Unit tests of the board reads and the cache helpers of the `board` group."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tests.acceptance.m07_support import BOARD, close_caches, make_ctx, seed_standard  # noqa: F401
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import board as mod
from trelloctl.app.resolver import Resolver


def test_combined_meta_is_a_hit_only_when_every_collection_is() -> None:
    metas = {
        "metadata": {"hit": True, "age_seconds": 5, "stale": False},
        "cards": {"hit": False, "age_seconds": 9, "stale": True, "error": {"code": "TIMEOUT"}},
    }
    resolver: Any = SimpleNamespace(cache_meta=lambda board, collection: metas[collection])
    assert mod.combined_cache_meta(resolver, "b", ("metadata", "cards")) == {
        "hit": False,
        "age_seconds": 9,
        "stale": True,
        "error": {"code": "TIMEOUT"},
    }
    assert mod.combined_cache_meta(resolver, "b", ("metadata",)) == {
        "hit": True,
        "age_seconds": 5,
        "stale": False,
    }


def test_show_with_no_cache_leaves_no_database_and_reports_a_miss(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    result = mod.show(ctx, {})
    assert result["data"]["lists"] == 2
    assert result["meta"]["cache"]["hit"] is False
    assert not (trelloctl_home / "cache.db").exists()


def test_a_default_board_that_does_not_resolve_is_not_the_default(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    ctx = make_ctx(trelloctl_home, fake_trello, board="No such board", board_flag=BOARD)
    assert mod.show(ctx, {})["data"]["is_default"] is False


def test_card_rows_survive_a_rewrite_without_a_label(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    fake_trello.state.cards[ids["c1"]]["idLabels"] = [ids["bug"]]
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD)
    with Resolver(ctx) as resolver:
        resolver.reload_all(BOARD)
        before = resolver.cache.get_card(BOARD, ids["c1"])
        assert before is not None
        assert mod.strip_label_from_cards(resolver, BOARD, ids["bug"]) == 1
        assert mod.strip_label_from_cards(resolver, BOARD, ids["bug"]) == 0
        after = resolver.cache.get_card(BOARD, ids["c1"])
    assert after is not None
    assert after["id_labels"] == []
    assert {k: v for k, v in after.items() if k not in ("id_labels", "fetched_at")} == {
        k: v for k, v in before.items() if k not in ("id_labels", "fetched_at")
    }

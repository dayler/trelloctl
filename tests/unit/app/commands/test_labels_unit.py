"""Unit tests of the `board label` commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m07_support import BOARD, add_label, close_caches, make_ctx, seed_standard  # noqa: F401
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import labels
from trelloctl.domain.errors import TrelloctlError


def test_color_validation_and_the_none_alias() -> None:
    assert labels.api_color("red") == "red"
    assert labels.api_color("pink_dark") == "pink_dark"
    assert labels.api_color("none") == "null"
    for bad in ("magenta", "RED", "red_medium", "", None, 3):
        with pytest.raises(TrelloctlError) as caught:
            labels.api_color(bad)
        assert caught.value.code == "USAGE"


def test_stored_label_prefers_trellos_answer() -> None:
    previous = {"id": "1", "name": "old", "color": "red"}
    assert labels._stored({"name": "new", "extra": 1}, previous) == {
        "id": "1",
        "name": "new",
        "color": "red",
    }
    assert labels._stored(None, previous) == previous


def test_create_and_update_validate_their_arguments_before_any_call(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    for call, args in (
        (labels.create, {"color": "red"}),
        (labels.create, {"name": "x", "color": "magenta"}),
        (labels.update, {"label": "bug"}),
        (labels.update, {"label": "bug", "color": "magenta"}),
    ):
        with pytest.raises(TrelloctlError) as caught:
            call(ctx, args)
        assert caught.value.code == "USAGE"
    assert fake_trello.requests == []


def test_an_unexpected_create_answer_is_an_api_error(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    fake_trello.fail_next("POST", "/labels", 200, "[]")
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    with pytest.raises(TrelloctlError) as caught:
        labels.create(ctx, {"name": "x", "color": "red"})
    assert caught.value.code == "API_ERROR"


def test_remove_works_with_the_memory_cache_and_an_unnamed_label(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    plain = add_label(fake_trello, "", None)
    ctx = make_ctx(trelloctl_home, fake_trello, board=BOARD, no_cache=True)
    result = labels.remove(ctx, {"label": plain})
    assert result["data"]["cards_affected"] == 0
    assert result["data"]["label"] == {"id": plain, "name": "", "color": None}
    assert list(fake_trello.state.labels) == [ids["bug"]]
    assert not (trelloctl_home / "cache.db").exists()

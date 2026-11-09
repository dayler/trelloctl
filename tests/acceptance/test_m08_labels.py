"""M08 acceptance tests: `board label` commands (RF-10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m08_support import (  # noqa: F401
    BOARD,
    add_card,
    add_checklist,
    add_label,
    ago,
    close_caches,
    configure,
    prime,
    query,
    run_json,
    seed_standard,
    writes,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

Capsys = pytest.CaptureFixture[str]


@pytest.mark.tc("TC-M08-023")
@pytest.mark.parametrize(
    ("color", "sent", "stored"),
    [
        ("green", "green", "green"),
        ("sky_light", "sky_light", "sky_light"),
        ("black_dark", "black_dark", "black_dark"),
        ("none", "null", None),
    ],
)
def test_label_create_with_each_kind_of_color(
    color: str,
    sent: str,
    stored: str | None,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: Capsys,
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "label", "create", "--name", "tag", "--color", color)
    assert (code, envelope["ok"]) == (0, True)
    label = envelope["data"]["label"]
    assert (label["name"], label["color"]) == ("tag", stored)
    post = writes(fake_trello)
    assert [(r.method, r.path) for r in post] == [("POST", "/labels")]
    assert query(post[0]) == {"name": "tag", "color": sent, "idBoard": BOARD}
    assert fake_trello.state.labels[label["id"]]["color"] == stored
    cached = {r["id"]: r for r in Cache.open(trelloctl_home).list_labels(BOARD)}
    assert (cached[label["id"]]["name"], cached[label["id"]]["color"]) == ("tag", stored)


@pytest.mark.tc("TC-M08-024")
def test_label_create_with_an_unknown_color_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(
        capsys, "board", "label", "create", "--name", "tag", "--color", "magenta"
    )
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M08-025")
def test_label_update_sends_only_the_given_field(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "label", "update", "bug", "--name", "defect")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["label"] == {"id": ids["bug"], "name": "defect", "color": "red"}
    code, envelope = run_json(capsys, "board", "label", "update", "defect", "--color", "orange")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["label"] == {"id": ids["bug"], "name": "defect", "color": "orange"}
    first, second = writes(fake_trello)
    assert (first.method, first.path) == ("PUT", f"/labels/{ids['bug']}")
    assert query(first) == {"name": "defect"}
    assert query(second) == {"color": "orange"}
    assert fake_trello.state.labels[ids["bug"]] == {
        "id": ids["bug"],
        "idBoard": BOARD,
        "name": "defect",
        "color": "orange",
    }
    cached = Cache.open(trelloctl_home).list_labels(BOARD)
    assert [(r["name"], r["color"]) for r in cached] == [("defect", "orange")]


@pytest.mark.tc("TC-M08-026")
def test_label_remove_deletes_it_and_strips_it_from_the_cached_cards(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_standard(fake_trello)
    other = add_label(fake_trello, "urgent", "yellow")
    third = add_card(fake_trello, ids["todo"], "Third card", id_short=3)
    for card in (ids["c1"], ids["c2"], third):
        fake_trello.state.cards[card]["idLabels"] = [ids["bug"]]
    fake_trello.state.cards[ids["c1"]]["idLabels"].append(other)
    add_checklist(fake_trello, ids["c1"], "QA", ("unit", "e2e"))
    configure(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, ago(100))
    cache = Cache.open(trelloctl_home)
    assert all(ids["bug"] in c["id_labels"] for c in cache.list_cards(BOARD))
    stamp = cache.get_board(BOARD)["metadata_fetched_at"]  # type: ignore[index]
    code, envelope = run_json(capsys, "board", "label", "remove", "bug", "--yes")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["cards_affected"] == 3
    assert envelope["data"]["label"]["id"] == ids["bug"]
    assert [(r.method, r.path) for r in writes(fake_trello)] == [
        ("DELETE", f"/labels/{ids['bug']}")
    ]
    assert ids["bug"] not in fake_trello.state.labels
    assert all(ids["bug"] not in c["idLabels"] for c in fake_trello.state.cards.values())
    rows = {c["id"]: c for c in cache.list_cards(BOARD)}
    assert all(ids["bug"] not in row["id_labels"] for row in rows.values())
    assert rows[ids["c1"]]["id_labels"] == [other]
    assert [x["id"] for x in cache.list_labels(BOARD)] == [other]
    checklists = cache.list_checklists(BOARD, ids["c1"])
    assert [(c["name"], [i["name"] for i in c["items"]]) for c in checklists] == [
        ("QA", ["unit", "e2e"])
    ]
    assert cache.get_board(BOARD)["metadata_fetched_at"] == stamp  # type: ignore[index]


@pytest.mark.tc("TC-M08-027")
def test_label_remove_without_yes_asks_for_confirmation(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "label", "remove", "bug")
    assert (code, envelope["error"]["code"]) == (6, "CONFIRMATION_REQUIRED")
    assert fake_trello.requests == []
    assert ids["bug"] in fake_trello.state.labels

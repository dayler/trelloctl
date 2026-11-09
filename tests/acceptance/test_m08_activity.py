"""M08 acceptance tests: `board activity` (RF-11, AC-13.4)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m08_support import (  # noqa: F401
    BASE_DATE,
    BOARD,
    MEMBER,
    add_action,
    add_actions,
    calls,
    close_caches,
    configure,
    query,
    run_json,
    seed_standard,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

Capsys = pytest.CaptureFixture[str]
ACTIONS = f"/boards/{BOARD}/actions"


def setup(home: Path, fake: FakeTrello) -> None:
    seed_standard(fake)
    configure(home, fake)


@pytest.mark.tc("TC-M08-028")
def test_activity_returns_the_50_newest_and_flags_the_truncation(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    ids = add_actions(fake_trello, 80)
    code, envelope = run_json(capsys, "board", "activity")
    assert (code, envelope["ok"]) == (0, True)
    returned = [a["id"] for a in envelope["data"]["actions"]]
    assert returned == ids[::-1][:50]
    assert envelope["meta"]["truncated"] is True
    assert query(calls(fake_trello, ACTIONS)[0])["limit"] == "51"
    code, envelope = run_json(capsys, "board", "activity", "--limit", "80")
    assert len(envelope["data"]["actions"]) == 80
    assert envelope["meta"]["truncated"] is False


@pytest.mark.tc("TC-M08-029")
def test_activity_without_limit_pages_with_before_until_exhausted(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    ids = add_actions(fake_trello, 1500)
    code, envelope = run_json(capsys, "board", "activity", "--limit", "0")
    assert (code, envelope["ok"]) == (0, True)
    returned = [a["id"] for a in envelope["data"]["actions"]]
    assert returned == ids[::-1]
    assert envelope["meta"]["truncated"] is False
    pages = calls(fake_trello, ACTIONS)
    assert [query(p)["limit"] for p in pages] == ["1000", "1000"]
    assert "before" not in query(pages[0])
    assert query(pages[1])["before"] == ids[::-1][999]


@pytest.mark.tc("TC-M08-030")
def test_since_and_before_are_sent_to_trello_and_filter(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    days = [add_action(fake_trello, "updateCard", BASE_DATE + timedelta(days=d)) for d in range(5)]
    code, envelope = run_json(
        capsys,
        "board",
        "activity",
        "--since",
        "2026-03-02T00:00:00Z",
        "--before",
        "2026-03-04",
    )
    assert (code, envelope["ok"]) == (0, True)
    assert [a["id"] for a in envelope["data"]["actions"]] == [days[2], days[1]]
    sent = query(calls(fake_trello, ACTIONS)[0])
    assert sent["since"] == "2026-03-02T00:00:00Z"
    assert sent["before"] == "2026-03-04T00:00:00Z"


@pytest.mark.tc("TC-M08-031")
@pytest.mark.parametrize("option", ["--since", "--before"])
def test_a_date_that_is_not_iso_8601_is_a_usage_error(
    option: str, trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "activity", option, "ayer")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M08-032")
def test_actions_are_normalized_by_kind(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    card = {"id": "c" * 24, "name": "Fix login"}
    todo = {"id": "d" * 24, "name": "Todo"}
    doing = {"id": "e" * 24, "name": "Doing"}
    created = add_action(fake_trello, "createCard", BASE_DATE, data={"card": card, "list": todo})
    moved = add_action(
        fake_trello,
        "updateCard",
        BASE_DATE + timedelta(minutes=1),
        data={"card": card, "listBefore": todo, "listAfter": doing, "old": {"idList": todo["id"]}},
    )
    commented = add_action(
        fake_trello,
        "commentCard",
        BASE_DATE + timedelta(minutes=2),
        data={"card": card, "text": "looks good"},
    )
    renamed = add_action(
        fake_trello,
        "updateList",
        BASE_DATE + timedelta(minutes=3),
        data={"list": {"id": doing["id"], "name": "Doing now"}, "old": {"name": "Doing"}},
    )
    code, envelope = run_json(capsys, "board", "activity")
    assert (code, envelope["ok"]) == (0, True)
    by_id: dict[str, Any] = {a["id"]: a for a in envelope["data"]["actions"]}
    member = {"id": MEMBER, "username": "ana"}
    assert by_id[created] == {
        "id": created,
        "type": "createCard",
        "date": "2026-03-01T12:00:00.000Z",
        "member": member,
        "card": card,
        "list": todo,
        "data": {"name": "Fix login"},
    }
    assert by_id[moved]["card"] == card
    assert by_id[moved]["list"] == doing
    assert by_id[moved]["data"]["from_list"] == "Todo"
    assert by_id[moved]["data"]["to_list"] == "Doing"
    assert by_id[commented]["data"] == {"text": "looks good"}
    assert "list" not in by_id[commented]
    assert "card" not in by_id[renamed]
    assert by_id[renamed]["list"] == {"id": doing["id"], "name": "Doing now"}
    assert by_id[renamed]["data"]["changes"] == {"name": {"from": "Doing", "to": "Doing now"}}
    assert by_id[renamed]["member"] == member


@pytest.mark.tc("TC-M08-033")
def test_type_filters_the_actions(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    for minute, kind in enumerate(("createCard", "updateCard", "commentCard", "createList")):
        add_action(fake_trello, kind, BASE_DATE + timedelta(minutes=minute))
    code, envelope = run_json(capsys, "board", "activity", "--type", "createCard,commentCard")
    assert (code, envelope["ok"]) == (0, True)
    assert [a["type"] for a in envelope["data"]["actions"]] == ["commentCard", "createCard"]
    assert query(calls(fake_trello, ACTIONS)[0])["filter"] == "createCard,commentCard"


@pytest.mark.tc("TC-M08-034")
def test_an_unknown_action_type_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "activity", "--type", "createCard,fooBar")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert envelope["error"]["details"]["unknown"] == ["fooBar"]
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M08-035")
def test_activity_is_never_cached(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    add_actions(fake_trello, 3)
    for _ in range(2):
        code, envelope = run_json(capsys, "--board", BOARD, "board", "activity")
        assert (code, envelope["ok"]) == (0, True)
        assert envelope["meta"]["cache"]["hit"] is False
    assert len(calls(fake_trello, ACTIONS)) == 2
    cache = Cache.open(trelloctl_home)
    assert cache.list_boards() == []
    assert cache.member_boards() == []
    assert cache.list_cards(BOARD) == []
    assert cache.list_lists(BOARD) == []


@pytest.mark.tc("TC-M08-036")
def test_fields_limits_the_keys_of_every_action(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    add_actions(fake_trello, 3)
    code, envelope = run_json(capsys, "board", "activity", "--fields", "id,type")
    assert (code, envelope["ok"]) == (0, True)
    assert len(envelope["data"]["actions"]) == 3
    assert all(set(a) == {"id", "type"} for a in envelope["data"]["actions"])


@pytest.mark.tc("TC-M08-037")
def test_an_unknown_field_is_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: Capsys
) -> None:
    setup(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "board", "activity", "--fields", "id,nope")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert envelope["error"]["details"]["unknown"] == ["nope"]
    assert fake_trello.requests == []

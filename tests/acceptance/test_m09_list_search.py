"""M09 acceptance tests: `card list` and `card search` (RF-13: AC-13.2 to AC-13.6, A-5, A-20)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m09_support import (
    BOARD,
    NEWER,
    OLD,
    add_checklist,
    cached,
    calls,
    card,
    close_caches,  # noqa: F401
    comment,
    finish,
    names,
    open_cache,
    request_params,
    run,
    seed_world,
)
from tests.fakes.trello import FakeTrello


def ids_of(envelope: dict[str, object]) -> list[str]:
    return [c["id"] for c in envelope["data"]["cards"]]  # type: ignore[index]


# --- card list ---


@pytest.mark.tc("TC-M09-021")
def test_list_filters_combine_and_read_only_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    match = card(
        fake_trello,
        ids["todo"],
        "Match",
        1,
        labels=(ids["bug"],),
        members=(ids["ana"],),
        due="2026-12-05T10:00:00.000Z",
        done=True,
    )
    open_one = card(
        fake_trello,
        ids["todo"],
        "Open match",
        2,
        labels=(ids["bug"],),
        members=(ids["ana"],),
        due="2026-12-06T10:00:00.000Z",
    )
    card(fake_trello, ids["doing"], "Other list", 3, labels=(ids["bug"],), members=(ids["ana"],))
    card(fake_trello, ids["todo"], "No label", 4, members=(ids["ana"],), due="2026-12-05T10:00:00Z")
    card(fake_trello, ids["todo"], "No member", 5, labels=(ids["bug"],), due="2026-12-05T10:00:00Z")
    card(fake_trello, ids["todo"], "Too early", 6, labels=(ids["bug"],), members=(ids["ana"],),
         due="2026-11-01T10:00:00.000Z")  # fmt: skip
    card(fake_trello, ids["todo"], "Too late", 7, labels=(ids["bug"],), members=(ids["ana"],),
         due="2027-01-01T10:00:00.000Z")  # fmt: skip
    card(fake_trello, ids["todo"], "Archived", 8, labels=(ids["bug"],), closed=True)
    finish(trelloctl_home, fake_trello)
    base = [
        "card",
        "list",
        "--list",
        "Todo",
        "--label",
        "bug",
        "--member",
        "ana",
        "--due-before",
        "2026-12-31",
        "--due-after",
        "2026-12-01",
    ]
    code, out = run(capsys, *base, "--done")
    assert (code, ids_of(out)) == (0, [match])
    code, out = run(capsys, *base, "--not-done")
    assert (code, ids_of(out)) == (0, [open_one])
    code, out = run(capsys, *base)
    assert ids_of(out) == [match, open_one]
    assert fake_trello.requests == []
    assert out["meta"]["cache"]["hit"] is True


@pytest.mark.tc("TC-M09-022")
def test_list_without_filters_returns_every_open_card_in_board_order(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    third = card(fake_trello, ids["done"], "c", 3)
    second = card(fake_trello, ids["doing"], "b", 2)
    first_b = card(fake_trello, ids["todo"], "a2", 5, pos=20)
    first_a = card(fake_trello, ids["todo"], "a1", 4, pos=10)
    card(fake_trello, ids["todo"], "archived", 6, closed=True)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "list")
    assert (code, ids_of(out)) == (0, [first_a, first_b, second, third])
    assert out["meta"]["truncated"] is False
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-023")
def test_done_and_not_done_together_are_a_usage_error(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "list", "--done", "--not-done")
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


# --- card search ---


@pytest.mark.tc("TC-M09-024")
def test_search_without_wildcards_means_contains_in_name_and_description(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    by_name = card(fake_trello, ids["todo"], "Deploy docs", 1)
    by_desc = card(fake_trello, ids["doing"], "Docs", 2, desc="how to deploy it")
    both = card(fake_trello, ids["done"], "Redeploy", 3, desc="deploy steps")
    card(fake_trello, ids["done"], "Unrelated", 4, desc="nothing")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "deploy")
    assert code == 0
    assert {c["id"]: c["matched"] for c in out["data"]["cards"]} == {
        by_name: ["name"],
        by_desc: ["desc"],
        both: ["name", "desc"],
    }
    code, star = run(capsys, "card", "search", "*deploy*")
    assert ids_of(star) == ids_of(out)
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-025")
def test_question_mark_is_one_character_and_star_any_sequence(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    for index, name in enumerate(("core-78", "core-79", "core-780", "core-7", "xcore-78"), 1):
        card(fake_trello, ids["todo"], name, index)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "core-7?")
    assert (code, names(out)) == (0, ["core-78", "core-79"])
    code, out = run(capsys, "card", "search", "core-78*")
    assert (code, names(out)) == (0, ["core-78", "core-780"])


@pytest.mark.tc("TC-M09-026")
def test_search_ignores_case_also_for_non_ascii_letters(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    card(fake_trello, ids["todo"], "árbol", 1)
    card(fake_trello, ids["todo"], "deploy", 2)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "ÁRBOL")
    assert (code, names(out)) == (0, ["árbol"])
    code, out = run(capsys, "card", "search", "Deploy")
    assert (code, names(out)) == (0, ["deploy"])


@pytest.mark.tc("TC-M09-027")
def test_search_in_checklists_matches_checklist_and_item_names(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    one = card(fake_trello, ids["todo"], "One", 1, desc="release notes")
    two = card(fake_trello, ids["todo"], "Two", 2)
    add_checklist(fake_trello, one, "Release steps", ("tag", "publish"))
    add_checklist(fake_trello, two, "QA", ("run smoke tests",))
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "release", "--in", "checklists")
    assert (code, ids_of(out)) == (0, [one])
    assert out["data"]["cards"][0]["matched"] == ["checklists"]
    code, out = run(capsys, "card", "search", "smoke", "--in", "checklists")
    assert (code, ids_of(out)) == (0, [two])
    assert out["data"]["cards"][0]["matched"] == ["checklists"]
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-028")
def test_search_in_comments_makes_one_search_request_for_the_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    one = card(fake_trello, ids["todo"], "One", 1)
    card(fake_trello, ids["todo"], "Two", 2)
    said = comment(fake_trello, one, "please deploy the fix today")
    comment(fake_trello, card(fake_trello, ids["todo"], "Three", 3), "unrelated text")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "deploy", "--in", "comments")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["cards"][0]["id"] == one
    assert [(c["matched"], c["comment_ids"]) for c in out["data"]["cards"]] == [
        (["comments"], [said])
    ]
    (search,) = calls(fake_trello, "/search")
    params = request_params(search)
    assert params["query"] == "comment:deploy"
    assert params["idBoards"] == BOARD
    assert params["partial"] == "true"
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M09-029")
def test_comment_search_drops_wildcards_and_warns_while_local_search_keeps_them(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    local = card(fake_trello, ids["todo"], "core-78 fix", 1)
    remote = card(fake_trello, ids["todo"], "Other", 2)
    card(fake_trello, ids["todo"], "xcore-78", 3)
    said = comment(fake_trello, remote, "see core-78 later")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "core-78*", "--in", "name,comments")
    assert (code, out["ok"]) == (0, True)
    (search,) = calls(fake_trello, "/search")
    assert request_params(search)["query"] == "comment:core-78"
    assert any("wildcard" in w for w in out["meta"]["warnings"])
    found = {c["id"]: (c["matched"], c.get("comment_ids")) for c in out["data"]["cards"]}
    assert found == {local: (["name"], None), remote: (["comments"], [said])}


@pytest.mark.tc("TC-M09-030")
@pytest.mark.parametrize("pattern", ["*", "?*?", "[]"])
def test_a_pattern_that_is_empty_without_wildcards_cannot_search_comments(
    pattern: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", pattern, "--in", "comments")
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-031")
@pytest.mark.parametrize("fields", ["labels", "name,labels", "bogus"])
def test_search_in_an_unknown_field_is_a_usage_error(
    fields: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "x", "--in", fields)
    assert (code, out["error"]["code"]) == (2, "USAGE")
    assert fake_trello.requests == []


# --- limits, fields, archived, fresh ---


def seed_many(fake: FakeTrello, count: int) -> None:
    ids = seed_world(fake)
    for index in range(1, count + 1):
        card(fake, ids["todo"], f"card {index:03d}", index)


@pytest.mark.tc("TC-M09-032")
@pytest.mark.parametrize("command", ["list", "search"])
def test_listings_are_limited_to_50_by_default(
    command: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_many(fake_trello, 120)
    finish(trelloctl_home, fake_trello)
    base = ["card", command] + (["card"] if command == "search" else [])
    code, out = run(capsys, *base)
    assert (code, len(out["data"]["cards"]), out["meta"]["truncated"]) == (0, 50, True)
    code, out = run(capsys, *base, "--limit", "10")
    assert (len(out["data"]["cards"]), out["meta"]["truncated"]) == (10, True)
    code, out = run(capsys, *base, "--limit", "0")
    assert (len(out["data"]["cards"]), out["meta"]["truncated"]) == (120, False)
    code, out = run(capsys, *base, "--fields", "id,name")
    assert all(set(c) == {"id", "name"} for c in out["data"]["cards"])
    code, out = run(capsys, *base, "--fields", "bogus")
    assert (code, out["error"]["code"]) == (2, "USAGE")
    code, out = run(capsys, *base, "--limit", "-1")
    assert (code, out["error"]["code"]) == (2, "USAGE")


@pytest.mark.tc("TC-M09-033")
@pytest.mark.parametrize(("count", "truncated"), [(50, False), (51, True)])
def test_truncated_is_true_only_when_results_were_left_out(
    count: int,
    truncated: bool,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_many(fake_trello, count)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "list")
    assert (code, len(out["data"]["cards"]) == 50, out["meta"]["truncated"]) == (0, True, truncated)
    code, out = run(capsys, "card", "search", "card")
    assert out["meta"]["truncated"] is truncated


def seed_archived(fake: FakeTrello) -> dict[str, str]:
    ids = seed_world(fake)
    ids["open"] = card(fake, ids["todo"], "Deploy open", 1)
    ids["gone"] = card(fake, ids["todo"], "Deploy archived", 2, closed=True)
    return ids


@pytest.mark.tc("TC-M09-034")
def test_include_archived_reloads_the_closed_cards_when_there_is_no_snapshot(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_archived(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "list", "--include-archived")
    assert (code, out["ok"]) == (0, True)
    assert {c["id"]: c["archived"] for c in out["data"]["cards"]} == {
        ids["open"]: False,
        ids["gone"]: True,
    }
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/closed")) == 1


@pytest.mark.tc("TC-M09-035")
def test_a_fresh_archived_snapshot_costs_nothing_and_search_skips_archived_by_default(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_archived(fake_trello)
    finish(trelloctl_home, fake_trello, archived=True)
    code, out = run(capsys, "card", "list", "--include-archived")
    assert (code, [c["id"] for c in out["data"]["cards"]]) == (0, [ids["open"], ids["gone"]])
    assert fake_trello.requests == []
    code, out = run(capsys, "card", "search", "deploy")
    assert (code, ids_of(out)) == (0, [ids["open"]])
    code, out = run(capsys, "card", "list")
    assert ids_of(out) == [ids["open"]]
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M09-036")
def test_fresh_reads_from_trello_and_updates_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    one = card(fake_trello, ids["todo"], "Before", 1)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "get", one)
    assert out["data"]["name"] == "Before"
    fake_trello.state.cards[one].update(name="After get", dateLastActivity=NEWER)
    code, out = run(capsys, "card", "get", one)
    assert out["data"]["name"] == "Before"
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "get", one, "--fresh")
    assert (code, out["data"]["name"]) == (0, "After get")
    assert calls(fake_trello, f"/cards/{one}")
    assert out["meta"]["cache"]["hit"] is False
    row = cached(trelloctl_home, one)
    assert row is not None
    assert row["name"] == "After get"
    for command, name in (("list", "After list"), ("search", "After search")):
        fake_trello.state.cards[one].update(name=name, dateLastActivity=NEWER)
        fake_trello.requests.clear()
        argv = ["card", command] + (["After"] if command == "search" else [])
        code, out = run(capsys, *argv)
        assert out["data"]["cards"][0]["name"] != name
        assert fake_trello.requests == []
        code, out = run(capsys, *argv, "--fresh")
        assert (code, out["data"]["cards"][0]["name"]) == (0, name)
        assert calls(fake_trello, f"/boards/{BOARD}/cards/all")
        assert out["meta"]["cache"]["hit"] is False
        row = cached(trelloctl_home, one)
        assert row is not None
        assert row["name"] == name


# --- search details of A-20 and archived search ---


@pytest.mark.tc("TC-M09-057")
def test_brackets_are_a_glob_class_locally_and_are_dropped_for_comments(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_world(fake_trello)
    for index, name in enumerate(("fix1", "fix2", "fix3", "fix12", "other"), 1):
        card(fake_trello, ids["todo"], name, index)
    holder = card(fake_trello, ids["doing"], "Holder", 9)
    comment(fake_trello, holder, "applied fix12 yesterday")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "fix[12]")
    assert (code, names(out)) == (0, ["fix1", "fix2"])
    assert fake_trello.requests == []
    code, out = run(capsys, "card", "search", "fix[12]", "--in", "comments")
    assert (code, out["ok"]) == (0, True)
    (search,) = calls(fake_trello, "/search")
    assert request_params(search)["query"] == "comment:fix12"
    assert "[" not in request_params(search)["query"]
    assert any("brackets" in w for w in out["meta"]["warnings"])
    assert ids_of(out) == [holder]


@pytest.mark.tc("TC-M09-058")
def test_search_include_archived_reloads_a_missing_or_expired_snapshot(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = seed_archived(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "search", "deploy", "--include-archived")
    assert (code, out["ok"]) == (0, True)
    assert {c["id"]: c["archived"] for c in out["data"]["cards"]} == {
        ids["open"]: False,
        ids["gone"]: True,
    }
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/closed")) == 1
    cache = open_cache(trelloctl_home)
    cache.replace_archived(BOARD, [], OLD)
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "search", "deploy", "--include-archived")
    assert [c["archived"] for c in out["data"]["cards"]] == [False, True]
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/closed")) == 1

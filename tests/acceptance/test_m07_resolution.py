"""M07 acceptance tests: resolution of every entity, reloads on a miss and stale references."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    OTHER,
    add_card,
    add_checklist,
    add_label,
    add_list,
    add_member,
    calls,
    close_caches,
    make_ctx,
    make_resolver,
    new_id,
    paths,
    prime,
    seed_board,
    seed_standard,
)
from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import Request, Response, text_response

from trelloctl.app.resolver import Resolver, iso_utc
from trelloctl.domain.errors import TrelloctlError

T0 = datetime(2026, 3, 1, tzinfo=UTC)
LATER = iso_utc(T0 + timedelta(seconds=60))


def fresh_resolver(home: Path, fake: FakeTrello, **kwargs: Any) -> Resolver:
    """A resolver at T0 over a cache primed at T0: nothing is expired."""
    return make_resolver(home, fake, FakeClock(T0), **kwargs)


def setup(home: Path, fake: FakeTrello, *, checklist: bool = True) -> dict[str, str]:
    ids = seed_standard(fake)
    if checklist:
        ids["qa"] = add_checklist(fake, ids["c1"], "QA", ("unit tests", "e2e tests"))
    prime(home, fake, T0)
    fake.requests.clear()
    return ids


# --- plain resolution (AC-7.2) ---


@pytest.mark.tc("TC-M07-025")
def test_a_list_is_resolved_by_id_and_by_name_in_any_case(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    for ref in (ids["todo"], "Todo", "todo", "TODO", " ToDo "):
        assert resolver.resolve_list(BOARD, ref)["id"] == ids["todo"]
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M07-026")
def test_every_entity_resolves_by_its_identifiers(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    for ref in (ids["bug"], "bug", "BUG:red", "bug:RED"):
        assert resolver.resolve_label(BOARD, ref)["id"] == ids["bug"]
    for ref in (ids["ana"], "ana", "@ana", "Ana Gomez"):
        assert resolver.resolve_member(BOARD, ref)["id"] == ids["ana"]
    for ref in (ids["qa"], "qa", "QA"):
        assert resolver.resolve_checklist(BOARD, ids["c1"], ref)["id"] == ids["qa"]
    checklist, item = resolver.resolve_checkitem(BOARD, ids["c1"], "QA", "E2E tests")
    assert (checklist["id"], item["name"]) == (ids["qa"], "e2e tests")
    assert (
        resolver.resolve_checkitem(BOARD, ids["c1"], ids["qa"], item["id"])[1]["id"] == item["id"]
    )
    comment = "c" * 24
    assert resolver.resolve_comment(comment) == comment
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M07-027")
@pytest.mark.parametrize("text", ["a comment text", "ccc", "c" * 23])
def test_a_comment_given_as_text_is_not_found(
    text: str, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_comment(text)
    assert info.value.code == "COMMENT_NOT_FOUND"
    assert info.value.details["comment"] == text
    assert fake_trello.requests == []


def seed_duplicates(fake: FakeTrello) -> dict[str, str]:
    ids = seed_standard(fake)
    ids["todo2"] = add_list(fake, "Todo", 3)
    add_label(fake, "Dup", "red")
    add_label(fake, "dup", "blue")
    add_member(fake, "ana2", "Ana Gomez")
    add_card(fake, ids["todo"], "Dup card", id_short=10)
    add_card(fake, ids["doing"], "dup CARD", id_short=11)
    ids["d1"] = add_checklist(fake, ids["c1"], "Dups", ("same", "Same"), pos=1)
    add_checklist(fake, ids["c1"], "Dups", (), pos=2)
    return ids


@pytest.mark.tc("TC-M07-028")
@pytest.mark.parametrize(
    ("entity", "ref", "context"),
    [
        ("list", "todo", "closed"),
        ("label", "dup", "color"),
        ("member", "ana gomez", "username"),
        ("checklist", "dups", None),
        ("checkitem", "same", "checklist"),
        ("card", "dup card", "list"),
    ],
)
def test_an_ambiguous_name_lists_the_candidates_and_writes_nothing(
    entity: str,
    ref: str,
    context: str | None,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
) -> None:
    ids = seed_duplicates(fake_trello)
    prime(trelloctl_home, fake_trello, T0)
    fake_trello.requests.clear()
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    calls_by_entity: dict[str, Callable[[], object]] = {
        "list": lambda: resolver.resolve_list(BOARD, ref),
        "label": lambda: resolver.resolve_label(BOARD, ref),
        "member": lambda: resolver.resolve_member(BOARD, ref),
        "checklist": lambda: resolver.resolve_checklist(BOARD, ids["c1"], ref),
        "checkitem": lambda: resolver.resolve_checkitem(BOARD, ids["c1"], ids["d1"], ref),
        "card": lambda: resolver.resolve_card(BOARD, ref),
    }
    with pytest.raises(TrelloctlError) as info:
        calls_by_entity[entity]()
    assert info.value.code == f"AMBIGUOUS_{entity.upper()}"
    candidates = info.value.details["candidates"]
    assert len(candidates) == 2
    assert all(c["id"] and c["name"] for c in candidates)
    assert len({c["id"] for c in candidates}) == 2
    if context is not None:
        assert all(context in c for c in candidates)
    if entity == "card":
        assert {c["list"] for c in candidates} == {"Todo", "Doing"}
    if entity == "label":
        assert {c["color"] for c in candidates} == {"red", "blue"}
    assert {r.method for r in fake_trello.requests} <= {"GET"}


@pytest.mark.tc("TC-M07-029")
def test_a_label_name_with_a_color_suffix_picks_the_right_one(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_board(fake_trello)
    red = add_label(fake_trello, "bug", "red")
    blue = add_label(fake_trello, "bug", "blue")
    colon = add_label(fake_trello, "ratio:3", "green")
    prime(trelloctl_home, fake_trello, T0)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    assert resolver.resolve_label(BOARD, "bug:red")["id"] == red
    assert resolver.resolve_label(BOARD, "Bug:Blue")["id"] == blue
    assert resolver.resolve_label(BOARD, "ratio:3")["id"] == colon
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_label(BOARD, "bug")
    assert info.value.code == "AMBIGUOUS_LABEL"
    assert {c["color"] for c in info.value.details["candidates"]} == {"red", "blue"}


# --- a miss reloads the collection once (AC-22.12, AC-22.9) ---


@pytest.mark.tc("TC-M07-030")
@pytest.mark.parametrize("entity", ["list", "label", "member", "card", "checklist", "checkitem"])
def test_a_missing_name_reloads_its_collection_once_and_then_fails(
    entity: str, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    by_entity: dict[str, tuple[Callable[[], object], list[str]]] = {
        "list": (lambda: resolver.resolve_list(BOARD, "Nowhere"), [f"/boards/{BOARD}"]),
        "label": (lambda: resolver.resolve_label(BOARD, "Nowhere"), [f"/boards/{BOARD}"]),
        "member": (lambda: resolver.resolve_member(BOARD, "Nowhere"), [f"/boards/{BOARD}"]),
        "card": (
            lambda: resolver.resolve_card(BOARD, "Nowhere"),
            [f"/boards/{BOARD}/cards/all"],
        ),
        "checklist": (
            lambda: resolver.resolve_checklist(BOARD, ids["c1"], "Nowhere"),
            [f"/cards/{ids['c1']}"],
        ),
        "checkitem": (
            lambda: resolver.resolve_checkitem(BOARD, ids["c1"], "QA", "Nowhere"),
            [f"/cards/{ids['c1']}"],
        ),
    }
    action, expected = by_entity[entity]
    with pytest.raises(TrelloctlError) as info:
        action()
    assert info.value.code == f"{entity.upper()}_NOT_FOUND"
    assert info.value.details[entity] == "Nowhere"
    assert paths(fake_trello) == expected  # one reload, one retry from the reloaded cache
    assert {r.method for r in fake_trello.requests} == {"GET"}


@pytest.mark.tc("TC-M07-031")
def test_a_name_that_appears_after_the_reload_resolves(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    review = add_list(fake_trello, "Review", 3)
    feature = add_label(fake_trello, "feature", "green")
    bob = add_member(fake_trello, "bob")
    fresh_card = add_card(fake_trello, ids["todo"], "Fresh card", id_short=5, changed=LATER)
    new_checklist = add_checklist(fake_trello, ids["c1"], "Release", ("tag",))
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    assert resolver.resolve_list(BOARD, "review")["id"] == review
    assert resolver.resolve_label(BOARD, "feature")["id"] == feature
    assert resolver.resolve_member(BOARD, "bob")["id"] == bob
    assert resolver.resolve_card(BOARD, "fresh card")["id"] == fresh_card
    assert resolver.resolve_checklist(BOARD, ids["c1"], "release")["id"] == new_checklist
    assert len(calls(fake_trello, f"/boards/{BOARD}")) == 1  # the metadata reload is shared
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/all")) == 1


@pytest.mark.tc("TC-M07-032")
def test_an_id_short_that_appears_after_the_incremental_reload_resolves(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    new = add_card(fake_trello, ids["todo"], "Created later", id_short=77, changed=LATER)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    assert resolver.resolve_card(BOARD, "77")["id"] == new
    [request] = calls(fake_trello, f"/boards/{BOARD}/cards/all")
    assert request.query["modifiedSince"] == [iso_utc(T0)]
    assert paths(fake_trello) == [f"/boards/{BOARD}/cards/all"]


@pytest.mark.tc("TC-M07-033")
def test_an_id_short_missing_after_the_reload_is_card_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_card(BOARD, "999")
    assert info.value.code == "CARD_NOT_FOUND"
    assert info.value.details["card"] == "999"
    assert len(calls(fake_trello, f"/boards/{BOARD}/cards/all")) == 1


# --- stale references on writes (AC-22.13, A-22) ---


@dataclass
class Case:
    entity: str
    name: str
    method: str
    template: str
    reload_path: Callable[[dict[str, str]], str]
    replace: Callable[[FakeTrello, dict[str, str]], str]
    old: Callable[[dict[str, str]], str]
    kwargs: Callable[[dict[str, str]], dict[str, Any]]
    confirm: str


def _replace_list(fake: FakeTrello, ids: dict[str, str]) -> str:
    del fake.state.lists[ids["todo"]]
    return add_list(fake, "Todo", 1)


def _replace_label(fake: FakeTrello, ids: dict[str, str]) -> str:
    del fake.state.labels[ids["bug"]]
    return add_label(fake, "bug", "red")


def _replace_checklist(fake: FakeTrello, ids: dict[str, str]) -> str:
    old = fake.state.checklists.pop(ids["qa"])
    return add_checklist(fake, ids["c1"], "QA", tuple(i["name"] for i in old["checkItems"]))


def _replace_item(fake: FakeTrello, ids: dict[str, str]) -> str:
    checklist = fake.state.checklists[ids["qa"]]
    checklist["checkItems"] = [
        {"id": new_id(fake), "name": i["name"], "state": "incomplete", "pos": i["pos"]}
        for i in checklist["checkItems"]
    ]
    return str(checklist["checkItems"][0]["id"])


def _replace_card(fake: FakeTrello, ids: dict[str, str]) -> str:
    del fake.state.cards[ids["c1"]]
    return add_card(fake, ids["todo"], "First card", id_short=1, changed=LATER)


CASES = [
    Case(
        "list",
        "Todo",
        "PUT",
        "/lists/{rid}",
        lambda ids: f"/boards/{BOARD}",
        _replace_list,
        lambda ids: ids["todo"],
        lambda ids: {},
        "/lists/{rid}",
    ),
    Case(
        "label",
        "bug",
        "PUT",
        "/labels/{rid}",
        lambda ids: f"/boards/{BOARD}",
        _replace_label,
        lambda ids: ids["bug"],
        lambda ids: {},
        "/labels/{rid}",
    ),
    Case(
        "checklist",
        "QA",
        "PUT",
        "/checklists/{rid}",
        lambda ids: f"/cards/{ids['c1']}",
        _replace_checklist,
        lambda ids: ids["qa"],
        lambda ids: {"card_id": ids["c1"]},
        "/checklists/{rid}",
    ),
    Case(
        "checkitem",
        "unit tests",
        "PUT",
        "/cards/{card}/checkItem/{rid}",
        lambda ids: f"/cards/{ids['c1']}",
        _replace_item,
        lambda ids: next(iter(i for i in ids["items"].split(","))),  # replaced in the test
        lambda ids: {"card_id": ids["c1"], "checklist": "QA"},
        "/checklists/{checklist_id}/checkItems/{rid}",
    ),
    Case(
        "card",
        "First card",
        "PUT",
        "/cards/{rid}",
        lambda ids: f"/boards/{BOARD}/cards/open",
        _replace_card,
        lambda ids: ids["c1"],
        lambda ids: {},
        "/cards/{rid}",
    ),
]


def write_route(
    fake: FakeTrello, case: Case, gone: set[str], failure: Response, log: list[str]
) -> None:
    template = case.template.replace("{rid}", "{resource}")

    def handler(req: Request) -> Response:
        resource = req.params["resource"]
        log.append(resource)
        if resource in gone:
            return failure
        return Response(200, b'{"id": "' + resource.encode() + b'"}')

    fake.router.route(case.method, template)(handler)


def confirm_route(fake: FakeTrello, case: Case, log: list[str], found: bool) -> None:
    template = case.confirm.replace("{rid}", "{resource}").replace("{checklist_id}", "{checklist}")

    def handler(req: Request) -> Response:
        resource = req.params["resource"]
        log.append(resource)
        if found:
            return Response(200, b'{"id": "' + resource.encode() + b'"}')
        return text_response("The requested resource was not found.", 404)

    if case.entity != "card":  # the fake already serves GET /cards/{id}
        fake.router.route("GET", template)(handler)


def stale_setup(home: Path, fake: FakeTrello, case: Case) -> tuple[Resolver, dict[str, str], str]:
    ids = setup(home, fake)
    ids["items"] = ",".join(i["id"] for i in fake.state.checklists[ids["qa"]]["checkItems"])
    old = case.old(ids)
    fake.requests.clear()
    return fresh_resolver(home, fake), ids, old


def invoke(resolver: Resolver, case: Case, ids: dict[str, str], ref: str, log: list[Any]) -> Any:
    def call(row: dict[str, Any]) -> Any:
        return resolver.client.put(_path(case, ids, row["id"]), {"value": "x"})

    return resolver.guarded_write(case.entity, BOARD, ref, call, **case.kwargs(ids))


def _path(case: Case, ids: dict[str, str], resource: str) -> str:
    return (
        case.template.replace("{rid}", resource)
        .replace("{card}", ids["c1"])
        .replace("{resource}", resource)
    )


FAILURES = [
    pytest.param(404, "The requested resource was not found.", id="404"),
    pytest.param(400, "invalid id", id="400-invalid-id"),
]


@pytest.mark.tc("TC-M07-034")
@pytest.mark.parametrize("case", CASES, ids=lambda c: c.entity)
@pytest.mark.parametrize(("status", "body"), FAILURES)
def test_a_stale_id_from_a_name_reloads_resolves_again_and_retries_once(
    case: Case, status: int, body: str, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    resolver, ids, old = stale_setup(trelloctl_home, fake_trello, case)
    if case.entity == "checkitem":
        old = ids["items"].split(",")[0]
    new = case.replace(fake_trello, ids)
    writes: list[str] = []
    write_route(fake_trello, case, {old}, text_response(body, status), writes)
    result = invoke(resolver, case, ids, case.name, [])
    assert result["id"] == new
    assert writes == [old, new]
    assert len(calls(fake_trello, case.reload_path(ids))) == 1
    assert {r.method for r in fake_trello.requests} == {"GET", "PUT"}


@pytest.mark.tc("TC-M07-034")
def test_a_second_stale_answer_after_the_retry_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    case = CASES[0]
    resolver, ids, old = stale_setup(trelloctl_home, fake_trello, case)
    new = case.replace(fake_trello, ids)
    writes: list[str] = []
    write_route(fake_trello, case, {old, new}, text_response("gone", 404), writes)
    with pytest.raises(TrelloctlError) as info:
        invoke(resolver, case, ids, case.name, [])
    assert info.value.code == "LIST_NOT_FOUND"
    assert writes == [old, new]


USER_CASES = [*CASES, "member"]


@pytest.mark.tc("TC-M07-035")
@pytest.mark.parametrize("entity", ["list", "label", "checklist", "checkitem", "card", "member"])
@pytest.mark.parametrize(("status", "body"), FAILURES)
def test_a_stale_id_given_by_the_user_is_confirmed_and_not_retried(
    entity: str, status: int, body: str, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    if entity == "member":
        case = Case(
            "member",
            "",
            "PUT",
            "/members/{rid}",
            lambda ids: "",
            lambda fake, ids: "",
            lambda ids: ids["ana"],
            lambda ids: {},
            "/members/{rid}",
        )
    else:
        case = next(c for c in CASES if c.entity == entity)
    resolver, ids, old = stale_setup(trelloctl_home, fake_trello, case)
    if entity == "checkitem":
        old = ids["items"].split(",")[0]
    if entity == "member":
        del fake_trello.state.members[ids["ana"]]
    elif entity == "card":
        del fake_trello.state.cards[old]
    writes: list[str] = []
    confirms: list[str] = []
    write_route(fake_trello, case, {old}, text_response(body, status), writes)
    confirm_route(fake_trello, case, confirms, found=False)
    with pytest.raises(TrelloctlError) as info:
        invoke(resolver, case, ids, old, [])
    assert info.value.code == f"{entity.upper()}_NOT_FOUND"
    assert info.value.details[entity] == old
    assert writes == [old]  # the write is never retried
    if entity != "card" and entity != "member":
        assert confirms == [old]
    assert len(calls(fake_trello, f"/boards/{BOARD}")) == 0  # no collection reload
    assert resolver.cache.is_card_deleted(BOARD, old) is (entity == "card")


@pytest.mark.tc("TC-M07-036")
def test_an_id_already_marked_deleted_fails_without_any_request(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    resolver = fresh_resolver(trelloctl_home, fake_trello)
    resolver.cache.mark_card_deleted(BOARD, ids["c1"])
    fake_trello.requests.clear()
    with pytest.raises(TrelloctlError) as info:
        resolver.resolve_card(BOARD, ids["c1"])
    assert info.value.code == "CARD_NOT_FOUND"
    with pytest.raises(TrelloctlError) as located:
        resolver.locate_card(ids["c1"])
    assert located.value.code == "CARD_NOT_FOUND"
    invoked: list[dict[str, Any]] = []
    with pytest.raises(TrelloctlError) as guarded:
        resolver.guarded_write("card", BOARD, ids["c1"], invoked.append)
    assert guarded.value.code == "CARD_NOT_FOUND"
    assert invoked == []
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M07-050")
@pytest.mark.parametrize("entity", ["list", "card"])
def test_a_400_on_a_user_id_whose_confirmation_finds_the_entity_keeps_the_original_error(
    entity: str, trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    case = next(c for c in CASES if c.entity == entity)
    resolver, ids, old = stale_setup(trelloctl_home, fake_trello, case)
    writes: list[str] = []
    confirms: list[str] = []
    write_route(fake_trello, case, {old}, text_response("invalid id", 400), writes)
    confirm_route(fake_trello, case, confirms, found=True)
    with pytest.raises(TrelloctlError) as info:
        invoke(resolver, case, ids, old, [])
    assert info.value.code == "API_ERROR"
    assert info.value.details["status"] == 400
    assert writes == [old]
    assert resolver.cache.is_card_deleted(BOARD, old) is False
    assert len(calls(fake_trello, f"/boards/{BOARD}")) == 0
    gets = [r.path for r in fake_trello.requests if r.method == "GET"]
    assert gets == [f"/{entity}s/{old}"]


# --- locating a card without a selected board (AC-28.7, A-15) ---


@pytest.mark.tc("TC-M07-051")
def test_a_card_is_located_without_a_selected_board_only_by_id_or_url(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    elsewhere = add_list(fake_trello, "Inbox", 1, OTHER)
    far = add_card(fake_trello, elsewhere, "Far card", id_short=1, board_id=OTHER)
    ctx = make_ctx(trelloctl_home, fake_trello)
    assert ctx.settings.board_default == ""

    def located(ref: str) -> Any:
        return Resolver(ctx, clock=FakeClock(T0).now).locate_card(ref)

    result = located(far)
    assert (result.board_id, result.card["id"]) == (OTHER, far)
    assert paths(fake_trello) == [f"/cards/{far}"]
    query = calls(fake_trello, f"/cards/{far}")[0].query
    assert (query["attachments"], query["checklists"]) == (["true"], ["all"])
    fake_trello.requests.clear()
    result = located("https://trello.com/c/cardAAA1/1-first-card")
    assert (result.board_id, result.card["id"]) == (BOARD, ids["c1"])
    assert paths(fake_trello) == ["/cards/cardAAA1"]
    fake_trello.requests.clear()
    for ref in ("cardBBB2", "2", "Second card"):
        with pytest.raises(TrelloctlError) as info:
            located(ref)
        assert info.value.code == "BOARD_NOT_CONFIGURED"
    assert fake_trello.requests == []
    assert located(far).card["id"] == far  # now served from the cache of its own board
    assert fake_trello.requests == []

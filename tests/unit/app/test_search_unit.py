"""Unit tests of `app/search.py`: patterns, field lists, local matching and comment hits."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from trelloctl.app import search
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import Cache

NOW = datetime(2026, 3, 1, tzinfo=UTC)
BOARD = "5f1b2c3d4e5f6a7b8c9d0e1f"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ("name", "desc")),
        (["desc", "name"], ("name", "desc")),
        ([" Comments ", "checklists"], ("checklists", "comments")),
        (["name", "name"], ("name",)),
    ],
)
def test_fields_are_canonical(raw: list[str] | None, expected: tuple[str, ...]) -> None:
    assert search.parse_fields(raw) == expected


@pytest.mark.parametrize("raw", [["labels"], ["name", "bogus"], [], [" "]])
def test_unknown_or_empty_fields_are_usage(raw: list[str]) -> None:
    with pytest.raises(TrelloctlError) as info:
        search.parse_fields(raw)
    assert info.value.code == "USAGE"


@pytest.mark.parametrize(
    ("pattern", "value", "expected"),
    [
        ("deploy", "Redeploy now", True),
        ("deploy", "nothing", False),
        ("DEPLOY", "deploy", True),
        ("árbol", "ÁRBOL grande", True),
        ("core-7?", "core-78", True),
        ("core-7?", "core-780", False),
        ("core-78*", "core-78 fix", True),
        ("core-78*", "xcore-78", False),
        ("*fix", "a\nfix", True),
        ("a**b", "axxb", True),
        ("fix[12]", "fix1", True),
        ("fix[12]", "fix3", False),
        ("fix[^12]", "fix3", True),
        ("fix[^12]", "fix1", False),
        ("v[0-9]", "v7", True),
        ("v[0-9]", "va", False),
        ("a[]]b", "a]b", True),
        ("a[^]]b", "axb", True),
        ("a[b", "xa[bx", True),
        ("a[b", "ab", False),
        ("[z-a]", "[z-a]", True),
        ("a.b", "xa.bx", True),
        ("a.b", "axb", False),
        ("a(b)", "a(b)", True),
        ("[a\\]", "a", True),
    ],
)
def test_matcher(pattern: str, value: str, expected: bool) -> None:
    assert search.matcher(pattern)(value) is expected


@pytest.mark.parametrize(
    ("pattern", "terms", "changed"),
    [
        ("core-78*", "core-78", True),
        ("fix[12]", "fix12", True),
        ("two words", "two words", False),
        ("  padded  ", "padded", False),
        ("*", "", True),
        ("?[]", "", True),
    ],
)
def test_comment_terms(pattern: str, terms: str, changed: bool) -> None:
    assert search.comment_terms(pattern) == (terms, changed)


def test_comment_terms_cannot_be_empty() -> None:
    assert search.require_comment_terms("a*") == "a"
    with pytest.raises(TrelloctlError) as info:
        search.require_comment_terms("*?")
    assert info.value.code == "USAGE"


@pytest.mark.parametrize(
    ("terms", "query"),
    [
        ("deploy", "comment:deploy"),
        ("two words", 'comment:"two words"'),
        ('say "hi" now', 'comment:"say hi now"'),
        ('"quoted"', "comment:quoted"),
    ],
)
def test_comment_query(terms: str, query: str) -> None:
    assert search.comment_query(terms) == query


def seeded_cache() -> Cache:
    cache = Cache.memory()
    cards = [
        {"id": "a1", "idList": "l1", "name": "Alpha deploy", "desc": "first", "pos": 1},
        {"id": "a2", "idList": "l1", "name": "Beta", "desc": "needs DEPLOY", "pos": 2},
        {
            "id": "a3",
            "idList": "l1",
            "name": "Gamma",
            "desc": "",
            "pos": 3,
            "checklists": [
                {
                    "id": "k1",
                    "name": "Release",
                    "pos": 1,
                    "checkItems": [
                        {"id": "i1", "name": "ship it", "state": "incomplete", "pos": 1}
                    ],
                }
            ],
        },
        {"id": "a4", "idList": "l1", "name": None, "desc": None, "pos": 4},
    ]
    cache.replace_cards(BOARD, cards, NOW)
    return cache


def test_local_matches_report_the_fields_in_canonical_order() -> None:
    cache = seeded_cache()
    rows = cache.list_cards(BOARD)
    found = search.local_matches(cache, BOARD, rows, "deploy", ("name", "desc"))
    assert found == {"a1": ["name"], "a2": ["desc"]}
    found = search.local_matches(cache, BOARD, rows, "ship", ("checklists",))
    assert found == {"a3": ["checklists"]}
    found = search.local_matches(cache, BOARD, rows, "release", ("checklists", "name"))
    assert found == {"a3": ["checklists"]}
    found = search.local_matches(cache, BOARD, rows, "*", ("name", "comments"))
    assert set(found) == {"a1", "a2", "a3", "a4"}
    assert search.local_matches(cache, BOARD, rows, "nothing", ("name", "desc", "checklists")) == {}


class StubClient:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def get(self, path: str, params: dict[str, Any]) -> Any:
        self.requests.append((path, params))
        return self.data


def hits(data: Any) -> dict[str, list[str]]:
    client = StubClient(data)
    result = search.comment_hits(client, BOARD, "deploy")  # type: ignore[arg-type]
    ((path, params),) = client.requests
    assert path == "/search"
    assert params["query"] == "comment:deploy"
    assert params["idBoards"] == BOARD
    assert params["partial"] is True
    return result


def test_comment_hits_collect_comment_ids_per_card() -> None:
    data = {
        "cards": [{"id": "c1"}, {"id": "c2"}, "junk", {"name": "no id"}],
        "actions": [
            {"id": "x1", "data": {"card": {"id": "c1"}}},
            {"id": "x2", "data": {"card": {"id": "c1"}}},
            {"id": "x2", "data": {"card": {"id": "c1"}}},
            {"id": "x3", "data": {"card": {"id": "c3"}}},
            {"id": "x4", "data": {}},
            {"id": "x5", "data": "bad"},
            {"data": {"card": {"id": "c4"}}},
            "junk",
        ],
    }
    assert hits(data) == {"c1": ["x1", "x2"], "c2": [], "c3": ["x3"]}


@pytest.mark.parametrize("data", [None, [], "text", {}, {"cards": None, "actions": None}])
def test_comment_hits_tolerate_unexpected_responses(data: Any) -> None:
    assert hits(data) == {}

"""Card search: wildcard patterns over the cache and comment search through Trello (RF-13).

`name`, `desc` and `checklists` are matched locally: the pattern is evaluated as a GLOB
(`*`, `?` and `[...]` classes, A-20) against the lowercased field, so case never matters, not
even for non-ASCII letters. A pattern without wildcards means `*pattern*`. `comments` is
delegated to `GET /search` (one call): Trello does not support wildcards or brackets, so they
are removed from the query and the caller reports a warning (AC-13.3, A-20).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import Cache
from trelloctl.infra.trello_client import TrelloClient

SEARCH_FIELDS = ("name", "desc", "checklists", "comments")
DEFAULT_FIELDS = ("name", "desc")
COMMENT_WILDCARDS = "*?[]"
SEARCH_LIMIT = 1000

Row = dict[str, Any]
Matcher = Callable[[str], bool]


def parse_fields(raw: Sequence[str] | None) -> tuple[str, ...]:
    """The `--in` fields in canonical order; an unknown field or an empty list is `USAGE`."""
    if raw is None:
        return DEFAULT_FIELDS
    wanted = [item.strip().lower() for item in raw if item.strip()]
    unknown = [item for item in wanted if item not in SEARCH_FIELDS]
    if unknown or not wanted:
        raise TrelloctlError(
            "USAGE",
            "--in accepts only: " + ", ".join(SEARCH_FIELDS) + ".",
            {"unknown": unknown, "allowed": list(SEARCH_FIELDS)},
        )
    return tuple(name for name in SEARCH_FIELDS if name in wanted)


def _class_body(body: str) -> str:
    return "".join("\\" + c if c in "\\[]^" else c for c in body)


def _translate(pattern: str) -> tuple[re.Pattern[str], bool]:
    """Regular expression of a GLOB pattern and whether it contains any wildcard."""
    out: list[str] = []
    wild = False
    i, size = 0, len(pattern)
    while i < size:
        char = pattern[i]
        if char == "*":
            if not out or out[-1] != ".*":
                out.append(".*")
            wild = True
        elif char == "?":
            out.append(".")
            wild = True
        elif char == "[":
            end = _class_end(pattern, i)
            if end is None:
                out.append(re.escape(char))
            else:
                body = pattern[i + 1 : end]
                negate = body.startswith("^")
                out.append("[" + ("^" if negate else "") + _class_body(body[negate:]) + "]")
                wild = True
                i = end
        else:
            out.append(re.escape(char))
        i += 1
    try:
        return re.compile("".join(out), re.DOTALL), wild
    except re.error:
        return re.compile(re.escape(pattern), re.DOTALL), False


def _class_end(pattern: str, start: int) -> int | None:
    """Index of the `]` closing the class opened at `start`, or `None` when it never closes."""
    j = start + 1
    if j < len(pattern) and pattern[j] == "^":
        j += 1
    if j < len(pattern) and pattern[j] == "]":
        j += 1
    end = pattern.find("]", j)
    return None if end == -1 else end


def matcher(pattern: str) -> Matcher:
    """A case-insensitive test of a field value against the pattern.

    Without wildcards the pattern is a substring (`*pattern*`); with wildcards the whole value
    must match, as `lower(field) GLOB lower(pattern)` does.
    """
    lowered = pattern.lower()
    regex, wild = _translate(lowered)
    if not wild:
        return lambda value: lowered in value.lower()
    return lambda value: regex.fullmatch(value.lower()) is not None


def comment_terms(pattern: str) -> tuple[str, bool]:
    """The pattern without `*`, `?`, `[` and `]`, and whether anything was removed (A-20)."""
    terms = "".join(c for c in pattern if c not in COMMENT_WILDCARDS).strip()
    return terms, terms != pattern.strip()


def require_comment_terms(pattern: str) -> str:
    """The comment query terms; a pattern that is empty without wildcards is `USAGE` (A-5)."""
    terms, _ = comment_terms(pattern)
    if not terms:
        raise TrelloctlError(
            "USAGE",
            "The pattern is empty once wildcards are removed; comments cannot be searched "
            "with wildcards only.",
            {"pattern": pattern},
        )
    return terms


def comment_query(terms: str) -> str:
    """Trello query for the `comment:` operator; several words are quoted."""
    clean = terms.replace('"', "")
    return f'comment:"{clean}"' if re.search(r"\s", clean) else f"comment:{clean}"


def local_matches(
    cache: Cache,
    board_id: str,
    rows: Sequence[Row],
    pattern: str,
    fields: Sequence[str],
) -> dict[str, list[str]]:
    """Cards of `rows` matching in `name`, `desc` or `checklists`: card id -> fields."""
    test = matcher(pattern)
    wanted = [name for name in fields if name != "comments"]
    found: dict[str, list[str]] = {}
    for row in rows:
        hit: list[str] = []
        if "name" in wanted and test(row["name"] or ""):
            hit.append("name")
        if "desc" in wanted and test(row["desc"] or ""):
            hit.append("desc")
        if "checklists" in wanted and _checklists_match(cache, board_id, row["id"], test):
            hit.append("checklists")
        if hit:
            found[row["id"]] = hit
    return found


def _checklists_match(cache: Cache, board_id: str, card_id: str, test: Matcher) -> bool:
    for checklist in cache.list_checklists(board_id, card_id):
        if test(checklist["name"] or ""):
            return True
        if any(test(item["name"] or "") for item in checklist["items"]):
            return True
    return False


def comment_hits(client: TrelloClient, board_id: str, terms: str) -> dict[str, list[str]]:
    """One `GET /search`: card id -> ids of the comments that matched (AC-13.3).

    The query uses the `comment:` operator, is restricted to the board and asks for partial
    words. Cards reported without a comment id are kept with an empty list.
    """
    data = client.get(
        "/search",
        {
            "query": comment_query(terms),
            "idBoards": board_id,
            "modelTypes": "cards,actions",
            "partial": True,
            "cards_limit": SEARCH_LIMIT,
            "card_fields": "name",
        },
    )
    hits: dict[str, list[str]] = {}
    if not isinstance(data, Mapping):
        return hits
    for card in data.get("cards") or []:
        if isinstance(card, Mapping) and card.get("id"):
            hits.setdefault(str(card["id"]), [])
    for action in data.get("actions") or []:
        if not isinstance(action, Mapping):
            continue
        inner = action.get("data")
        card = inner.get("card") if isinstance(inner, Mapping) else None
        card_id = card.get("id") if isinstance(card, Mapping) else None
        if card_id and action.get("id"):
            ids = hits.setdefault(str(card_id), [])
            if str(action["id"]) not in ids:
                ids.append(str(action["id"]))
    return hits

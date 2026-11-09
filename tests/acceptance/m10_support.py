"""Shared helpers of the M10 acceptance tests: comments, labels, members and checklists."""

from __future__ import annotations

from typing import Any

from tests.acceptance.m09_support import (  # noqa: F401
    BOARD,
    OLD,
    add_checklist,
    add_label,
    add_member,
    cached,
    calls,
    card,
    close_caches,
    comment,
    finish,
    request_params,
    run,
    seed_world,
    writes,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.members import DEFAULT_ME

ME = DEFAULT_ME["id"]


def error_code(out: dict[str, Any]) -> str:
    assert out["ok"] is False, out
    return str(out["error"]["code"])


def checklist_state(fake: FakeTrello, checklist_id: str) -> dict[str, str]:
    """Item name to state of a checklist of the fake."""
    return {i["name"]: i["state"] for i in fake.state.checklists[checklist_id]["checkItems"]}


def item_id(fake: FakeTrello, checklist_id: str, name: str) -> str:
    return next(
        i["id"] for i in fake.state.checklists[checklist_id]["checkItems"] if i["name"] == name
    )


def item_counts(fake: FakeTrello, card_id: str) -> tuple[int, int]:
    """`(total, checked)` of the items of every checklist of a card in the fake."""
    items = [
        i for c in fake.state.checklists.values() if c["idCard"] == card_id for i in c["checkItems"]
    ]
    return len(items), sum(1 for i in items if i["state"] == "complete")


def checklist_names(fake: FakeTrello, card_id: str) -> list[str]:
    ordered = sorted(
        (c for c in fake.state.checklists.values() if c["idCard"] == card_id),
        key=lambda c: (c["pos"], c["id"]),
    )
    return [c["name"] for c in ordered]

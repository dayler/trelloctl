"""Pure move calculations: list shifts, list indexes and `--pos` conversion (RF-14, A-3, A-21)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from trelloctl.domain.errors import TrelloctlError
from trelloctl.domain.models import TrelloList

TrelloPos = str | float


def open_lists(lists: Sequence[TrelloList]) -> list[TrelloList]:
    """Open lists ordered by position."""
    return sorted((item for item in lists if not item.closed), key=lambda item: item.pos)


@dataclass(frozen=True)
class ShiftResult:
    from_index: int
    to_index: int
    list_count: int
    target_list_id: str
    unchanged: bool


def _out_of_range(from_index: int | None, to_index: int, count: int) -> TrelloctlError:
    return TrelloctlError(
        "MOVE_OUT_OF_RANGE",
        "The target position is outside the open lists.",
        {"from_index": from_index, "to_index": to_index, "list_count": count},
    )


def compute_shift(
    lists: Sequence[TrelloList],
    current_list_id: str,
    direction: Literal["left", "right"],
    n: int = 1,
    clamp: bool = False,
) -> ShiftResult:
    """Target list for `--left N` / `--right N` among open lists (AC-14.2, AC-14.3)."""
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise TrelloctlError("USAGE", "N must be an integer greater than or equal to 1.")
    ordered = open_lists(lists)
    count = len(ordered)
    from_index = next((i for i, item in enumerate(ordered) if item.id == current_list_id), None)
    if from_index is None:
        raise _out_of_range(None, 0, count)
    to_index = from_index + (n if direction == "right" else -n)
    if not 0 <= to_index < count:
        if not clamp:
            raise _out_of_range(from_index, to_index, count)
        to_index = max(0, min(count - 1, to_index))
    return ShiftResult(from_index, to_index, count, ordered[to_index].id, to_index == from_index)


def parse_index(raw: str | int) -> int:
    """Parse a non-negative integer index; anything else is USAGE."""
    message = "The index must be a non-negative integer."
    if isinstance(raw, bool):
        raise TrelloctlError("USAGE", message)
    if isinstance(raw, int):
        value = raw
    else:
        text = raw.strip()
        if not text.isascii() or not text.isdigit():
            raise TrelloctlError("USAGE", message)
        value = int(text)
    if value < 0:
        raise TrelloctlError("USAGE", message)
    return value


def position_at_index(other_positions: Sequence[float], index: int) -> TrelloPos:
    """Trello position that leaves the element at final `index` among `other_positions`.

    `other_positions` holds the positions of the siblings excluding the moved element.
    The valid range is 0..len (A-3, A-21); len equals `bottom`.
    """
    ordered = sorted(other_positions)
    count = len(ordered)
    if index < 0 or index > count:
        raise TrelloctlError(
            "USAGE",
            f"The position index must be between 0 and {count}.",
            {"index": index, "max": count},
        )
    if index == count:
        return "bottom"
    if index == 0:
        return "top"
    return (ordered[index - 1] + ordered[index]) / 2


def to_trello_pos(value: str | int | None, other_positions: Sequence[float]) -> TrelloPos:
    """Convert `--pos top|bottom|<index>` to a Trello position (AC-9.1, AC-12.2, A-3)."""
    if value is None:
        return "bottom"
    if isinstance(value, str) and value in ("top", "bottom"):
        return value
    return position_at_index(other_positions, parse_index(value))


def list_position_for_index(
    lists: Sequence[TrelloList], list_id: str, to_index: int | str
) -> TrelloPos:
    """Trello position for `board list move --to-index N` among open lists (AC-9.4, A-21)."""
    index = parse_index(to_index)
    ordered = open_lists(lists)
    count = len(ordered)
    if index >= count:
        raise _out_of_range(
            next((i for i, item in enumerate(ordered) if item.id == list_id), None),
            index,
            count,
        )
    others = [item.pos for item in ordered if item.id != list_id]
    return position_at_index(others, index)

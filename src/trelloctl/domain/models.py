"""Trello domain models and pure identifier classification (PRD 10.6, AC-1.9, AC-7.3)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

Json = Mapping[str, Any]


def _str(data: Json, key: str, default: str = "") -> str:
    value = data.get(key)
    return value if isinstance(value, str) else default


def _opt_str(data: Json, key: str) -> str | None:
    value = data.get(key)
    return value if isinstance(value, str) else None


def _num(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


def _int(data: Json, key: str, default: int = 0) -> int:
    value = _num(data.get(key))
    return default if value is None else int(value)


def _float(data: Json, key: str, default: float = 0.0) -> float:
    value = _num(data.get(key))
    return default if value is None else float(value)


def _bool(data: Json, key: str) -> bool:
    return data.get(key) is True


def _dicts(value: Any) -> list[Json]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _strs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


BoardKind = Literal["board", "inbox"]


@dataclass(frozen=True)
class Board:
    id: str
    name: str
    short_link: str | None = None
    url: str | None = None
    kind: BoardKind = "board"
    closed: bool = False

    @classmethod
    def from_json(cls, data: Json, kind: BoardKind = "board") -> Board:
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            short_link=_opt_str(data, "shortLink"),
            url=_opt_str(data, "url"),
            kind=kind,
            closed=_bool(data, "closed"),
        )


@dataclass(frozen=True)
class TrelloList:
    id: str
    name: str
    pos: float = 0.0
    closed: bool = False

    @classmethod
    def from_json(cls, data: Json) -> TrelloList:
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            pos=_float(data, "pos"),
            closed=_bool(data, "closed"),
        )


@dataclass(frozen=True)
class Label:
    id: str
    name: str = ""
    color: str | None = None

    @classmethod
    def from_json(cls, data: Json) -> Label:
        return cls(id=_str(data, "id"), name=_str(data, "name"), color=_opt_str(data, "color"))


@dataclass(frozen=True)
class Member:
    id: str
    username: str = ""
    full_name: str = ""

    @classmethod
    def from_json(cls, data: Json) -> Member:
        return cls(
            id=_str(data, "id"),
            username=_str(data, "username"),
            full_name=_str(data, "fullName"),
        )


@dataclass(frozen=True)
class Comment:
    id: str
    text: str = ""
    date: str | None = None
    member_id: str | None = None

    @classmethod
    def from_json(cls, data: Json) -> Comment:
        raw = data.get("data")
        inner: Json = raw if isinstance(raw, Mapping) else {}
        return cls(
            id=_str(data, "id"),
            text=_str(inner, "text") or _str(data, "text"),
            date=_opt_str(data, "date"),
            member_id=_opt_str(data, "idMemberCreator") or _opt_str(data, "memberId"),
        )


@dataclass(frozen=True)
class CheckItem:
    id: str
    name: str = ""
    state: str = "incomplete"
    pos: float = 0.0

    @classmethod
    def from_json(cls, data: Json) -> CheckItem:
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            state=_str(data, "state", "incomplete"),
            pos=_float(data, "pos"),
        )


@dataclass(frozen=True)
class Checklist:
    id: str
    name: str = ""
    pos: float = 0.0
    items: tuple[CheckItem, ...] = ()

    @classmethod
    def from_json(cls, data: Json) -> Checklist:
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            pos=_float(data, "pos"),
            items=tuple(CheckItem.from_json(i) for i in _dicts(data.get("checkItems"))),
        )


@dataclass(frozen=True)
class Attachment:
    id: str
    name: str = ""
    bytes: int | None = None
    mime_type: str | None = None
    date: str | None = None
    url: str | None = None
    is_upload: bool = False
    local_path: str | None = None

    @classmethod
    def from_json(cls, data: Json) -> Attachment:
        raw_bytes = _num(data.get("bytes"))
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            bytes=None if raw_bytes is None else int(raw_bytes),
            mime_type=_opt_str(data, "mimeType"),
            date=_opt_str(data, "date"),
            url=_opt_str(data, "url"),
            is_upload=_bool(data, "isUpload"),
            local_path=_opt_str(data, "localPath"),
        )


@dataclass(frozen=True)
class Badges:
    comments: int = 0
    attachments: int = 0
    check_items: int = 0
    check_items_checked: int = 0

    @classmethod
    def from_json(cls, data: Json | None) -> Badges:
        if not isinstance(data, Mapping):
            return cls()
        return cls(
            comments=_int(data, "comments"),
            attachments=_int(data, "attachments"),
            check_items=_int(data, "checkItems"),
            check_items_checked=_int(data, "checkItemsChecked"),
        )


@dataclass(frozen=True)
class Card:
    id: str
    name: str = ""
    short_link: str | None = None
    id_short: int | None = None
    desc: str = ""
    list_id: str | None = None
    label_ids: tuple[str, ...] = ()
    member_ids: tuple[str, ...] = ()
    labels: tuple[Label, ...] = ()
    members: tuple[Member, ...] = ()
    due: str | None = None
    due_complete: bool = False
    pos: float = 0.0
    closed: bool = False
    url: str | None = None
    date_last_activity: str | None = None
    badges: Badges = field(default_factory=Badges)

    @classmethod
    def from_json(cls, data: Json) -> Card:
        labels = tuple(Label.from_json(i) for i in _dicts(data.get("labels")))
        members = tuple(Member.from_json(i) for i in _dicts(data.get("members")))
        id_short = _num(data.get("idShort"))
        return cls(
            id=_str(data, "id"),
            name=_str(data, "name"),
            short_link=_opt_str(data, "shortLink"),
            id_short=None if id_short is None else int(id_short),
            desc=_str(data, "desc"),
            list_id=_opt_str(data, "idList"),
            label_ids=_strs(data.get("idLabels")) or tuple(label.id for label in labels),
            member_ids=_strs(data.get("idMembers")) or tuple(m.id for m in members),
            labels=labels,
            members=members,
            due=_opt_str(data, "due"),
            due_complete=_bool(data, "dueComplete"),
            pos=_float(data, "pos"),
            closed=_bool(data, "closed"),
            url=_opt_str(data, "url"),
            date_last_activity=_opt_str(data, "dateLastActivity"),
            badges=Badges.from_json(data.get("badges")),
        )


# --- Identifier classification (pure; nothing is resolved against any store) ---

RefKind = Literal["id", "short_link", "id_short", "name"]

_ID_RE = re.compile(r"[0-9a-fA-F]{24}")
_SHORT_RE = re.compile(r"[A-Za-z0-9]{8}")
_DIGITS_RE = re.compile(r"[0-9]+")
_CARD_URL_RE = re.compile(r"https?://(?:www\.)?trello\.com/c/([A-Za-z0-9]{8})(?:[/?#]|$)")
_BOARD_URL_RE = re.compile(r"https?://(?:www\.)?trello\.com/b/([A-Za-z0-9]{8})(?:[/?#]|$)")


@dataclass(frozen=True)
class Candidate:
    """One interpretation of a user-supplied identifier."""

    kind: RefKind
    value: str


def classify_card_ref(value: str) -> list[Candidate]:
    """Interpretations of a card identifier, in lookup order (AC-7.3, A-14)."""
    if _DIGITS_RE.fullmatch(value):
        return [Candidate("id_short", value)]
    if _ID_RE.fullmatch(value):
        return [Candidate("id", value)]
    url = _CARD_URL_RE.match(value)
    if url:
        return [Candidate("short_link", url.group(1))]
    if _SHORT_RE.fullmatch(value):
        return [Candidate("name", value), Candidate("short_link", value)]
    return [Candidate("name", value)]


def classify_board_ref(value: str) -> list[Candidate]:
    """Interpretations of a board identifier, in lookup order (AC-1.9, A-14)."""
    if _ID_RE.fullmatch(value):
        return [Candidate("id", value)]
    url = _BOARD_URL_RE.match(value)
    if url:
        return [Candidate("short_link", url.group(1))]
    if _SHORT_RE.fullmatch(value):
        return [Candidate("name", value), Candidate("short_link", value)]
    return [Candidate("name", value)]

"""Typed errors: the catalog of error codes and their exit-code classes (PRD 10.5)."""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ErrorClass(IntEnum):
    """Error class; the value is the process exit code."""

    SUCCESS = 0
    GENERIC = 1
    USAGE = 2
    CONFIG = 3
    AUTH = 4
    NOT_FOUND = 5
    DOMAIN_RULE = 6
    REMOTE_API = 7
    NETWORK = 8
    STALE_CACHE = 9


_CLASS_CODES: dict[ErrorClass, tuple[str, ...]] = {
    ErrorClass.GENERIC: ("INTERNAL",),
    ErrorClass.USAGE: ("USAGE",),
    ErrorClass.CONFIG: (
        "CONFIG_NOT_FOUND",
        "CONFIG_INVALID",
        "BOARD_NOT_CONFIGURED",
        "HOME_NOT_WRITABLE",
    ),
    ErrorClass.AUTH: ("AUTH_MISSING", "AUTH_INVALID"),
    ErrorClass.NOT_FOUND: (
        "BOARD_NOT_FOUND",
        "LIST_NOT_FOUND",
        "CARD_NOT_FOUND",
        "LABEL_NOT_FOUND",
        "MEMBER_NOT_FOUND",
        "CHECKLIST_NOT_FOUND",
        "CHECKITEM_NOT_FOUND",
        "COMMENT_NOT_FOUND",
        "ATTACHMENT_NOT_FOUND",
        "ATTACHMENT_GONE",
        "AMBIGUOUS_BOARD",
        "AMBIGUOUS_LIST",
        "AMBIGUOUS_CARD",
        "AMBIGUOUS_LABEL",
        "AMBIGUOUS_MEMBER",
        "AMBIGUOUS_CHECKLIST",
        "AMBIGUOUS_CHECKITEM",
        "INBOX_UNAVAILABLE",
    ),
    ErrorClass.DOMAIN_RULE: ("MOVE_OUT_OF_RANGE", "CONFIRMATION_REQUIRED", "ATTACHMENT_TOO_LARGE"),
    ErrorClass.REMOTE_API: ("API_ERROR", "RATE_LIMITED"),
    ErrorClass.NETWORK: ("NETWORK_ERROR", "TIMEOUT"),
    ErrorClass.STALE_CACHE: ("CACHE_STALE",),
}

# Error code -> error class. Each code belongs to exactly one class.
CATALOG: dict[str, ErrorClass] = {
    code: error_class for error_class, codes in _CLASS_CODES.items() for code in codes
}

INTERNAL = "INTERNAL"


def exit_code_for(code: str) -> int:
    """Exit code of an error code of the catalog."""
    return int(CATALOG[code])


class TrelloctlError(Exception):
    """Exception carrying a code of the catalog, a message and structured details."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        if code not in CATALOG:
            raise ValueError(f"unknown error code: {code}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details: dict[str, Any] = dict(details) if details else {}

    @property
    def error_class(self) -> ErrorClass:
        return CATALOG[self.code]

    @property
    def exit_code(self) -> int:
        return int(self.error_class)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def to_trelloctl_error(exc: BaseException) -> TrelloctlError:
    """Return `exc` if typed, otherwise an INTERNAL error that does not leak internals."""
    if isinstance(exc, TrelloctlError):
        return exc
    return TrelloctlError(
        INTERNAL,
        "Unexpected internal error.",
        {"exception": type(exc).__name__},
    )

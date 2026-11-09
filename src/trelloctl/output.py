"""JSON envelope, date serialization and console encoding (PRD 10.5, RF-3, RNF-7)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import IO, Any

from trelloctl.domain.errors import TrelloctlError, to_trelloctl_error

SCHEMA_VERSION = "1"


def to_utc_iso(value: str | datetime) -> str:
    """ISO-8601 in UTC with a ``Z`` suffix; naive values are taken as UTC."""
    moment = datetime.fromisoformat(value) if isinstance(value, str) else value
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    timespec = "milliseconds" if moment.microsecond else "seconds"
    return moment.isoformat(timespec=timespec).replace("+00:00", "Z")


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return to_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _meta(
    command: str, board_id: str | None, elapsed_ms: int, extra: dict[str, Any] | None
) -> dict[str, Any]:
    meta: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "command": command}
    if board_id is not None:
        meta["board_id"] = board_id
    meta["elapsed_ms"] = elapsed_ms
    if extra:
        meta.update(extra)
    return meta


def success_envelope(
    data: Any,
    command: str,
    *,
    board_id: str | None = None,
    elapsed_ms: int = 0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Success envelope; `meta` adds keys such as `cache`, `truncated` or `warnings`."""
    return {"ok": True, "data": data, "meta": _meta(command, board_id, elapsed_ms, meta)}


def error_envelope(
    error: BaseException,
    command: str,
    *,
    board_id: str | None = None,
    elapsed_ms: int = 0,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Error envelope; untyped exceptions are reported as INTERNAL."""
    typed: TrelloctlError = to_trelloctl_error(error)
    return {
        "ok": False,
        "error": {"code": typed.code, "message": typed.message, "details": typed.details},
        "meta": _meta(command, board_id, elapsed_ms, meta),
    }


def render(envelope: dict[str, Any], *, compact: bool = False) -> str:
    """Serialize an envelope: two-space indent (or one line), unescaped UTF-8, final newline."""
    if compact:
        text = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"), default=_default)
    else:
        text = json.dumps(envelope, ensure_ascii=False, indent=2, default=_default)
    return text + "\n"


def configure_console(*streams: IO[str]) -> None:
    """Reconfigure text streams to UTF-8; streams that cannot be reconfigured are skipped."""
    for stream in streams:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

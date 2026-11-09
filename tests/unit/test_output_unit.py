"""Unit tests of the output helpers."""

from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime

import pytest

from trelloctl.domain.errors import TrelloctlError
from trelloctl.output import configure_console, error_envelope, render, success_envelope, to_utc_iso


def test_naive_datetime_is_taken_as_utc_and_microseconds_become_millis() -> None:
    assert to_utc_iso(datetime(2026, 1, 2, 3, 4, 5)) == "2026-01-02T03:04:05Z"
    assert (
        to_utc_iso(datetime(2026, 1, 2, 3, 4, 5, 123000, tzinfo=UTC)) == "2026-01-02T03:04:05.123Z"
    )


def test_meta_includes_board_id_and_extras_in_order() -> None:
    envelope = success_envelope(
        {}, "card list", board_id="b1", elapsed_ms=5, meta={"truncated": False}
    )
    assert list(envelope["meta"]) == [
        "schema_version",
        "command",
        "board_id",
        "elapsed_ms",
        "truncated",
    ]
    assert "board_id" not in success_envelope({}, "x")["meta"]


def test_error_envelope_with_board_and_meta() -> None:
    envelope = error_envelope(
        TrelloctlError("USAGE", "m"), "x", board_id="b", meta={"warnings": ["w"]}
    )
    assert envelope["meta"]["board_id"] == "b"
    assert envelope["meta"]["warnings"] == ["w"]
    assert envelope["error"]["details"] == {}


def test_dates_and_unsupported_types_in_render() -> None:
    text = render(success_envelope({"d": date(2026, 1, 2)}, "x"))
    assert json.loads(text)["data"]["d"] == "2026-01-02"
    with pytest.raises(TypeError):
        render(success_envelope({"o": object()}, "x"))


def test_configure_console_skips_streams_without_reconfigure() -> None:
    configure_console(io.StringIO())

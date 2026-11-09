"""Unit tests of the error types."""

from __future__ import annotations

import pytest

from trelloctl.domain.errors import TrelloctlError, to_trelloctl_error


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(ValueError, match="NOPE"):
        TrelloctlError("NOPE", "x")


def test_details_are_copied_and_str_has_code() -> None:
    details = {"a": 1}
    error = TrelloctlError("USAGE", "bad", details)
    details["a"] = 2
    assert error.details == {"a": 1}
    assert str(error) == "USAGE: bad"
    assert TrelloctlError("USAGE", "bad").details == {}


def test_to_trelloctl_error_passes_typed_errors_through() -> None:
    error = TrelloctlError("USAGE", "bad")
    assert to_trelloctl_error(error) is error
    internal = to_trelloctl_error(KeyError("k"))
    assert internal.code == "INTERNAL"
    assert internal.details == {"exception": "KeyError"}

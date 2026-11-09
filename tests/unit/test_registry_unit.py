"""Unit tests of the operation registry and the shared dispatcher."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from trelloctl.app.commands.context import (
    CommandContext,
    Need,
    Outcome,
    fallback_settings,
    load_settings_lenient,
)
from trelloctl.domain.errors import TrelloctlError
from trelloctl.registry import (
    FRESH,
    GROUP_HELP,
    YES,
    Operation,
    Param,
    Registry,
    default_registry,
    execute,
)


def ok_handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    return ctx.success({"args": dict(args)})


def op(*path: str, **kwargs: Any) -> Operation:
    return Operation(path, "summary", "example", kwargs.pop("handler", ok_handler), **kwargs)


def make_ctx(home: Path, **kwargs: Any) -> CommandContext:
    settings, error = load_settings_lenient({}, home)
    return CommandContext(settings=settings, config_error=error, environ={}, **kwargs)


def test_names_and_flags() -> None:
    operation = op("card", "checklist-x", "add")
    assert operation.name == "card checklist-x add"
    assert operation.tool_name == "card_checklist_x_add"
    assert Param("to_index", "d").flag == "--to-index"


def test_implicit_parameters() -> None:
    plain = op("a", "b")
    assert plain.all_params == ()
    both = op("a", "c", cached_read=True, requires_yes=True, params=(Param("x", "d"),))
    assert [p.name for p in both.all_params] == ["x", FRESH.name, YES.name]


def test_registry_rejects_duplicates_and_prefix_collisions() -> None:
    registry = Registry([op("a", "b")])
    with pytest.raises(ValueError, match="duplicate"):
        registry.add(op("a", "b"))
    with pytest.raises(ValueError, match="collides"):
        registry.add(op("a"))
    with pytest.raises(ValueError, match="collides"):
        registry.add(op("a", "b", "c"))
    registry.add(op("a", "d"))
    assert registry.get(["a", "d"]) is not None
    assert registry.get(["zz"]) is None
    extended = registry.extended(op("z"))
    assert len(extended.operations()) == 3
    assert len(registry.operations()) == 2


def test_default_registry_is_consistent() -> None:
    registry = default_registry()
    assert registry.get(["version"]) is not None
    assert registry.get(["config", "board", "set"]) is not None
    for operation in registry.operations():
        assert operation.summary
        assert operation.example.startswith("trelloctl ")
        for size in range(1, len(operation.path)):
            assert operation.path[:size] in GROUP_HELP
        assert operation.path[:1] in GROUP_HELP


def test_success_runs_handler_and_fills_elapsed(tmp_path: Path) -> None:
    outcome = execute(op("a", "b"), {"x": 1}, make_ctx(tmp_path))
    assert outcome.envelope["ok"] is True
    assert outcome.envelope["data"] == {"args": {"x": 1}}
    assert outcome.envelope["meta"]["command"] == "a b"
    assert isinstance(outcome.envelope["meta"]["elapsed_ms"], int)


def test_handler_may_return_an_outcome(tmp_path: Path) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> Outcome:
        return Outcome(ctx.success("report"), text="report", exit_code=1)

    outcome = execute(op("a", "b", handler=handler), {}, make_ctx(tmp_path))
    assert (outcome.text, outcome.exit_code) == ("report", 1)


def test_typed_errors_become_envelopes(tmp_path: Path) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        raise TrelloctlError("LIST_NOT_FOUND", "no such list", {"list": "x"})

    envelope = execute(op("a", "b", handler=handler), {}, make_ctx(tmp_path)).envelope
    assert envelope["error"] == {
        "code": "LIST_NOT_FOUND",
        "message": "no such list",
        "details": {"list": "x"},
    }


def test_unexpected_errors_become_internal_and_are_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        raise KeyError("secret-detail")

    logger = logging.getLogger("trelloctl.test")
    with caplog.at_level(logging.ERROR, logger="trelloctl.test"):
        envelope = execute(
            op("a", "b", handler=handler), {}, make_ctx(tmp_path, logger=logger)
        ).envelope
    assert envelope["error"]["code"] == "INTERNAL"
    assert "secret-detail" not in str(envelope)
    assert any(record.exc_info for record in caplog.records)


def test_confirmation_is_required_before_the_handler_runs(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(1)
        return ctx.success({})

    operation = op("a", "rm", handler=handler, requires_yes=True)
    refused = execute(operation, {"yes": False}, make_ctx(tmp_path)).envelope
    assert refused["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert calls == []
    assert execute(operation, {"yes": True}, make_ctx(tmp_path)).envelope["ok"] is True
    assert calls == [1]


def test_preconditions_run_before_the_handler(tmp_path: Path) -> None:
    calls: list[int] = []

    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(1)
        return ctx.success({}, board_id=ctx.board_ref)

    operation = op("a", "b", handler=handler, need=Need.BOARD)
    first = execute(operation, {}, make_ctx(tmp_path)).envelope
    assert first["error"]["code"] == "CONFIG_NOT_FOUND"
    (tmp_path / "config.toml").write_text('[auth]\napi_key = "k"\ntoken = "t"\n', encoding="utf-8")
    second = execute(operation, {}, make_ctx(tmp_path)).envelope
    assert second["error"]["code"] == "BOARD_NOT_CONFIGURED"
    assert calls == []
    envelope = execute(operation, {}, make_ctx(tmp_path, board_flag="mine")).envelope
    assert envelope["meta"]["board_id"] == "mine"


def test_settings_warnings_are_added_to_success_meta(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[extra]\nx = 1\n", encoding="utf-8")
    meta = execute(op("a", "b"), {}, make_ctx(tmp_path)).envelope["meta"]
    assert meta["warnings"] == ["Unknown configuration key ignored: extra"]

    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        warnings = ["own", "Unknown configuration key ignored: extra"]
        return ctx.success({}, meta={"warnings": warnings})

    meta = execute(op("a", "b", handler=handler), {}, make_ctx(tmp_path)).envelope["meta"]
    assert meta["warnings"] == ["own", "Unknown configuration key ignored: extra"]


def test_config_error_is_kept_for_lenient_operations(tmp_path: Path) -> None:
    (tmp_path / "config.toml").write_text("[http]\ntimeout_seconds = 'x'\n", encoding="utf-8")
    settings, error = load_settings_lenient({"TRELLO_BOARD": "b"}, tmp_path)
    assert error is not None
    assert error.code == "CONFIG_INVALID"
    assert settings.config_found is True
    assert settings.env_board == "b"
    assert settings.timeout_seconds == 30
    assert fallback_settings({"TRELLO_TOKEN": "t"}, tmp_path).token == "t"
    ctx = CommandContext(settings=settings, config_error=error)
    with pytest.raises(TrelloctlError) as info:
        ctx.client()
    assert info.value.code == "AUTH_MISSING"

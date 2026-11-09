"""Unit tests of the MCP adapter: schemas, argument checks, dispatch and the missing SDK."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m08_support import BOARD, configure, seed_standard
from tests.fakes.trello import FakeTrello

from trelloctl import mcp_server
from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.domain.errors import TrelloctlError
from trelloctl.mcp_server import McpAdapter, build_args, input_schema, tool_definitions
from trelloctl.registry import Operation, ParamType, default_registry

REGISTRY = default_registry()
EXPOSED = [op for op in REGISTRY.operations() if op.mcp]
TYPES = {ParamType.STR: "string", ParamType.INT: "integer", ParamType.BOOL: "boolean"}


def op(name: str) -> Operation:
    found = next(o for o in EXPOSED if o.tool_name == name)
    return found


@pytest.mark.tc("TC-M15-003")
def test_schema_of_every_tool_matches_its_registry_operation() -> None:
    definitions = tool_definitions(REGISTRY)
    assert [d["name"] for d in definitions] == [o.tool_name for o in EXPOSED]
    for operation, definition in zip(EXPOSED, definitions, strict=True):
        schema = definition["inputSchema"]
        props = schema["properties"]
        declared = {p.name: p for p in operation.all_params}
        assert set(props) == set(declared) | {"board", "include_archived_boards"}
        for name, param in declared.items():
            if param.type is ParamType.CSV or param.nargs in ("*", "+"):
                assert props[name]["type"] == "array"
                assert props[name]["items"] == {"type": "string"}
            else:
                assert props[name]["type"] == TYPES[param.type]
            if param.choices:
                assert props[name]["enum"] == list(param.choices)
        required = {p.name for p in declared.values() if p.required and p.exclusive is None}
        assert set(schema.get("required", [])) == required
        assert schema["additionalProperties"] is False
        assert definition["description"]
    assert "yes" in input_schema(op("board_label_remove"))["properties"]
    assert "fresh" in input_schema(op("card_list"))["properties"]


def test_build_args_fills_defaults_and_coerces_lists() -> None:
    args = build_args(op("card_create"), {"list": "Todo", "name": "x", "labels": "a, b"})
    assert args["labels"] == ["a", "b"]
    assert args["desc"] is None
    args = build_args(op("card_label_add"), {"card": "c", "labels": ["a", "b"]})
    assert args["labels"] == ["a", "b"]
    assert build_args(op("card_list"), {"board": "x"})["fresh"] is False


@pytest.mark.parametrize(
    ("tool", "arguments", "fragment"),
    [
        ("card_list", {"nope": 1}, "Unknown parameter"),
        ("card_list", {"fresh": "yes"}, "must be a boolean"),
        ("card_list", {"limit": "5"}, "must be an integer"),
        ("card_list", {"limit": True}, "must be an integer"),
        ("card_list", {"fields": [1]}, "array of strings"),
        ("card_list", {"fields": 5}, "array of strings"),
        ("card_list", {"list": 5}, "must be a string"),
        ("card_get", {}, "Missing required parameter: card"),
        ("card_create", {"list": "a", "name": "b", "desc": "x", "desc_file": "y"}, "only one"),
        ("board_list_move", {"list": "a"}, "Give one of"),
        ("card_create", {"list": "a", "name": "b", "desc": "-"}, "stdin not available in MCP"),
    ],
)
def test_build_args_rejects_invalid_arguments(
    tool: str, arguments: dict[str, Any], fragment: str
) -> None:
    with pytest.raises(TrelloctlError) as info:
        build_args(op(tool), arguments)
    assert info.value.code == "USAGE"
    assert fragment in info.value.message


def test_build_args_rejects_values_outside_choices() -> None:
    with_choices = next(
        (o, p) for o in EXPOSED for p in o.all_params if p.choices and p.type is ParamType.STR
    )
    operation, param = with_choices
    arguments: dict[str, Any] = {
        p.name: "x" for p in operation.all_params if p.required and p.exclusive is None
    }
    arguments[param.name] = "not-a-choice"
    with pytest.raises(TrelloctlError, match="must be one of"):
        build_args(operation, arguments)


def make_adapter(home: Path, logger: logging.Logger | None = None, **overrides: Any) -> McpAdapter:
    settings, error = load_settings_lenient({}, home)
    ctx = CommandContext(
        settings=settings,
        config_error=error,
        environ={},
        logger=logger or logging.getLogger("test-mcp"),
        **overrides,
    )
    return McpAdapter(ctx)


def test_adapter_calls_a_tool_and_reports_errors(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    adapter = make_adapter(trelloctl_home)
    assert adapter.has_tool("card_get")
    assert not adapter.has_tool("doctor")
    assert len(adapter.tools()) == len(EXPOSED)
    envelope, is_error = adapter.call("card_get", {"card": ids["c1"]})
    assert not is_error
    assert envelope["data"]["name"] == "First card"
    envelope, is_error = adapter.call("card_get", {"card": "6b00000000000000000000ff"})
    assert is_error
    assert envelope["error"]["code"] == "CARD_NOT_FOUND"
    envelope, is_error = adapter.call("card_get", {})
    assert is_error
    assert envelope["error"]["code"] == "USAGE"
    assert envelope["meta"]["command"] == "card get"
    envelope, is_error = adapter.call("card_list", None)
    assert not is_error
    assert envelope["meta"]["board_id"] == BOARD


def test_per_call_board_does_not_leak_into_the_next_call(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    adapter = make_adapter(trelloctl_home, board_flag="Work")
    envelope, _ = adapter.call("card_list", {"board": BOARD})
    assert envelope["meta"]["board_id"] == BOARD
    assert adapter._base.board_flag == "Work"
    assert adapter._base.board_ref is None


def test_serialize_is_compact_without_trailing_newline() -> None:
    text = mcp_server.serialize({"ok": True, "data": {"n": "é"}})
    assert text == '{"ok":true,"data":{"n":"é"}}'


def test_serve_without_the_sdk_prints_a_usage_envelope(
    trelloctl_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "mcp", None)
    monkeypatch.setitem(sys.modules, "mcp_types", None)
    settings, error = load_settings_lenient({}, trelloctl_home)
    ctx = CommandContext(settings=settings, config_error=error, environ={})
    outcome = mcp_server.serve(ctx, {})
    envelope = json.loads(capsys.readouterr().out)
    assert envelope == outcome.envelope
    assert envelope["error"]["code"] == "USAGE"
    assert mcp_server.INSTALL_HINT in envelope["error"]["details"]["install"]
    assert "trelloctl[mcp]" in envelope["error"]["message"]


def test_mcp_serve_is_registered_but_not_exposed() -> None:
    operation = REGISTRY.get(("mcp", "serve"))
    assert operation is not None
    assert operation.own_stdout
    assert not operation.mcp
    assert "mcp_serve" not in {o.tool_name for o in EXPOSED}

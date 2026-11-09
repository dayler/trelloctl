"""MCP adapter over stdio (PRD 10.10, RF-25, experimental).

Thin adapter over the registry: the tool list is generated from the operations that are not
marked `mcp=False`, and every `tools/call` runs the same dispatcher as the CLI. The official
`mcp` SDK is imported lazily, only when the server starts; everything that touches it lives in
`_run_server`. The rest of the module (tool schemas, argument checks and `McpAdapter`) is
plain Python and does not need the SDK.

The configuration is read once, when the server starts (AC-25.5): a change in `config.toml`
needs a restart. Cache TTLs are evaluated on every call because each call opens the cache and
asks the clock again.
"""

from __future__ import annotations

import contextlib
import dataclasses
import sys
import time
from collections.abc import Mapping
from typing import Any

from trelloctl.app.commands.context import CommandContext, Outcome
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.logging import log_invocation
from trelloctl.output import error_envelope, render
from trelloctl.registry import Operation, Param, ParamType, Registry, default_registry, execute

INSTALL_HINT = "uv tool install 'trelloctl[mcp]'  (or: pip install 'trelloctl[mcp]')"
STDIN_UNAVAILABLE = "stdin not available in MCP"
# Text parameters that accept `-` (standard input) in the CLI; in MCP stdin is the protocol
# channel, so they are refused (A-25).
STDIN_PARAMS = ("desc", "text")
BOARD_PARAMS: dict[str, dict[str, str]] = {
    "board": {
        "type": "string",
        "description": "board: id, shortLink, url or name (same as --board)",
    },
    "include_archived_boards": {
        "type": "boolean",
        "description": "also consider archived boards when resolving a board by name",
    },
}
_JSON_TYPES = {
    ParamType.STR: "string",
    ParamType.INT: "integer",
    ParamType.BOOL: "boolean",
}


def exposed_operations(registry: Registry) -> list[Operation]:
    """Operations published as tools: every operation not marked `mcp=False`."""
    return [op for op in registry.operations() if op.mcp]


def _property(param: Param) -> dict[str, Any]:
    schema: dict[str, Any]
    if param.type is ParamType.CSV or param.nargs in ("*", "+"):
        schema = {"type": "array", "items": {"type": "string"}}
    else:
        schema = {"type": _JSON_TYPES[param.type]}
    if param.choices:
        schema["enum"] = list(param.choices)
    description = param.description
    if param.exclusive:
        description += f" (mutually exclusive group: {param.exclusive})"
    schema["description"] = description
    return schema


def input_schema(operation: Operation) -> dict[str, Any]:
    """JSON schema of the arguments of a tool: the registry params plus the board params."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in operation.all_params:
        properties[param.name] = _property(param)
        if param.required and param.exclusive is None:
            required.append(param.name)
    properties.update({name: dict(schema) for name, schema in BOARD_PARAMS.items()})
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def tool_definitions(registry: Registry) -> list[dict[str, Any]]:
    """`name`, `description` and `inputSchema` of every tool, in registry order."""
    return [
        {
            "name": op.tool_name,
            "description": op.description or op.summary,
            "inputSchema": input_schema(op),
        }
        for op in exposed_operations(registry)
    ]


def _usage(message: str, **details: Any) -> TrelloctlError:
    return TrelloctlError("USAGE", message, details)


def _coerce(param: Param, value: Any) -> Any:
    name = param.name
    if param.type is ParamType.BOOL:
        if not isinstance(value, bool):
            raise _usage(f"`{name}` must be a boolean.", param=name)
        return value
    if param.type is ParamType.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise _usage(f"`{name}` must be an integer.", param=name)
        return value
    if param.type is ParamType.CSV or param.nargs in ("*", "+"):
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise _usage(f"`{name}` must be an array of strings.", param=name)
        return list(value)
    if not isinstance(value, str):
        raise _usage(f"`{name}` must be a string.", param=name)
    if param.choices and value not in param.choices:
        raise _usage(
            f"`{name}` must be one of: {', '.join(param.choices)}.",
            param=name,
            choices=list(param.choices),
        )
    return value


def build_args(operation: Operation, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the call arguments against the operation and fill the defaults.

    Mirrors what argparse enforces in the CLI: unknown names, types, choices, required
    parameters and mutually exclusive groups. Errors are `USAGE` (A-25 for stdin).
    """
    params = {p.name: p for p in operation.all_params}
    unknown = sorted(set(arguments) - set(params) - set(BOARD_PARAMS))
    if unknown:
        raise _usage(f"Unknown parameter: {', '.join(unknown)}.", unknown=unknown)
    args: dict[str, Any] = {}
    for param in params.values():
        value = arguments.get(param.name)
        if value is None:
            args[param.name] = False if param.type is ParamType.BOOL else param.default
            continue
        args[param.name] = _coerce(param, value)
    for param in params.values():
        if param.required and param.exclusive is None and arguments.get(param.name) is None:
            raise _usage(f"Missing required parameter: {param.name}.", param=param.name)
    groups: dict[str, list[Param]] = {}
    for param in params.values():
        if param.exclusive is not None:
            groups.setdefault(param.exclusive, []).append(param)
    for label, members in groups.items():
        given = [p.name for p in members if arguments.get(p.name) not in (None, False)]
        if len(given) > 1:
            raise _usage(f"Give only one of: {', '.join(given)}.", params=given, group=label)
        if not given and any(p.required for p in members):
            names = ", ".join(p.name for p in members)
            raise _usage(f"Give one of: {names}.", params=[p.name for p in members])
    for name in STDIN_PARAMS:
        if args.get(name) == "-":
            raise _usage(STDIN_UNAVAILABLE, param=name)
    return args


class McpAdapter:
    """Runs tool calls against the registry with the settings loaded at startup."""

    def __init__(self, base: CommandContext, registry: Registry | None = None) -> None:
        self._base = base
        self._registry = registry if registry is not None else default_registry()
        self._operations = {op.tool_name: op for op in exposed_operations(self._registry)}

    def tools(self) -> list[dict[str, Any]]:
        return tool_definitions(self._registry)

    def has_tool(self, name: str) -> bool:
        return name in self._operations

    def call(self, name: str, arguments: Mapping[str, Any] | None) -> tuple[dict[str, Any], bool]:
        """Execute a tool; returns the envelope and whether it is an error."""
        operation = self._operations[name]
        arguments = arguments or {}
        started = time.monotonic()
        ctx = dataclasses.replace(
            self._base,
            command=operation.name,
            board_flag=_text_or_none(arguments.get("board")) or self._base.board_flag,
            include_archived_boards=bool(
                arguments.get("include_archived_boards") or self._base.include_archived_boards
            ),
            board_ref=None,
        )
        outcome: Outcome
        try:
            outcome = execute(operation, build_args(operation, arguments), ctx)
        except TrelloctlError as exc:
            outcome = Outcome(error_envelope(exc, operation.name))
        envelope = outcome.envelope
        ok = bool(envelope.get("ok"))
        log_invocation(
            ctx.logger,
            operation.name,
            int((time.monotonic() - started) * 1000),
            "ok" if ok else str(envelope["error"]["code"]),
            ok=ok,
        )
        return envelope, not ok


def _text_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def serialize(envelope: dict[str, Any]) -> str:
    """Compact envelope as the text content of a tool result (AC-3.3)."""
    return render(envelope, compact=True).rstrip("\n")


def _sdk_missing(ctx: CommandContext) -> Outcome:
    error = _usage(
        "The MCP server needs the optional dependency `mcp`. Install the extra with: "
        + INSTALL_HINT,
        install=INSTALL_HINT,
    )
    envelope = error_envelope(error, "mcp serve")
    sys.stdout.write(render(envelope, compact=False))
    sys.stdout.flush()
    return Outcome(envelope)


def serve(ctx: CommandContext, args: Mapping[str, Any]) -> Outcome:
    """`mcp serve`: run the stdio server until the harness closes stdin."""
    try:
        import mcp.server.lowlevel
        import mcp.server.stdio  # noqa: F401
        import mcp_types  # noqa: F401
    except ImportError:
        return _sdk_missing(ctx)
    adapter = McpAdapter(ctx)
    ctx.logger.info("mcp server started tools=%d", len(adapter.tools()))
    with contextlib.suppress(KeyboardInterrupt):
        _run_server(adapter)
    ctx.logger.info("mcp server stopped")
    return Outcome({"ok": True, "data": {}, "meta": {"command": "mcp serve"}}, exit_code=0)


def _run_server(adapter: McpAdapter) -> None:
    """Start the SDK server on stdio. The only place that touches the SDK."""
    import anyio
    import mcp_types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server
    from mcp.shared.exceptions import MCPError

    from trelloctl import __version__

    async def list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t["name"], description=t["description"], input_schema=t["inputSchema"]
                )
                for t in adapter.tools()
            ]
        )

    async def call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
        if not adapter.has_tool(params.name):
            raise MCPError(types.INVALID_PARAMS, f"Unknown tool: {params.name}")
        envelope, is_error = await anyio.to_thread.run_sync(
            adapter.call, params.name, params.arguments
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=serialize(envelope))],
            is_error=is_error,
        )

    server: Server[Any] = Server(
        "trelloctl", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool
    )

    async def main() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    anyio.run(main)

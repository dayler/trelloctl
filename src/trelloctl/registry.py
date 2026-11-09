"""Single registry of operations (PRD 10.10).

Every operation is declared once here. `cli.py` builds the argparse tree from it and
`mcp_server.py` generates its tool list from it. Operations are declared in one delimited
block per command group; a milestone edits only the block of its own group:

    # ==== BEGIN block: <group> ====
    @block("<group>")
    def _<group>() -> list[Operation]:
        ...
    # ==== END block: <group> ====

`execute` is the dispatcher shared by the adapters: preconditions, confirmation, handler,
error translation and the common `meta` fields.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from trelloctl.app.commands.context import CommandContext, Need, Outcome, check_preconditions
from trelloctl.domain.errors import TrelloctlError
from trelloctl.output import error_envelope

Handler = Callable[[CommandContext, Mapping[str, Any]], "dict[str, Any] | Outcome"]

# Help text of every group and subgroup, in the order shown by the general help.
GROUP_HELP: dict[tuple[str, ...], str] = {
    ("config",): "configuration and default board",
    ("config", "board"): "default board stored in config.toml",
    ("doctor",): "environment diagnostics (--api checks the Trello contract)",
    ("board",): "board: lists, labels, members, activity",
    ("board", "list"): "manage the lists of the board",
    ("board", "label"): "manage the labels of the board",
    ("card",): "cards: create, search, move, label, checklists, attachments",
    ("card", "comment"): "comments of a card",
    ("card", "label"): "labels of a card",
    ("card", "member"): "members of a card",
    ("card", "checklist"): "checklists of a card",
    ("card", "checklist", "item"): "items of a checklist",
    ("card", "attachments"): "attachments of a card",
    ("inbox",): "personal Inbox: list, add, move (to a list of the board)",
    ("cache",): "local cache: reload, status, clear",
    ("skills",): "skill for the harness: list, install, export",
    ("mcp",): "MCP server over stdio (experimental)",
    ("version",): "version as JSON",
}


class ParamType(Enum):
    """Value type of a parameter."""

    STR = "str"
    INT = "int"
    BOOL = "bool"  # a flag: present means true
    CSV = "csv"  # comma-separated list, delivered as a list of strings


@dataclass(frozen=True)
class Param:
    """A positional argument or an option of an operation.

    `name` is snake_case (the handler key and the MCP property); the CLI flag is the same
    name with dashes. Parameters sharing an `exclusive` label are mutually exclusive; if any
    of them has `required`, exactly one of the group must be given.
    """

    name: str
    description: str
    type: ParamType = ParamType.STR
    positional: bool = False
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    short: str | None = None
    metavar: str | None = None
    nargs: str | None = None  # "*" or "+" for positionals that take several values
    exclusive: str | None = None

    @property
    def flag(self) -> str:
        return "--" + self.name.replace("_", "-")


FRESH = Param("fresh", "read from Trello instead of the local cache", ParamType.BOOL)
YES = Param("yes", "confirm this irreversible operation", ParamType.BOOL)


@dataclass(frozen=True)
class Operation:
    """Declaration of one operation: its CLI shape, preconditions and handler.

    `cached_read` adds `--fresh`; `requires_yes` adds `--yes` and makes the dispatcher
    answer `CONFIRMATION_REQUIRED` without it. `mcp=False` keeps the operation out of the MCP
    tools. `text_output(args)` is true when, for those arguments, stdout carries text instead
    of JSON (AC-3.1); `own_stdout` marks operations that manage stdout themselves
    (`mcp serve`).
    """

    path: tuple[str, ...]
    summary: str
    example: str
    handler: Handler
    params: tuple[Param, ...] = ()
    description: str = ""
    need: Need = Need.NONE
    requires_yes: bool = False
    cached_read: bool = False
    mcp: bool = True
    text_output: Callable[[Mapping[str, Any]], bool] | None = None
    own_stdout: bool = False

    @property
    def name(self) -> str:
        """Command name as shown in `meta.command`, for example `card list`."""
        return " ".join(self.path)

    @property
    def tool_name(self) -> str:
        """MCP tool name: the command name in snake_case."""
        return "_".join(part.replace("-", "_") for part in self.path)

    @property
    def all_params(self) -> tuple[Param, ...]:
        """Declared parameters plus the implicit `--fresh` and `--yes`."""
        extra = tuple(
            p for p, wanted in ((FRESH, self.cached_read), (YES, self.requires_yes)) if wanted
        )
        return self.params + extra


class Registry:
    """Ordered collection of operations, unique by path."""

    def __init__(self, operations: Iterable[Operation] = ()) -> None:
        self._operations: dict[tuple[str, ...], Operation] = {}
        for operation in operations:
            self.add(operation)

    def add(self, operation: Operation) -> None:
        if operation.path in self._operations:
            raise ValueError(f"duplicate operation: {operation.name}")
        for existing in self._operations:
            shorter, longer = sorted((existing, operation.path), key=len)
            if longer[: len(shorter)] == shorter:
                raise ValueError(f"{operation.name} collides with {' '.join(existing)}")
        self._operations[operation.path] = operation

    def operations(self) -> tuple[Operation, ...]:
        return tuple(self._operations.values())

    def get(self, path: Sequence[str]) -> Operation | None:
        return self._operations.get(tuple(path))

    def extended(self, *operations: Operation) -> Registry:
        """A copy of this registry with more operations (used to register test operations)."""
        return Registry((*self.operations(), *operations))


_BLOCKS: list[Callable[[], Sequence[Operation]]] = []


def block(
    group: str,
) -> Callable[[Callable[[], Sequence[Operation]]], Callable[[], Sequence[Operation]]]:
    """Register the builder of the operations of one command group."""

    def decorator(
        builder: Callable[[], Sequence[Operation]],
    ) -> Callable[[], Sequence[Operation]]:
        builder.__dict__["group"] = group
        _BLOCKS.append(builder)
        return builder

    return decorator


def default_registry() -> Registry:
    """The registry with the operations of every group block."""
    registry = Registry()
    for builder in _BLOCKS:
        for operation in builder():
            registry.add(operation)
    return registry


def _confirmation_required(operation: Operation) -> TrelloctlError:
    return TrelloctlError(
        "CONFIRMATION_REQUIRED",
        f"`{operation.name}` is irreversible; repeat it with --yes to confirm.",
        {"command": operation.name},
    )


def execute(operation: Operation, args: Mapping[str, Any], ctx: CommandContext) -> Outcome:
    """Run an operation: preconditions (A-7), confirmation, handler and error translation.

    Never raises for operation failures: typed errors become their envelope and any other
    exception becomes `INTERNAL`, with the traceback sent to the log only.
    """
    started = time.monotonic()
    ctx.command = operation.name
    outcome: Outcome
    try:
        ctx.board_ref = check_preconditions(
            operation.need,
            ctx.settings,
            config_error=ctx.config_error,
            board_flag=ctx.board_flag,
        )
        if operation.requires_yes and not args.get("yes"):
            raise _confirmation_required(operation)
        result = operation.handler(ctx, args)
        outcome = result if isinstance(result, Outcome) else Outcome(result)
    except TrelloctlError as exc:
        outcome = Outcome(error_envelope(exc, operation.name))
    except Exception as exc:
        ctx.logger.exception('unhandled error in command="%s"', operation.name)
        outcome = Outcome(error_envelope(exc, operation.name))
    meta = outcome.envelope.setdefault("meta", {})
    meta["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if outcome.envelope.get("ok") and ctx.settings.warnings:
        warnings = list(meta.get("warnings", []))
        warnings.extend(w for w in ctx.settings.warnings if w not in warnings)
        meta["warnings"] = warnings
    return outcome


# ==== BEGIN block: config ====
@block("config")
def _config() -> list[Operation]:
    from trelloctl.app.commands import config as cmd

    return [
        Operation(
            ("config", "init"),
            "create config.toml with the minimal configuration",
            "trelloctl config init --force",
            cmd.init,
            params=(Param("force", "overwrite an existing config.toml", ParamType.BOOL),),
            description=(
                "Create the working directory and a config.toml with an empty [auth] section; "
                "every other key is listed commented, with its default value."
            ),
            mcp=False,
        ),
        Operation(
            ("config", "show"),
            "show the effective configuration (credentials masked)",
            "trelloctl config show",
            cmd.show,
            mcp=False,
        ),
        Operation(
            ("config", "validate"),
            "validate config.toml and access to the default board",
            "trelloctl config validate",
            cmd.validate,
            need=Need.CONFIG_EXISTS,
            mcp=False,
        ),
        Operation(
            ("config", "board", "show"),
            "show the default board (id, name, url)",
            "trelloctl config board show",
            cmd.board_show,
            need=Need.CONFIG,
            mcp=False,
        ),
        Operation(
            ("config", "board", "set"),
            "store the default board in config.toml",
            "trelloctl config board set 5f1b2c3d4e5f6a7b8c9d0e1f",
            cmd.board_set,
            params=(
                Param(
                    "board",
                    "board id (24 hexadecimal characters)",
                    positional=True,
                    required=True,
                ),
            ),
            need=Need.CONFIG,
            mcp=False,
        ),
        Operation(
            ("config", "board", "unset"),
            "remove the default board from config.toml",
            "trelloctl config board unset",
            cmd.board_unset,
            need=Need.CONFIG,
            mcp=False,
        ),
    ]


# ==== END block: config ====


# ==== BEGIN block: doctor ====
# M14 adds the `doctor` operation here.
# ==== END block: doctor ====


# ==== BEGIN block: board ====
# M08 adds the `board` operations here.
# ==== END block: board ====


# ==== BEGIN block: card ====
# M09, M10 and M11 add the `card` operations here.
# ==== END block: card ====


# ==== BEGIN block: inbox ====
# M12 adds the `inbox` operations here.
# ==== END block: inbox ====


# ==== BEGIN block: cache ====
# M07 adds the `cache` operations here.
# ==== END block: cache ====


# ==== BEGIN block: skills ====
# M13 adds the `skills` operations here.
# ==== END block: skills ====


# ==== BEGIN block: mcp ====
# M15 adds the `mcp serve` operation here (own_stdout=True).
# ==== END block: mcp ====


# ==== BEGIN block: version ====
@block("version")
def _version() -> list[Operation]:
    from trelloctl.app.commands import version as cmd

    return [
        Operation(
            ("version",),
            "version as JSON",
            "trelloctl version",
            cmd.version,
            description="Print the trelloctl version, the Python version and the platform as JSON.",
            mcp=False,
        ),
    ]


# ==== END block: version ====

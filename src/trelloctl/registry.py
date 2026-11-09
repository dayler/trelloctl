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
@block("doctor")
def _doctor() -> list[Operation]:
    from trelloctl import doctor as cmd

    return [
        Operation(
            ("doctor",),
            "environment diagnostics (--api checks the Trello contract)",
            "trelloctl doctor --api",
            cmd.run,
            params=(
                Param("json", "print the report as a JSON envelope", ParamType.BOOL),
                Param(
                    "api", "check the Trello contract and read the default board", ParamType.BOOL
                ),
            ),
            description=(
                "Check the interpreter, working directory, configuration, credentials, DNS, "
                "TLS, token, default board, cache, MCP extra, skill and Inbox. Prints a "
                "text report (JSON with --json); exits 1 when any check fails."
            ),
            text_output=lambda args: not args.get("json"),
            mcp=False,
        ),
    ]


# ==== END block: doctor ====


# ==== BEGIN block: board ====
@block("board")
def _board() -> list[Operation]:
    from trelloctl.app.commands import activity, board, labels, lists

    list_ref = Param("list", "list: id or name", positional=True, required=True)
    label_ref = Param("label", "label: id, name or name:color", positional=True, required=True)
    return [
        Operation(
            ("board", "show"),
            "board id, name, url, default flag and counts",
            "trelloctl board show",
            board.show,
            description="Served from the cache: metadata and the open-cards snapshot.",
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("board", "lists"),
            "open lists by position with index and card count",
            "trelloctl board lists --all",
            board.lists,
            params=(Param("all", "include archived lists", ParamType.BOOL),),
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("board", "labels"),
            "labels of the board",
            "trelloctl board labels",
            board.labels,
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("board", "members"),
            "members of the board",
            "trelloctl board members",
            board.members,
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("board", "activity"),
            "actions of the board, newest first (never cached)",
            "trelloctl board activity --type createCard,commentCard --limit 20",
            activity.activity,
            params=(
                Param("since", "only actions after this ISO-8601 date", metavar="ISO8601"),
                Param("before", "only actions before this ISO-8601 date", metavar="ISO8601"),
                Param("type", "comma-separated Trello action types", ParamType.CSV),
                Param(
                    "limit",
                    "maximum number of actions (default 50, 0 for all)",
                    ParamType.INT,
                ),
                Param("fields", "comma-separated fields of each action", ParamType.CSV),
            ),
            description=(
                "Reads GET /boards/{id}/actions, paginating with `before`. At most 50 actions "
                "by default; meta.truncated tells whether more exist. Fields: id, type, date, "
                "member, card, list, data."
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "create"),
            "create a list",
            'trelloctl board list create --name "Review" --pos 2',
            lists.create,
            params=(
                Param("name", "name of the list", required=True),
                Param(
                    "pos",
                    "top, bottom (default) or a 0-based index among the open lists",
                    metavar="POS",
                ),
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "rename"),
            "rename a list",
            'trelloctl board list rename "To Do" --name Backlog',
            lists.rename,
            params=(list_ref, Param("name", "new name", required=True)),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "archive"),
            "archive a list (its cards are not archived)",
            'trelloctl board list archive "Done"',
            lists.archive,
            params=(list_ref,),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "unarchive"),
            "restore an archived list",
            'trelloctl board list unarchive "Done"',
            lists.unarchive,
            params=(list_ref,),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "move"),
            "move a list among the open lists",
            'trelloctl board list move "Doing" --right 1 --dry-run',
            lists.move,
            params=(
                list_ref,
                Param(
                    "to_index",
                    "final 0-based index among the open lists",
                    ParamType.INT,
                    required=True,
                    exclusive="target",
                    metavar="N",
                ),
                Param(
                    "left",
                    "move N positions to the left",
                    ParamType.INT,
                    exclusive="target",
                    metavar="N",
                ),
                Param(
                    "right",
                    "move N positions to the right",
                    ParamType.INT,
                    exclusive="target",
                    metavar="N",
                ),
                Param("clamp", "stop at the first or last list instead of failing", ParamType.BOOL),
                Param("dry_run", "compute the move without writing", ParamType.BOOL),
            ),
            description=(
                "Reads fresh metadata first, then sets the position. Out of range gives "
                "MOVE_OUT_OF_RANGE unless --clamp is given."
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "move-cards"),
            "move all open cards of a list to another list",
            'trelloctl board list move-cards "Doing" --to "Done"',
            lists.move_cards,
            params=(list_ref, Param("to", "target list: id or name", required=True)),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "list", "archive-cards"),
            "archive all open cards of a list",
            'trelloctl board list archive-cards "Done"',
            lists.archive_cards,
            params=(list_ref,),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "label", "create"),
            "create a label",
            "trelloctl board label create --name bug --color red",
            labels.create,
            params=(
                Param("name", "name of the label", required=True),
                Param(
                    "color",
                    "label color (or none)",
                    required=True,
                    choices=labels.COLORS,
                    metavar="COLOR",
                ),
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "label", "update"),
            "change the name or the color of a label",
            "trelloctl board label update bug --color orange",
            labels.update,
            params=(
                label_ref,
                Param("name", "new name"),
                Param("color", "new color (or none)", choices=labels.COLORS, metavar="COLOR"),
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("board", "label", "remove"),
            "delete a label from the board and from all its cards",
            "trelloctl board label remove bug --yes",
            labels.remove,
            params=(label_ref,),
            need=Need.BOARD,
            requires_yes=True,
        ),
    ]


# ==== END block: board ====


# ==== BEGIN block: card ====
@block("card")
def _card() -> list[Operation]:
    from trelloctl.app.commands import card as cmd

    card_ref = Param(
        "card",
        "id, shortLink, URL, number or name",
        positional=True,
        required=True,
        metavar="CARD",
    )
    desc_params = (
        Param("desc", "description; - reads standard input (UTF-8)", exclusive="desc"),
        Param(
            "desc_file",
            "read the description from a UTF-8 file",
            metavar="PATH",
            exclusive="desc",
        ),
    )
    pos = Param("pos", "top, bottom or final index (0-based)", metavar="POS")
    limit = Param("limit", "maximum cards (default 50; 0 = no limit)", ParamType.INT)
    fields = Param("fields", "comma-separated fields to keep in each card", ParamType.CSV)
    return [
        Operation(
            ("card", "create"),
            "create a card at the end of a list",
            'trelloctl card create --list "To Do" --name "Review PR 42" --labels bug,urgent',
            cmd.create,
            params=(
                Param("list", "list id or name", required=True),
                Param("name", "card name", required=True),
                *desc_params,
                Param("due", "due date, ISO-8601 (date alone: 12:00 UTC)", metavar="DATE"),
                Param("labels", "labels: ids, names or name:color", ParamType.CSV),
                Param("members", "members: ids, usernames or full names", ParamType.CSV),
                pos,
            ),
            description=(
                "Create the card at the end of the list, or at --pos, and print the complete "
                "card. The description is sent as is (Markdown). Labels and members must "
                "exist; otherwise nothing is created."
            ),
            need=Need.BOARD,
        ),
        Operation(
            ("card", "get"),
            "show a card; --full adds checklists, attachments and comments",
            'trelloctl card get "Review PR 42" --full',
            cmd.get,
            params=(
                card_ref,
                Param(
                    "full",
                    "add checklists, attachments and comments",
                    ParamType.BOOL,
                    exclusive="detail",
                ),
                Param(
                    "with",
                    "add some of: checklists, attachments, comments",
                    ParamType.CSV,
                    metavar="PARTS",
                    exclusive="detail",
                ),
            ),
            description=(
                "Show the card with its list, labels, members, due date, url, last activity and "
                "badges. Checklists and attachments come from the cache; comments cost one "
                "request. A card given by id or URL does not need a selected board."
            ),
            need=Need.CREDENTIALS,
            cached_read=True,
        ),
        Operation(
            ("card", "list"),
            "list the open cards of the board, with optional filters",
            'trelloctl card list --list "To Do" --label bug --not-done',
            cmd.list_cards,
            params=(
                Param("list", "only cards of this list"),
                Param("label", "only cards with this label"),
                Param("member", "only cards of this member"),
                Param("due_before", "only cards due before this moment", metavar="DATE"),
                Param("due_after", "only cards due after this moment", metavar="DATE"),
                Param("done", "only completed cards", ParamType.BOOL, exclusive="state"),
                Param("not_done", "only cards not completed", ParamType.BOOL, exclusive="state"),
                Param("include_archived", "also list archived cards", ParamType.BOOL),
                limit,
                fields,
            ),
            description=(
                "Cards that satisfy every filter, in board order, from the cache. At most 50 "
                "by default; meta.truncated tells whether more were left out."
            ),
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("card", "search"),
            "search cards by name, description, checklists or comments",
            'trelloctl card search "core-78*" --in name,desc,comments',
            cmd.find,
            params=(
                Param("pattern", "text; * and ? are wildcards", positional=True, required=True),
                Param(
                    "in",
                    "name, desc, checklists, comments",
                    ParamType.CSV,
                    metavar="FIELDS",
                ),
                Param("include_archived", "also search archived cards", ParamType.BOOL),
                limit,
                fields,
            ),
            description=(
                "Case-insensitive search; --in defaults to name,desc. A pattern without "
                "wildcards means *pattern*. Name, "
                "description and checklists are searched in the cache; comments are searched "
                "through Trello (one request) without wildcards, and meta.warnings says so."
            ),
            need=Need.BOARD,
            cached_read=True,
        ),
        Operation(
            ("card", "move"),
            "move a card to a list, or N lists to the left or right",
            'trelloctl card move "Review PR 42" --right 1 --dry-run',
            cmd.move,
            params=(
                card_ref,
                Param("to", "target list id or name", exclusive="target"),
                Param("left", "N lists to the left (N >= 1)", ParamType.INT, exclusive="target"),
                Param("right", "N lists to the right (N >= 1)", ParamType.INT, exclusive="target"),
                pos,
                Param("clamp", "stop at the first or last list", ParamType.BOOL),
                Param("dry_run", "compute and report the move without writing", ParamType.BOOL),
            ),
            description=(
                "With --left or --right the metadata is read fresh first and only open lists "
                "count. The card goes to the end of the target list unless --pos is given."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "copy"),
            "copy a card into a list",
            'trelloctl card copy "Review PR 42" --to Done --keep checklists,labels',
            cmd.copy,
            params=(
                card_ref,
                Param("to", "target list id or name", required=True),
                Param("name", "name of the copy (default: the same name)"),
                pos,
                Param(
                    "keep",
                    "all (default), none or a list of parts",
                    ParamType.CSV,
                    metavar="PARTS",
                ),
            ),
            description=(
                "Create the copy with idCardSource and print the new card. --keep takes all, "
                "none or a list of: attachments, checklists, comments, due, labels, members, "
                "stickers."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "update"),
            "change fields of a card",
            'trelloctl card update "Review PR 42" --due 2026-11-20 --labels bug',
            cmd.update,
            params=(
                card_ref,
                Param("name", "new name"),
                *desc_params,
                Param("due", "new due date, ISO-8601", metavar="DATE", exclusive="due"),
                Param("no_due", "remove the due date", ParamType.BOOL, exclusive="due"),
                Param("done", "mark as completed", ParamType.BOOL, exclusive="state"),
                Param("not_done", "mark as not completed", ParamType.BOOL, exclusive="state"),
                Param("labels", "replace the labels with these", ParamType.CSV),
                Param("members", "replace the members with these", ParamType.CSV),
                pos,
            ),
            description="Only the given fields change. --labels and --members replace the sets.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "done"),
            "mark a card as completed",
            'trelloctl card done "Review PR 42"',
            cmd.done,
            params=(card_ref,),
            description="Set dueComplete; the card does not need a due date.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "undone"),
            "mark a card as not completed",
            'trelloctl card undone "Review PR 42"',
            cmd.undone,
            params=(card_ref,),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "archive"),
            "archive a card",
            'trelloctl card archive "Review PR 42"',
            cmd.archive,
            params=(card_ref,),
            description="Cards are never deleted; archive is reversible with card unarchive.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "unarchive"),
            "restore an archived card to its list",
            "trelloctl card unarchive 6b00000000000000000000c1",
            cmd.unarchive,
            params=(card_ref,),
            description="Use the id, shortLink, URL or number; names skip archived cards.",
            need=Need.CREDENTIALS,
        ),
    ]


# ==== END block: card ====


# ==== BEGIN block: card attachments ====
_CARD_PARAM = Param(
    "card",
    "card: id, URL, shortLink, idShort or name",
    positional=True,
    required=True,
)


@block("card attachments")
def _card_attachments() -> list[Operation]:
    from trelloctl.app.commands import attachments as cmd

    return [
        Operation(
            ("card", "attachments", "list"),
            "list the attachments of a card, with the local path of downloaded files",
            "trelloctl card attachments list 6b00000000000000000000c1",
            cmd.list_attachments,
            params=(_CARD_PARAM,),
            description="Served from the local cache; --fresh reads the card from Trello.",
            need=Need.CREDENTIALS,
            cached_read=True,
        ),
        Operation(
            ("card", "attachments", "download"),
            "download the files of a card (--all) or one file (--id)",
            "trelloctl card attachments download 6b00000000000000000000c1 --all",
            cmd.download,
            params=(
                _CARD_PARAM,
                Param(
                    "all",
                    "download new and changed files, keep equal ones, delete orphans",
                    ParamType.BOOL,
                    required=True,
                    exclusive="target",
                ),
                Param("id", "attachment id to download", exclusive="target"),
            ),
            description=(
                "Reads the card from Trello (one call) and downloads each uploaded file, one "
                "request at a time, to <HOME>/attachments/<board_id>/<card_id>/. Links are "
                "reported as skipped. On RATE_LIMITED the error carries what was done in "
                "error.details.partial."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "attachments", "add"),
            "upload a file or attach a link to a card",
            "trelloctl card attachments add 6b00000000000000000000c1 --file report.pdf",
            cmd.add,
            params=(
                _CARD_PARAM,
                Param(
                    "file",
                    "path of the file to upload",
                    metavar="PATH",
                    required=True,
                    exclusive="source",
                ),
                Param("url", "link to attach", exclusive="source"),
                Param("name", "attachment name"),
            ),
            description=(
                "Give --file or --url. A file larger than attachments.max_upload_bytes fails "
                "with ATTACHMENT_TOO_LARGE before any call to Trello."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "attachments", "clear"),
            "delete the downloaded files of a card (or one attachment)",
            "trelloctl card attachments clear 6b00000000000000000000c1",
            cmd.clear,
            params=(_CARD_PARAM, Param("id", "attachment id; without it, every file of the card")),
            description=(
                "Deletes local copies only, never the attachments in Trello. Does not call "
                "Trello and does not need config.toml."
            ),
            mcp=False,
        ),
    ]


# ==== END block: card attachments ====


# ==== BEGIN block: card comment ====
_DETAIL_CARD = Param(
    "card",
    "card: id, URL, shortLink, idShort or name",
    positional=True,
    required=True,
    metavar="CARD",
)
_COMMENT_TEXT = (
    Param("text", "comment text; - reads standard input (UTF-8)", exclusive="text", required=True),
    Param("text_file", "read the text from a UTF-8 file", metavar="PATH", exclusive="text"),
)
_COMMENT_REF = Param("comment", "comment id", positional=True, required=True)


@block("card comment")
def _card_comment() -> list[Operation]:
    from trelloctl.app.commands import card as cmd

    return [
        Operation(
            ("card", "comments"),
            "list the comments of a card (never cached)",
            "trelloctl card comments 6b00000000000000000000c1 --limit 10",
            cmd.comments,
            params=(
                _DETAIL_CARD,
                Param("limit", "maximum comments (default 50; 0 = no limit)", ParamType.INT),
                Param(
                    "fields",
                    "comma-separated fields to keep",
                    ParamType.CSV,
                ),
            ),
            description="Newest first. Every call reads the comments from Trello.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "comment", "add"),
            "add a comment to a card",
            'trelloctl card comment add 6b00000000000000000000c1 --text "Done, @ana"',
            cmd.comment_add,
            params=(_DETAIL_CARD, *_COMMENT_TEXT),
            description=(
                "The text is sent as is (Markdown); @username mentions notify the member. "
                "Prints the id of the new comment."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "comment", "edit"),
            "replace the text of one of your comments",
            'trelloctl card comment edit 6b00000000000000000000c1 <comment-id> --text "New"',
            cmd.comment_edit,
            params=(_DETAIL_CARD, _COMMENT_REF, *_COMMENT_TEXT),
            description="Only comments created with the configured token can be edited.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "comment", "remove"),
            "delete one of your comments",
            "trelloctl card comment remove 6b00000000000000000000c1 <comment-id> --yes",
            cmd.comment_remove,
            params=(_DETAIL_CARD, _COMMENT_REF),
            description="Irreversible. Only comments created with the configured token.",
            need=Need.CREDENTIALS,
            requires_yes=True,
        ),
    ]


# ==== END block: card comment ====


# ==== BEGIN block: card label ====
@block("card label")
def _card_label() -> list[Operation]:
    from trelloctl.app.commands import card as cmd

    labels = Param(
        "labels",
        "labels: ids, names or name:color",
        ParamType.CSV,
        positional=True,
        required=True,
    )
    return [
        Operation(
            ("card", "label", "add"),
            "add labels to a card; the ones already present are ignored",
            "trelloctl card label add 6b00000000000000000000c1 bug,urgent",
            cmd.label_add,
            params=(_DETAIL_CARD, labels),
            description="Every label must exist; otherwise nothing is written.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "label", "remove"),
            "remove labels from a card; the ones absent are ignored",
            "trelloctl card label remove 6b00000000000000000000c1 bug",
            cmd.label_remove,
            params=(_DETAIL_CARD, labels),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "label", "set"),
            "leave exactly these labels on a card",
            "trelloctl card label set 6b00000000000000000000c1 bug,urgent",
            cmd.label_set,
            params=(_DETAIL_CARD, labels),
            description="Equivalent to `card update --labels`.",
            need=Need.CREDENTIALS,
        ),
    ]


# ==== END block: card label ====


# ==== BEGIN block: card member ====
@block("card member")
def _card_member() -> list[Operation]:
    from trelloctl.app.commands import card as cmd

    members = Param(
        "members",
        "members: ids, usernames or full names",
        ParamType.CSV,
        positional=True,
        required=True,
    )
    return [
        Operation(
            ("card", "member", "add"),
            "assign members to a card; the ones already assigned are ignored",
            "trelloctl card member add 6b00000000000000000000c1 ana,luis",
            cmd.member_add,
            params=(_DETAIL_CARD, members),
            description="Every member must exist; otherwise nothing is written.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "member", "remove"),
            "unassign members from a card; the ones absent are ignored",
            "trelloctl card member remove 6b00000000000000000000c1 ana",
            cmd.member_remove,
            params=(_DETAIL_CARD, members),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "member", "set"),
            "leave exactly these members on a card",
            "trelloctl card member set 6b00000000000000000000c1 ana,luis",
            cmd.member_set,
            params=(_DETAIL_CARD, members),
            description="Equivalent to `card update --members`.",
            need=Need.CREDENTIALS,
        ),
    ]


# ==== END block: card member ====


# ==== BEGIN block: card checklist ====
@block("card checklist")
def _card_checklist() -> list[Operation]:
    from trelloctl.app.commands import checklists as cmd

    checklist = Param("checklist", "checklist: id or name", positional=True, required=True)
    items = Param("items", "items: ids or names", ParamType.CSV, positional=True, required=True)
    pos = Param("pos", "top, bottom or final index (0-based)", metavar="POS")
    state_items = (
        _DETAIL_CARD,
        checklist,
        Param("items", "items: ids or names", ParamType.CSV, positional=True, nargs="?"),
        Param("all", "every item of the checklist", ParamType.BOOL),
    )
    return [
        Operation(
            ("card", "checklist", "list"),
            "list the checklists of a card with progress and items",
            "trelloctl card checklist list 6b00000000000000000000c1",
            cmd.list_checklists,
            params=(_DETAIL_CARD,),
            description="Served from the local cache; --fresh reads the card from Trello.",
            need=Need.CREDENTIALS,
            cached_read=True,
        ),
        Operation(
            ("card", "checklist", "add"),
            "create a checklist, optionally with initial items",
            "trelloctl card checklist add 6b00000000000000000000c1 --name QA --items a,b,c",
            cmd.add,
            params=(
                _DETAIL_CARD,
                Param("name", "checklist name", required=True),
                pos,
                Param("items", "initial items", ParamType.CSV, exclusive="items"),
                Param(
                    "items_file",
                    "read items from a UTF-8 file, one per line",
                    metavar="PATH",
                    exclusive="items",
                ),
            ),
            description="The checklist is created first and then each item, in order.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "rename"),
            "rename a checklist",
            "trelloctl card checklist rename 6b00000000000000000000c1 QA --name Tests",
            cmd.rename,
            params=(_DETAIL_CARD, checklist, Param("name", "new name", required=True)),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "remove"),
            "delete a checklist with its items",
            "trelloctl card checklist remove 6b00000000000000000000c1 QA --yes",
            cmd.remove,
            params=(_DETAIL_CARD, checklist),
            description="Irreversible.",
            need=Need.CREDENTIALS,
            requires_yes=True,
        ),
        Operation(
            ("card", "checklist", "item", "add"),
            "add an item to a checklist",
            "trelloctl card checklist item add 6b00000000000000000000c1 QA --name e2e --checked",
            cmd.item_add,
            params=(
                _DETAIL_CARD,
                checklist,
                Param("name", "item name", required=True),
                pos,
                Param("checked", "create the item already checked", ParamType.BOOL),
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "item", "check"),
            "mark items as complete; the ones already complete are ignored",
            "trelloctl card checklist item check 6b00000000000000000000c1 QA a,b",
            cmd.item_check,
            params=state_items,
            description="Give the items, or --all for every item of the checklist.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "item", "uncheck"),
            "mark items as incomplete; the ones already incomplete are ignored",
            "trelloctl card checklist item uncheck 6b00000000000000000000c1 QA --all",
            cmd.item_uncheck,
            params=state_items,
            description="Give the items, or --all for every item of the checklist.",
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "item", "rename"),
            "rename a checklist item",
            "trelloctl card checklist item rename 6b00000000000000000000c1 QA a --name a2",
            cmd.item_rename,
            params=(
                _DETAIL_CARD,
                checklist,
                Param("item", "item: id or name", positional=True, required=True),
                Param("name", "new name", required=True),
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("card", "checklist", "item", "remove"),
            "delete checklist items",
            "trelloctl card checklist item remove 6b00000000000000000000c1 QA a,b --yes",
            cmd.item_remove,
            params=(_DETAIL_CARD, checklist, items),
            description="Irreversible.",
            need=Need.CREDENTIALS,
            requires_yes=True,
        ),
    ]


# ==== END block: card checklist ====


# ==== BEGIN block: inbox ====
@block("inbox")
def _inbox() -> list[Operation]:
    from trelloctl.app.commands import inbox as cmd

    return [
        Operation(
            ("inbox", "list"),
            "list the cards of the personal Inbox",
            "trelloctl inbox list --limit 20",
            cmd.list_cards,
            params=(
                Param("limit", "maximum cards (default 50; 0 = no limit)", ParamType.INT),
                Param("fields", "comma-separated fields to keep in each card", ParamType.CSV),
            ),
            description=(
                "Served from the local cache; needs no selected board. INBOX_UNAVAILABLE when "
                "Trello does not return an Inbox for the account."
            ),
            need=Need.CREDENTIALS,
            cached_read=True,
        ),
        Operation(
            ("inbox", "add"),
            "add a card to the personal Inbox",
            'trelloctl inbox add --name "Review November invoice" --due 2026-11-15',
            cmd.add,
            params=(
                Param("name", "card name", required=True),
                Param("desc", "description; - reads standard input (UTF-8)", exclusive="desc"),
                Param(
                    "desc_file",
                    "read the description from a UTF-8 file",
                    metavar="PATH",
                    exclusive="desc",
                ),
                Param("due", "due date, ISO-8601 (date alone: 12:00 UTC)", metavar="DATE"),
            ),
            description=(
                "Create the card in the single list of the Inbox and print it. The description "
                "is sent as is (Markdown). Needs no selected board; takes no list, labels or "
                "members."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("inbox", "move"),
            "move a card of the Inbox to a list of the selected board",
            'trelloctl inbox move "Review November invoice" --to "To Do" --pos 0',
            cmd.move,
            params=(
                Param(
                    "card",
                    "card of the Inbox: id, URL, shortLink, number or name",
                    positional=True,
                    required=True,
                    metavar="CARD",
                ),
                Param("to", "target list id or name, on the selected board", required=True),
                Param("pos", "top, bottom or final index (0-based)", metavar="POS"),
            ),
            description=(
                "The only operation that crosses boards: the card is resolved against the Inbox "
                "and moved to a list of the board selected by --board, TRELLO_BOARD or the "
                "default one."
            ),
            need=Need.BOARD,
        ),
    ]


# ==== END block: inbox ====


# ==== BEGIN block: cache ====
@block("cache")
def _cache() -> list[Operation]:
    from trelloctl.app.commands import cache as cmd

    return [
        Operation(
            ("cache", "reload"),
            "reload the cache of the board from Trello",
            'trelloctl cache reload --list "To Do"',
            cmd.reload,
            params=(
                Param("list", "reload only the open cards of this list", exclusive="scope"),
                Param(
                    "card",
                    "reload only this card, with attachments and checklists",
                    exclusive="scope",
                ),
                Param(
                    "archived",
                    "reload the archived cards of the board",
                    ParamType.BOOL,
                    exclusive="scope",
                ),
            ),
            description=(
                "Without options: the user's boards, the board metadata and its open cards "
                "(three reads, plus one per further page of 1000 cards), replacing the "
                "snapshot and renewing its TTLs. With --list, --card or --archived only those "
                "rows are updated and no TTL is renewed."
            ),
            need=Need.CREDENTIALS,
        ),
        Operation(
            ("cache", "status"),
            "age and size of each cached snapshot, and attachment space",
            "trelloctl cache status",
            cmd.status,
            description="Does not call Trello and does not need config.toml.",
        ),
        Operation(
            ("cache", "clear"),
            "empty the local cache",
            "trelloctl cache clear --attachments",
            cmd.clear,
            params=(
                Param(
                    "attachments",
                    "also delete the downloaded attachments",
                    ParamType.BOOL,
                ),
            ),
            description=(
                "Empties cache.db and keeps the downloaded attachments unless --attachments "
                "is given. Does not call Trello and does not need config.toml."
            ),
            mcp=False,
        ),
    ]


# ==== END block: cache ====


# ==== BEGIN block: skills ====
@block("skills")
def _skills() -> list[Operation]:
    from trelloctl.app.commands import skills as cmd

    return [
        Operation(
            ("skills", "list"),
            "list the skills shipped with trelloctl",
            "trelloctl skills list",
            cmd.list_skills,
            mcp=False,
        ),
        Operation(
            ("skills", "install"),
            "install the skill for a harness (only claude)",
            "trelloctl skills install --harness claude --scope user",
            cmd.install,
            params=(
                Param(
                    "harness",
                    "harness to install for (only claude)",
                    required=True,
                    choices=cmd.HARNESSES,
                ),
                Param(
                    "scope",
                    "user (default) or project",
                    choices=cmd.SCOPES,
                    exclusive="target",
                ),
                Param(
                    "dir",
                    "directory that receives SKILL.md",
                    metavar="PATH",
                    exclusive="target",
                ),
                Param("force", "overwrite an existing SKILL.md", ParamType.BOOL),
            ),
            description=(
                "Copy the skill to ~/.claude/skills/trelloctl/ (default), "
                "./.claude/skills/trelloctl/ or the given directory. "
                "An existing SKILL.md is not overwritten without --force."
            ),
            mcp=False,
        ),
        Operation(
            ("skills", "export"),
            "write SKILL.md to a directory, for other harnesses",
            "trelloctl skills export --dir ./skill",
            cmd.export,
            params=(
                Param(
                    "dir",
                    "directory that receives SKILL.md",
                    required=True,
                    metavar="PATH",
                ),
            ),
            mcp=False,
        ),
    ]


# ==== END block: skills ====


# ==== BEGIN block: mcp ====
@block("mcp")
def _mcp() -> list[Operation]:
    from trelloctl import mcp_server

    return [
        Operation(
            ("mcp", "serve"),
            "run the MCP server over stdio (experimental)",
            "trelloctl mcp serve",
            mcp_server.serve,
            description=(
                "Experimental. Serve the operations as MCP tools over stdio until the client "
                "closes standard input. Needs the extra: uv tool install 'trelloctl[mcp]'. "
                "The configuration is read once at startup; stdout carries only protocol "
                "messages."
            ),
            mcp=False,
            own_stdout=True,
        ),
    ]


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

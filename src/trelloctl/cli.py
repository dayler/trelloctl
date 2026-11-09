"""Command-line adapter: argparse tree generated from the registry, exit codes, logging.

It translates argv into an operation call and the resulting envelope into stdout and an
exit code; the logic lives in `app/commands` and the shared dispatcher in `registry.py`.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from trelloctl import __version__
from trelloctl.app.commands.context import CommandContext, Outcome, load_settings_lenient
from trelloctl.domain.errors import TrelloctlError, exit_code_for
from trelloctl.infra.logging import configure_logging, log_invocation, shutdown_logging
from trelloctl.infra.paths import LOGS_DIR, default_home, ensure_home
from trelloctl.infra.trello_client import Transport
from trelloctl.output import configure_console, error_envelope, render
from trelloctl.registry import (
    GROUP_HELP,
    Operation,
    Param,
    ParamType,
    Registry,
    default_registry,
    execute,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_STALE = 9
LOG_LEVELS = ("debug", "info", "warning", "error")

GENERAL_HELP = """\
trelloctl — operate Trello boards from the command line, built for agent harnesses.

usage: trelloctl [--board B] [--include-archived-boards] [--compact] [--no-cache]
                 [--log-level L] [-v] <group> <command> ...

groups:
  config     configuration and default board
  doctor     environment diagnostics (--api checks the Trello contract)
  board      board: lists, labels, members, activity
  card       cards: create, search, move, label, checklists, attachments
  inbox      personal Inbox: list, add, move (to a list of the board)
  cache      local cache: reload, status, clear
  skills     skill for the harness: list, install, export
  mcp        MCP server over stdio (experimental)
  version    version as JSON

global options (before or after the command):
  --board B                   board: id, shortLink, url or name
  --include-archived-boards   also consider archived boards when resolving a board by name
  --compact                   JSON on a single line (default: indented)
  --no-cache                  do not read or write cache.db for this invocation
  --log-level L               debug|info|warning|error
  -v, --verbose               mirror the log to stderr
  -h, --help                  this help (text)
  --version                   version (text)

All output is indented JSON except --help, --version, no arguments and doctor without --json.
Board: --board (id, shortLink, url or name) > TRELLO_BOARD >
  [board].default in ~/.trelloctl/config.toml.
Archived boards are skipped when resolving a board by name,
  unless --include-archived-boards is given.
Cache: metadata 30 min, cards 5 min (cache.*_ttl_seconds). --fresh forces a read from Trello.
Irreversible, require --yes: board label remove, card comment remove,
  card checklist remove, card checklist item remove.
Per-command help: trelloctl <group> <command> --help
"""


class _UsageError(TrelloctlError):
    """Argument error raised by the parser, with the command it was detected in."""

    def __init__(self, message: str, command: str) -> None:
        super().__init__("USAGE", message)
        self.command = command


_CREDENTIAL_WORDS = frozenset(
    {"token", "key", "apikey", "password", "passwd", "secret", "credential", "credentials"}
)


def _credential_values(args: Sequence[str]) -> list[str]:
    """Values given to credential-looking options, which must never be echoed."""
    found: list[str] = []
    for index, arg in enumerate(args):
        if not arg.startswith("--"):
            continue
        name, equals, value = arg[2:].partition("=")
        if not set(name.lower().split("-")) & _CREDENTIAL_WORDS:
            continue
        if equals:
            found.append(value)
        elif index + 1 < len(args) and not args[index + 1].startswith("-"):
            found.append(args[index + 1])
    return [v for v in found if v]


def _redacted(exc: _UsageError, args: Sequence[str]) -> _UsageError:
    """The usage error with every credential value replaced by `***`."""
    message = exc.message
    for secret in sorted(_credential_values(args), key=len, reverse=True):
        message = message.replace(secret, "***")
    return exc if message == exc.message else _UsageError(message, exc.command)


def _command_of(prog: str) -> str:
    return " ".join(prog.split()[1:])


class _Parser(argparse.ArgumentParser):
    """Parser whose errors are exceptions, so the CLI can answer them as JSON."""

    def error(self, message: str) -> NoReturn:
        raise _UsageError(message, _command_of(self.prog))


class _TextAction(argparse.Action):
    """Prints a fixed text on stdout and exits successfully (`--help`, `--version`)."""

    text = ""

    def __init__(self, option_strings: Sequence[str], dest: str, **kwargs: Any) -> None:
        super().__init__(option_strings, dest, nargs=0, default=argparse.SUPPRESS, **kwargs)

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: Any,
        option_string: str | None = None,
    ) -> None:
        sys.stdout.write(self.text)
        parser.exit(EXIT_OK)


class _GeneralHelp(_TextAction):
    text = GENERAL_HELP


class _Version(_TextAction):
    text = f"trelloctl {__version__}\n"


def _add_globals(parser: argparse.ArgumentParser, *, leaf: bool) -> None:
    """Global options; on commands they are accepted but do not override earlier values."""

    def default(value: Any) -> Any:
        return argparse.SUPPRESS if leaf else value

    def text(value: str) -> str:
        return argparse.SUPPRESS if leaf else value

    parser.add_argument(
        "--board", metavar="BOARD", default=default(None), help=text("board"), dest="board"
    )
    parser.add_argument(
        "--include-archived-boards",
        action="store_true",
        default=default(False),
        help=text("include archived boards when resolving a board by name"),
        dest="include_archived_boards",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        default=default(False),
        help=text("JSON on one line"),
        dest="compact",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=default(False),
        help=text("skip cache.db"),
        dest="no_cache",
    )
    parser.add_argument(
        "--log-level",
        choices=LOG_LEVELS,
        default=default(None),
        help=text("log level"),
        dest="log_level",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        default=default(False),
        help=text("mirror the log to stderr"),
        dest="verbose",
    )


def _convert_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid integer value: {value!r}") from None


def _convert_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _escape(text: str) -> str:
    return text.replace("%", "%%")


def _add_param(target: Any, param: Param, *, optional_group: bool = False) -> None:
    kwargs: dict[str, Any] = {"help": _escape(param.description)}
    if param.type is ParamType.BOOL:
        kwargs.update(action="store_true", default=bool(param.default))
    else:
        if param.type is ParamType.INT:
            kwargs["type"] = _convert_int
        elif param.type is ParamType.CSV:
            kwargs["type"] = _convert_csv
        if param.choices:
            kwargs["choices"] = param.choices
        if param.metavar or param.positional:
            kwargs["metavar"] = param.metavar or param.name.upper()
    if param.positional:
        if param.nargs:
            kwargs["nargs"] = param.nargs
        target.add_argument(param.name, **kwargs)
        return
    flags = [param.flag] + ([param.short] if param.short else [])
    if param.type is not ParamType.BOOL:
        kwargs["default"] = param.default
        kwargs["required"] = param.required and not optional_group
    target.add_argument(*flags, dest=param.name, **kwargs)


def _add_operation_arguments(parser: argparse.ArgumentParser, operation: Operation) -> None:
    groups: dict[str, Any] = {}
    params = operation.all_params
    for param in params:
        if param.exclusive is None or param.positional:
            _add_param(parser, param)
            continue
        if param.exclusive not in groups:
            required = any(p.required for p in params if p.exclusive == param.exclusive)
            groups[param.exclusive] = parser.add_mutually_exclusive_group(required=required)
        _add_param(groups[param.exclusive], param, optional_group=True)


def _leaf_parser(sub: Any, name: str, operation: Operation) -> None:
    parser = sub.add_parser(
        name,
        help=_escape(operation.summary),
        description=_escape(operation.description or operation.summary),
        epilog="Example:\n  " + _escape(operation.example),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    _add_operation_arguments(parser, operation)
    _add_globals(parser, leaf=True)
    parser.set_defaults(_operation=operation)


def _build_level(
    parser: argparse.ArgumentParser, operations: Sequence[Operation], prefix: tuple[str, ...]
) -> None:
    names: list[str] = []
    for operation in operations:
        name = operation.path[len(prefix)]
        if name not in names:
            names.append(name)
    sub = parser.add_subparsers(
        dest=f"_level{len(prefix)}",
        metavar="<group>" if not prefix else "<command>",
        parser_class=_Parser,
        required=True,
    )
    for name in names:
        path = (*prefix, name)
        children = [op for op in operations if op.path[: len(path)] == path]
        if len(children) == 1 and children[0].path == path:
            _leaf_parser(sub, name, children[0])
            continue
        text = _escape(GROUP_HELP.get(path, name))
        group = sub.add_parser(name, help=text, description=text, allow_abbrev=False)
        _build_level(group, children, path)


def build_parser(registry: Registry) -> argparse.ArgumentParser:
    """The argparse tree of the registry, with the global options at the top."""
    parser = _Parser(prog="trelloctl", add_help=False, allow_abbrev=False)
    parser.add_argument("-h", "--help", action=_GeneralHelp, help="show the general help")
    parser.add_argument("--version", action=_Version, help="show the version")
    _add_globals(parser, leaf=False)
    operations = registry.operations()
    if operations:
        _build_level(parser, operations, ())
    return parser


def _parse(parser: argparse.ArgumentParser, argv: Sequence[str]) -> argparse.Namespace:
    namespace, extras = parser.parse_known_args(list(argv))
    if extras:
        operation: Operation | None = getattr(namespace, "_operation", None)
        raise _UsageError(
            f"unrecognized arguments: {' '.join(extras)}",
            operation.name if operation else "",
        )
    return namespace


def _write(stream: Any, text: str) -> None:
    stream.write(text)
    stream.flush()


def _exit_code(outcome: Outcome) -> int:
    if outcome.exit_code is not None:
        return outcome.exit_code
    envelope = outcome.envelope
    if not envelope.get("ok"):
        return exit_code_for(envelope["error"]["code"])
    cache = envelope.get("meta", {}).get("cache")
    if isinstance(cache, dict) and cache.get("stale"):
        return EXIT_STALE
    return EXIT_OK


def _fail(error: BaseException, command: str, compact: bool) -> int:
    """Answer an error that happened before the operation ran."""
    outcome = Outcome(error_envelope(error, command or "trelloctl"))
    _write(sys.stdout, render(outcome.envelope, compact=compact))
    return _exit_code(outcome)


def _log_usage_error(env: Mapping[str, str], command: str, started: float) -> None:
    """Best-effort log line for an argument error; never fails the invocation."""
    try:
        home = ensure_home(default_home(env))
        settings, _ = load_settings_lenient(env, home)
        logger = configure_logging(
            home / LOGS_DIR,
            level=settings.log_level,
            max_bytes=settings.log_max_bytes,
            backup_count=settings.log_backup_count,
        )
        elapsed = int((time.monotonic() - started) * 1000)
        log_invocation(logger, command or "trelloctl", elapsed, "USAGE", ok=False)
    except (TrelloctlError, OSError):
        pass
    finally:
        shutdown_logging()


def _run(
    namespace: argparse.Namespace,
    env: Mapping[str, str],
    transport: Transport | None,
    started: float,
) -> int:
    operation: Operation = namespace._operation
    compact = bool(namespace.compact)
    args = {p.name: getattr(namespace, p.name, None) for p in operation.all_params}
    try:
        home = ensure_home(default_home(env))
    except TrelloctlError as exc:
        return _fail(exc, operation.name, compact)
    settings, config_error = load_settings_lenient(env, home)
    logger = configure_logging(
        home / LOGS_DIR,
        level=namespace.log_level or settings.log_level,
        max_bytes=settings.log_max_bytes,
        backup_count=settings.log_backup_count,
        verbose=bool(namespace.verbose),
        secrets=lambda: settings.secrets,
    )
    try:
        ctx = CommandContext(
            settings=settings,
            command=operation.name,
            config_error=config_error,
            environ=env,
            board_flag=namespace.board,
            include_archived_boards=bool(namespace.include_archived_boards),
            no_cache=bool(namespace.no_cache),
            transport=transport,
            logger=logger,
        )
        logger.debug('start command="%s"', operation.name)
        outcome = execute(operation, args, ctx)
        code = _exit_code(outcome)
        ok = bool(outcome.envelope.get("ok"))
        log_invocation(
            logger,
            operation.name,
            int((time.monotonic() - started) * 1000),
            "ok" if ok else str(outcome.envelope["error"]["code"]),
            ok=ok,
        )
        if operation.own_stdout:
            return code
        if outcome.text is not None:
            text = outcome.text if outcome.text.endswith("\n") else outcome.text + "\n"
            _write(sys.stdout, text)
        else:
            _write(sys.stdout, render(outcome.envelope, compact=compact))
        return code
    finally:
        shutdown_logging()


def main(
    argv: Sequence[str] | None = None,
    *,
    registry: Registry | None = None,
    environ: Mapping[str, str] | None = None,
    transport: Transport | None = None,
) -> int:
    """Run the CLI and return the process exit code."""
    started = time.monotonic()
    args = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    configure_console(sys.stdout, sys.stderr)
    if not args:
        _write(sys.stdout, GENERAL_HELP)
        return EXIT_USAGE
    parser = build_parser(registry if registry is not None else default_registry())
    try:
        namespace = _parse(parser, args)
    except SystemExit as exc:  # --help, --version
        return exc.code if isinstance(exc.code, int) else EXIT_OK
    except _UsageError as raised:
        usage = _redacted(raised, args)
        _log_usage_error(env, usage.command, started)
        return _fail(usage, usage.command, "--compact" in args)
    return _run(namespace, env, transport, started)

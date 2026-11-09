"""Unit tests of the argparse tree generated from the registry and of the CLI flow."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.transport import StubTransport, response

from trelloctl.app.commands.context import CommandContext, Outcome
from trelloctl.cli import EXIT_STALE, build_parser, main
from trelloctl.registry import Operation, Param, ParamType, Registry, default_registry


def capture(seen: dict[str, Any]) -> Any:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        seen.update(args)
        return ctx.success({})

    return handler


def registry_with(*params: Param, **kwargs: Any) -> tuple[Registry, dict[str, Any]]:
    seen: dict[str, Any] = {}
    operation = Operation(
        ("probe", "run"), "a probe", "trelloctl probe run", capture(seen), params=params, **kwargs
    )
    return default_registry().extended(operation), seen


def call(capsys: pytest.CaptureFixture[str], registry: Registry, *argv: str) -> tuple[int, Any]:
    code = main(list(argv), registry=registry)
    return code, json.loads(capsys.readouterr().out)


def single(path: tuple[str, ...], handler: Any, **kwargs: Any) -> Registry:
    operation = Operation(path, "s", "trelloctl " + " ".join(path), handler, **kwargs)
    return default_registry().extended(operation)


def test_parameter_types_and_defaults(capsys: pytest.CaptureFixture[str]) -> None:
    registry, seen = registry_with(
        Param("name", "the name", positional=True, required=True),
        Param("rest", "more names", positional=True, nargs="*"),
        Param("count", "a count", ParamType.INT, short="-n", default=3),
        Param("tags", "tags", ParamType.CSV),
        Param("mode", "mode", choices=("a", "b"), metavar="MODE"),
        Param("dry_run", "no writes", ParamType.BOOL),
    )
    argv = [
        "probe",
        "run",
        "x",
        "y",
        "z",
        "-n",
        "7",
        "--tags",
        "a, b,,c",
        "--mode",
        "b",
        "--dry-run",
    ]
    code, envelope = call(capsys, registry, *argv)
    assert (code, envelope["ok"]) == (0, True)
    assert seen == {
        "name": "x",
        "rest": ["y", "z"],
        "count": 7,
        "tags": ["a", "b", "c"],
        "mode": "b",
        "dry_run": True,
    }
    seen.clear()
    call(capsys, registry, "probe", "run", "x")
    assert seen["count"] == 3
    assert seen["rest"] == []
    assert seen["dry_run"] is False
    assert seen["tags"] is None


@pytest.mark.parametrize(
    "argv",
    [
        ["probe", "run", "x", "--count", "many"],
        ["probe", "run", "x", "--mode", "c"],
        ["probe", "run"],
        ["probe", "run", "x", "--cou", "1"],
        ["probe", "run", "x", "stray"],
    ],
)
def test_invalid_input_is_usage(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    registry, seen = registry_with(
        Param("name", "the name", positional=True, required=True),
        Param("count", "a count", ParamType.INT),
        Param("mode", "mode", choices=("a", "b")),
    )
    code, envelope = call(capsys, registry, *argv)
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert seen == {}


def test_usage_error_names_the_command(capsys: pytest.CaptureFixture[str]) -> None:
    registry, _ = registry_with(Param("name", "the name", positional=True, required=True))
    _, envelope = call(capsys, registry, "probe", "run", "--bad", "x")
    assert envelope["meta"]["command"] == "probe run"
    _, envelope = call(capsys, registry, "probe", "run")
    assert envelope["meta"]["command"] == "probe run"
    _, envelope = call(capsys, registry, "--compact", "bogus")
    assert envelope["meta"]["command"] == "trelloctl"


def test_implicit_flags_reach_the_handler(capsys: pytest.CaptureFixture[str]) -> None:
    registry, seen = registry_with(cached_read=True, requires_yes=True)
    code, envelope = call(capsys, registry, "probe", "run")
    assert (code, envelope["error"]["code"]) == (6, "CONFIRMATION_REQUIRED")
    code, envelope = call(capsys, registry, "probe", "run", "--fresh", "--yes")
    assert code == 0
    assert seen == {"fresh": True, "yes": True}


def test_percent_signs_in_help_do_not_break_the_formatter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry, _ = registry_with(Param("pct", "uses 100% of it"))
    assert main(["probe", "run", "--help"], registry=registry) == 0
    assert "100% of it" in capsys.readouterr().out


def test_global_options_before_and_after_the_command_keep_their_values() -> None:
    parser = build_parser(default_registry())
    first = parser.parse_args(["--board", "B", "--no-cache", "-v", "version"])
    assert (first.board, first.no_cache, first.verbose) == ("B", True, True)
    second = parser.parse_args(["--board", "B", "version", "--compact"])
    assert (second.board, second.compact) == ("B", True)
    third = parser.parse_args(["version", "--log-level", "debug"])
    assert third.log_level == "debug"
    assert parser.parse_args(["version"]).board is None


def test_empty_registry_builds_a_parser() -> None:
    assert build_parser(Registry()).parse_known_args(["--compact"])[0].compact is True


def test_text_outcome_and_exit_code_override(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> Outcome:
        return Outcome(ctx.success("report"), text="plain report", exit_code=1)

    assert main(["probe", "text"], registry=single(("probe", "text"), handler)) == 1
    assert capsys.readouterr().out == "plain report\n"


def test_own_stdout_operations_print_nothing_and_return_their_code(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> Outcome:
        return Outcome(ctx.success(None), exit_code=0)

    registry = single(("probe", "serve"), handler, own_stdout=True)
    assert main(["probe", "serve"], registry=registry) == 0
    assert capsys.readouterr().out == ""


def test_stale_cache_exits_with_9(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        return ctx.success([], meta={"cache": {"hit": True, "stale": True}})

    code = main(["probe", "stale"], registry=single(("probe", "stale"), handler))
    assert code == EXIT_STALE
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_usage_error_is_logged_when_the_home_is_writable(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["version", "--nope"]) == 2
    capsys.readouterr()
    log = (trelloctl_home / "logs" / "trelloctl.log").read_text(encoding="utf-8")
    assert "result=USAGE" in log


def test_usage_error_with_an_unusable_home_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TRELLOCTL_HOME", str(blocker))
    assert main(["version", "--nope"]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USAGE"


def test_environment_and_transport_are_injectable(capsys: pytest.CaptureFixture[str]) -> None:
    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        return ctx.success(ctx.client().get("/members/me"))

    transport = StubTransport(response(200, '{"id": "m1"}'))
    code = main(
        ["probe", "me"],
        registry=single(("probe", "me"), handler),
        environ={"TRELLO_API_KEY": "k", "TRELLO_TOKEN": "t"},
        transport=transport,
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["data"] == {"id": "m1"}
    assert "key=k" in transport.requests[0].url

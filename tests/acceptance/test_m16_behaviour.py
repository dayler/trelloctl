"""M16 acceptance tests: non-functional requirements and completeness checked end to end."""

from __future__ import annotations

import copy
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import close_caches  # noqa: F401
from tests.acceptance.m08_support import add_actions
from tests.acceptance.m09_support import writes
from tests.acceptance.m16_support import (
    COMMENT,
    README,
    REVIEW,
    SKILL,
    assert_english,
    environment,
    extract_examples,
    run_cli,
)
from tests.fakes.trello import FakeTrello

from trelloctl import cli, doctor
from trelloctl.app.commands.config import CONFIG_TEMPLATE
from trelloctl.registry import GROUP_HELP, Operation, default_registry

OPERATIONS = [op for op in default_registry().operations() if not op.own_stdout]
Capsys = pytest.CaptureFixture[str]


def example_argv(operation: Operation) -> list[str]:
    """The registry example of an operation as argv, runnable against the documented account."""
    if operation.path == ("doctor",):
        return ["doctor", "--json"]  # the example (`--api`) prints text
    return shlex.split(operation.example.replace("<comment-id>", COMMENT))[1:]


@pytest.fixture(autouse=True)
def offline_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS and TLS are simulated: no test resolves or connects to a real host."""
    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)


# --- TC-M16-004 --------------------------------------------------------------------------


@pytest.mark.tc("TC-M16-004")
def test_every_json_command_declares_schema_version_1_on_success_and_on_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    assert len(OPERATIONS) >= 60
    with environment(tmp_path, monkeypatch, capsys, "schema") as env:
        for operation in OPERATIONS:
            env.reset()
            (Path.cwd() / "report.pdf").write_bytes(b"x")
            env.run("cache", "reload")
            ok_code, ok = env.run(*example_argv(operation))
            assert ok["ok"] is True, (operation.name, ok)
            assert ok_code == 0, operation.name
            assert ok["meta"]["schema_version"] == "1", operation.name
            assert ok["meta"]["command"] == operation.name

            bad_code, bad = env.run(*operation.path, "--no-such-option")
            assert (bad_code, bad["ok"]) == (2, False), operation.name
            assert bad["error"]["code"] == "USAGE"
            assert bad["meta"]["schema_version"] == "1", operation.name
            assert bad["meta"]["command"] == operation.name


# --- TC-M16-006 --------------------------------------------------------------------------

# Scenario -> (command, whether the second run is answered without any write). Every
# operation skips the request when the state already matches (RNF-6).
IDEMPOTENT = {
    "label already on the card": (("card", "label", "add", REVIEW, "bug"), True),
    "member added twice": (("card", "member", "add", REVIEW, "ana"), True),
    "item checked twice": (
        ("card", "checklist", "item", "check", REVIEW, "QA", "e2e tests"),
        True,
    ),
    "card archived twice": (("card", "archive", REVIEW), True),
    "card done twice": (("card", "done", REVIEW), True),
    "list archived twice": (("board", "list", "archive", "Done"), True),
}


def steady(fake: FakeTrello) -> Any:
    """The data of the fake without the activity timestamps that every write refreshes."""
    state = copy.deepcopy((fake.state.cards, fake.state.lists, fake.state.checklists))
    for item in state[0].values():
        item.pop("dateLastActivity", None)
    return state


@pytest.mark.tc("TC-M16-006")
@pytest.mark.parametrize("scenario", sorted(IDEMPOTENT))
def test_repeating_an_idempotent_operation_is_ok_and_changes_nothing(
    scenario: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    argv, quiet = IDEMPOTENT[scenario]
    with environment(tmp_path, monkeypatch, capsys, "idempotent") as env:
        code, _ = env.run(*argv)
        assert code == 0
        before = steady(env.fake)
        env.fake.requests.clear()
        code, again = env.run(*argv)
        assert (code, again["ok"]) == (0, True)
        sent = [(r.method, r.path) for r in writes(env.fake)]
        assert quiet
        assert sent == []
        after = steady(env.fake)
        assert after == before


# --- TC-M16-007 --------------------------------------------------------------------------

UTC_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3})?Z$")
DATE_KEYS = {"date", "due", "dateLastActivity"}


def moments(node: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in DATE_KEYS and value is not None:
                yield key, value
            else:
                yield from moments(value)
    elif isinstance(node, list):
        for item in node:
            yield from moments(item)


@pytest.mark.tc("TC-M16-007")
def test_dates_of_every_group_are_iso_8601_in_utc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    with environment(tmp_path, monkeypatch, capsys, "dates") as env:
        add_actions(env.fake, 2)
        scenarios = {
            "board": ("board", "activity"),
            "card get": ("card", "get", REVIEW, "--full"),
            "card comments": ("card", "comments", REVIEW),
            "card update": ("card", "update", REVIEW, "--due", "2026-11-20T10:00:00+02:00"),
            "attachments": ("card", "attachments", "list", REVIEW),
            "inbox": ("inbox", "add", "--name", "Invoice", "--due", "2026-11-15"),
        }
        found: dict[str, list[tuple[str, Any]]] = {}
        for name, argv in scenarios.items():
            code, envelope = env.run(*argv)
            assert code == 0, (name, envelope)
            found[name] = list(moments(envelope["data"]))
            assert found[name], name
        for name, items in found.items():
            for key, value in items:
                assert UTC_MOMENT.match(value), (name, key, value)
        assert ("due", "2026-11-20T08:00:00.000Z") in found["card update"]
        assert ("due", "2026-11-15T12:00:00.000Z") in found["inbox"]


# --- TC-M16-010 --------------------------------------------------------------------------


def help_of(capsys: Capsys, *argv: str) -> str:
    assert cli.main(list(argv)) == 0, argv
    return capsys.readouterr().out


@pytest.mark.tc("TC-M16-010")
def test_help_of_every_command_and_group_is_english(capsys: Capsys) -> None:
    assert_english(help_of(capsys, "--help"), "general help")
    for group in GROUP_HELP:
        assert_english(help_of(capsys, *group, "--help"), " ".join(group))
    for operation in default_registry().operations():
        assert_english(help_of(capsys, *operation.path, "--help"), operation.name)
    assert cli.main([]) == 2
    assert_english(capsys.readouterr().out, "no arguments")


@pytest.mark.tc("TC-M16-010")
def test_template_doctor_skill_and_logs_are_english_and_the_readme_is_excluded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    assert_english(CONFIG_TEMPLATE, "config.toml template")
    assert_english(SKILL.read_text(encoding="utf-8"), "SKILL.md")
    with environment(tmp_path, monkeypatch, capsys, "english") as env:
        assert cli.main(["doctor"]) == 0
        assert_english(capsys.readouterr().out, "doctor text")
        for argv in (
            ("board", "show"),
            ("card", "get", "No such card"),
            ("card", "move", REVIEW, "--right", "9"),
            ("-v", "board", "labels"),
        ):
            env.run(*argv)
        log = (env.home / "logs" / "trelloctl.log").read_text(encoding="utf-8")
        assert log.strip()
        assert_english(log, "log file")
    # The README is written in Spanish by decision of the maintainer: it is the one document
    # outside docs/ that the language check leaves out (it still has to exist and be Spanish).
    readme = README.read_text(encoding="utf-8")
    assert re.search(r"[áéíóúñ]", readme)


# --- TC-M16-013 --------------------------------------------------------------------------

SKILL_EXAMPLES = extract_examples(SKILL)
README_EXAMPLES = extract_examples(README)


@pytest.mark.tc("TC-M16-013")
def test_the_documents_carry_examples_and_the_skill_shows_an_error_too() -> None:
    assert len(SKILL_EXAMPLES) >= 10
    assert len(README_EXAMPLES) >= 15
    assert any(not example.ok for example in SKILL_EXAMPLES)
    assert any(example.code == "MOVE_OUT_OF_RANGE" for example in SKILL_EXAMPLES)


@pytest.mark.tc("TC-M16-013")
@pytest.mark.parametrize("example", SKILL_EXAMPLES + README_EXAMPLES, ids=lambda e: e.id)
def test_every_documented_example_ends_as_the_document_shows(
    example: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    with environment(tmp_path, monkeypatch, capsys, "example") as env:
        if example.argv[:2] == ("config", "init"):
            (env.home / "config.toml").unlink()  # `config init` creates the file
        code, envelope, text = run_cli(capsys, *example.argv)
    if envelope is None:  # help and version print text
        assert example.ok, example.id
        assert code == 0, (example.id, text)
        return
    assert envelope["ok"] is example.ok, (example.id, envelope)
    error = envelope.get("error")
    assert (error["code"] if error else None) == example.code, (example.id, envelope)
    assert (code == 0) is example.ok, (example.id, code)


# --- TC-M16-014 --------------------------------------------------------------------------

# AC-1.2 and A-4: the commands that never need config.toml (`mcp serve` follows A-25 and is
# not an operation of this table: it starts without a configuration and answers per call).
NO_CONFIG_NEEDED = {
    ("config", "init"),
    ("config", "show"),
    ("doctor",),
    ("version",),
    ("skills", "list"),
    ("skills", "install"),
    ("skills", "export"),
    ("cache", "status"),
    ("cache", "clear"),
    ("card", "attachments", "clear"),
}


@pytest.mark.tc("TC-M16-014")
@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.name)
def test_only_the_documented_commands_run_without_config_toml(
    operation: Operation, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Capsys
) -> None:
    with environment(tmp_path, monkeypatch, capsys, "noconfig") as env:
        (env.home / "config.toml").unlink()
        code, envelope, _ = run_cli(capsys, *example_argv(operation))
    assert envelope is not None, operation.name
    error_code = (envelope.get("error") or {}).get("code")
    if operation.path in NO_CONFIG_NEEDED:
        assert error_code != "CONFIG_NOT_FOUND", (operation.name, envelope)
    else:
        assert (code, error_code) == (3, "CONFIG_NOT_FOUND"), (operation.name, envelope)


@pytest.mark.tc("TC-M16-014")
def test_help_and_version_need_no_config_toml_and_mcp_serve_is_outside_the_table(
    capsys: Capsys,
) -> None:
    for argv in (["--help"], ["--version"], ["card", "move", "--help"]):
        assert cli.main(argv) == 0
        assert capsys.readouterr().out.strip()
    registry = default_registry()
    serve = registry.get(("mcp", "serve"))
    assert serve is not None
    assert serve.own_stdout
    assert {op.path for op in OPERATIONS} >= NO_CONFIG_NEEDED

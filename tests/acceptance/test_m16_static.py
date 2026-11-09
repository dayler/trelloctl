"""M16 acceptance tests: static analysis, licences, credentials and completeness of the CLI."""

from __future__ import annotations

import ast
import importlib.metadata as metadata
import re
import socket
import tomllib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import close_caches  # noqa: F401
from tests.acceptance.m16_support import ROOT, seed_docs, write_docs_config
from tests.fakes.trello import FakeTrello

from trelloctl import cli, doctor
from trelloctl.registry import default_registry

SRC = ROOT / "src" / "trelloctl"


def sources() -> Iterator[tuple[str, ast.Module]]:
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def dotted(node: ast.AST) -> str:
    """`a.b.c` for a chain of attribute accesses on a name, otherwise an empty string."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return ""


# --- TC-M16-001 / TC-M16-002 -------------------------------------------------------------

PLATFORM_PROBES = {"sys.platform", "os.name", "platform.system", "platform.uname"}
# Platform branches are allowed only for file permissions (AC-27.1); HOME resolution and the
# console encoding need none: they use `Path.home()` and `reconfigure`.
ALLOWED_PLATFORM_BRANCHES = {("app/commands/config.py", "init")}


def enclosing_function(tree: ast.Module, target: ast.AST) -> str:
    best = ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and any(
            child is target for child in ast.walk(node)
        ):
            best = node.name
    return best


@pytest.mark.tc("TC-M16-001")
def test_platform_branches_are_limited_to_home_console_and_file_permissions() -> None:
    found: set[tuple[str, str]] = set()
    for name, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and dotted(node) in PLATFORM_PROBES:
                found.add((name, enclosing_function(tree, node)))
            if isinstance(node, ast.Call) and dotted(node.func) in PLATFORM_PROBES:
                found.add((name, enclosing_function(tree, node)))
    assert found, "the static analysis found no platform probe at all"
    assert found <= ALLOWED_PLATFORM_BRANCHES, found - ALLOWED_PLATFORM_BRANCHES
    # The one allowed branch guards the file permissions of config.toml.
    text = (SRC / "app" / "commands" / "config.py").read_text(encoding="utf-8")
    assert re.search(r'if os\.name == "posix":\s+os\.chmod\(', text)


FORBIDDEN_MODULES = {"subprocess", "pty", "popen2", "commands"}
FORBIDDEN_CALLS = {
    "os.system",
    "os.popen",
    "os.spawnl",
    "os.spawnle",
    "os.spawnlp",
    "os.spawnlpe",
    "os.spawnv",
    "os.spawnve",
    "os.spawnvp",
    "os.spawnvpe",
    "os.execl",
    "os.execle",
    "os.execlp",
    "os.execlpe",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.startfile",
    "os.fork",
    "os.forkpty",
    "pty.spawn",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
    "webbrowser.open",
}


@pytest.mark.tc("TC-M16-002")
def test_src_never_invokes_a_shell_or_an_external_process() -> None:
    offenders: list[str] = []
    for name, tree in sources():
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{name}: import {a.name}" for a in node.names if a.name in FORBIDDEN_MODULES
                ]
            elif (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] in FORBIDDEN_MODULES
            ):
                offenders.append(f"{name}: from {node.module} import ...")
            elif isinstance(node, ast.Call):
                if dotted(node.func) in FORBIDDEN_CALLS:
                    offenders.append(f"{name}: {dotted(node.func)}()")
                offenders += [
                    f"{name}: shell=True"
                    for kw in node.keywords
                    if kw.arg == "shell"
                    and not (isinstance(kw.value, ast.Constant) and kw.value.value is False)
                ]
    assert offenders == []


# --- TC-M16-008 --------------------------------------------------------------------------

URL_LITERAL = re.compile(r"https?://\S+")
EXPECTED_URLS = {
    "https://api.trello.com/1",  # default base_url (settings and client)
    "https://developer.atlassian.com/cloud/trello/swagger.v3.json",  # OpenAPI specification
}


@pytest.mark.tc("TC-M16-008")
def test_the_only_url_literals_of_src_are_the_base_url_and_the_specification() -> None:
    found: set[str] = set()
    for _name, tree in sources():
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and URL_LITERAL.fullmatch(node.value)
            ):
                found.add(node.value)
    assert found == EXPECTED_URLS


@pytest.mark.tc("TC-M16-008")
def test_runtime_connections_only_reach_the_base_url_and_the_specification(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_docs(fake_trello)
    write_docs_config(trelloctl_home, fake_trello)
    monkeypatch.setattr(
        doctor, "OPENAPI_URL", f"http://127.0.0.1:{fake_trello.port}/swagger.v3.json"
    )
    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)
    connections: list[tuple[Any, ...]] = []
    real_connect = socket.socket.connect

    def record(self: socket.socket, address: Any) -> None:
        connections.append(tuple(address))
        real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", record)
    scenarios = [
        ["board", "show"],
        ["board", "lists"],
        ["card", "list"],
        ["card", "get", "Review PR 42", "--full"],
        ["card", "create", "--list", "To Do", "--name", "Probe"],
        ["inbox", "list"],
        ["doctor", "--api"],
    ]
    for argv in scenarios:
        cli.main(argv)
        capsys.readouterr()
    assert connections, "no connection was recorded"
    assert {(host, port) for host, port, *_ in connections} == {("127.0.0.1", fake_trello.port)}
    paths = {r.path for r in fake_trello.requests}
    assert "/swagger.v3.json" in paths
    assert not any("trello.com" in str(r.headers.get("host", "")) for r in fake_trello.requests)


# --- TC-M16-009 --------------------------------------------------------------------------

ALLOWED_LICENCE = re.compile(
    r"^(MIT(-0)?|MIT License|BSD(-[23]-Clause)?|BSD License|Apache-2\.0|Apache Software License|"
    r"PSF(-2\.0)?|Python Software Foundation License|ISC|ISC License.*|"
    r"MPL-2\.0|Mozilla Public License 2\.0.*)$",
    re.IGNORECASE,
)


def licence_allowed(expression: str) -> bool:
    """`A AND B` needs both sides allowed, `A OR B` needs one of them."""
    cleaned = expression.replace("(", " ").replace(")", " ")
    return all(
        any(ALLOWED_LICENCE.match(option.strip()) for option in clause.split(" OR "))
        for clause in cleaned.split(" AND ")
    )


def licences_of(dist: metadata.Distribution) -> list[str]:
    meta = dist.metadata
    declared = [meta.get("License-Expression") or ""]
    declared += [
        c.split("::")[-1].strip()
        for c in (meta.get_all("Classifier") or [])
        if c.startswith("License")
    ]
    free = (meta.get("License") or "").strip()
    if free and "\n" not in free and len(free) < 40:
        declared.append(free)
    return [d for d in declared if d]


@pytest.mark.tc("TC-M16-009")
def test_project_licence_is_mit() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "MIT"
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    own = metadata.distribution("trelloctl")
    assert own.metadata.get("License-Expression") == "MIT"


@pytest.mark.tc("TC-M16-009")
def test_installed_development_and_extra_dependencies_use_allowed_licences() -> None:
    checked = 0
    unknown: list[str] = []
    rejected: list[str] = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name.lower() == "trelloctl":
            continue
        checked += 1
        declared = licences_of(dist)
        if not declared:
            unknown.append(name)
        # Dual-expression metadata may list several forms; one allowed form is enough.
        elif not any(licence_allowed(d) for d in declared):
            rejected.append(f"{name}: {declared}")
    assert checked >= 5
    assert unknown == []
    assert rejected == []


# --- TC-M16-011 / TC-M16-012 -------------------------------------------------------------

CREDENTIAL_WORDS = {
    "token",
    "key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "credential",
    "credentials",
}


def option_strings(parser: Any) -> Iterator[str]:
    import argparse

    for action in parser._actions:
        yield from action.option_strings
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                yield from option_strings(sub)


@pytest.mark.tc("TC-M16-011")
def test_no_option_of_any_command_accepts_credentials() -> None:
    registry = default_registry()
    for operation in registry.operations():
        for param in operation.all_params:
            words = set(re.split(r"[-_]", param.name))
            assert not words & CREDENTIAL_WORDS, (operation.name, param.name)
            assert "api_key" not in param.name
    options = set(option_strings(cli.build_parser(registry)))
    assert "--board" in options
    for option in options:
        assert not set(option.lstrip("-").split("-")) & CREDENTIAL_WORDS, option
        assert "api-key" not in option


@pytest.mark.tc("TC-M16-011")
@pytest.mark.parametrize("flag", ["--token", "--api-key", "--key", "--password"])
def test_a_credential_option_is_refused_as_usage(
    flag: str, capsys: pytest.CaptureFixture[str]
) -> None:
    import json
    import os

    argvs = (
        ["board", "show", flag, "secret-value"],
        [flag, "secret-value", "board", "show"],
        ["board", "show", f"{flag}=secret-value"],
    )
    for argv in argvs:
        code = cli.main(argv)
        captured = capsys.readouterr()
        assert code == 2, argv
        envelope = json.loads(captured.out)
        assert envelope["error"]["code"] == "USAGE"
        assert "secret-value" not in captured.out + captured.err
    home = Path(os.environ["TRELLOCTL_HOME"])
    for log in home.rglob("*.log*"):
        assert "secret-value" not in log.read_text(encoding="utf-8", errors="replace")


PRD = ROOT / "docs" / "PRD.md"


def prd_commands() -> set[tuple[str, ...]]:
    """Command paths of the `Grupos y comandos` block of PRD 10.5."""
    text = PRD.read_text(encoding="utf-8").replace("‑", "-")
    block = text.split("Grupos y comandos:", 1)[1].split("Opciones de lectura:", 1)[0]
    commands: set[tuple[str, ...]] = set()
    group: tuple[str, ...] = ()
    for line in block.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("|"):
            rest: str | None = line.strip()[1:].strip()  # continuation of the previous group
        else:
            head = re.match(r"^ {2}(\S+(?: \S+)*?)(?: {2,}(.*))?$", line)
            assert head, line
            if "|" in head.group(1):  # `card attachments list | download ...`: one-space layout
                before, after = line.strip().split("|", 1)
                words = before.split()
                group, rest = tuple(words[:-1]), f"{words[-1]} | {after}"
            else:
                group, rest = tuple(head.group(1).split()), head.group(2)
        segments = split_top_level(rest) if rest else []
        if not segments:
            commands.add(group)  # a group that is itself the command (`version`)
        for segment in segments:
            word = segment.split()[0]
            commands.add((*group, word) if re.fullmatch(r"[a-z][a-z-]*", word) else group)
    return commands


def split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in text:
        if char in "[(":
            depth += 1
        elif char in "])":
            depth -= 1
        if char == "|" and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())
    return parts


@pytest.mark.tc("TC-M16-012")
def test_the_registry_matches_the_command_list_of_the_prd_one_to_one() -> None:
    expected = prd_commands()
    actual = {operation.path for operation in default_registry().operations()}
    assert len(expected) >= 60
    assert actual - expected == set()
    assert expected - actual == set()
    assert len(actual) == len(default_registry().operations())

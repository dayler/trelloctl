"""M06 acceptance tests: registry, CLI, help, version and the `config` commands."""

from __future__ import annotations

import json
import os
import platform
import re
import stat
import subprocess
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.trello import FakeTrello

from trelloctl import __version__
from trelloctl.app.commands.config import CONFIG_TEMPLATE
from trelloctl.app.commands.context import CommandContext, Need, check_preconditions
from trelloctl.cli import GENERAL_HELP, main
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.settings import DEFAULTS, load_settings
from trelloctl.registry import (
    Operation,
    Param,
    ParamType,
    Registry,
    default_registry,
)

BOARD_ID = "5f1b2c3d4e5f6a7b8c9d0e1f"
OTHER_BOARD_ID = "6a1b2c3d4e5f6a7b8c9d0e2a"
POWER_UPS_URL = "https://trello.com/power-ups/admin"
AUTHORIZE_URL = (
    "https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token"
    "&key=<api_key>"
)


# --- helpers ---------------------------------------------------------------------------


def run(
    capsys: pytest.CaptureFixture[str],
    *argv: str,
    registry: Registry | None = None,
) -> tuple[int, str, str]:
    code = main(list(argv), registry=registry)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_json(
    capsys: pytest.CaptureFixture[str],
    *argv: str,
    registry: Registry | None = None,
) -> tuple[int, dict[str, Any]]:
    code, out, _ = run(capsys, *argv, registry=registry)
    return code, json.loads(out)


def write_config(home: Path, text: str) -> Path:
    path = home / "config.toml"
    path.write_bytes(text.encode("utf-8"))
    return path


def auth_block(fake: FakeTrello) -> str:
    return f'[auth]\napi_key = "{fake.api_key}"\ntoken = "{fake.token}"\n'


def configure(home: Path, fake: FakeTrello, *, board: str | None = None, extra: str = "") -> Path:
    text = auth_block(fake) + f'\n[http]\nbase_url = "{fake.base_url}"\n'
    if board is not None:
        text += f'\n[board]\ndefault = "{board}"\n'
    return write_config(home, text + extra)


def seed_board(fake: FakeTrello, board_id: str = BOARD_ID, name: str = "Work") -> None:
    fake.state.boards[board_id] = {
        "id": board_id,
        "name": name,
        "url": f"https://trello.com/b/abcd1234/{name.lower()}",
        "shortLink": "abcd1234",
        "closed": False,
    }


def make_op(
    name: str,
    handler: Any = None,
    params: tuple[Param, ...] = (),
    **kwargs: Any,
) -> Operation:
    def default_handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        return ctx.success({"args": dict(args)})

    return Operation(
        ("probe", name),
        "probe operation",
        f"trelloctl probe {name}",
        handler or default_handler,
        params=params,
        **kwargs,
    )


def with_probe(*operations: Operation) -> Registry:
    return default_registry().extended(*operations)


def read_log(home: Path) -> str:
    return (home / "logs" / "trelloctl.log").read_text(encoding="utf-8")


def is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


# --- help, version and text output ------------------------------------------------------


@pytest.mark.tc("TC-M06-001")
@pytest.mark.parametrize("argv", [["--help"], ["-h"], ["--version"], []])
def test_text_outputs_are_not_json(argv: list[str]) -> None:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "trelloctl", *argv],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ),
        timeout=60,
        check=False,
    )
    assert result.stdout.strip()
    assert not is_json(result.stdout)
    assert result.stdout.startswith(("trelloctl", "usage"))
    assert result.returncode == (2 if not argv else 0)


def _help_paths(registry: Registry) -> set[tuple[str, ...]]:
    paths: set[tuple[str, ...]] = set()
    for operation in registry.operations():
        for size in range(1, len(operation.path) + 1):
            paths.add(operation.path[:size])
    return paths


@pytest.mark.tc("TC-M06-002")
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_at_every_level(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    registry = default_registry()
    code, out, _ = run(capsys, flag)
    assert code == 0
    assert out.startswith("trelloctl")
    commands = {operation.path: operation for operation in registry.operations()}
    for path in sorted(_help_paths(registry)):
        code, out, _ = run(capsys, *path, flag)
        assert code == 0, path
        assert not is_json(out)
        operation = commands.get(path)
        if operation is None:
            for child in registry.operations():
                if child.path[: len(path)] == path:
                    assert child.path[len(path)] in out
            continue
        assert "Example:" in out
        assert operation.example in out
        for param in operation.all_params:
            assert (param.name.upper() if param.positional else param.flag) in out
            assert param.description in out


@pytest.mark.tc("TC-M06-003")
def test_no_arguments_prints_general_help_and_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys)
    assert code == 2
    assert out == GENERAL_HELP
    assert out.startswith("trelloctl")


@pytest.mark.tc("TC-M06-004")
def test_general_help_documents_ttl_board_rule_and_confirmations(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == 0
    assert "metadata 30 min, cards 5 min" in out
    assert "--board" in out
    assert "TRELLO_BOARD" in out
    assert "[board].default" in out
    assert "require --yes" in out
    for command in (
        "board label remove",
        "card comment remove",
        "card checklist remove",
        "card checklist item remove",
    ):
        assert command in out


@pytest.mark.tc("TC-M06-005")
def test_version_text_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(capsys, "--version")
    assert (code, out) == (0, f"trelloctl {__version__}\n")
    code, envelope = run_json(capsys, "version")
    assert code == 0
    assert envelope["ok"] is True
    assert envelope["data"] == {
        "version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    assert envelope["meta"]["command"] == "version"


PROBE_ARGS: dict[str, list[str]] = {
    "config board set": [BOARD_ID],
    "config init": ["--force"],
}


@pytest.mark.tc("TC-M06-006")
def test_every_json_command_prints_exactly_one_object(capsys: pytest.CaptureFixture[str]) -> None:
    registry = default_registry()
    checked = 0
    for operation in registry.operations():
        if operation.own_stdout or operation.text_output is not None:
            continue
        for extra in (PROBE_ARGS.get(operation.name, []), []):
            code, out, _ = run(capsys, *operation.path, *extra)
            decoded = json.loads(out)
            assert isinstance(decoded, dict), operation.name
            assert out.endswith("\n")
            assert decoded["ok"] is (code == 0)
        checked += 1
    assert checked >= 7


# --- usage errors and internal errors ---------------------------------------------------


@pytest.mark.tc("TC-M06-007")
def test_usage_errors_are_json_with_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    int_op = make_op("number", params=(Param("count", "a count", ParamType.INT, required=True),))
    registry = with_probe(int_op)
    cases = [
        ["version", "--nope"],
        ["config", "board", "set"],
        ["probe", "number", "--count", "abc"],
        ["probe", "number"],
        ["config"],
        ["nonexistent"],
    ]
    for argv in cases:
        code, out, err = run(capsys, *argv, registry=registry)
        assert code == 2, argv
        envelope = json.loads(out)
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == "USAGE"
        assert envelope["error"]["message"]
        assert "usage:" not in out
        assert "usage:" not in err


@pytest.mark.tc("TC-M06-008")
def test_unhandled_exception_is_internal_and_traceback_goes_to_the_log(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def boom(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        raise RuntimeError("kaboom detail")

    code, out, err = run(capsys, "probe", "boom", registry=with_probe(make_op("boom", boom)))
    assert code == 1
    envelope = json.loads(out)
    assert envelope["error"]["code"] == "INTERNAL"
    assert envelope["ok"] is False
    assert "Traceback" not in out
    assert "kaboom" not in out
    assert "Traceback" not in err
    log = read_log(trelloctl_home)
    assert "Traceback" in log
    assert "RuntimeError: kaboom detail" in log
    assert "result=INTERNAL" in log


@pytest.mark.tc("TC-M06-009")
def test_compact_output_is_a_single_line(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (["version", "--compact"], ["--compact", "version"]):
        code, out, _ = run(capsys, *argv)
        assert code == 0
        assert out.count("\n") == 1
        assert out.endswith("}\n")
        assert json.loads(out)["data"]["version"] == __version__
    code, out, _ = run(capsys, "version")
    assert out.count("\n") > 5
    assert '\n  "ok": true' in out


# --- config init ------------------------------------------------------------------------


def _view(settings: Any) -> dict[str, Any]:
    view = settings.effective_view()
    view.pop("home")
    view.pop("config_path")
    return view


@pytest.mark.tc("TC-M06-010")
def test_config_init_writes_the_minimal_configuration_with_a_commented_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "fresh" / "home"
    monkeypatch.setenv("TRELLOCTL_HOME", str(home))
    assert not home.exists()
    code, envelope = run_json(capsys, "config", "init")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["path"] == str(home / "config.toml")
    assert (home / "logs").is_dir()
    text = (home / "config.toml").read_text(encoding="utf-8")
    assert tomllib.loads(text) == {"auth": {"api_key": "", "token": ""}}
    assert POWER_UPS_URL in text
    assert AUTHORIZE_URL in text
    assert "# schema_version = 1" in text
    for section, keys in DEFAULTS.items():
        if section == "auth":
            continue
        assert f"# [{section}]" in text
        for key, default in keys.items():
            assert f"# {key} = {json.dumps(default)}" in text, (section, key)
    assert "email-to-board" in text
    assert "Slack" in text
    assert "working board" in text
    minimal = tmp_path / "minimal"
    minimal.mkdir()
    write_config(minimal, '[auth]\napi_key = ""\ntoken = ""\n')
    assert _view(load_settings({}, home=home)) == _view(load_settings({}, home=minimal))
    assert _view(load_settings({}, home=home))["http"] == DEFAULTS["http"]
    assert text == CONFIG_TEMPLATE


@pytest.mark.tc("TC-M06-011")
def test_config_init_does_not_overwrite_an_existing_file(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(trelloctl_home, '[auth]\napi_key = "mine"\ntoken = "mine"\n')
    before = path.read_bytes()
    code, envelope = run_json(capsys, "config", "init")
    assert code == 2
    assert envelope["error"]["code"] == "USAGE"
    assert path.read_bytes() == before


@pytest.mark.tc("TC-M06-012")
def test_config_init_force_rewrites_the_minimal_configuration(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_config(trelloctl_home, '[auth]\napi_key = "mine"\ntoken = "mine"\n')
    code, envelope = run_json(capsys, "config", "init", "--force")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["overwritten"] is True
    assert path.read_text(encoding="utf-8") == CONFIG_TEMPLATE
    assert tomllib.loads(CONFIG_TEMPLATE) == {"auth": {"api_key": "", "token": ""}}


@pytest.mark.tc("TC-M06-013")
@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions only")
def test_config_init_creates_the_file_with_0600(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _ = run_json(capsys, "config", "init")
    assert code == 0
    assert stat.S_IMODE((trelloctl_home / "config.toml").stat().st_mode) == 0o600


# --- preconditions ----------------------------------------------------------------------


@pytest.mark.tc("TC-M06-014")
def test_commands_that_do_not_need_the_config_file(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "--help")[0] == 0
    assert run(capsys, "--version")[0] == 0
    for argv in (["version"], ["config", "show"], ["config", "init"]):
        code, envelope = run_json(capsys, *argv)
        assert envelope["ok"] is True, argv
        assert code == 0
        assert envelope.get("error", {}).get("code") != "CONFIG_NOT_FOUND"


@pytest.mark.tc("TC-M06-015")
def test_commands_that_need_the_config_file(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = [
        ["config", "validate"],
        ["config", "board", "show"],
        ["config", "board", "set", BOARD_ID],
        ["config", "board", "unset"],
    ]
    for argv in cases:
        code, envelope = run_json(capsys, *argv)
        assert code == 3, argv
        assert envelope["error"]["code"] == "CONFIG_NOT_FOUND"
        assert str(trelloctl_home / "config.toml") in envelope["error"]["message"]
    assert not (trelloctl_home / "config.toml").exists()


@pytest.mark.tc("TC-M06-029")
def test_single_precondition_check_reports_in_the_order_of_a7(
    trelloctl_home: Path,
) -> None:
    def check(need: Need = Need.BOARD) -> str | None:
        settings = load_settings({}, home=trelloctl_home, require_file=False)
        return check_preconditions(need, settings)

    with pytest.raises(TrelloctlError) as info:
        check()
    assert info.value.code == "CONFIG_NOT_FOUND"
    write_config(trelloctl_home, "[http]\ntimeout_seconds = 10\n")
    with pytest.raises(TrelloctlError) as info:
        check()
    assert info.value.code == "AUTH_MISSING"
    assert check(Need.CONFIG) is None
    write_config(trelloctl_home, '[auth]\napi_key = "k"\ntoken = "t"\n')
    with pytest.raises(TrelloctlError) as info:
        check()
    assert info.value.code == "BOARD_NOT_CONFIGURED"
    assert check(Need.CREDENTIALS) is None
    write_config(
        trelloctl_home, f'[auth]\napi_key = "k"\ntoken = "t"\n[board]\ndefault = "{BOARD_ID}"\n'
    )
    assert check() == BOARD_ID


# --- config show / validate / board -----------------------------------------------------


@pytest.mark.tc("TC-M06-016")
def test_config_show_masks_the_token_and_honours_the_environment(
    trelloctl_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    file_token = "filetoken-abcdef1234"
    write_config(
        trelloctl_home, f'[auth]\napi_key = "filekey-0123456789"\ntoken = "{file_token}"\n'
    )
    code, out, _ = run(capsys, "config", "show")
    envelope = json.loads(out)
    assert code == 0
    assert envelope["data"]["auth"]["token"] == "***1234"
    assert file_token not in out
    assert envelope["data"]["http"]["base_url"] == "https://api.trello.com/1"
    env_token = "envtoken-9999999999"
    monkeypatch.setenv("TRELLO_TOKEN", env_token)
    monkeypatch.setenv("TRELLO_API_KEY", "envkey-8888888888")
    code, out, _ = run(capsys, "config", "show")
    envelope = json.loads(out)
    assert envelope["data"]["auth"] == {"api_key": "***8888", "token": "***9999"}
    assert env_token not in out
    assert "envkey-8888888888" not in out


@pytest.mark.tc("TC-M06-017")
def test_config_validate_ok_with_an_accessible_default_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello)
    configure(trelloctl_home, fake_trello, board=BOARD_ID)
    code, envelope = run_json(capsys, "config", "validate")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["valid"] is True
    assert envelope["data"]["default_board"] == {"id": BOARD_ID, "name": "Work"}
    assert [r.path for r in fake_trello.requests] == [f"/boards/{BOARD_ID}"]


@pytest.mark.tc("TC-M06-018")
def test_config_validate_reports_every_problem_with_its_key(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(
        trelloctl_home,
        '[http]\ntimeout_seconds = "soon"\n\n[logging]\nmax_bytes = "big"\n',
    )
    code, envelope = run_json(capsys, "config", "validate")
    assert code == 3
    assert envelope["error"]["code"] == "CONFIG_INVALID"
    problems = envelope["error"]["details"]["problems"]
    assert len(problems) == 3
    assert {p["key"] for p in problems} == {"http.timeout_seconds", "logging.max_bytes", "auth"}
    assert all(p["reason"] for p in problems)


@pytest.mark.tc("TC-M06-019")
def test_config_validate_without_default_board_makes_no_api_call(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "config", "validate")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["default_board"] is None
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M06-020")
def test_config_validate_reports_an_inaccessible_default_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    configure(trelloctl_home, fake_trello, board=OTHER_BOARD_ID)
    code, envelope = run_json(capsys, "config", "validate")
    assert code == 3
    assert envelope["error"]["code"] == "CONFIG_INVALID"
    problems = envelope["error"]["details"]["problems"]
    assert [p["key"] for p in problems] == ["board.default"]


@pytest.mark.tc("TC-M06-021")
def test_config_board_show_with_and_without_default(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "config", "board", "show")
    assert (code, envelope["data"]["configured"]) == (0, False)
    assert envelope["data"]["id"] is None
    assert fake_trello.requests == []
    configure(trelloctl_home, fake_trello, board=BOARD_ID)
    code, envelope = run_json(capsys, "config", "board", "show")
    assert code == 0
    assert envelope["data"] == {
        "configured": True,
        "id": BOARD_ID,
        "name": "Work",
        "url": "https://trello.com/b/abcd1234/work",
    }
    assert envelope["meta"]["board_id"] == BOARD_ID


@pytest.mark.tc("TC-M06-022")
def test_config_board_set_and_unset(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    path = configure(trelloctl_home, fake_trello, extra="\n# keep this comment\n")
    original = path.read_text(encoding="utf-8")
    code, envelope = run_json(capsys, "config", "board", "set", BOARD_ID)
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["default"] == BOARD_ID
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed["board"]["default"] == BOARD_ID
    assert parsed["auth"] == tomllib.loads(original)["auth"]
    assert parsed["http"] == tomllib.loads(original)["http"]
    assert "# keep this comment" in path.read_text(encoding="utf-8")
    assert (trelloctl_home / "config.toml.bak").read_text(encoding="utf-8") == original
    code, envelope = run_json(capsys, "config", "board", "unset")
    assert (code, envelope["ok"]) == (0, True)
    assert tomllib.loads(path.read_text(encoding="utf-8")).get("board", {}) == {}
    assert load_settings({}, home=trelloctl_home).board_default == ""
    assert "# keep this comment" in path.read_text(encoding="utf-8")
    code, envelope = run_json(capsys, "config", "board", "set", "not-an-id")
    assert (code, envelope["error"]["code"]) == (2, "USAGE")


@pytest.mark.tc("TC-M06-032")
def test_board_set_appends_an_active_section_and_keeps_the_commented_template(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_json(capsys, "config", "init")[0] == 0
    path = trelloctl_home / "config.toml"
    filled = (
        path.read_text(encoding="utf-8")
        .replace('api_key = ""', 'api_key = "my-key"', 1)
        .replace('token = ""', 'token = "my-token"', 1)
    )
    path.write_bytes(filled.encode("utf-8"))
    code, envelope = run_json(capsys, "config", "board", "set", BOARD_ID)
    assert (code, envelope["ok"]) == (0, True)
    text = path.read_text(encoding="utf-8")
    assert text.startswith(filled)
    assert text[len(filled) :].strip().splitlines() == ["[board]", f'default = "{BOARD_ID}"']
    parsed = tomllib.loads(text)
    assert parsed == {
        "auth": {"api_key": "my-key", "token": "my-token"},
        "board": {"default": BOARD_ID},
    }
    assert "# [board]" in text
    assert "# default = " in text
    assert (trelloctl_home / "config.toml.bak").read_text(encoding="utf-8") == filled


# --- logging ----------------------------------------------------------------------------


@pytest.mark.tc("TC-M06-023")
def test_each_invocation_appends_a_log_line(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(capsys, "version")[0] == 0
    assert run(capsys, "config", "validate")[0] == 3
    lines = read_log(trelloctl_home).splitlines()
    pattern = re.compile(
        r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z (?P<level>[A-Z]+) pid=(?P<pid>\d+) "
        r'command="(?P<command>[^"]+)" duration_ms=(?P<ms>\d+) result=(?P<result>\w+)$'
    )
    entries = [m.groupdict() for m in map(pattern.match, lines) if m]
    assert [(e["command"], e["result"], e["level"]) for e in entries] == [
        ("version", "ok", "INFO"),
        ("config validate", "CONFIG_NOT_FOUND", "ERROR"),
    ]
    assert all(int(e["pid"]) == os.getpid() for e in entries)


@pytest.mark.tc("TC-M06-024")
def test_verbose_and_log_level_control_the_log_and_stderr(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello)
    configure(trelloctl_home, fake_trello, board=BOARD_ID)
    code, out, err = run(capsys, "config", "board", "show")
    assert code == 0
    assert err == ""
    assert "http GET" not in read_log(trelloctl_home)
    code, out, err = run(capsys, "-v", "--log-level", "debug", "config", "board", "show")
    assert code == 0
    assert json.loads(out)["ok"] is True
    assert f"http GET /boards/{BOARD_ID} status=200" in err
    assert 'command="config board show"' in err
    assert fake_trello.token not in err
    log = read_log(trelloctl_home)
    assert f"http GET /boards/{BOARD_ID} status=200" in log
    assert fake_trello.token not in log
    code, out, err = run(capsys, "-v", "config", "board", "show")
    assert 'command="config board show"' in err
    assert "http GET" not in err
    assert is_json(out)


@pytest.mark.tc("TC-M06-025")
def test_real_process_keeps_stdout_pure_json(trelloctl_home: Path, fake_trello: FakeTrello) -> None:
    seed_board(fake_trello)
    configure(trelloctl_home, fake_trello, board=BOARD_ID)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "trelloctl",
            "--log-level",
            "debug",
            "-v",
            "config",
            "board",
            "show",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ),
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    envelope = json.loads(result.stdout)
    assert envelope["data"]["name"] == "Work"
    assert "http GET" in result.stderr
    assert "http GET" not in result.stdout
    assert "duration_ms" in result.stderr


# --- credentials, home and environment --------------------------------------------------


@pytest.mark.tc("TC-M06-026")
def test_credentials_cannot_be_given_on_the_command_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    for option in ("--token", "--api-key", "--key", "--password", "--api_key"):
        for argv in (["version", option, "x"], [option, "x", "version"]):
            code, envelope = run_json(capsys, *argv)
            assert code == 2, argv
            assert envelope["error"]["code"] == "USAGE"
    for operation in default_registry().operations():
        for param in operation.all_params:
            assert not {"token", "api_key", "key", "password"} & set(param.name.split("-"))
            assert param.name not in {"token", "api_key", "key", "password"}


@pytest.mark.tc("TC-M06-027")
def test_home_pointing_to_a_file_is_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "a-file"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("TRELLOCTL_HOME", str(target))
    for argv in (["version"], ["config", "show"], ["config", "init"]):
        code, envelope = run_json(capsys, *argv)
        assert code == 3, argv
        assert envelope["error"]["code"] == "HOME_NOT_WRITABLE"
    assert target.read_text(encoding="utf-8") == "x"


@pytest.mark.tc("TC-M06-028")
def test_environment_credentials_override_the_file(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_board(fake_trello)
    write_config(
        trelloctl_home,
        '[auth]\napi_key = "file-key"\ntoken = "file-token"\n'
        f'[http]\nbase_url = "{fake_trello.base_url}"\n[board]\ndefault = "{BOARD_ID}"\n',
    )
    code, envelope = run_json(capsys, "config", "board", "show")
    assert (code, envelope["error"]["code"]) == (4, "AUTH_INVALID")
    monkeypatch.setenv("TRELLO_API_KEY", fake_trello.api_key)
    monkeypatch.setenv("TRELLO_TOKEN", fake_trello.token)
    fake_trello.requests.clear()
    code, envelope = run_json(capsys, "config", "board", "show")
    assert (code, envelope["ok"]) == (0, True)
    request = fake_trello.requests[0]
    assert request.param("key") == fake_trello.api_key
    assert request.param("token") == fake_trello.token


# --- registry ---------------------------------------------------------------------------


@pytest.mark.tc("TC-M06-030")
def test_mutually_exclusive_options_are_a_usage_error_without_api_calls(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[Mapping[str, Any]] = []

    def handler(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
        calls.append(args)
        ctx.client().get("/members/me")
        return ctx.success({})

    operation = make_op(
        "either",
        handler,
        params=(
            Param("file", "read from a file", exclusive="source", required=True),
            Param("url", "read from a url", exclusive="source"),
        ),
    )
    registry = with_probe(operation)
    configure(trelloctl_home, fake_trello)
    code, envelope = run_json(
        capsys, "probe", "either", "--file", "a", "--url", "b", registry=registry
    )
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    assert calls == []
    assert fake_trello.requests == []
    code, envelope = run_json(capsys, "probe", "either", registry=registry)
    assert (code, envelope["error"]["code"]) == (2, "USAGE")
    code, envelope = run_json(capsys, "probe", "either", "--url", "b", registry=registry)
    assert (code, envelope["ok"]) == (0, True)
    assert calls == [{"file": None, "url": "b"}]
    assert len(fake_trello.requests) == 1


@pytest.mark.tc("TC-M06-031")
def test_unknown_config_keys_warn_and_there_is_no_format_setting(
    trelloctl_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_config(trelloctl_home, '[auth]\napi_key = "k"\ntoken = "t"\n\n[output]\nindent = 0\n')
    monkeypatch.setenv("TRELLOCTL_FORMAT", "compact")
    code, out, _ = run(capsys, "config", "show")
    assert code == 0
    assert out.count("\n") > 5
    assert '\n  "ok": true' in out
    envelope = json.loads(out)
    assert envelope["meta"]["warnings"] == ["Unknown configuration key ignored: output"]
    assert "output" not in envelope["data"]


@pytest.mark.tc("TC-M06-033")
def test_include_archived_boards_is_a_documented_global_option(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, _ = run(capsys, "--help")
    assert code == 0
    assert "--include-archived-boards" in out
    assert "archived" in out
    for argv in (
        ["--include-archived-boards", "version"],
        ["version", "--include-archived-boards"],
    ):
        code, envelope = run_json(capsys, *argv)
        assert (code, envelope["ok"]) == (0, True)

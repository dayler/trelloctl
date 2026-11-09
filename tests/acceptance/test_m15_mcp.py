"""M15 acceptance tests: `mcp serve` against a real server process and the fake Trello (RF-25)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.acceptance.m08_support import (
    BOARD,
    OTHER,
    add_card,
    add_label,
    add_list,
    configure,
    seed_board,
    seed_standard,
    write_config,
    writes,
)
from tests.fakes.mcp_client import McpClient
from tests.fakes.trello import FakeTrello

from trelloctl.cli import GENERAL_HELP
from trelloctl.registry import default_registry

ROOT = Path(__file__).resolve().parents[2]
EXCLUDED = (
    "config_init",
    "config_show",
    "config_validate",
    "config_board_show",
    "config_board_set",
    "config_board_unset",
    "doctor",
    "version",
    "skills_list",
    "skills_install",
    "skills_export",
    "cache_clear",
    "card_attachments_clear",
)


@pytest.fixture
def clients() -> Iterator[list[McpClient]]:
    """Servers started by a test; every process is closed at the end."""
    started: list[McpClient] = []
    yield started
    for client in started:
        client.close()


def start(clients: list[McpClient], *args: str, initialize: bool = True) -> McpClient:
    client = McpClient(args=(*args, "mcp", "serve"))
    clients.append(client)
    if initialize:
        reply = client.initialize()
        assert "result" in reply
    return client


def only_protocol(client: McpClient) -> None:
    assert client.stdout_lines
    for line in client.stdout_lines:
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0"
        assert "result" in message or "error" in message or "method" in message


def card_list_requests(fake: FakeTrello) -> list[str]:
    return [
        f"{r.path}"
        for r in fake.requests
        if r.method == "GET" and r.path.endswith(("/cards/open", "/cards/all"))
    ]


# --- tools/list (AC-25.1) ---


@pytest.mark.tc("TC-M15-001")
def test_tools_list_has_one_snake_case_tool_per_operation(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    configure(trelloctl_home, fake_trello)
    client = McpClient()
    clients.append(client)
    reply = client.initialize()
    assert reply["result"]["serverInfo"]["name"] == "trelloctl"
    tools = client.list_tools()
    names = [t["name"] for t in tools]
    expected = [op.tool_name for op in default_registry().operations() if op.mcp]
    assert sorted(names) == sorted(expected)
    assert len(set(names)) == len(names)
    for name in names:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*", name)
    for must in (
        "board_show",
        "board_label_remove",
        "card_create",
        "card_get",
        "card_list",
        "card_move",
        "card_checklist_item_check",
        "card_comment_add",
        "card_attachments_download",
        "inbox_list",
        "inbox_move",
        "cache_reload",
        "cache_status",
    ):
        assert must in names
    for tool in tools:
        props = tool["inputSchema"]["properties"]
        assert props["board"]["type"] == "string"
        assert "board" not in tool["inputSchema"].get("required", [])
        assert props["include_archived_boards"]["type"] == "boolean"


@pytest.mark.tc("TC-M15-002")
def test_excluded_operations_are_neither_listed_nor_callable(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    names = {t["name"] for t in client.list_tools()}
    assert not names & set(EXCLUDED)
    assert not [n for n in names if n.startswith(("config_", "skills_", "mcp_"))]
    assert not {"doctor", "version", "cache_clear", "card_attachments_clear"} & names
    for name in EXCLUDED:
        reply = client.call_raw(name, {})
        assert "error" in reply
        assert "result" not in reply
        assert reply["error"]["code"] == -32602
    is_error, envelope = client.call("cache_status", {})
    assert not is_error
    assert envelope["ok"] is True


@pytest.mark.tc("TC-M15-004")
def test_card_list_with_board_by_name(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    other_list = add_list(fake_trello, "Only", 1, OTHER)
    add_card(fake_trello, other_list, "Elsewhere", id_short=1, board_id=OTHER)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    is_error, envelope = client.call("card_list", {"board": "Other"})
    assert not is_error
    assert envelope["meta"]["board_id"] == OTHER
    assert [c["name"] for c in envelope["data"]["cards"]] == ["Elsewhere"]
    is_error, envelope = client.call("card_list", {})
    assert envelope["meta"]["board_id"] == BOARD


# --- results (AC-25.2) ---


@pytest.mark.tc("TC-M15-005")
def test_card_get_returns_the_compact_envelope_as_text(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    reply = client.call_raw("card_get", {"card": ids["c1"]})
    result = reply["result"]
    assert result["isError"] is False
    (block,) = result["content"]
    assert block["type"] == "text"
    envelope = json.loads(block["text"])
    assert envelope["ok"] is True
    assert envelope["data"]["name"] == "First card"
    assert envelope["meta"]["command"] == "card get"
    assert block["text"] == json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    assert "\n" not in block["text"]


@pytest.mark.tc("TC-M15-006")
def test_card_get_of_a_missing_card_is_an_error_result(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    reply = client.call_raw("card_get", {"card": "6b00000000000000000000ff"})
    assert reply["result"]["isError"] is True
    envelope = json.loads(reply["result"]["content"][0]["text"])
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "CARD_NOT_FOUND"
    assert envelope["meta"]["command"] == "card get"


@pytest.mark.tc("TC-M15-007")
def test_label_remove_needs_yes_true(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    is_error, envelope = client.call("board_label_remove", {"label": "bug"})
    assert is_error
    assert envelope["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert not writes(fake_trello)
    assert ids["bug"] in fake_trello.state.labels
    is_error, envelope = client.call("board_label_remove", {"label": "bug", "yes": True})
    assert not is_error
    assert envelope["ok"] is True
    assert [r.method for r in writes(fake_trello)] == ["DELETE"]
    assert ids["bug"] not in fake_trello.state.labels


# --- stdout, logs, lifecycle (AC-25.3, §10.10) ---


@pytest.mark.tc("TC-M15-008")
def test_stdout_is_protocol_only_with_debug_logging(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients, "--log-level", "debug", "-v")
    assert client.call("board_show", {})[1]["ok"]
    is_error, envelope = client.call("card_get", {"card": "6b00000000000000000000ff"})
    assert is_error
    assert envelope["error"]["code"] == "CARD_NOT_FOUND"
    assert client.call("card_get", {"card": ids["c1"]})[1]["ok"]
    assert client.close() == 0
    only_protocol(client)
    assert 'command="card get"' in client.stderr
    assert "DEBUG" in client.stderr
    log = (trelloctl_home / "logs" / "trelloctl.log").read_text(encoding="utf-8")
    assert 'command="card get"' in log
    assert "result=CARD_NOT_FOUND" in log
    assert "DEBUG" in log


@pytest.mark.tc("TC-M15-009")
def test_cache_ttl_is_evaluated_on_every_call(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    write_config(
        trelloctl_home,
        fake_trello,
        board=BOARD,
        extra="\n[cache]\nmetadata_ttl_seconds = 1\ncards_ttl_seconds = 1\n",
    )
    client = start(clients)
    assert client.call("card_list", {})[1]["ok"]
    first = len(card_list_requests(fake_trello))
    assert first >= 1
    assert client.call("card_list", {})[1]["ok"]
    assert len(card_list_requests(fake_trello)) == first
    time.sleep(1.3)
    assert client.call("card_list", {})[1]["ok"]
    assert len(card_list_requests(fake_trello)) > first


@pytest.mark.tc("TC-M15-010")
def test_configuration_is_read_once_at_startup(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    add_list(fake_trello, "Only", 1, OTHER)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    assert client.call("card_list", {})[1]["meta"]["board_id"] == BOARD
    write_config(trelloctl_home, fake_trello, board=OTHER)
    assert client.call("card_list", {})[1]["meta"]["board_id"] == BOARD
    assert client.close() == 0
    restarted = start(clients)
    assert restarted.call("card_list", {})[1]["meta"]["board_id"] == OTHER


@pytest.mark.tc("TC-M15-011")
def test_two_servers_share_cache_and_log(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    first = start(clients)
    second = start(clients)
    for index in range(3):
        for client in (first, second):
            is_error, envelope = client.call(
                "card_create", {"list": "Todo", "name": f"card {id(client)} {index}"}
            )
            assert not is_error, envelope
            assert client.call("cache_reload", {})[1]["ok"]
    assert ids["todo"] in {c["idList"] for c in fake_trello.state.cards.values()}
    assert first.close() == 0
    assert second.close() == 0
    assert (trelloctl_home / "cache.db").is_file()
    log = (trelloctl_home / "logs" / "trelloctl.log").read_text(encoding="utf-8")
    pids = set(re.findall(r"pid=(\d+)", log))
    # On Windows the launcher PID differs from the interpreter PID: compare counts.
    assert len(pids) == 2
    assert "ERROR" not in log


@pytest.mark.tc("TC-M15-012")
def test_no_cache_never_creates_cache_db(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    ids = seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients, "--no-cache")
    for tool, arguments in (
        ("board_show", {}),
        ("board_lists", {}),
        ("card_list", {}),
        ("card_get", {"card": ids["c1"]}),
        ("card_list", {}),
    ):
        is_error, envelope = client.call(tool, arguments)
        assert not is_error, envelope
        assert not (trelloctl_home / "cache.db").exists()
    assert client.close() == 0
    assert not (trelloctl_home / "cache.db").exists()


# --- optional dependency (AC-25.4, A-1) ---


def run_python(code: str, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=dict(os.environ),
        cwd=home,
        timeout=60,
        check=False,
    )


@pytest.mark.tc("TC-M15-013")
def test_mcp_serve_without_the_sdk_is_a_usage_error(trelloctl_home: Path) -> None:
    code = (
        "import sys\n"
        "sys.modules['mcp'] = None\n"
        "sys.modules['mcp_types'] = None\n"
        "from trelloctl.cli import main\n"
        "sys.exit(main(['mcp', 'serve']))\n"
    )
    done = run_python(code, trelloctl_home)
    assert done.returncode == 2
    envelope = json.loads(done.stdout)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "USAGE"
    assert "trelloctl[mcp]" in envelope["error"]["message"]
    assert "install" in envelope["error"]["message"].lower()
    assert envelope["meta"]["command"] == "mcp serve"


@pytest.mark.tc("TC-M15-014")
def test_the_sdk_is_not_imported_by_other_commands(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    code = (
        "import sys\n"
        "from trelloctl.cli import main\n"
        "code = main(['card', 'list'])\n"
        "loaded = sorted(m for m in sys.modules if m == 'mcp' or m.startswith(('mcp.', 'mcp_')))\n"
        "sys.stderr.write('LOADED=' + repr(loaded) + '\\n')\n"
        "sys.exit(code)\n"
    )
    done = run_python(code, trelloctl_home)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["ok"] is True
    assert "LOADED=[]" in done.stderr


@pytest.mark.tc("TC-M15-015")
def test_documentation_describes_mcp_as_experimental() -> None:
    assert "MCP server over stdio (experimental)" in GENERAL_HELP
    serve_help = subprocess.run(
        [sys.executable, "-m", "trelloctl", "mcp", "serve", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    ).stdout
    skill = (ROOT / "src" / "trelloctl" / "skills" / "trelloctl" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for text in (serve_help, skill, readme):
        assert "xperiment" in text
        assert "trelloctl[mcp]" in text


@pytest.mark.tc("TC-M15-016")
def test_closing_stdin_ends_the_server_silently(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    configure(trelloctl_home, fake_trello)
    idle = McpClient()
    clients.append(idle)
    assert idle.close() == 0
    assert idle.stdout_lines == []
    used = start(clients)
    assert used.list_tools()
    count = len(used.stdout_lines)
    assert used.close() == 0
    assert len(used.stdout_lines) == count
    only_protocol(used)


# --- boards, configuration and stdin (A-24, A-25) ---


@pytest.mark.tc("TC-M15-017")
def test_archived_boards_need_include_archived_boards(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Old", "archiv01", closed=True)
    old_list = add_list(fake_trello, "Done", 1, OTHER)
    add_card(fake_trello, old_list, "Ancient", id_short=1, board_id=OTHER)
    add_label(fake_trello, "legacy", "blue", OTHER)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    is_error, envelope = client.call("card_list", {"board": "Old"})
    assert is_error
    assert envelope["error"]["code"] == "BOARD_NOT_FOUND"
    is_error, envelope = client.call("card_list", {"board": "Old", "include_archived_boards": True})
    assert not is_error, envelope
    assert envelope["meta"]["board_id"] == OTHER
    assert [c["name"] for c in envelope["data"]["cards"]] == ["Ancient"]


@pytest.mark.tc("TC-M15-018")
def test_server_starts_without_config_and_reports_it_per_call(
    trelloctl_home: Path, clients: list[McpClient]
) -> None:
    assert not (trelloctl_home / "config.toml").exists()
    client = start(clients)
    assert client.list_tools()
    reply = client.call_raw("card_list", {})
    assert reply["result"]["isError"] is True
    envelope = json.loads(reply["result"]["content"][0]["text"])
    assert envelope["error"]["code"] == "CONFIG_NOT_FOUND"
    assert client.close() == 0
    only_protocol(client)


@pytest.mark.tc("TC-M15-019")
def test_stdin_value_is_refused_and_the_session_continues(
    trelloctl_home: Path, fake_trello: FakeTrello, clients: list[McpClient]
) -> None:
    seed_standard(fake_trello)
    configure(trelloctl_home, fake_trello)
    client = start(clients)
    is_error, envelope = client.call("card_create", {"list": "Todo", "name": "x", "desc": "-"})
    assert is_error
    assert envelope["error"]["code"] == "USAGE"
    assert "stdin not available in MCP" in envelope["error"]["message"]
    assert not writes(fake_trello)
    is_error, envelope = client.call("card_comment_add", {"card": "First card", "text": "-"})
    assert is_error
    assert envelope["error"]["code"] == "USAGE"
    is_error, envelope = client.call("board_show", {})
    assert not is_error
    assert envelope["ok"] is True

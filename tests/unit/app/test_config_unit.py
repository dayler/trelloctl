"""Unit tests of the `config` and `version` command handlers and the fake boards route."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.fakes.transport import StubTransport, response
from tests.fakes.trello import FakeTrello

from trelloctl.app.commands import config as cmd
from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.domain.errors import TrelloctlError

BOARD_ID = "5f1b2c3d4e5f6a7b8c9d0e1f"


def make_ctx(home: Path, env: dict[str, str] | None = None, **kwargs: Any) -> CommandContext:
    environ = env or {}
    settings, error = load_settings_lenient(environ, home)
    return CommandContext(settings=settings, config_error=error, environ=environ, **kwargs)


def write(home: Path, text: str) -> None:
    (home / "config.toml").write_text(text, encoding="utf-8")


def test_show_raises_the_config_error(tmp_path: Path) -> None:
    write(tmp_path, "[http]\ntimeout_seconds = 'x'\n")
    with pytest.raises(TrelloctlError) as info:
        cmd.show(make_ctx(tmp_path), {})
    assert info.value.code == "CONFIG_INVALID"


def test_init_reports_a_write_failure(tmp_path: Path) -> None:
    (tmp_path / "config.toml").mkdir()
    with pytest.raises(TrelloctlError) as info:
        cmd.init(make_ctx(tmp_path), {"force": True})
    assert info.value.code == "HOME_NOT_WRITABLE"


def test_validate_reraises_a_malformed_file(tmp_path: Path) -> None:
    write(tmp_path, "[auth\n")
    with pytest.raises(TrelloctlError) as info:
        cmd.validate(make_ctx(tmp_path), {})
    assert info.value.code == "CONFIG_INVALID"
    assert "problems" not in info.value.details


def test_validate_missing_credentials_with_an_auth_section(tmp_path: Path) -> None:
    write(tmp_path, '[auth]\napi_key = "k"\ntoken = ""\n')
    with pytest.raises(TrelloctlError) as info:
        cmd.validate(make_ctx(tmp_path), {})
    assert [p["key"] for p in info.value.details["problems"]] == ["auth.token"]


def test_validate_accepts_credentials_from_the_environment(tmp_path: Path) -> None:
    write(tmp_path, "[http]\ntimeout_seconds = 5\n")
    env = {"TRELLO_API_KEY": "k", "TRELLO_TOKEN": "t"}
    envelope = cmd.validate(make_ctx(tmp_path, env), {})
    assert envelope["data"]["valid"] is True


def test_validate_propagates_other_api_errors(tmp_path: Path) -> None:
    write(tmp_path, f'[auth]\napi_key = "k"\ntoken = "t"\n[board]\ndefault = "{BOARD_ID}"\n')
    transport = StubTransport(response(500, "boom"))
    with pytest.raises(TrelloctlError) as info:
        cmd.validate(make_ctx(tmp_path, transport=transport), {})
    assert info.value.code == "API_ERROR"


def test_board_show_maps_missing_boards_and_keeps_other_errors(tmp_path: Path) -> None:
    write(tmp_path, f'[auth]\napi_key = "k"\ntoken = "t"\n[board]\ndefault = "{BOARD_ID}"\n')
    with pytest.raises(TrelloctlError) as info:
        cmd.board_show(make_ctx(tmp_path, transport=StubTransport(response(404, "gone"))), {})
    assert info.value.code == "BOARD_NOT_FOUND"
    with pytest.raises(TrelloctlError) as info:
        cmd.board_show(make_ctx(tmp_path, transport=StubTransport(response(500, "boom"))), {})
    assert info.value.code == "API_ERROR"


def test_board_show_without_credentials_is_auth_missing(tmp_path: Path) -> None:
    write(tmp_path, f'[board]\ndefault = "{BOARD_ID}"\n')
    with pytest.raises(TrelloctlError) as info:
        cmd.board_show(make_ctx(tmp_path), {})
    assert info.value.code == "AUTH_MISSING"


def test_board_show_tolerates_a_non_object_body(tmp_path: Path) -> None:
    write(tmp_path, f'[auth]\napi_key = "k"\ntoken = "t"\n[board]\ndefault = "{BOARD_ID}"\n')
    transport = StubTransport(response(200, "[]"))
    data = cmd.board_show(make_ctx(tmp_path, transport=transport), {})["data"]
    assert data == {"configured": True, "id": BOARD_ID, "name": None, "url": None}


def test_board_set_is_idempotent_and_unset_without_a_section_changes_nothing(
    tmp_path: Path,
) -> None:
    write(tmp_path, '[auth]\napi_key = "k"\ntoken = "t"\n')
    assert cmd.board_unset(make_ctx(tmp_path), {})["data"]["changed"] is False
    assert cmd.board_set(make_ctx(tmp_path), {"board": BOARD_ID.upper()})["data"]["changed"] is True
    again = cmd.board_set(make_ctx(tmp_path), {"board": BOARD_ID.upper()})
    assert again["data"]["changed"] is False


def test_fake_boards_route_finds_by_id_and_short_link(fake_trello: FakeTrello) -> None:
    from trelloctl.infra.trello_client import TrelloClient

    fake_trello.state.boards[BOARD_ID] = {
        "id": BOARD_ID,
        "name": "Work",
        "shortLink": "abcd1234",
        "url": "u",
    }
    client = TrelloClient(fake_trello.base_url, fake_trello.api_key, fake_trello.token, 5)
    assert client.get(f"/boards/{BOARD_ID}")["name"] == "Work"
    assert client.get("/boards/abcd1234", {"fields": "name"}) == {"id": BOARD_ID, "name": "Work"}
    with pytest.raises(TrelloctlError) as info:
        client.get("/boards/ffffffffffffffffffffffff")
    assert info.value.details["status"] == 404

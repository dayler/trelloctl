"""M03 acceptance tests: configuration reading, precedence and the `[board]` writer."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra import paths, settings
from trelloctl.infra.logging import LOGGER_NAME, configure_logging, shutdown_logging

KEY = "0123456789abcdef0123456789abcdef"
TOKEN = "f" * 64
FILE_AUTH = '[auth]\napi_key = "filekey"\ntoken = "filetoken"\n'


def write_config(home: Path, text: str) -> Path:
    path = home / settings.CONFIG_FILE
    path.write_bytes(text.encode("utf-8"))
    return path


def load(env: dict[str, str] | None = None, **kwargs: Any) -> settings.Settings:
    merged = {"TRELLOCTL_HOME": os.environ["TRELLOCTL_HOME"], **(env or {})}
    return settings.load_settings(merged, **kwargs)


def raises(code: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        fn(*args, **kwargs)
    assert info.value.code == code
    return info.value


@pytest.fixture(autouse=True)
def _close_logging() -> Iterator[None]:
    yield
    shutdown_logging()


@pytest.mark.tc("TC-M03-001")
def test_home_defaults_to_user_directory(tmp_path: Path) -> None:
    user_home = tmp_path / "user-home"
    assert paths.default_home({}) == user_home / ".trelloctl"
    assert settings.load_settings({}, require_file=False).home == user_home / ".trelloctl"


@pytest.mark.tc("TC-M03-002")
def test_home_from_environment(tmp_path: Path) -> None:
    target = tmp_path / "custom"
    target.mkdir()
    loaded = settings.load_settings({"TRELLOCTL_HOME": str(target)}, require_file=False)
    assert loaded.home == target
    assert loaded.config_path == target / "config.toml"


@pytest.mark.tc("TC-M03-003")
def test_missing_config_file(trelloctl_home: Path) -> None:
    error = raises("CONFIG_NOT_FOUND", settings.load_settings)
    assert error.details["path"] == str(trelloctl_home / "config.toml")
    assert error.exit_code == 3


@pytest.mark.tc("TC-M03-004")
def test_precedence(trelloctl_home: Path) -> None:
    write_config(
        trelloctl_home,
        '[auth]\napi_key = "filekey"\ntoken = "filetoken"\n[board]\ndefault = "filebrd"\n',
    )
    env = {"TRELLO_API_KEY": "envkey", "TRELLO_TOKEN": "envtoken", "TRELLO_BOARD": "envbrd"}
    loaded = load(env)
    assert settings.require_credentials(loaded) == ("envkey", "envtoken")
    assert settings.select_board(loaded, "flagbrd") == "flagbrd"
    assert settings.select_board(loaded) == "envbrd"
    assert loaded.board_default == "filebrd"
    # File wins over the built-in defaults.
    write_config(trelloctl_home, FILE_AUTH + "[http]\ntimeout_seconds = 5\n")
    from_file = load({})
    assert from_file.timeout_seconds == 5
    assert from_file.base_url == "https://api.trello.com/1"
    assert settings.require_credentials(from_file) == ("filekey", "filetoken")


@pytest.mark.tc("TC-M03-005")
def test_auth_missing(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, "[board]\n")
    error = raises("AUTH_MISSING", settings.require_credentials, load({}))
    assert error.exit_code == 4


@pytest.mark.tc("TC-M03-006")
def test_empty_credentials_count_as_absent(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, '[auth]\napi_key = ""\ntoken = ""\n')
    raises("AUTH_MISSING", settings.require_credentials, load({}))


@pytest.mark.tc("TC-M03-007")
def test_credentials_only_in_environment(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, "schema_version = 1\n")
    loaded = load({"TRELLO_API_KEY": KEY, "TRELLO_TOKEN": TOKEN})
    assert settings.require_credentials(loaded) == (KEY, TOKEN)


@pytest.mark.tc("TC-M03-008")
def test_effective_view_masks_token(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, f'[auth]\napi_key = "{KEY}"\ntoken = "{TOKEN}"\n')
    view = load({}).effective_view()
    assert TOKEN not in repr(view)
    assert KEY not in repr(view)
    assert view["auth"]["token"] != TOKEN
    assert view["auth"]["token"].startswith(settings.MASK)
    assert view["http"]["timeout_seconds"] == 30


@pytest.mark.tc("TC-M03-009")
def test_invalid_values_report_one_problem_per_key(trelloctl_home: Path) -> None:
    write_config(
        trelloctl_home,
        FILE_AUTH
        + '[http]\ntimeout_seconds = "x"\n[cache]\nmetadata_ttl_seconds = -5\n'
        + '[logging]\nlevel = "loud"\n',
    )
    error = raises("CONFIG_INVALID", load)
    problems = {p["key"]: p["reason"] for p in error.details["problems"]}
    assert set(problems) == {
        "http.timeout_seconds",
        "cache.metadata_ttl_seconds",
        "logging.level",
    }
    assert all(problems.values())
    assert error.exit_code == 3


@pytest.mark.tc("TC-M03-010")
def test_malformed_toml_reports_line_and_column(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, '[auth]\napi_key = "ok"\ntoken = = 3\n')
    error = raises("CONFIG_INVALID", load)
    assert error.details["line"] == 3
    assert isinstance(error.details["column"], int)


@pytest.mark.tc("TC-M03-011")
def test_board_selection_order(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH + '[board]\ndefault = "filebrd"\n')
    with_env = load({"TRELLO_BOARD": "envbrd"})
    assert settings.select_board(with_env, "flagbrd") == "flagbrd"
    assert settings.select_board(with_env, None) == "envbrd"
    assert settings.select_board(load({}), None) == "filebrd"


@pytest.mark.tc("TC-M03-012")
def test_no_board_source(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH)
    error = raises("BOARD_NOT_CONFIGURED", settings.select_board, load({}))
    assert error.exit_code == 3


@pytest.mark.tc("TC-M03-013")
def test_empty_default_board(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH + '[board]\ndefault = ""\n')
    raises("BOARD_NOT_CONFIGURED", settings.select_board, load({}))


SAMPLE = (
    "# my config\n"
    'schema_version = 1\n\n[auth]\napi_key = "k"  # key\ntoken = "t"\n\n'
    '[board]\ndefault = "old"          # board id\n\n[http]\ntimeout_seconds = 10\n'
)


@pytest.mark.tc("TC-M03-014")
def test_writer_changes_only_board_line(trelloctl_home: Path) -> None:
    path = write_config(trelloctl_home, SAMPLE)
    assert settings.write_board_default(path, "NEWID123") is True
    assert path.read_bytes().decode() == SAMPLE.replace('"old"', '"NEWID123"')
    assert (trelloctl_home / "config.toml.bak").read_bytes() == SAMPLE.encode()
    assert load({}).board_default == "NEWID123"


@pytest.mark.tc("TC-M03-015")
def test_writer_appends_missing_section(trelloctl_home: Path) -> None:
    original = FILE_AUTH + "# trailing comment"
    path = write_config(trelloctl_home, original)
    settings.write_board_default(path, "abc12345")
    result = path.read_bytes().decode()
    assert result.startswith(original)
    assert result.endswith('[board]\ndefault = "abc12345"\n')
    assert load({}).board_default == "abc12345"


@pytest.mark.tc("TC-M03-016")
def test_writer_keeps_crlf(trelloctl_home: Path) -> None:
    crlf = SAMPLE.replace("\n", "\r\n")
    path = write_config(trelloctl_home, crlf)
    settings.write_board_default(path, "NEWID123")
    assert path.read_bytes().decode() == crlf.replace('"old"', '"NEWID123"')
    path = write_config(trelloctl_home, FILE_AUTH.replace("\n", "\r\n"))
    settings.write_board_default(path, "x1")
    data = path.read_bytes()
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert data.endswith(b'default = "x1"\r\n')


@pytest.mark.tc("TC-M03-017")
def test_writer_unsets_default(trelloctl_home: Path) -> None:
    path = write_config(trelloctl_home, SAMPLE)
    assert settings.write_board_default(path, None) is True
    result = path.read_text(encoding="utf-8")
    assert "default" not in result
    assert "[board]" in result
    assert result == SAMPLE.replace('default = "old"          # board id\n', "")
    assert (trelloctl_home / "config.toml.bak").read_bytes() == SAMPLE.encode()
    assert load({}).board_default == ""


@pytest.mark.tc("TC-M03-018")
def test_values_exposed_to_logging_http_cache(trelloctl_home: Path) -> None:
    write_config(
        trelloctl_home,
        FILE_AUTH
        + '[logging]\nlevel = "debug"\nmax_bytes = 2048\nbackup_count = 2\n'
        + '[http]\nbase_url = "http://127.0.0.1:9999"\ntimeout_seconds = 7\n'
        + "[cache]\nmetadata_ttl_seconds = 11\ncards_ttl_seconds = 22\n",
    )
    loaded = load({})
    (trelloctl_home / "logs").mkdir()
    logger = configure_logging(
        trelloctl_home / "logs",
        level=loaded.log_level,
        max_bytes=loaded.log_max_bytes,
        backup_count=loaded.log_backup_count,
    )
    assert logger.level == logging.DEBUG
    assert logger.name == LOGGER_NAME
    handler: Any = logger.handlers[0]
    assert (handler.maxBytes, handler.backupCount) == (2048, 2)
    assert (loaded.base_url, loaded.timeout_seconds) == ("http://127.0.0.1:9999", 7)
    assert (loaded.metadata_ttl_seconds, loaded.cards_ttl_seconds) == (11, 22)


@pytest.mark.tc("TC-M03-019")
def test_unknown_keys_warn_and_cache_stays_on(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH + "[cache]\nenabled = false\nmystery = 1\n")
    loaded = load({})
    assert len(loaded.warnings) == 2
    assert any("cache.enabled" in w for w in loaded.warnings)
    assert any("cache.mystery" in w for w in loaded.warnings)
    assert "enabled" not in loaded.effective_view()["cache"]
    assert loaded.metadata_ttl_seconds == 1800


@pytest.mark.tc("TC-M03-020")
def test_auth_only_file_is_valid(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH)
    loaded = load({})
    assert loaded.warnings == ()
    view = loaded.effective_view()
    assert view["http"] == {"base_url": "https://api.trello.com/1", "timeout_seconds": 30}
    assert view["cache"] == {"metadata_ttl_seconds": 1800, "cards_ttl_seconds": 300}
    assert view["attachments"]["max_upload_bytes"] == 10_485_760
    assert view["logging"] == {"level": "info", "max_bytes": 1_048_576, "backup_count": 5}


@pytest.mark.tc("TC-M03-021")
def test_partial_http_section(trelloctl_home: Path) -> None:
    write_config(trelloctl_home, FILE_AUTH + "[http]\ntimeout_seconds = 12\n")
    loaded = load({})
    assert loaded.timeout_seconds == 12
    assert loaded.base_url == "https://api.trello.com/1"

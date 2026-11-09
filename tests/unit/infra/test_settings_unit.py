"""Unit tests for edge cases of `infra/settings.py`."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra import settings


def cfg(home: Path, text: str | bytes) -> Path:
    path = home / "config.toml"
    path.write_bytes(text if isinstance(text, bytes) else text.encode())
    return path


def load(env: dict[str, str] | None = None, **kwargs: Any) -> settings.Settings:
    merged = {"TRELLOCTL_HOME": os.environ["TRELLOCTL_HOME"], **(env or {})}
    return settings.load_settings(merged, **kwargs)


def err(fn: Callable[..., Any], *args: Any) -> TrelloctlError:
    with pytest.raises(TrelloctlError) as info:
        fn(*args)
    return info.value


def test_mask_secret() -> None:
    assert settings.mask_secret("") == ""
    assert settings.mask_secret("short") == "***"
    assert settings.mask_secret("abcdefghijkl") == "***ijkl"


def test_defaults_without_file(trelloctl_home: Path) -> None:
    loaded = load({}, require_file=False)
    assert not loaded.config_found
    assert loaded.secrets == ()
    assert loaded.effective_view()["board"]["default"] is None


def test_secrets_listed(trelloctl_home: Path) -> None:
    env = {"TRELLO_API_KEY": "a", "TRELLO_TOKEN": "b"}
    assert load(env, require_file=False).secrets == ("a", "b")


def test_invalid_utf8(trelloctl_home: Path) -> None:
    cfg(trelloctl_home, b"\xff\xfe\x00bad")
    assert err(load, {}).code == "CONFIG_INVALID"


def test_directory_is_not_a_config_file(trelloctl_home: Path) -> None:
    (trelloctl_home / "config.toml").mkdir()
    assert err(load, {}).code == "CONFIG_NOT_FOUND"


def test_unreadable_file(trelloctl_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg(trelloctl_home, "x = 1\n")

    def boom(self: Path) -> bytes:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(Path, "read_bytes", boom)
    assert err(load, {}).code == "CONFIG_INVALID"


def test_read_toml_missing(trelloctl_home: Path) -> None:
    assert err(settings._read_toml, trelloctl_home / "nope.toml").code == "CONFIG_NOT_FOUND"


def test_type_problems(trelloctl_home: Path) -> None:
    cfg(
        trelloctl_home,
        'schema_version = "1"\nunknown_top = 1\n[http]\ntimeout_seconds = true\nbase_url = " "\n'
        "[auth]\ntoken = 5\n[board]\ndefault = 3\n[cache]\ncards_ttl_seconds = 1.5\n"
        "[logging]\nlevel = 4\nmax_bytes = 0\n",
    )
    keys = {p["key"] for p in err(load, {}).details["problems"]}
    assert keys == {
        "schema_version",
        "http.timeout_seconds",
        "http.base_url",
        "auth.token",
        "board.default",
        "cache.cards_ttl_seconds",
        "logging.level",
        "logging.max_bytes",
    }


def test_schema_version_and_section_type(trelloctl_home: Path) -> None:
    cfg(trelloctl_home, "schema_version = 2\nhttp = 3\n")
    keys = {p["key"] for p in err(load, {}).details["problems"]}
    assert keys == {"schema_version", "http"}


def test_level_case_insensitive(trelloctl_home: Path) -> None:
    cfg(trelloctl_home, '[logging]\nlevel = "DEBUG"\n')
    assert load({}).log_level == "debug"


def test_unknown_section_warns(trelloctl_home: Path) -> None:
    cfg(trelloctl_home, "[extra]\na = 1\n")
    assert load({}).warnings == ("Unknown configuration key ignored: extra",)


def test_parse_error_position_fallbacks() -> None:
    parse: Any = settings._parse_error_position
    assert parse(ValueError("Invalid value (at line 4, column 9)")) == (4, 9)
    assert parse(ValueError("Unterminated (at end of document)")) == (None, None)


def test_malformed_without_position_omits_line(trelloctl_home: Path) -> None:
    cfg(trelloctl_home, '[auth]\napi_key = "unterminated')
    error = err(load, {})
    assert error.code == "CONFIG_INVALID"
    assert "reason" in error.details


def test_select_board_whitespace_and_env() -> None:
    loaded = load({"TRELLO_BOARD": "  b1  "}, require_file=False)
    assert settings.select_board(loaded, "  ") == "b1"
    assert err(settings.select_board, load({}, require_file=False)).code == ("BOARD_NOT_CONFIGURED")


def test_auth_missing_names_missing_keys(trelloctl_home: Path) -> None:
    loaded = load({"TRELLO_API_KEY": "k"}, require_file=False)
    assert err(settings.require_credentials, loaded).details["missing"] == ["token"]


def test_writer_missing_and_invalid(trelloctl_home: Path) -> None:
    path = trelloctl_home / "config.toml"
    assert err(settings.write_board_default, path, "x").code == "CONFIG_NOT_FOUND"
    cfg(trelloctl_home, "a = = 1\n")
    assert err(settings.write_board_default, path, "x").code == "CONFIG_INVALID"
    cfg(trelloctl_home, b"\xff\xfe")
    assert err(settings.write_board_default, path, "x").code == "CONFIG_INVALID"
    assert not (trelloctl_home / "config.toml.bak").exists()


def test_writer_unset_noop(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, '[auth]\ntoken = "t"\n')
    assert settings.write_board_default(path, None) is False
    cfg(trelloctl_home, "[board]\n")
    assert settings.write_board_default(path, None) is False
    assert not (trelloctl_home / "config.toml.bak").exists()


def test_writer_inserts_key_in_empty_section(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, "[board]\n# note\n[http]\ntimeout_seconds = 3\n")
    settings.write_board_default(path, "id1")
    assert path.read_text() == '[board]\ndefault = "id1"\n# note\n[http]\ntimeout_seconds = 3\n'


def test_writer_header_without_eol_and_empty_file(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, "[board]")
    settings.write_board_default(path, "id1")
    assert path.read_text() == '[board]\ndefault = "id1"\n'
    path = cfg(trelloctl_home, "")
    settings.write_board_default(path, "id2")
    assert path.read_text() == '[board]\ndefault = "id2"\n'


def test_writer_single_quoted_value_keeps_comment(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, "[board]\ndefault = 'old'  # c\n")
    settings.write_board_default(path, "new")
    assert path.read_text() == '[board]\ndefault = "new"  # c\n'


def test_writer_non_string_default_replaced(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, "[board]\ndefault = 5\n")
    settings.write_board_default(path, "new")
    assert path.read_text() == '[board]\ndefault = "new"\n'


def test_writer_blank_line_before_new_section(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, "a = 1")
    settings.write_board_default(path, "id")
    assert path.read_text() == 'a = 1\n\n[board]\ndefault = "id"\n'


def test_writer_unset_only_touches_board(trelloctl_home: Path) -> None:
    path = cfg(trelloctl_home, '[http]\ndefault = "x"\n[board]\ndefault = "y"\n')
    settings.write_board_default(path, None)
    assert path.read_text() == '[http]\ndefault = "x"\n[board]\n'


def test_writer_write_failure(trelloctl_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = cfg(trelloctl_home, "[board]\n")

    def boom(src: object, dst: object) -> None:
        raise PermissionError(13, "denied")

    monkeypatch.setattr(settings.os, "replace", boom)
    assert err(settings.write_board_default, path, "x").code == "HOME_NOT_WRITABLE"

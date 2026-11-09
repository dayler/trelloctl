"""Effective configuration: `config.toml`, environment, defaults and the `[board]` writer."""

from __future__ import annotations

import json
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.paths import default_home

CONFIG_FILE = "config.toml"
BACKUP_FILE = "config.toml.bak"
API_KEY_ENV = "TRELLO_API_KEY"
TOKEN_ENV = "TRELLO_TOKEN"  # noqa: S105 - variable name, not a secret
BOARD_ENV = "TRELLO_BOARD"
SCHEMA_VERSION = 1
LOG_LEVELS = ("debug", "info", "warning", "error", "critical")
MASK = "***"

# Defaults of the config.toml template (PRD 10.5).
DEFAULTS: dict[str, dict[str, Any]] = {
    "auth": {"api_key": "", "token": ""},
    "board": {"default": ""},
    "http": {"base_url": "https://api.trello.com/1", "timeout_seconds": 30},
    "cache": {"metadata_ttl_seconds": 1800, "cards_ttl_seconds": 300},
    "attachments": {"max_upload_bytes": 10_485_760},
    "logging": {"level": "info", "max_bytes": 1_048_576, "backup_count": 5},
}

# Numeric keys: (section, key) -> minimum allowed value.
_MIN_INT: dict[tuple[str, str], int] = {
    ("http", "timeout_seconds"): 1,
    ("cache", "metadata_ttl_seconds"): 0,
    ("cache", "cards_ttl_seconds"): 0,
    ("attachments", "max_upload_bytes"): 1,
    ("logging", "max_bytes"): 1,
    ("logging", "backup_count"): 0,
}
_STR_KEYS = {
    ("auth", "api_key"),
    ("auth", "token"),
    ("board", "default"),
    ("http", "base_url"),
    ("logging", "level"),
}


@dataclass(frozen=True)
class Settings:
    """Effective configuration of one invocation (file, environment and defaults merged)."""

    home: Path
    config_path: Path
    config_found: bool
    api_key: str
    token: str
    board_default: str
    base_url: str
    timeout_seconds: int
    metadata_ttl_seconds: int
    cards_ttl_seconds: int
    max_upload_bytes: int
    log_level: str
    log_max_bytes: int
    log_backup_count: int
    warnings: tuple[str, ...] = ()
    env_board: str = ""

    @property
    def secrets(self) -> tuple[str, ...]:
        """Credential values to mask in logs and output."""
        return tuple(s for s in (self.api_key, self.token) if s)

    def effective_view(self) -> dict[str, Any]:
        """Effective configuration with credentials masked; the token is never complete."""
        return {
            "home": str(self.home),
            "config_path": str(self.config_path),
            "config_found": self.config_found,
            "schema_version": SCHEMA_VERSION,
            "auth": {"api_key": mask_secret(self.api_key), "token": mask_secret(self.token)},
            "board": {"default": self.board_default or None},
            "http": {"base_url": self.base_url, "timeout_seconds": self.timeout_seconds},
            "cache": {
                "metadata_ttl_seconds": self.metadata_ttl_seconds,
                "cards_ttl_seconds": self.cards_ttl_seconds,
            },
            "attachments": {"max_upload_bytes": self.max_upload_bytes},
            "logging": {
                "level": self.log_level,
                "max_bytes": self.log_max_bytes,
                "backup_count": self.log_backup_count,
            },
        }


def mask_secret(value: str) -> str:
    """Mask a credential, keeping at most the last 4 characters of long values."""
    if not value:
        return ""
    if len(value) <= 8:
        return MASK
    return f"{MASK}{value[-4:]}"


def config_path_for(home: Path) -> Path:
    return home / CONFIG_FILE


def _parse_error_position(exc: tomllib.TOMLDecodeError) -> tuple[int | None, int | None]:
    line = getattr(exc, "lineno", None)
    column = getattr(exc, "colno", None)
    if isinstance(line, int) and isinstance(column, int):
        return line, column
    match = re.search(r"line (\d+), column (\d+)", str(exc))
    if match:
        return int(match.group(1)), int(match.group(2))
    return None, None


def _invalid_toml(path: Path, exc: tomllib.TOMLDecodeError) -> TrelloctlError:
    line, column = _parse_error_position(exc)
    details: dict[str, Any] = {"path": str(path), "reason": str(exc)}
    if line is not None and column is not None:
        details["line"] = line
        details["column"] = column
    return TrelloctlError("CONFIG_INVALID", f"config.toml is not valid TOML: {exc}", details)


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise _not_found(path) from exc
    except OSError as exc:
        raise TrelloctlError(
            "CONFIG_INVALID",
            f"config.toml cannot be read: {exc.strerror or type(exc).__name__}",
            {"path": str(path)},
        ) from exc
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise _invalid_toml(path, exc) from exc
    except UnicodeDecodeError as exc:
        raise TrelloctlError(
            "CONFIG_INVALID", "config.toml is not valid UTF-8.", {"path": str(path)}
        ) from exc


def _not_found(path: Path) -> TrelloctlError:
    return TrelloctlError(
        "CONFIG_NOT_FOUND",
        f"Configuration file not found: {path}. Run `trelloctl config init` to create it.",
        {"path": str(path)},
    )


def _check_value(section: str, key: str, value: Any) -> tuple[Any, str | None]:
    """Return the normalised value and a problem reason, if any."""
    pair = (section, key)
    if pair in _MIN_INT:
        if isinstance(value, bool) or not isinstance(value, int):
            return value, "must be an integer"
        if value < _MIN_INT[pair]:
            return value, f"must be at least {_MIN_INT[pair]}"
        return value, None
    if pair in _STR_KEYS:
        if not isinstance(value, str):
            return value, "must be a string"
        if pair == ("logging", "level"):
            if value.lower() not in LOG_LEVELS:
                return value, f"must be one of {', '.join(LOG_LEVELS)}"
            return value.lower(), None
        if pair == ("http", "base_url") and not value.strip():
            return value, "must not be empty"
        return value, None
    return value, None


def _merge_file(data: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str], list[Any]]:
    """Merge the parsed file over the defaults. Returns (values, warnings, problems)."""
    values = {section: dict(keys) for section, keys in DEFAULTS.items()}
    warnings: list[str] = []
    problems: list[dict[str, str]] = []

    def warn(key: str) -> None:
        warnings.append(f"Unknown configuration key ignored: {key}")

    for name, content in data.items():
        if name == "schema_version":
            if isinstance(content, bool) or not isinstance(content, int):
                problems.append({"key": name, "reason": "must be an integer"})
            elif content != SCHEMA_VERSION:
                problems.append({"key": name, "reason": f"unsupported, expected {SCHEMA_VERSION}"})
            continue
        if name not in DEFAULTS:
            warn(name)
            continue
        if not isinstance(content, dict):
            problems.append({"key": name, "reason": "must be a table"})
            continue
        for key, value in content.items():
            if key not in DEFAULTS[name]:
                warn(f"{name}.{key}")
                continue
            checked, reason = _check_value(name, key, value)
            if reason:
                problems.append({"key": f"{name}.{key}", "reason": reason})
            else:
                values[name][key] = checked
    return values, warnings, problems


def load_settings(
    environ: Mapping[str, str] | None = None,
    *,
    home: Path | None = None,
    require_file: bool = True,
) -> Settings:
    """Load the effective configuration: environment > `config.toml` > defaults.

    Flags are applied by the caller (see `select_board`). With `require_file` a missing
    `config.toml` raises `CONFIG_NOT_FOUND`; without it the defaults apply. Credentials and
    board are not required here: see `require_credentials` and `select_board`.
    """
    env = os.environ if environ is None else environ
    root = home if home is not None else default_home(env)
    path = config_path_for(root)
    found = path.is_file()
    if not found and require_file:
        raise _not_found(path)
    data = _read_toml(path) if found else {}
    values, warnings, problems = _merge_file(data)
    if problems:
        keys = ", ".join(p["key"] for p in problems)
        raise TrelloctlError(
            "CONFIG_INVALID",
            f"config.toml has invalid values: {keys}.",
            {"path": str(path), "problems": problems},
        )
    return Settings(
        home=root,
        config_path=path,
        config_found=found,
        api_key=env.get(API_KEY_ENV) or values["auth"]["api_key"],
        token=env.get(TOKEN_ENV) or values["auth"]["token"],
        board_default=values["board"]["default"],
        base_url=values["http"]["base_url"],
        timeout_seconds=values["http"]["timeout_seconds"],
        metadata_ttl_seconds=values["cache"]["metadata_ttl_seconds"],
        cards_ttl_seconds=values["cache"]["cards_ttl_seconds"],
        max_upload_bytes=values["attachments"]["max_upload_bytes"],
        log_level=values["logging"]["level"],
        log_max_bytes=values["logging"]["max_bytes"],
        log_backup_count=values["logging"]["backup_count"],
        warnings=tuple(warnings),
        env_board=env.get(BOARD_ENV, ""),
    )


def require_credentials(settings: Settings) -> tuple[str, str]:
    """Return (api_key, token); an empty value counts as absent (`AUTH_MISSING`)."""
    if not settings.api_key or not settings.token:
        missing = [
            name
            for name, value in (("api_key", settings.api_key), ("token", settings.token))
            if not value
        ]
        raise TrelloctlError(
            "AUTH_MISSING",
            f"Missing Trello credentials ({', '.join(missing)}). Set [auth] in config.toml "
            f"or {API_KEY_ENV} / {TOKEN_ENV} in the environment.",
            {"missing": missing},
        )
    return settings.api_key, settings.token


def select_board(settings: Settings, flag: str | None = None) -> str:
    """Board reference of the invocation: `--board` > `TRELLO_BOARD` > `[board].default`."""
    for candidate in (flag, settings.env_board, settings.board_default):
        if candidate and candidate.strip():
            return candidate.strip()
    raise TrelloctlError(
        "BOARD_NOT_CONFIGURED",
        "No board selected. Use --board, set TRELLO_BOARD or run `trelloctl config board set`.",
    )


# --- [board] writer -------------------------------------------------------------------

_BOARD_HEADER = re.compile(r"^\s*\[board\]\s*(#.*)?$")
_ANY_HEADER = re.compile(r"^\s*\[")
_DEFAULT_LINE = re.compile(r"^(\s*default\s*=\s*)(\"(?:[^\"\\]|\\.)*\"|'[^']*')(.*)$")
_DEFAULT_KEY = re.compile(r"^\s*default\s*=")


def _split_eol(line: str) -> tuple[str, str]:
    body = line.rstrip("\r\n")
    return body, line[len(body) :]


def _rewrite_board(text: str, value: str | None) -> str:
    lines = text.splitlines(keepends=True)
    eol = "\n"
    for line in lines:
        eol = _split_eol(line)[1] or eol
        if _split_eol(line)[1]:
            break
    new_line = f"default = {json.dumps(value)}" if value is not None else ""

    start = next((i for i, ln in enumerate(lines) if _BOARD_HEADER.match(_split_eol(ln)[0])), None)
    if start is None:
        if value is None:
            return text
        if lines and not _split_eol(lines[-1])[1]:
            lines[-1] += eol
        if lines and _split_eol(lines[-1])[0].strip():
            lines.append(eol)
        lines.extend([f"[board]{eol}", f"{new_line}{eol}"])
        return "".join(lines)

    end = next((i for i in range(start + 1, len(lines)) if _ANY_HEADER.match(lines[i])), len(lines))
    key_index = next(
        (i for i in range(start + 1, end) if _DEFAULT_KEY.match(lines[i])),
        None,
    )
    if key_index is None:
        if value is None:
            return text
        header_body, header_eol = _split_eol(lines[start])
        if not header_eol:
            lines[start] = header_body + eol
        lines.insert(start + 1, f"{new_line}{eol}")
        return "".join(lines)
    body, line_eol = _split_eol(lines[key_index])
    if value is None:
        del lines[key_index]
    else:
        match = _DEFAULT_LINE.match(body)
        lines[key_index] = (
            f"{match.group(1)}{json.dumps(value)}{match.group(3)}{line_eol}"
            if match
            else f"{new_line}{line_eol}"
        )
    return "".join(lines)


def write_board_default(config_path: Path, value: str | None) -> bool:
    """Set (or, with None, remove) `[board].default`, touching only the `[board]` section.

    The rest of the file is kept byte for byte. `config.toml.bak` receives the previous
    content before writing. Returns False when nothing needed to change.
    """
    try:
        raw = config_path.read_bytes()
    except FileNotFoundError as exc:
        raise _not_found(config_path) from exc
    try:
        text = raw.decode("utf-8")
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise _invalid_toml(config_path, exc) from exc
    except UnicodeDecodeError as exc:
        raise TrelloctlError(
            "CONFIG_INVALID", "config.toml is not valid UTF-8.", {"path": str(config_path)}
        ) from exc
    updated = _rewrite_board(text, value)
    if updated == text:
        return False
    try:
        tomllib.loads(updated)
    except tomllib.TOMLDecodeError as exc:
        raise _invalid_toml(config_path, exc) from exc
    backup = config_path.with_name(BACKUP_FILE)
    try:
        backup.write_bytes(raw)
        temp = config_path.with_name(f"{CONFIG_FILE}.tmp")
        temp.write_bytes(updated.encode("utf-8"))
        os.replace(temp, config_path)
    except OSError as exc:
        raise TrelloctlError(
            "HOME_NOT_WRITABLE",
            f"Cannot write {config_path}: {exc.strerror or type(exc).__name__}.",
            {"path": str(config_path)},
        ) from exc
    return True

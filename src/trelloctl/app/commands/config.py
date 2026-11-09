"""`config` commands: init, show, validate and the default board (RF-1)."""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from typing import Any

from trelloctl.app.commands.context import CommandContext
from trelloctl.app.resolver import Resolver
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.settings import (
    API_KEY_ENV,
    TOKEN_ENV,
    require_credentials,
    write_board_default,
)

BOARD_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_API_ERROR_STATUSES_AS_NOT_FOUND = (400, 401, 403, 404)

# Written by `config init` (A-23): only [auth] is active; everything else is commented out
# with its default value, so the effective configuration equals the defaults.
CONFIG_TEMPLATE = """\
# trelloctl configuration file.
#
# Only [auth] is required. Every other section and key is optional: a missing key takes
# the default value shown in the commented lines below. Uncomment a line to change it.
# Precedence: command-line flags > environment variables > this file > defaults.
# schema_version = 1

[auth]
# API key: create a Power-Up at https://trello.com/power-ups/admin
# (Power-Up > Trello Auth > "Generate a new API Key").
api_key = ""
# Token: open https://trello.com/1/authorize?expiration=never&scope=read,write&response_type=token&key=<api_key>
# and authorize the application. You can also set TRELLO_API_KEY and TRELLO_TOKEN in the
# environment instead of writing the credentials here.
token = ""

# Default board. Each command works on one board: --board > TRELLO_BOARD > default.
# `trelloctl config board set|unset` edits only this section. Other boards are given with
# --board.
# Tip: point Trello's email-to-board address and the Trello app for Slack at a list of this
# board (your working board), so those cards land where trelloctl works; the personal
# Inbox keeps the rest.
# [board]
# default = ""   # board id or shortLink

# [http]
# base_url = "https://api.trello.com/1"   # change only for test environments
# timeout_seconds = 30                    # no retries: a 429 or a network failure fails at once

# [cache]
# The cache is always on; --no-cache skips it for one invocation.
# metadata_ttl_seconds = 1800   # lists, labels, members and the user's boards
# cards_ttl_seconds = 300       # card snapshot, archived cards and each single card

# [attachments]
# max_upload_bytes = 10485760   # 10 MB, Trello free plan limit; 250 MB on paid plans

# [logging]
# level = "info"                # debug, info, warning or error
# max_bytes = 1048576           # the log file rotates at this size
# backup_count = 5              # rotated copies kept
"""


def init(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Create `config.toml` with the minimal configuration."""
    path = ctx.settings.config_path
    existed = path.exists()
    if existed and not args.get("force"):
        raise TrelloctlError(
            "USAGE",
            f"{path} already exists; use --force to overwrite it.",
            {"path": str(path)},
        )
    try:
        path.write_bytes(CONFIG_TEMPLATE.encode("utf-8"))
        if os.name == "posix":
            os.chmod(path, 0o600)
    except OSError as exc:
        raise TrelloctlError(
            "HOME_NOT_WRITABLE",
            f"Cannot write {path}: {exc.strerror or type(exc).__name__}.",
            {"path": str(path)},
        ) from exc
    return ctx.success({"path": str(path), "created": not existed, "overwritten": existed})


def show(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Effective configuration with the credentials masked."""
    if ctx.config_error is not None:
        raise ctx.config_error
    return ctx.success(ctx.settings.effective_view())


def _problem(key: str, reason: str) -> dict[str, str]:
    return {"key": key, "reason": reason}


def _auth_problems(ctx: CommandContext, raw: Mapping[str, Any]) -> list[dict[str, str]]:
    table = raw.get("auth")
    file_auth: Mapping[str, Any] = table if isinstance(table, dict) else {}
    environ = ctx.environ
    effective = {
        "api_key": environ.get(API_KEY_ENV) or file_auth.get("api_key") or "",
        "token": environ.get(TOKEN_ENV) or file_auth.get("token") or "",
    }
    if "auth" not in raw and not all(effective.values()):
        return [_problem("auth", "missing required section [auth] (api_key and token)")]
    env_names = {"api_key": API_KEY_ENV, "token": TOKEN_ENV}
    return [
        _problem(f"auth.{key}", f"is required (or set {env_names[key]})")
        for key, value in effective.items()
        if not value
    ]


def _fetch_board(ctx: CommandContext, reference: str, fields: str) -> dict[str, Any]:
    """`GET /boards/{id}`; a missing or inaccessible board is `BOARD_NOT_FOUND`."""
    try:
        board = ctx.client().get(f"/boards/{reference}", {"fields": fields})
    except TrelloctlError as exc:
        if (
            exc.code == "API_ERROR"
            and exc.details.get("status") in _API_ERROR_STATUSES_AS_NOT_FOUND
        ):
            raise TrelloctlError(
                "BOARD_NOT_FOUND",
                f"Board {reference} was not found or is not accessible.",
                {"board": reference},
            ) from exc
        raise
    return board if isinstance(board, dict) else {}


def validate(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Check types, required fields and access to the default board; report every problem."""
    path = ctx.settings.config_path
    problems: list[dict[str, str]] = []
    if ctx.config_error is not None:
        if "problems" not in ctx.config_error.details:
            raise ctx.config_error  # malformed TOML or unreadable file: nothing more to check
        problems.extend(ctx.config_error.details["problems"])
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    problems.extend(_auth_problems(ctx, raw))
    default = ctx.settings.board_default
    board: dict[str, Any] | None = None
    if not problems and default:
        try:
            board = _fetch_board(ctx, default, "name")
        except TrelloctlError as exc:
            if exc.code != "BOARD_NOT_FOUND":
                raise
            problems.append(_problem("board.default", "board not found or not accessible"))
    if problems:
        keys = ", ".join(p["key"] for p in problems)
        raise TrelloctlError(
            "CONFIG_INVALID",
            f"config.toml has problems: {keys}.",
            {"path": str(path), "problems": problems},
        )
    return ctx.success(
        {
            "valid": True,
            "config_path": str(path),
            "default_board": None
            if board is None
            else {"id": board.get("id", default), "name": board.get("name")},
        }
    )


def board_show(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """The default board: id, name and url, or a note that there is none."""
    default = ctx.settings.board_default
    if not default:
        return ctx.success({"configured": False, "id": None, "name": None, "url": None})
    board = _fetch_board(ctx, default, "name,url")
    return ctx.success(
        {
            "configured": True,
            "id": board.get("id", default),
            "name": board.get("name"),
            "url": board.get("url"),
        },
        board_id=board.get("id", default),
    )


def board_set(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Store the default board given as an id, shortLink, URL or name (AC-1.9, AC-1.10).

    A board id is stored as given; any other form is resolved against Trello, which needs
    credentials (`AUTH_MISSING`). Archived boards are considered with the global
    `--include-archived-boards` (A-24).
    """
    value = str(args["board"]).strip()
    if not value:
        raise TrelloctlError("USAGE", "config board set expects a board.", {"board": value})
    if BOARD_ID.match(value):
        board_id = value
    else:
        require_credentials(ctx.settings)
        with Resolver(ctx) as resolver:
            board_id = resolver.resolve_board(value)
    changed = write_board_default(ctx.settings.config_path, board_id)
    return ctx.success({"default": board_id, "changed": changed}, board_id=board_id)


def board_unset(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Remove the default board."""
    changed = write_board_default(ctx.settings.config_path, None)
    return ctx.success({"default": None, "changed": changed})

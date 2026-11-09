"""Per-invocation context shared by the adapters, and the single precondition check (A-7)."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.logging import LOGGER_NAME
from trelloctl.infra.settings import (
    API_KEY_ENV,
    BOARD_ENV,
    DEFAULTS,
    TOKEN_ENV,
    Settings,
    config_path_for,
    load_settings,
    require_credentials,
    select_board,
)
from trelloctl.infra.trello_client import Transport, TrelloClient
from trelloctl.output import success_envelope


class Need(IntEnum):
    """What an operation needs before it may run; each level includes the previous ones."""

    NONE = 0  # nothing: config errors are ignored (the handler may report them)
    CONFIG_EXISTS = 1  # config.toml must exist; its content is not checked
    CONFIG = 2  # config.toml must exist and be valid
    CREDENTIALS = 3  # plus api_key and token (file or environment)
    BOARD = 4  # plus a selected board (flag, environment or default)


@dataclass
class Outcome:
    """Result of an operation when the envelope alone is not enough.

    `text` replaces the JSON on stdout (human-readable output), `exit_code` overrides the
    code derived from the envelope.
    """

    envelope: dict[str, Any]
    text: str | None = None
    exit_code: int | None = None


@dataclass
class CommandContext:
    """Everything a handler needs; built by the CLI (or the MCP server) per invocation."""

    settings: Settings
    command: str = ""
    config_error: TrelloctlError | None = None
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    board_flag: str | None = None
    include_archived_boards: bool = False
    no_cache: bool = False
    transport: Transport | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(LOGGER_NAME))
    board_ref: str | None = None

    def client(self) -> TrelloClient:
        """Trello client built from the effective settings; `AUTH_MISSING` without credentials."""
        api_key, token = require_credentials(self.settings)
        return TrelloClient(
            self.settings.base_url,
            api_key,
            token,
            self.settings.timeout_seconds,
            transport=self.transport,
            logger=self.logger,
        )

    def success(
        self,
        data: Any,
        *,
        board_id: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Success envelope for this command (the dispatcher fills `elapsed_ms`)."""
        return success_envelope(data, self.command, board_id=board_id, meta=meta)


def fallback_settings(environ: Mapping[str, str], home: Path) -> Settings:
    """Defaults plus environment, for when `config.toml` cannot be loaded."""
    path = config_path_for(home)
    return Settings(
        home=home,
        config_path=path,
        config_found=path.is_file(),
        api_key=environ.get(API_KEY_ENV, ""),
        token=environ.get(TOKEN_ENV, ""),
        board_default="",
        base_url=DEFAULTS["http"]["base_url"],
        timeout_seconds=DEFAULTS["http"]["timeout_seconds"],
        metadata_ttl_seconds=DEFAULTS["cache"]["metadata_ttl_seconds"],
        cards_ttl_seconds=DEFAULTS["cache"]["cards_ttl_seconds"],
        max_upload_bytes=DEFAULTS["attachments"]["max_upload_bytes"],
        log_level=DEFAULTS["logging"]["level"],
        log_max_bytes=DEFAULTS["logging"]["max_bytes"],
        log_backup_count=DEFAULTS["logging"]["backup_count"],
        env_board=environ.get(BOARD_ENV, ""),
    )


def load_settings_lenient(
    environ: Mapping[str, str], home: Path
) -> tuple[Settings, TrelloctlError | None]:
    """Load the settings; an invalid file yields the defaults and the error to report later."""
    try:
        return load_settings(environ, home=home, require_file=False), None
    except TrelloctlError as exc:
        return fallback_settings(environ, home), exc


def check_preconditions(
    need: Need,
    settings: Settings,
    *,
    config_error: TrelloctlError | None = None,
    board_flag: str | None = None,
) -> str | None:
    """Validate, in the order of A-7, what an operation needs before calling the API.

    Order: `config.toml` (`CONFIG_NOT_FOUND`, `CONFIG_INVALID`) > credentials (`AUTH_MISSING`)
    > board (`BOARD_NOT_CONFIGURED`). Argument and working-directory checks happen earlier,
    in the adapter. Returns the selected board reference when `need` is `Need.BOARD`.
    """
    if need >= Need.CONFIG_EXISTS and not settings.config_found:
        raise TrelloctlError(
            "CONFIG_NOT_FOUND",
            f"Configuration file not found: {settings.config_path}. "
            "Run `trelloctl config init` to create it.",
            {"path": str(settings.config_path)},
        )
    if need >= Need.CONFIG and config_error is not None:
        raise config_error
    if need >= Need.CREDENTIALS:
        require_credentials(settings)
    if need >= Need.BOARD:
        return select_board(settings, board_flag)
    return None

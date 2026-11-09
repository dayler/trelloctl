"""`version`: the tool version, the interpreter version and the platform (RF-6)."""

from __future__ import annotations

import platform
from collections.abc import Mapping
from typing import Any

from trelloctl import __version__
from trelloctl.app.commands.context import CommandContext


def version(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    return ctx.success(
        {
            "version": __version__,
            "python": platform.python_version(),
            "platform": platform.platform(),
        }
    )

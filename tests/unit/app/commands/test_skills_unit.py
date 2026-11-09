"""Unit tests of the `skills` command handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from trelloctl.app.commands import skills as cmd
from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.domain.errors import TrelloctlError


def make_ctx(home: Path) -> CommandContext:
    settings, error = load_settings_lenient({}, home)
    ctx = CommandContext(settings=settings, config_error=error, environ={})
    ctx.command = "skills"
    return ctx


def test_parse_frontmatter() -> None:
    assert cmd.parse_frontmatter("---\nname: x\nversion: 1.2\n---\nbody: no\n") == {
        "name": "x",
        "version": "1.2",
    }
    assert cmd.parse_frontmatter("no frontmatter") == {}
    assert cmd.parse_frontmatter("") == {}


def test_install_rejects_scope_with_dir_and_unknown_values(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    for args in (
        {"harness": "claude", "scope": "user", "dir": str(tmp_path)},
        {"harness": "codex"},
        {"harness": "claude", "scope": "global"},
    ):
        with pytest.raises(TrelloctlError) as info:
            cmd.install(ctx, args)
        assert info.value.code == "USAGE"


def test_write_failure_is_home_not_writable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("file", encoding="utf-8")
    with pytest.raises(TrelloctlError) as info:
        cmd.export(make_ctx(tmp_path), {"dir": str(blocker / "sub")})
    assert info.value.code == "HOME_NOT_WRITABLE"


def test_export_creates_nested_directories(tmp_path: Path) -> None:
    envelope = cmd.export(make_ctx(tmp_path), {"dir": str(tmp_path / "a" / "b")})
    assert envelope["ok"] is True
    assert (tmp_path / "a" / "b" / "SKILL.md").read_text(encoding="utf-8") == cmd.skill_text()

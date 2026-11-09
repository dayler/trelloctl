"""Unit tests of the working-directory helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from trelloctl.infra import paths


def test_default_home_honours_override_and_falls_back(tmp_path: Path) -> None:
    assert paths.default_home({"TRELLOCTL_HOME": str(tmp_path / "h")}) == tmp_path / "h"
    assert paths.default_home({}).name == ".trelloctl"
    assert paths.default_home({"TRELLOCTL_HOME": ""}).name == ".trelloctl"


def test_default_home_reads_the_process_environment(
    trelloctl_home: Path,
) -> None:
    assert paths.default_home() == trelloctl_home


def test_ensure_home_creates_missing_parents(tmp_path: Path) -> None:
    home = paths.ensure_home(tmp_path / "a" / "b")
    assert (home / "logs").is_dir()
    assert list(home.glob(".write-test-*")) == []


def test_delete_owned_file_missing_returns_false(tmp_path: Path) -> None:
    paths.ensure_home(tmp_path)
    assert paths.delete_owned_file(tmp_path, tmp_path / "cache.db") is False
    assert paths.delete_owned_file(tmp_path, Path("attachments") / "b" / "c.bin") is False


def test_delete_owned_file_cache_companions_and_nested_attachment(tmp_path: Path) -> None:
    paths.ensure_home(tmp_path)
    nested = tmp_path / "attachments" / "b" / "c"
    nested.mkdir(parents=True)
    (nested / "1_x.txt").write_text("x", encoding="utf-8")
    (tmp_path / "cache.db-wal").write_bytes(b"w")
    assert paths.delete_owned_file(tmp_path, Path("attachments/b/c/1_x.txt")) is True
    assert paths.delete_owned_file(tmp_path, tmp_path / "cache.db-wal") is True


def test_delete_rejects_escape_with_dotdot(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths.ensure_home(home)
    (tmp_path / "victim.txt").write_text("v", encoding="utf-8")
    with pytest.raises(paths.PathNotAllowedError):
        paths.delete_owned_file(home, Path("attachments") / ".." / ".." / "victim.txt")
    assert (tmp_path / "victim.txt").exists()

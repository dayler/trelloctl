"""Unit tests of the package skeleton."""

from __future__ import annotations

import runpy

import pytest

from trelloctl import __version__
from trelloctl.cli import EXIT_OK, main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == EXIT_OK
    assert capsys.readouterr().out == f"trelloctl {__version__}\n"


def test_main_reads_sys_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["trelloctl", "--version"])
    assert main() == EXIT_OK


def test_dunder_main_delegates_to_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["trelloctl", "--version"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("trelloctl", run_name="__main__")
    assert exc.value.code == 0

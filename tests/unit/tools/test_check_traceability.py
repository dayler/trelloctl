"""Unit tests of the traceability checker entry point."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.test_m00_harness import make_repo

import check_traceability as ct


def test_main_reports_ok(tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    make_repo(tmp_path, plan_status=("🟢", "⚪"))
    assert ct.main([str(tmp_path)]) == 0
    out = capsysbinary.readouterr().out.decode()
    assert "traceability: OK" in out
    assert "WARNING" in out


def test_main_reports_failure(tmp_path: Path, capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    make_repo(tmp_path, tests='@pytest.mark.tc("TC-M99-001")\ndef test_x(): ...\n')
    assert ct.main([str(tmp_path)]) == 1
    assert b"traceability: FAILED" in capsysbinary.readouterr().out


def test_unknown_clarification_is_error(tmp_path: Path) -> None:
    make_repo(tmp_path, m00=("in_progress", "| TC-M00-001 | AC-1.1, A-77 | P | C | s | r |\n"))
    assert any("A-77" in e for e in ct.check(tmp_path).errors)


def test_range_in_manual_expands() -> None:
    refs = ct._refs("AC-12.1 a AC-12.3, RNF-2")
    assert {"AC-12.1", "AC-12.2", "AC-12.3", "RNF-2"} <= set(refs)

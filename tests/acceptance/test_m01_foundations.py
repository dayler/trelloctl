"""M01 acceptance tests: paths, errors, output envelope, logging and console."""

from __future__ import annotations

import ast
import io
import json
import logging
import re
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from trelloctl.domain.errors import CATALOG, ErrorClass, TrelloctlError, exit_code_for
from trelloctl.infra import paths
from trelloctl.infra.logging import (
    LOG_FILE,
    LOGGER_NAME,
    configure_logging,
    log_invocation,
    shutdown_logging,
)
from trelloctl.output import (
    configure_console,
    error_envelope,
    render,
    success_envelope,
    to_utc_iso,
)

SRC = Path(__file__).resolve().parents[2] / "src"
KEY = "0123456789abcdef0123456789abcdef"
TOKEN = "f" * 64


@pytest.fixture(autouse=True)
def _close_logging() -> Any:
    yield
    shutdown_logging()


# --- paths -----------------------------------------------------------------


@pytest.mark.tc("TC-M01-001")
def test_missing_subdirectories_are_created_and_files_kept(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text("keep", encoding="utf-8")
    (home / "cache.db").write_bytes(b"db")
    assert not (home / "logs").exists()
    assert not (home / "attachments").exists()

    assert paths.ensure_home(home) == home

    assert (home / "logs").is_dir()
    assert (home / "attachments").is_dir()
    assert (home / "config.toml").read_text(encoding="utf-8") == "keep"
    assert (home / "cache.db").read_bytes() == b"db"


@pytest.mark.tc("TC-M01-002")
def test_deleting_outside_owned_files_is_rejected(tmp_path: Path) -> None:
    home = tmp_path / "home"
    paths.ensure_home(home)
    config = home / "config.toml"
    config.write_text("secret", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    (home / "attachments" / "ok.bin").write_bytes(b"1")
    (home / "cache.db").write_bytes(b"db")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    for target in (
        outside,
        Path("..") / "outside.txt",
        config,
        home / "attachments",
        home / "logs",
    ):
        with pytest.raises(paths.PathNotAllowedError):
            paths.delete_owned_file(home, target)

    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before
    # Owned files are still deletable.
    assert paths.delete_owned_file(home, home / "attachments" / "ok.bin") is True
    assert paths.delete_owned_file(home, home / "cache.db") is True


@pytest.mark.tc("TC-M01-003")
def test_home_occupied_by_a_file(tmp_path: Path) -> None:
    occupied = tmp_path / "home"
    occupied.write_text("i am a file", encoding="utf-8")

    with pytest.raises(TrelloctlError) as raised:
        paths.ensure_home(occupied)

    assert raised.value.code == "HOME_NOT_WRITABLE"
    assert raised.value.error_class is ErrorClass.CONFIG
    assert raised.value.exit_code == 3


@pytest.mark.tc("TC-M01-004")
def test_home_write_probe_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()

    def deny(_home: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(paths, "_probe_writable", deny)

    with pytest.raises(TrelloctlError) as raised:
        paths.ensure_home(home)

    assert raised.value.code == "HOME_NOT_WRITABLE"
    assert raised.value.exit_code == 3


# --- output ----------------------------------------------------------------

_DATA = {"name": "Reunión ñandú", "words": ["á", "日本語", "中文"]}


@pytest.mark.tc("TC-M01-005")
def test_indented_envelope_is_unescaped_utf8_with_final_newline() -> None:
    text = render(success_envelope(_DATA, "card list", elapsed_ms=3))

    assert text.endswith("}\n")
    assert not text.endswith("\n\n")
    assert '\n  "ok": true,' in text
    assert '\n    "name": "Reunión ñandú"' in text
    assert "日本語" in text
    assert "\\u" not in text
    text.encode("utf-8")
    assert json.loads(text)["data"] == _DATA


@pytest.mark.tc("TC-M01-006")
def test_compact_envelope_is_one_line_and_equal_to_indented() -> None:
    envelope = success_envelope(_DATA, "card list", elapsed_ms=3)
    compact = render(envelope, compact=True)
    indented = render(envelope)

    assert compact.count("\n") == 1
    assert compact.endswith("\n")
    assert json.loads(compact) == json.loads(indented)
    assert compact == json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) + chr(10)


@pytest.mark.tc("TC-M01-007")
def test_control_characters_and_emoji_stay_valid_json() -> None:
    data = {"text": "a\x00b\x1f\tc\nd\r\x7f \U0001f680 \U0001f469‍\U0001f4bb"}
    for compact in (False, True):
        text = render(success_envelope(data, "x"), compact=compact)
        assert json.loads(text)["data"] == data
        assert text.count("\n") == (1 if compact else text.count("\n"))
        assert "\x00" not in text
        assert "\x1f" not in text


@pytest.mark.tc("TC-M01-008")
def test_error_envelope_shape() -> None:
    error = TrelloctlError(
        "MOVE_OUT_OF_RANGE",
        "Target list (index 7) is outside the board (5 lists).",
        {"from_index": 4, "to_index": 7, "list_count": 5},
    )

    envelope = json.loads(render(error_envelope(error, "card move", elapsed_ms=230)))

    assert envelope["ok"] is False
    assert "data" not in envelope
    assert envelope["error"]["code"] == "MOVE_OUT_OF_RANGE"
    assert envelope["error"]["message"] == "Target list (index 7) is outside the board (5 lists)."
    assert envelope["error"]["details"] == {"from_index": 4, "to_index": 7, "list_count": 5}
    assert envelope["meta"] == {"schema_version": "1", "command": "card move", "elapsed_ms": 230}


# --- errors ----------------------------------------------------------------

_PRD_CATALOG = {
    0: [],
    1: ["INTERNAL"],
    2: ["USAGE"],
    3: ["CONFIG_NOT_FOUND", "CONFIG_INVALID", "BOARD_NOT_CONFIGURED", "HOME_NOT_WRITABLE"],
    4: ["AUTH_MISSING", "AUTH_INVALID"],
    5: [
        "BOARD_NOT_FOUND",
        "LIST_NOT_FOUND",
        "CARD_NOT_FOUND",
        "LABEL_NOT_FOUND",
        "MEMBER_NOT_FOUND",
        "CHECKLIST_NOT_FOUND",
        "CHECKITEM_NOT_FOUND",
        "COMMENT_NOT_FOUND",
        "ATTACHMENT_NOT_FOUND",
        "ATTACHMENT_GONE",
        "AMBIGUOUS_BOARD",
        "AMBIGUOUS_LIST",
        "AMBIGUOUS_CARD",
        "AMBIGUOUS_LABEL",
        "AMBIGUOUS_MEMBER",
        "AMBIGUOUS_CHECKLIST",
        "AMBIGUOUS_CHECKITEM",
        "INBOX_UNAVAILABLE",
    ],
    6: ["MOVE_OUT_OF_RANGE", "CONFIRMATION_REQUIRED", "ATTACHMENT_TOO_LARGE"],
    7: ["API_ERROR", "RATE_LIMITED"],
    8: ["NETWORK_ERROR", "TIMEOUT"],
    9: ["CACHE_STALE"],
}


@pytest.mark.tc("TC-M01-009")
def test_catalog_matches_the_prd_table() -> None:
    expected = {code: ErrorClass(exit_) for exit_, codes in _PRD_CATALOG.items() for code in codes}
    assert expected == CATALOG
    assert sorted({int(c) for c in CATALOG.values()}) == list(range(1, 10))
    for code, exit_ in ((c, e) for e, cs in _PRD_CATALOG.items() for c in cs):
        error = TrelloctlError(code, "m")
        assert error.error_class is ErrorClass(exit_)
        assert error.exit_code == exit_ == exit_code_for(code)
    assert ErrorClass.SUCCESS == 0


@pytest.mark.tc("TC-M01-010")
def test_untyped_exception_becomes_internal() -> None:
    envelope = error_envelope(RuntimeError("boom with /secret/path"), "card get")

    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "INTERNAL"
    assert CATALOG["INTERNAL"] == 1
    assert "/secret/path" not in json.dumps(envelope)


# --- dates -----------------------------------------------------------------


@pytest.mark.tc("TC-M01-011")
def test_dates_are_serialized_as_utc_with_z() -> None:
    assert to_utc_iso("2026-11-09T12:30:45.123Z") == "2026-11-09T12:30:45.123Z"
    assert to_utc_iso("2026-11-09T12:30:45Z") == "2026-11-09T12:30:45Z"
    assert to_utc_iso(datetime(2026, 11, 9, 12, 30, tzinfo=UTC)) == "2026-11-09T12:30:00Z"
    minus3 = timezone(timedelta(hours=-3))
    assert to_utc_iso(datetime(2026, 11, 9, 9, 30, tzinfo=minus3)) == "2026-11-09T12:30:00Z"

    out = json.loads(render(success_envelope({"due": datetime(2026, 11, 9, tzinfo=UTC)}, "x")))
    assert out["data"]["due"] == "2026-11-09T00:00:00Z"


@pytest.mark.tc("TC-M01-012")
def test_offset_date_is_converted_to_utc() -> None:
    assert to_utc_iso("2026-11-09T18:00:00+05:30") == "2026-11-09T12:30:00Z"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert to_utc_iso(datetime(2026, 11, 9, 0, 15, tzinfo=ist)) == "2026-11-08T18:45:00Z"


# --- logging ---------------------------------------------------------------


def _log_lines(logs: Path) -> list[str]:
    return (logs / LOG_FILE).read_text(encoding="utf-8").splitlines()


@pytest.mark.tc("TC-M01-013")
def test_invocation_closing_line(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path)

    log_invocation(logger, "card list", 412, "ok")
    shutdown_logging()

    lines = _log_lines(tmp_path)
    assert len(lines) == 1
    line = lines[0]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z INFO pid=\d+ ", line)
    stamp = line.split(" ", 1)[0]
    assert datetime.fromisoformat(stamp).tzinfo is not None
    assert 'command="card list"' in line
    assert "duration_ms=412" in line
    assert "result=ok" in line


@pytest.mark.tc("TC-M01-014")
def test_rotation_keeps_exactly_backup_count_copies(tmp_path: Path) -> None:
    tmp_path = tmp_path / "logs"
    tmp_path.mkdir()
    logger = configure_logging(tmp_path, max_bytes=400, backup_count=2)

    for i in range(60):
        logger.info("line %03d %s", i, "x" * 40)
    shutdown_logging()

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == [LOG_FILE, f"{LOG_FILE}.1", f"{LOG_FILE}.2"]
    assert not (tmp_path / f"{LOG_FILE}.3").exists()
    newest = _log_lines(tmp_path)[-1]
    assert "line 059" in newest


@pytest.mark.tc("TC-M01-015")
def test_rotation_failure_warns_and_writing_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = configure_logging(tmp_path, max_bytes=300, backup_count=2, verbose=True)
    handler = logger.handlers[0]

    def locked(_source: str, _dest: str) -> None:
        raise PermissionError(32, "The process cannot access the file")

    monkeypatch.setattr(handler, "rotate", locked)

    for i in range(20):
        logger.info("line %03d %s", i, "y" * 40)  # must not raise
    shutdown_logging()

    text = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert "Log rotation failed" in text
    assert "line 019" in text
    assert "Log rotation failed" in capsys.readouterr().err


@pytest.mark.tc("TC-M01-016")
def test_key_and_token_are_masked_in_file_and_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    logger = configure_logging(tmp_path, level="debug", verbose=True, secrets=lambda: [KEY, TOKEN])

    logger.debug("plain message with %s and token %s", KEY, TOKEN)
    logger.info("GET /boards/x?key=%s&token=%s", KEY, TOKEN)
    logger.error("literal %s", f"{KEY}{TOKEN}")
    try:
        raise RuntimeError(f"failed with {TOKEN}")
    except RuntimeError:
        logger.exception("boom")
    shutdown_logging()

    file_text = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    stderr_text = capsys.readouterr().err
    for text in (file_text, stderr_text):
        assert KEY not in text
        assert TOKEN not in text
        assert "***" in text
    assert "boom" in file_text
    assert "RuntimeError" in file_text


@pytest.mark.tc("TC-M01-017")
def test_no_stdout_handler_with_or_without_verbose(tmp_path: Path) -> None:
    def streams(logger: logging.Logger) -> list[Any]:
        return [h.stream for h in logger.handlers if isinstance(h, logging.StreamHandler)]

    quiet = configure_logging(tmp_path, verbose=False)
    quiet_streams = streams(quiet)
    assert sys.stdout not in quiet_streams
    assert sys.stderr not in quiet_streams
    assert len(quiet.handlers) == 1

    loud = configure_logging(tmp_path, verbose=True)
    loud_streams = streams(loud)
    assert sys.stdout not in loud_streams
    assert sys.stderr in loud_streams
    assert len(loud.handlers) == 2
    assert logging.getLogger(LOGGER_NAME) is loud
    assert loud.propagate is False


# --- console ---------------------------------------------------------------


@pytest.mark.tc("TC-M01-018")
def test_cp1252_streams_are_reconfigured_to_utf8() -> None:
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline=chr(10))
    err = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", newline=chr(10))
    assert out.encoding == "cp1252"

    configure_console(out, err)

    assert out.encoding.lower().replace("-", "") == "utf8"
    assert err.encoding.lower().replace("-", "") == "utf8"
    out.write("日本語 ñ\n")
    out.flush()
    assert out.buffer.getvalue() == "日本語 ñ\n".encode()  # type: ignore[attr-defined]


# --- static analysis -------------------------------------------------------


@pytest.mark.tc("TC-M01-019")
def test_src_never_uses_a_shell_or_subprocess() -> None:
    offenders: list[str] = []
    files = sorted(SRC.rglob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            where = f"{path.relative_to(SRC)}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Import):
                offenders += [
                    f"{where} import {a.name}" for a in node.names if a.name == "subprocess"
                ]
            elif isinstance(node, ast.ImportFrom):
                if node.module == "subprocess":
                    offenders.append(f"{where} from subprocess")
                if node.module == "os":
                    offenders += [
                        f"{where} from os import {a.name}"
                        for a in node.names
                        if a.name in ("system", "popen")
                    ]
            elif isinstance(node, ast.Attribute):
                if isinstance(node.value, ast.Name) and (
                    (node.value.id == "os" and node.attr in ("system", "popen"))
                    or node.value.id == "subprocess"
                ):
                    offenders.append(f"{where} {node.value.id}.{node.attr}")
            elif (
                isinstance(node, ast.keyword)
                and node.arg == "shell"
                and isinstance(node.value, ast.Constant)
                and node.value.value is True
            ):
                offenders.append(f"{where} shell=True")
    assert offenders == []

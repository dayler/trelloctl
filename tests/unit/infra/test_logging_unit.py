"""Unit tests of the logging helpers."""

from __future__ import annotations

import logging
from pathlib import Path

from trelloctl.infra.logging import (
    LOG_FILE,
    MaskingFilter,
    configure_logging,
    log_invocation,
    mask_text,
    shutdown_logging,
)


def test_mask_text_values_and_query_params() -> None:
    assert mask_text("a SECRET b", ["SECRET", ""]) == "a *** b"
    assert mask_text("u?key=abc&Token=def x") == "u?key=***&Token=*** x"


def test_longer_secret_is_masked_first() -> None:
    assert mask_text("xx-ABC-xx", ["AB", "ABC"]) == "xx-***-xx"


def test_filter_masks_exc_text_without_exc_info() -> None:
    record = logging.LogRecord("n", logging.INFO, "f", 1, "m %s", ("S3CR3T",), None)
    record.exc_text = "trace S3CR3T"
    assert MaskingFilter(lambda: ["S3CR3T"]).filter(record)
    assert record.getMessage() == "m ***"
    assert record.exc_text == "trace ***"


def test_log_invocation_error_level_and_reconfiguration(tmp_path: Path) -> None:
    logger = configure_logging(tmp_path, level="warning")
    logger = configure_logging(tmp_path, level="info")
    assert len(logger.handlers) == 1
    log_invocation(logger, "card get", 7, "CARD_NOT_FOUND", ok=False)
    shutdown_logging()
    assert logger.handlers == []
    line = (tmp_path / LOG_FILE).read_text(encoding="utf-8")
    assert " ERROR " in line
    assert "result=CARD_NOT_FOUND" in line

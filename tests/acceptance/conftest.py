"""Acceptance-suite fixtures."""

from __future__ import annotations

import gc
import warnings
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _collect_garbage_quietly() -> Iterator[None]:
    """Collect cyclic garbage after each test, ignoring resource warnings of finalizers.

    Unclosed HTTP error objects of earlier tests would otherwise be finalized at an
    arbitrary later point and surface as an unraisable-exception failure there.
    """
    yield
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ResourceWarning)
        gc.collect()

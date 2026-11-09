"""Working directory: resolution, creation, write check and restricted deletion."""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from pathlib import Path

from trelloctl.domain.errors import TrelloctlError

HOME_ENV = "TRELLOCTL_HOME"
LOGS_DIR = "logs"
ATTACHMENTS_DIR = "attachments"
CACHE_DB = "cache.db"
# SQLite WAL companions of the cache file are owned by the tool too.
_CACHE_FILES = (CACHE_DB, f"{CACHE_DB}-wal", f"{CACHE_DB}-shm")


class PathNotAllowedError(ValueError):
    """Raised when asked to delete a path the tool does not own."""


def default_home(environ: Mapping[str, str] | None = None) -> Path:
    """`TRELLOCTL_HOME` if set, otherwise `~/.trelloctl`."""
    env = os.environ if environ is None else environ
    override = env.get(HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".trelloctl"


def _not_writable(home: Path, reason: str) -> TrelloctlError:
    return TrelloctlError(
        "HOME_NOT_WRITABLE",
        f"Working directory is not writable: {home} ({reason}).",
        {"path": str(home), "reason": reason},
    )


def _probe_writable(home: Path) -> None:
    probe = home / f".write-test-{uuid.uuid4().hex}"
    try:
        probe.write_bytes(b"")
    finally:
        probe.unlink(missing_ok=True)


def ensure_home(home: Path) -> Path:
    """Create the working directory and its missing subdirectories; check it is writable."""
    try:
        home.mkdir(parents=True, exist_ok=True)
        for name in (LOGS_DIR, ATTACHMENTS_DIR):
            (home / name).mkdir(exist_ok=True)
        _probe_writable(home)
    except OSError as exc:
        raise _not_writable(home, exc.strerror or type(exc).__name__) from exc
    return home


def delete_owned_file(home: Path, target: Path) -> bool:
    """Delete a file the tool generated: the cache database or a file under `attachments/`.

    Any other path, including absolute paths outside the home and `..` escapes, raises
    `PathNotAllowedError` and nothing is deleted. Returns False if the file did not exist.
    """
    root = home.resolve()
    candidate = (root / target).resolve()
    attachments = root / ATTACHMENTS_DIR
    allowed = candidate.parent == root and candidate.name in _CACHE_FILES
    allowed = allowed or (candidate != attachments and attachments in candidate.parents)
    if not allowed:
        raise PathNotAllowedError(f"refusing to delete a path the tool does not own: {target}")
    if not candidate.is_file():
        return False
    candidate.unlink()
    return True

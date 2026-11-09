"""Shared fixtures: isolation, network guard, fake clock and fake Trello."""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fakes.clock import FakeClock
from tests.fakes.trello import FakeTrello


class NetworkGuardError(AssertionError):
    """Raised when a test tries to reach a non-loopback host."""


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


class NetworkGuard:
    def __init__(self) -> None:
        self.violations: list[str] = []

    def check(self, address: Any) -> None:
        if isinstance(address, tuple) and address:
            host = str(address[0])
            if not _is_loopback(host):
                self.violations.append(host)
                raise NetworkGuardError(f"network guard: connection to non-loopback host {host!r}")

    def clear(self) -> None:
        self.violations.clear()


@pytest.fixture(autouse=True)
def network_guard(monkeypatch: pytest.MonkeyPatch) -> Iterator[NetworkGuard]:
    guard = NetworkGuard()
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        guard.check(address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        guard.check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    yield guard
    assert not guard.violations, f"network guard violations: {guard.violations}"


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Temporary TRELLOCTL_HOME, user home and working directory; no TRELLO* variables."""
    for name in list(os.environ):
        if name.startswith(("TRELLO", "TRELLOCTL")):
            monkeypatch.delenv(name)
    home = tmp_path / "trelloctl-home"
    user_home = tmp_path / "user-home"
    cwd = tmp_path / "cwd"
    for folder in (home, user_home, cwd):
        folder.mkdir()
    monkeypatch.setenv("TRELLOCTL_HOME", str(home))
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("USERPROFILE", str(user_home))
    monkeypatch.chdir(cwd)
    return home


@pytest.fixture
def trelloctl_home(isolated_env: Path) -> Path:
    return isolated_env


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def fake_trello() -> Iterator[FakeTrello]:
    with FakeTrello() as server:
        yield server

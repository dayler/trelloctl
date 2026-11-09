"""Shared helpers of the M14 acceptance tests: a healthy fake account and `doctor` runners."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m09_support import add_checklist
from tests.acceptance.m12_support import (  # noqa: F401
    BOARD,
    calls,
    card,
    close_caches,
    hide_inbox,
    seed_account,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import RecordedRequest

from trelloctl import doctor
from trelloctl.cli import main

ORDER = [
    "python",
    "working_directory",
    "config",
    "credentials",
    "dns",
    "tls",
    "token",
    "board",
    "cache",
    "mcp",
    "skill_user",
    "skill_project",
    "inbox",
]


def spec_url(fake: FakeTrello) -> str:
    return f"http://127.0.0.1:{fake.port}/swagger.v3.json"


@pytest.fixture(autouse=True)
def spec_on_fake(fake_trello: FakeTrello, monkeypatch: pytest.MonkeyPatch) -> None:
    """The specification URL points to the fake Trello for every test of the module."""
    monkeypatch.setattr(doctor, "OPENAPI_URL", spec_url(fake_trello))
    # DNS and TLS are simulated: no test resolves or connects to a real host.
    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)


def healthy(home: Path, fake: FakeTrello, *, board: str | None = BOARD) -> dict[str, str]:
    """Board `Work` with a card that has a checklist, an attachment and a comment; Inbox; config."""
    ids = seed_account(fake)
    ids["card"] = card(fake, ids["todo"], "First", 1, labels=(ids["bug"],), members=(ids["ana"],))
    ids["checklist"] = add_checklist(fake, ids["card"], "Steps", ("one", "two"))
    attachment = "a" + fake.state.new_id()[1:]
    fake.state.attachments[attachment] = {
        "id": attachment,
        "idCard": ids["card"],
        "name": "note.txt",
        "bytes": 3,
        "isUpload": True,
        "url": "https://trello.com/1/cards/x/attachments/y/download/note.txt",
    }
    comment = "a" + fake.state.new_id()[1:]
    fake.state.actions[comment] = {
        "id": comment,
        "type": "commentCard",
        "idMemberCreator": ids["ana"],
        "date": "2026-03-01T10:00:00.000Z",
        "data": {"text": "hello", "card": {"id": ids["card"]}},
    }
    write_config(home, fake.base_url, fake, board=board)
    return ids


def write_config(
    home: Path,
    base_url: str,
    fake: FakeTrello,
    *,
    board: str | None = None,
    key: str | None = None,
    token: str | None = None,
) -> None:
    lines = [
        "[auth]",
        f'api_key = "{fake.api_key if key is None else key}"',
        f'token = "{fake.token if token is None else token}"',
        "",
        "[http]",
        f'base_url = "{base_url}"',
    ]
    if board is not None:
        lines += ["", "[board]", f'default = "{board}"']
    (home / "config.toml").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))


def doctor_json(
    capsys: pytest.CaptureFixture[str], *extra: str
) -> tuple[int, dict[str, Any], dict[str, dict[str, Any]]]:
    """Run `doctor --json`; returns the exit code, the envelope and the checks by name."""
    code = main(["doctor", "--json", *extra])
    envelope = json.loads(capsys.readouterr().out)
    checks = {c["name"]: c for c in envelope["data"]["checks"]}
    return code, envelope, checks


def doctor_text(capsys: pytest.CaptureFixture[str], *extra: str) -> tuple[int, str]:
    code = main(["doctor", *extra])
    return code, capsys.readouterr().out


def paths_of(fake: FakeTrello) -> list[str]:
    return [r.path for r in fake.requests]


def smoke_names() -> list[str]:
    return [f"smoke GET {e.path}" for e in doctor.INVENTORY if e.smoke]


def requests_of(fake: FakeTrello, prefix: str) -> list[RecordedRequest]:
    return [r for r in fake.requests if r.path.startswith(prefix)]

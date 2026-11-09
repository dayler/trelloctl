"""Shared helpers of the M16 acceptance tests: the documented account and example extraction."""

from __future__ import annotations

import json
import re
import shlex
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m09_support import (
    BOARD,
    add_checklist,
    add_label,
    add_list,
    add_member,
    card,
    comment,
    seed_board,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.core import State
from tests.fakes.trello.inbox import seed_inbox

from trelloctl.cli import main

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / "README.md"
SKILL = ROOT / "src" / "trelloctl" / "skills" / "trelloctl" / "SKILL.md"

# Ids used by the documented examples; the seed below creates the objects with these ids.
TODO = "6a00000000000000000000a1"
DOING = "6a00000000000000000000a2"
DONE = "6a00000000000000000000a3"
REVIEW = "6b00000000000000000000c1"
CORE = "6b00000000000000000000c2"
COMMENT = "6c00000000000000000000d1"
ITEM = "6d00000000000000000000e1"
UPLOAD = "6e00000000000000000000f1"
LINK = "6e00000000000000000000f2"


def _rekey(table: dict[str, dict[str, Any]], old: str, new: str) -> str:
    item = table.pop(old)
    item["id"] = new
    table[new] = item
    return new


def seed_docs(fake: FakeTrello) -> dict[str, str]:
    """Board `Work` with the lists, cards, labels and Inbox that the documented examples use."""
    seed_board(fake)
    ids = {
        "todo": _rekey(fake.state.lists, add_list(fake, "To Do", 1), TODO),
        "doing": _rekey(fake.state.lists, add_list(fake, "Doing", 2), DOING),
        "done": _rekey(fake.state.lists, add_list(fake, "Done", 3), DONE),
        "bug": add_label(fake, "bug", "red"),
        "urgent": add_label(fake, "urgent", "yellow"),
        "ana": add_member(fake, "ana", "Ana Gomez"),
        "luis": add_member(fake, "luis", "Luis Perez"),
    }
    ids["review"] = _rekey(
        fake.state.cards,
        card(fake, TODO, "Review PR 42", 42, desc="Check the diff", labels=(ids["bug"],)),
        REVIEW,
    )
    ids["core"] = _rekey(
        fake.state.cards, card(fake, DOING, "CORE-78 fix login", 78, desc="Login fails"), CORE
    )
    checklist = add_checklist(fake, REVIEW, "QA", ("e2e tests", "unit tests", "a", "b"))
    fake.state.checklists[checklist]["checkItems"][0]["id"] = ITEM
    ids["checklist"] = checklist
    comment(fake, REVIEW, "First comment", "0" * 22 + "aa")  # written by the fake account
    _rekey(fake.state.actions, next(iter(fake.state.actions)), COMMENT)
    for ident, name, upload in ((UPLOAD, "report.pdf", True), (LINK, "spec", False)):
        fake.state.attachments[ident] = {
            "id": ident,
            "idCard": REVIEW,
            "name": name,
            "bytes": 3 if upload else 0,
            "date": "2026-11-02T09:00:00.000Z",
            "isUpload": upload,
            "url": (
                f"https://trello.com/1/cards/{REVIEW}/attachments/{ident}/download/report.pdf"
                if upload
                else "https://example.com/spec"
            ),
            "content": b"pdf" if upload else b"",
        }
    inbox_board, inbox_list = seed_inbox(fake.state)
    ids["inbox_label"] = add_label(fake, "idea", "green", inbox_board)
    card(
        fake,
        inbox_list,
        "Review November invoice",
        1,
        board_id=inbox_board,
    )
    return ids


def write_docs_config(home: Path, fake: FakeTrello, *, auth: bool = True) -> None:
    """Credentials, the fake as base URL and the default board."""
    key, token = (fake.api_key, fake.token) if auth else ("", "")
    text = (
        f'[auth]\napi_key = "{key}"\ntoken = "{token}"\n\n'
        f'[http]\nbase_url = "{fake.base_url}"\n\n[board]\ndefault = "{BOARD}"\n'
    )
    (home / "config.toml").write_bytes(text.encode("utf-8"))


# --- example extraction --------------------------------------------------------------------

FENCE = re.compile(r"^```([\w-]*)[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Example:
    """One `trelloctl ...` invocation of a document and the result the document shows."""

    source: str
    argv: tuple[str, ...]
    ok: bool
    code: str | None

    @property
    def id(self) -> str:
        return f"{self.source}: {' '.join(self.argv)}"


def _commands(block: str) -> list[tuple[str, ...]]:
    lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.startswith("#")]
    return [tuple(shlex.split(ln)[1:]) for ln in lines if ln.startswith("trelloctl ")]


def extract_examples(path: Path) -> list[Example]:
    """Every invocation in a `bash` block. A `json` block right after it states the expectation.

    Without a following envelope the example is expected to succeed.
    """
    text = path.read_text(encoding="utf-8")
    blocks = list(FENCE.finditer(text))
    found: list[Example] = []
    for index, block in enumerate(blocks):
        if block.group(1) != "bash":
            continue
        commands = _commands(block.group(2))
        if not commands:
            continue
        ok, code = True, None
        following = blocks[index + 1] if index + 1 < len(blocks) else None
        if (
            following is not None
            and following.group(1) == "json"
            and text[block.end() : following.start()].strip() == ""
        ):
            try:
                envelope = json.loads(following.group(2))
            except json.JSONDecodeError:
                envelope = {}
            if isinstance(envelope, dict) and "ok" in envelope:
                ok = bool(envelope["ok"])
                code = None if ok else envelope["error"]["code"]
        found.extend(Example(path.name, argv, ok, code) for argv in commands)
    return found


def run_cli(
    capsys: pytest.CaptureFixture[str], *argv: str
) -> tuple[int, dict[str, Any] | None, str]:
    """Run the CLI in process; the envelope is None when stdout is text."""
    code = main(list(argv))
    out = capsys.readouterr().out
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return code, None, out
    return code, parsed if isinstance(parsed, dict) else None, out


# --- isolated environments for tests that build several worlds ----------------------------


@dataclass
class Env:
    """A fresh home and fake Trello with the documented account, and a CLI runner."""

    home: Path
    fake: FakeTrello
    capsys: pytest.CaptureFixture[str]
    ids: dict[str, str]

    def run(self, *argv: str) -> tuple[int, dict[str, Any]]:
        code, envelope, out = run_cli(self.capsys, *argv)
        assert envelope is not None, out
        return code, envelope

    def write(self, **kwargs: Any) -> None:
        write_docs_config(self.home, self.fake, **kwargs)

    def reset(self) -> None:
        """Back to the freshly seeded account: empty home, cache and request log."""
        from trelloctl.infra import cache as cache_mod

        for cache in list(cache_mod._REGISTRY.values()):
            cache.close()
        for child in self.home.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        self.fake.state = State()
        self.fake.requests.clear()
        self.ids = seed_docs(self.fake)
        self.write()


@contextmanager
def environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    *,
    seed: bool = True,
) -> Iterator[Env]:
    """A new `TRELLOCTL_HOME` and fake Trello, so one test can provoke many scenarios."""
    home = tmp_path / f"home-{name}"
    home.mkdir()
    monkeypatch.setenv("TRELLOCTL_HOME", str(home))
    with FakeTrello() as fake:
        ids = seed_docs(fake) if seed else {}
        env = Env(home, fake, capsys, ids)
        env.write()
        yield env
    from trelloctl.infra import cache as cache_mod

    for cache in list(cache_mod._REGISTRY.values()):
        cache.close()


# Common RNF-10 heuristic: Spanish-only characters and a short list of Spanish function words.
SPANISH_CHARS = re.compile("[áéíóúñ¿¡]", re.IGNORECASE)
SPANISH_WORDS = re.compile(
    r"\b(el|los|las|del|para|con|una|por|que|pero|como|esta|este|cuando|tambien|sin)\b",
    re.IGNORECASE,
)


def assert_english(text: str, label: str) -> None:
    assert not SPANISH_CHARS.search(text), label
    assert not SPANISH_WORDS.search(text), (label, SPANISH_WORDS.search(text))

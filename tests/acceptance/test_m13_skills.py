"""M13 acceptance tests: the packaged skill and `skills list|install|export`."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

from trelloctl import __version__
from trelloctl.app.commands.config import CONFIG_TEMPLATE
from trelloctl.app.commands.skills import parse_frontmatter, skill_text
from trelloctl.cli import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_FILE = REPO_ROOT / "src" / "trelloctl" / "skills" / "trelloctl" / "SKILL.md"

# Common RNF-10 heuristic: Spanish-only characters and a short list of Spanish function words.
SPANISH_CHARS = re.compile("[áéíóúñ¿¡]", re.IGNORECASE)
SPANISH_WORDS = re.compile(
    r"\b(el|los|las|del|para|con|una|por|que|pero|como|esta|este|cuando|tambien|sin)\b",
    re.IGNORECASE,
)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def sections(text: str) -> dict[str, str]:
    parts = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    return {part.split("\n", 1)[0].strip(): part for part in parts}


@pytest.mark.tc("TC-M13-001")
def test_skills_list_shows_the_skill_with_the_package_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, envelope = run(capsys, "skills", "list")
    assert code == 0
    assert envelope["ok"] is True
    assert envelope["meta"]["command"] == "skills list"
    skills = envelope["data"]["skills"]
    assert [s["name"] for s in skills] == ["trelloctl"]
    assert skills[0]["version"] == __version__
    assert skills[0]["description"]


@pytest.mark.tc("TC-M13-002")
def test_frontmatter_has_name_description_and_package_version() -> None:
    text = SKILL_FILE.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    meta = parse_frontmatter(text)
    assert meta["name"] == "trelloctl"
    assert meta["description"].strip()
    assert meta["version"] == __version__
    assert skill_text() == text


@pytest.mark.tc("TC-M13-003")
def test_wheel_includes_the_skill(tmp_path: Path) -> None:
    out = tmp_path / "dist"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "hatchling", "build", "-t", "wheel", "-d", str(out)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    wheels = list(out.glob("trelloctl-*.whl"))
    assert len(wheels) == 1
    with zipfile.ZipFile(wheels[0]) as wheel:
        assert "trelloctl/skills/trelloctl/SKILL.md" in wheel.namelist()
        packaged = wheel.read("trelloctl/skills/trelloctl/SKILL.md")
    # Compare bytes: the wheel carries the checkout copy, whatever its line endings.
    assert packaged == SKILL_FILE.read_bytes()


@pytest.mark.tc("TC-M13-004")
def test_install_claude_defaults_to_the_user_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, envelope = run(capsys, "skills", "install", "--harness", "claude")
    target = Path.home() / ".claude" / "skills" / "trelloctl" / "SKILL.md"
    assert code == 0
    assert envelope["ok"] is True
    assert Path(envelope["data"]["path"]) == target
    assert target.read_text(encoding="utf-8") == skill_text()


@pytest.mark.tc("TC-M13-005")
def test_install_project_scope_and_explicit_dir(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, envelope = run(capsys, "skills", "install", "--harness", "claude", "--scope", "project")
    project_target = Path.cwd() / ".claude" / "skills" / "trelloctl" / "SKILL.md"
    assert code == 0
    assert envelope["ok"] is True
    assert project_target.read_text(encoding="utf-8") == skill_text()
    assert not (Path.home() / ".claude").exists()

    chosen = tmp_path / "somewhere" / "skill"
    code, envelope = run(capsys, "skills", "install", "--harness", "claude", "--dir", str(chosen))
    assert code == 0
    assert envelope["ok"] is True
    assert (chosen / "SKILL.md").read_text(encoding="utf-8") == skill_text()


@pytest.mark.tc("TC-M13-006")
def test_install_over_an_existing_skill_without_force_is_usage(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    existing = tmp_path / "skill" / "SKILL.md"
    existing.parent.mkdir()
    existing.write_text("mine", encoding="utf-8")
    code, envelope = run(
        capsys, "skills", "install", "--harness", "claude", "--dir", str(existing.parent)
    )
    assert code == 2
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "USAGE"
    assert existing.read_text(encoding="utf-8") == "mine"


@pytest.mark.tc("TC-M13-007")
def test_install_over_an_existing_skill_with_force_overwrites(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    existing = tmp_path / "skill" / "SKILL.md"
    existing.parent.mkdir()
    existing.write_text("mine", encoding="utf-8")
    code, envelope = run(
        capsys, "skills", "install", "--harness", "claude", "--dir", str(existing.parent), "--force"
    )
    assert code == 0
    assert envelope["data"]["overwritten"] is True
    assert existing.read_text(encoding="utf-8") == skill_text()


@pytest.mark.tc("TC-M13-008")
def test_install_for_another_harness_is_usage(capsys: pytest.CaptureFixture[str]) -> None:
    code, envelope = run(capsys, "skills", "install", "--harness", "codex")
    assert code == 2
    assert envelope["error"]["code"] == "USAGE"
    assert not (Path.home() / ".claude").exists()


@pytest.mark.tc("TC-M13-009")
def test_install_scope_with_dir_is_usage(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "x"
    code, envelope = run(
        capsys, "skills", "install", "--harness", "claude", "--scope", "user", "--dir", str(target)
    )
    assert code == 2
    assert envelope["error"]["code"] == "USAGE"
    assert not target.exists()
    assert not (Path.home() / ".claude").exists()


@pytest.mark.tc("TC-M13-010")
def test_export_writes_the_skill_to_the_directory(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    target = tmp_path / "exported"
    code, envelope = run(capsys, "skills", "export", "--dir", str(target))
    assert code == 0
    assert envelope["ok"] is True
    assert Path(envelope["data"]["path"]) == target / "SKILL.md"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == skill_text()


@pytest.mark.tc("TC-M13-011")
def test_skill_has_the_ten_sections_with_their_commands() -> None:
    found = sections(skill_text())
    assert list(found) == [
        "When to use",
        "Board",
        "Cards",
        "Card details",
        "Attachments",
        "Inbox",
        "Cache",
        "MCP",
        "Output",
        "Rules",
    ]
    expected = {
        "Board": [
            "board show",
            "board lists",
            "board labels",
            "board members",
            "board activity",
            "board list create | rename | archive | unarchive | move | move-cards | archive-cards",
            "board label create | update | remove --yes",
        ],
        "Cards": [
            "card create",
            "card get",
            "card list",
            "card search",
            "card comments",
            "card update",
            "card move",
            "card copy",
            "card done",
            "card undone",
            "card archive",
            "card unarchive",
            "--left",
            "--right",
            "--full",
        ],
        "Card details": [
            "card comment add | edit | remove --yes",
            "card label add | remove | set",
            "card member add | remove | set",
            "card checklist list | add | rename | remove --yes",
            "card checklist item add | check | uncheck | rename | remove --yes",
            "@username",
        ],
        "Attachments": [
            "card attachments list",
            "card attachments download",
            "card attachments add",
            "card attachments clear",
            "attachments.max_upload_bytes",
            "isUpload",
        ],
        "Inbox": ["inbox list", "inbox add", "inbox move"],
        "Cache": ["cache reload", "cache status", "cache clear", "--fresh", "meta.cache"],
        "MCP": ["mcp serve", "trelloctl[mcp]", "experimental", "yes: true", "mcpServers"],
        "Output": ["--limit", "--fields", "--compact", "Exit codes"],
        "When to use": ["--board", "AMBIGUOUS_", "--include-archived-boards"],
    }
    for name, needles in expected.items():
        body = found[name]
        for needle in needles:
            assert needle in body, f"{needle!r} missing from section {name!r}"
    for name in ("Board", "Cards", "Card details", "Attachments", "Inbox", "Cache", "MCP"):
        assert "```json" in found[name], f"section {name!r} has no example output"


@pytest.mark.tc("TC-M13-012")
def test_skill_rules() -> None:
    rules = " ".join(sections(skill_text())["Rules"].split())
    assert "Archive instead of deleting" in rules
    assert "--yes" in rules
    assert "explicitly confirmed" in rules
    assert "--dry-run" in rules
    assert "card move --left/--right" in rules
    assert "board list move" in rules
    assert "Never read, edit or print `config.toml`" in rules


@pytest.mark.tc("TC-M13-013")
def test_skill_and_config_template_recommend_email_to_board_and_slack() -> None:
    inbox = sections(skill_text())["Inbox"]
    for text in (inbox, CONFIG_TEMPLATE):
        flat = " ".join(text.replace("# ", " ").split())
        assert "email-to-board" in flat
        assert "Slack" in flat
        assert "working board" in flat


@pytest.mark.tc("TC-M13-014")
def test_skill_text_is_english() -> None:
    text = skill_text()
    assert not SPANISH_CHARS.search(text)
    assert not SPANISH_WORDS.search(text)


@pytest.mark.tc("TC-M13-015")
def test_skills_commands_work_without_config_toml(
    capsys: pytest.CaptureFixture[str], trelloctl_home: Path, tmp_path: Path
) -> None:
    assert not (trelloctl_home / "config.toml").exists()
    for argv in (
        ("skills", "list"),
        ("skills", "install", "--harness", "claude"),
        ("skills", "export", "--dir", str(tmp_path / "out")),
    ):
        code, envelope = run(capsys, *argv)
        assert code == 0, argv
        assert envelope["ok"] is True
    assert not (trelloctl_home / "config.toml").exists()

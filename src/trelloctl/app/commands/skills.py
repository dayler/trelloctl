"""`skills` commands: list, install and export the packaged skill (RF-26)."""

from __future__ import annotations

from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from trelloctl import __version__
from trelloctl.app.commands.context import CommandContext
from trelloctl.domain.errors import TrelloctlError

SKILL_NAME = "trelloctl"
SKILL_FILE = "SKILL.md"
HARNESSES = ("claude",)
SCOPES = ("user", "project")


def skill_text() -> str:
    """The packaged `SKILL.md`."""
    resource = resources.files("trelloctl").joinpath("skills", SKILL_NAME, SKILL_FILE)
    return resource.read_text(encoding="utf-8")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Top-level `key: value` pairs of the leading `---` block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def list_skills(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """The skills shipped with the package."""
    meta = parse_frontmatter(skill_text())
    return ctx.success(
        {
            "skills": [
                {
                    "name": meta.get("name", SKILL_NAME),
                    "version": meta.get("version", __version__),
                    "description": meta.get("description", ""),
                }
            ]
        }
    )


def _write(target: Path, text: str) -> None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        raise TrelloctlError(
            "HOME_NOT_WRITABLE",
            f"Cannot write {target}: {exc.strerror or type(exc).__name__}.",
            {"path": str(target)},
        ) from exc


def _install_dir(args: Mapping[str, Any]) -> Path:
    directory = args.get("dir")
    scope = args.get("scope")
    if directory and scope:
        raise TrelloctlError("USAGE", "--scope and --dir are mutually exclusive.")
    if directory:
        return Path(str(directory)).expanduser()
    if scope == "project":
        return Path.cwd() / ".claude" / "skills" / SKILL_NAME
    return Path.home() / ".claude" / "skills" / SKILL_NAME


def install(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Copy the skill to the harness skills directory without overwriting unless `--force`."""
    harness = args.get("harness")
    if harness not in HARNESSES:
        raise TrelloctlError(
            "USAGE",
            f"Unsupported harness {harness!r}; only `claude` installs directly. "
            "Use `skills export --dir <path>` for other harnesses.",
            {"harness": harness},
        )
    scope = args.get("scope")
    if scope is not None and scope not in SCOPES:
        raise TrelloctlError("USAGE", "--scope must be user or project.", {"scope": scope})
    target = _install_dir(args) / SKILL_FILE
    existed = target.exists()
    if existed and not args.get("force"):
        raise TrelloctlError(
            "USAGE",
            f"{target} already exists; use --force to overwrite it.",
            {"path": str(target)},
        )
    _write(target, skill_text())
    return ctx.success(
        {
            "name": SKILL_NAME,
            "harness": harness,
            "path": str(target),
            "version": __version__,
            "installed": True,
            "overwritten": existed,
        }
    )


def export(ctx: CommandContext, args: Mapping[str, Any]) -> dict[str, Any]:
    """Write `SKILL.md` to any directory, for harnesses that accept Markdown instructions."""
    target = Path(str(args["dir"])).expanduser() / SKILL_FILE
    _write(target, skill_text())
    return ctx.success(
        {"name": SKILL_NAME, "path": str(target), "version": __version__, "exported": True}
    )

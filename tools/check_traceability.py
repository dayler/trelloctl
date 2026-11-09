"""Traceability checker: PRD criteria, milestone test cases and test markers.

Rules (docs/plan/01-estrategia-de-pruebas.md):
1. Every AC and RNF of the PRD appears in at least one TC (milestones or manual).
2. Every TC of an ``in_review`` or ``done`` milestone has a marked test.
3. No ``tc`` marker points to an unknown TC.
4. No ``done`` milestone has dependencies that are not ``done``.
5. Status mismatches between the master plan and a milestone are warnings.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

NBHYPHEN = "‑"
STATUS_EMOJI = {
    "⚪": "pending",
    "\U0001f7e1": "in_progress",
    "\U0001f535": "in_review",
    "\U0001f7e2": "done",
    "\U0001f534": "blocked",
}
TC_ROW = re.compile(r"^\|\s*(TC-M\d{2}-\d{3})\s*\|\s*([^|]*)\|", re.M)
MAN_ROW = re.compile(r"^\|\s*(TC-MAN-\d{2})\s*\|[^|]*\|\s*([^|]*)\|", re.M)
AC_DEF = re.compile(r"^[-*]\s+(AC-\d+\.\d+)\b", re.M)
RNF_DEF = re.compile(r"^\|\s*(RNF-\d+)\s*\|", re.M)
AC_RANGE = re.compile(r"(AC-(\d+)\.(\d+))\s+a\s+AC-\d+\.(\d+)")
TC_ID = re.compile(r"TC-(?:M\d{2}-\d{3}|MAN-\d{2})")
REF = re.compile(r"AC-\d+\.\d+|RNF-\d+|A-\d+")


@dataclass
class Result:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Milestone:
    id: str
    status: str
    depends_on: list[str]
    tcs: dict[str, list[str]]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace(NBHYPHEN, "-")


def _frontmatter(text: str) -> dict[str, str]:
    found = re.match(r"---\r?\n(.*?)\r?\n---", text, re.S)
    data: dict[str, str] = {}
    if found:
        for line in found.group(1).splitlines():
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
    return data


def _load_milestone(path: Path) -> Milestone:
    text = _read(path)
    meta = _frontmatter(text)
    deps = re.findall(r"M\d{2}", meta.get("depends_on", ""))
    tcs = {m.group(1): REF.findall(m.group(2)) for m in TC_ROW.finditer(text)}
    return Milestone(meta.get("id", path.stem[:3]), meta.get("status", ""), deps, tcs)


def _refs(cell: str) -> list[str]:
    refs = REF.findall(cell)
    for m in AC_RANGE.finditer(cell):
        refs.extend(f"AC-{m.group(2)}.{n}" for n in range(int(m.group(3)), int(m.group(4)) + 1))
    return refs


def _markers(tests_dir: Path) -> set[str]:
    found: set[str] = set()
    for path in sorted(tests_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tc"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "mark"
            ):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        found.update(TC_ID.findall(arg.value))
    return found


def check(root: Path) -> Result:
    """Run all rules against the repository at ``root``."""
    result = Result()
    docs = root / "docs"
    prd = _read(docs / "PRD.md")
    master = _read(docs / "plan" / "00-plan-maestro.md")
    manual = _read(docs / "tc-manuales-trello.md")
    milestones = [_load_milestone(p) for p in sorted((docs / "plan").glob("M[0-9][0-9]-*.md"))]
    clarifications = set(re.findall(r"^\|\s*(A-\d+)\s*\|", master, re.M))

    covered: set[str] = set()
    all_tcs: dict[str, str] = {}
    for ms in milestones:
        for tc, refs in ms.tcs.items():
            all_tcs[tc] = ms.id
            covered.update(r for r in refs if not r.startswith("A-"))
            for ref in refs:
                if ref.startswith("A-") and ref not in clarifications:
                    result.errors.append(f"{tc}: unknown clarification {ref}")
    for m in MAN_ROW.finditer(manual):
        all_tcs[m.group(1)] = "MAN"
        covered.update(_refs(m.group(2)))

    for ac in sorted(set(AC_DEF.findall(prd)) | set(RNF_DEF.findall(prd))):
        if ac not in covered:
            result.errors.append(f"{ac}: no test case covers it")

    marked = _markers(root / "tests")
    for ms in milestones:
        if ms.status in ("in_review", "done"):
            for tc in ms.tcs:
                if tc not in marked:
                    result.errors.append(
                        f"{tc}: milestone {ms.id} is {ms.status} but no test is marked"
                    )
    for tc in sorted(marked - set(all_tcs)):
        result.errors.append(f"{tc}: orphan marker, no such test case")

    status = {ms.id: ms.status for ms in milestones}
    for ms in milestones:
        if ms.status == "done":
            for dep in ms.depends_on:
                if status.get(dep) != "done":
                    result.errors.append(
                        f"{ms.id} is done but depends on {dep} ({status.get(dep)})"
                    )

    for row in re.finditer(r"^\|\s*\[\[(M\d{2})-[^\n]*$", master, re.M):
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", row.group(0).strip("|"))]
        ms_id = row.group(1)
        emoji = cells[8] if len(cells) > 8 else ""
        plan_status = STATUS_EMOJI.get(emoji)
        if ms_id in status and plan_status != status[ms_id]:
            result.warnings.append(
                f"{ms_id}: master plan says {plan_status}, milestone says {status[ms_id]}"
            )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path(__file__).resolve().parent.parent
    result = check(root)
    for line in result.warnings:
        sys.stdout.buffer.write(f"WARNING: {line}\n".encode())
    for line in result.errors:
        sys.stdout.buffer.write(f"ERROR: {line}\n".encode())
    sys.stdout.buffer.write(b"traceability: " + (b"OK\n" if result.ok else b"FAILED\n"))
    sys.stdout.buffer.flush()
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())

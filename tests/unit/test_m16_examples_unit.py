"""Unit tests of the example extractor used by the M16 documentation checks."""

from __future__ import annotations

from pathlib import Path

from tests.acceptance.m16_support import Example, extract_examples

DOC = """\
# Title

```bash
pipx install trelloctl
```

```bash
trelloctl board show
```

```json
{"ok": true, "data": {}}
```

```bash
trelloctl card move "Review PR 42" --right 9
```

```json
{"ok": false, "error": {"code": "MOVE_OUT_OF_RANGE"}}
```

```bash
# a comment
trelloctl card get 123
trelloctl card archive 123
```

Prose between the command and the output breaks the pairing.

```bash
trelloctl version
```

Text.

```json
{"ok": false, "error": {"code": "X"}}
```

```text
trelloctl card ignored
```
"""


def test_extracts_commands_and_their_expectation(tmp_path: Path) -> None:
    path = tmp_path / "doc.md"
    path.write_text(DOC, encoding="utf-8")
    found = extract_examples(path)
    assert found == [
        Example("doc.md", ("board", "show"), True, None),
        Example(
            "doc.md", ("card", "move", "Review PR 42", "--right", "9"), False, "MOVE_OUT_OF_RANGE"
        ),
        Example("doc.md", ("card", "get", "123"), True, None),
        Example("doc.md", ("card", "archive", "123"), True, None),
        Example("doc.md", ("version",), True, None),
    ]
    assert found[0].id == "doc.md: board show"

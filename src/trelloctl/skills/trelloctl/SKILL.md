---
name: trelloctl
description: Operate Trello boards from the command line with trelloctl, which prints one structured JSON envelope per command. Use it for any task on a Trello board - reading boards, lists and cards, searching, creating, moving, labelling, commenting, checklists, attachments and the personal Inbox.
version: 0.1.0
---

# trelloctl

`trelloctl` is a command line tool that operates Trello boards. Every command prints exactly
one JSON envelope on stdout and exits with a code that classifies the result. Logs go to
stderr and to a log file, never to stdout.

Envelope on success: `{"ok": true, "data": ..., "meta": {...}}`.
Envelope on failure: `{"ok": false, "error": {"code", "message", "details"}, "meta": {...}}`.

## When to use

Use trelloctl for any task on a Trello board: reading it, searching cards, creating or moving
them, managing labels, checklists, comments and attachments, or handling the personal Inbox.

Choosing the board:

- `--board <value>` accepts a board id, a shortLink, a URL or a name. Without it, trelloctl
  uses `TRELLO_BOARD`, then `[board].default` in `config.toml`.
- Prefer ids over names. Names can repeat; ids never do. Take ids from earlier output.
- By default a name only matches active boards. Add `--include-archived-boards` to also match
  archived boards; a name shared by an active and an archived board then fails with
  `AMBIGUOUS_BOARD`. An id, shortLink or URL always reaches an archived board.
- Every `AMBIGUOUS_*` error (`AMBIGUOUS_BOARD`, `AMBIGUOUS_LIST`, `AMBIGUOUS_CARD`, ...) carries
  `error.details.candidates` and executes nothing. Do not guess: pick one candidate id and
  repeat the command with that id, or ask the user which one is meant.

```bash
trelloctl --board 5f1b2c3d4e5f6a7b8c9d0e1f board show
```

```json
{"ok": true, "data": {"id": "5f1b2c3d4e5f6a7b8c9d0e1f", "name": "Work", "url": "https://trello.com/b/AbCdEf/work", "closed": false}, "meta": {"schema_version": "1", "command": "board show", "board_id": "5f1b2c3d4e5f6a7b8c9d0e1f", "elapsed_ms": 412, "cache": {"hit": false, "age_seconds": 0, "stale": false}}}
```

## Board

Commands:

- `board show`, `board lists [--all]`, `board labels`, `board members`, `board activity`
- `board list create | rename | archive | unarchive | move | move-cards | archive-cards`
- `board label create | update | remove --yes`

`board lists` returns the lists in board order. Each list has an `index` that starts at 0 for
the leftmost list. `--left N` and `--right N` in move commands count positions from that
order, so read `board lists` first when you need to know where a list or card will land.
`--all` also returns archived lists.

```bash
trelloctl board lists
```

```json
{"ok": true, "data": {"lists": [{"id": "6a00000000000000000000a1", "name": "To Do", "index": 0, "closed": false}, {"id": "6a00000000000000000000a2", "name": "Doing", "index": 1, "closed": false}]}, "meta": {"schema_version": "1", "command": "board lists", "board_id": "5f1b2c3d4e5f6a7b8c9d0e1f", "elapsed_ms": 38, "cache": {"hit": true, "age_seconds": 42, "stale": false}}}
```

```bash
trelloctl board list move "Doing" --right 1 --dry-run
```

```json
{"ok": true, "data": {"dry_run": true, "from_index": 1, "to_index": 2, "list_count": 4}, "meta": {"schema_version": "1", "command": "board list move", "elapsed_ms": 12}}
```

## Cards

Commands:

- `card create`, `card get [--full]`, `card list [--include-archived]`, `card search <pattern> [--in ...]`
- `card comments`, `card update`, `card copy`, `card done`, `card undone`
- `card move [--to <list> | --left N | --right N] [--clamp] [--dry-run]`
- `card archive`, `card unarchive`

`card move --left N` and `--right N` move the card to the list N positions to the left or
to the right of its current list. A target outside the board fails with `MOVE_OUT_OF_RANGE`
unless `--clamp` stops it at the first or last list. `card get --full` adds comments,
checklists, attachments and members.

Search patterns use `*` as a wildcard and match case-insensitively. `--in` chooses where to
look: any of `name`, `desc`, `comments`. Without `--in` it searches names.

```bash
trelloctl card create --list "To Do" --name "Review PR 42" --labels bug,urgent
```

```json
{"ok": true, "data": {"id": "6b00000000000000000000c1", "name": "Review PR 42", "list": {"id": "6a00000000000000000000a1", "name": "To Do"}, "labels": ["bug", "urgent"], "url": "https://trello.com/c/Xy12Ab/42-review-pr-42"}, "meta": {"schema_version": "1", "command": "card create", "board_id": "5f1b2c3d4e5f6a7b8c9d0e1f", "elapsed_ms": 530}}
```

```bash
trelloctl card search "core-78*" --in name,desc,comments --limit 5
```

```json
{"ok": true, "data": {"cards": [{"id": "6b00000000000000000000c2", "name": "CORE-78 fix login", "list": "Doing", "matched_in": ["name"]}]}, "meta": {"schema_version": "1", "command": "card search", "board_id": "5f1b2c3d4e5f6a7b8c9d0e1f", "elapsed_ms": 61, "cache": {"hit": true, "age_seconds": 12, "stale": false}, "truncated": false}}
```

```bash
trelloctl card move 6b00000000000000000000c2 --right 1 --dry-run
```

```json
{"ok": true, "data": {"dry_run": true, "from_list": "Doing", "to_list": "Done", "from_index": 1, "to_index": 2}, "meta": {"schema_version": "1", "command": "card move", "elapsed_ms": 9}}
```

## Card details

Commands:

- `card comment add | edit | remove --yes`
- `card label add | remove | set`
- `card member add | remove | set`
- `card checklist list | add | rename | remove --yes`
- `card checklist item add | check | uncheck | rename | remove --yes`

To mention a user in a comment write `@username` in the text; Trello notifies that member.
Use `card label set` or `card member set` to replace the whole set in one call, and `add` or
`remove` to change one entry. `card checklist item check` and `uncheck` mark items done or
not done; give the card, the checklist and the item by id when names may repeat.

```bash
trelloctl card comment add 6b00000000000000000000c1 "Looks good @ana, merging today"
```

```json
{"ok": true, "data": {"id": "6c00000000000000000000d1", "text": "Looks good @ana, merging today", "card_id": "6b00000000000000000000c1"}, "meta": {"schema_version": "1", "command": "card comment add", "elapsed_ms": 301}}
```

```bash
trelloctl card checklist item check "Review PR 42" QA "e2e tests"
```

```json
{"ok": true, "data": {"checklist": "QA", "item": {"id": "6d00000000000000000000e1", "name": "e2e tests", "state": "complete"}}, "meta": {"schema_version": "1", "command": "card checklist item check", "elapsed_ms": 280}}
```

## Attachments

Commands:

- `card attachments list`
- `card attachments download (--all | --id <attachment>)`
- `card attachments add (--file <path> | --url <url>)`
- `card attachments clear`

`card attachments add --file` is limited by `attachments.max_upload_bytes` in `config.toml`
(10 MB by default); a larger file fails with `ATTACHMENT_TOO_LARGE`. Each listed attachment
has `isUpload`: true for files stored in Trello, false for links. Only uploads can be
downloaded; a link has no file to fetch. `card attachments clear` deletes the downloaded
local copies, never the attachments in Trello.

```bash
trelloctl card attachments list 6b00000000000000000000c1
```

```json
{"ok": true, "data": {"attachments": [{"id": "6e00000000000000000000f1", "name": "report.pdf", "bytes": 48213, "isUpload": true}, {"id": "6e00000000000000000000f2", "name": "spec", "url": "https://example.com/spec", "isUpload": false}]}, "meta": {"schema_version": "1", "command": "card attachments list", "elapsed_ms": 95, "cache": {"hit": false, "age_seconds": 0, "stale": false}}}
```

## Inbox

Commands:

- `inbox list`
- `inbox add --name <text> [--due <date>]`
- `inbox move <card> --to <list> [--pos top|bottom|<index>]`

The Inbox is the personal Trello Inbox: cards that arrived by email, Slack or Siri. Use
`inbox list` to see what is waiting and `inbox move` to file a card into a list of the working
board. The Inbox is not part of the documented Trello API and can stop working;
`INBOX_UNAVAILABLE` means it cannot be reached.

Recommend to the user: point the email-to-board address of Trello and the Trello app for
Slack at a list of the working board, so those cards land where trelloctl works. The personal
Inbox keeps the rest.

```bash
trelloctl inbox add --name "Review November invoice" --due 2026-11-15
```

```json
{"ok": true, "data": {"id": "6f00000000000000000000a9", "name": "Review November invoice", "due": "2026-11-15T12:00:00.000Z"}, "meta": {"schema_version": "1", "command": "inbox add", "elapsed_ms": 340}}
```

## Cache

Commands:

- `cache reload [--list <list> | --card <card> | --archived]`
- `cache status`
- `cache clear [--attachments]`

Reads are served from a local cache: metadata (lists, labels, members, boards) for 30
minutes and cards for 5 minutes. Add `--fresh` to a read to bypass the cache for that call,
or `--no-cache` to neither read nor write it. `meta.cache` is present on every read:
`hit` tells whether the cache answered, `age_seconds` how old the data is, `stale` whether
the TTL expired. If `meta.cache.error` is set, the refresh failed and the data may be old
(exit code 9, `CACHE_STALE`, with `ok: true`). Writes made through trelloctl keep the
cache consistent.

```bash
trelloctl cache status
```

```json
{"ok": true, "data": {"path": "/home/user/.trelloctl/cache.db", "boards": [{"board_id": "5f1b2c3d4e5f6a7b8c9d0e1f", "cards": 120, "age_seconds": 42}]}, "meta": {"schema_version": "1", "command": "cache status", "elapsed_ms": 7}}
```

## MCP

Command: `mcp serve` (experimental; needs the extra `trelloctl[mcp]`).

`trelloctl mcp serve` runs an MCP server over stdio. It exposes the same operations as tools
named in snake_case (`card_move`, `board_lists`), each returning the same envelope. Where the
CLI requires `--yes`, the tool requires `yes: true`. The commands `config *`, `doctor`,
`version`, `skills *`, `cache clear` and `card attachments clear` are not exposed.
Every tool also takes the optional `board` and `include_archived_boards`. The value `-` (stdin)
for `desc` and `text` is refused with `USAGE` because stdin is the protocol channel.

Register it once in the harness:

```json
{
  "mcpServers": {
    "trelloctl": { "command": "trelloctl", "args": ["mcp", "serve"] }
  }
}
```

Example of a tool result (`card_get`):

```json
{"ok": true, "data": {"id": "6b00000000000000000000c2", "name": "CORE-78 fix login"}, "meta": {"schema_version": "1", "command": "card get", "elapsed_ms": 54}}
```

## Output

Every envelope has `meta.schema_version` and `meta.command`. Exit codes:

| Code | Meaning |
| ---- | ------- |
| 0 | success |
| 1 | generic error (`INTERNAL`) |
| 2 | usage (`USAGE`) |
| 3 | configuration (`CONFIG_NOT_FOUND`, `CONFIG_INVALID`, `BOARD_NOT_CONFIGURED`, `HOME_NOT_WRITABLE`) |
| 4 | authentication (`AUTH_MISSING`, `AUTH_INVALID`) |
| 5 | not found or ambiguous (`*_NOT_FOUND`, `AMBIGUOUS_*`) |
| 6 | domain rule (`MOVE_OUT_OF_RANGE`, `CONFIRMATION_REQUIRED`, `ATTACHMENT_TOO_LARGE`) |
| 7 | remote API (`API_ERROR`, `RATE_LIMITED`) |
| 8 | network (`NETWORK_ERROR`, `TIMEOUT`) |
| 9 | stale cache (`CACHE_STALE`) |

Save tokens: `--limit N` and `--fields a,b,c` work on `card list`, `card search`,
`card comments`, `board activity` and `inbox list`; `meta.truncated` tells whether the limit
cut the list. `--compact` prints the envelope on one line instead of indented. Never retry a
`RATE_LIMITED` error immediately: wait `error.details.retry_in_seconds`.

```json
{"ok": false, "error": {"code": "MOVE_OUT_OF_RANGE", "message": "Target list (index 7) is outside the board (5 lists).", "details": {"from_index": 4, "to_index": 7, "list_count": 5}}, "meta": {"schema_version": "1", "command": "card move", "elapsed_ms": 230}}
```

## Rules

- Archive instead of deleting: use `card archive` and `board list archive`. Prefer the
  reversible operation whenever one exists.
- Pass `--yes` only after the user explicitly confirmed that exact irreversible operation
  (`board label remove`, `card comment remove`, `card checklist remove`,
  `card checklist item remove`). Without `--yes` they fail with `CONFIRMATION_REQUIRED`;
  do not add the flag on your own to get past that error.
- Run `--dry-run` before `card move --left/--right` and before `board list move`, check the
  reported target, then repeat the command without `--dry-run`.
- Never read, edit or print `config.toml` and never ask for credentials on the command
  line. Credentials live in `config.toml` or in `TRELLO_API_KEY` and `TRELLO_TOKEN`. Change
  the default board only with `config board set`, and only when the user asks.

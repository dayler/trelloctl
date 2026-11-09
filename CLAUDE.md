# trelloctl

Python CLI that lets agent harnesses operate Trello boards with structured JSON output, plus an optional MCP server over stdio.

## Sources of truth

- `docs/PRD.md` — product requirements and architecture (Spanish). **Closed (v1.0.0): never modify it.** If something in it looks wrong or missing, stop and ask.
- `docs/plan/00-plan-maestro.md` — master plan: milestones, dependencies, waves, traffic-light status, execution protocol and the binding behaviour clarifications A-1 to A-25.
- `docs/plan/Mxx-*.md` — one plan per milestone: scope, files it owns, files it must not touch, phases and its automated test cases (TCs).
- `docs/plan/01-estrategia-de-pruebas.md` — test strategy: levels, fake Trello, isolation, traceability, coverage, CI.
- `docs/tc-manuales-trello.md` — the few manual test cases run against real Trello.

IDs in the PRD use a non-breaking hyphen (U+2011): `AC‑1.2`, `RF‑7`. Search with a regex such as `AC.1\.2`. Plan documents and test markers use the ASCII hyphen (`AC-1.2`, `TC-M03-004`, `A-14`).

## Hard rules

- **Language.** Everything the tool emits or installs is in English: help, `doctor` output, error messages, logs, the `config.toml` template, `SKILL.md`, README. Code, identifiers, comments, commit messages and PR text are in English too. Only `docs/` is in Spanish.
- **No attribution.** Never credit Claude, Anthropic or any AI tool or model in commits, PRs, issues, code comments, docs or generated files. No `Co-Authored-By` trailers of that kind.
- **Runtime is stdlib only** (Python ≥ 3.12): `argparse`, `urllib` + `ssl`, `tomllib`, `sqlite3`, `logging`, `json`, `pathlib`. The only optional runtime dependency is the official `mcp` SDK behind the `trelloctl[mcp]` extra, imported lazily inside `mcp_server.py`.
- **No shell.** Never use `subprocess`, `os.system`, `os.popen` or `shell=True` in `src/`.
- **No retries** against the Trello API (AC‑23.1). One request, one result.
- **Credentials** come only from `config.toml` or environment variables, never from command-line options, and never appear unmasked in output or logs.
- **stdout** carries exactly one JSON envelope per invocation (except help, `--version`, no arguments and `doctor` without `--json`). Logs go to stderr and the log file. In MCP mode stdout carries only protocol messages.

## Architecture

Layers depend downward only (PRD §10.3):

- Interfaces: `cli.py`, `mcp_server.py` — no business logic; translate input, call `app/commands`, serialize with `output.py`.
- Application: `app/commands/*` (one use case per operation, returns the envelope as data, never prints), `app/resolver.py` (name → id, cache reloads, stale references), `app/search.py`, `doctor.py`.
- Domain: `domain/models.py`, `domain/mover.py`, `domain/errors.py` — pure, no I/O, no imports from `infra` or `app`.
- Infrastructure: `infra/settings.py`, `infra/trello_client.py`, `infra/cache.py`, `infra/logging.py`, `infra/paths.py`.

Every operation is declared once in `registry.py`; the CLI parsers and the MCP tool list are generated from it. `registry.py` is split into one delimited block per command group; edit only the block of the group you are working on.

Package layout: PRD §10.4. Do not add top-level modules that the PRD does not list without asking.

## Development

```bash
uv sync --all-extras
```

```bash
uv run ruff check .
```

```bash
uv run ruff format --check .
```

```bash
uv run mypy
```

```bash
uv run pytest --cov=trelloctl --cov-branch --cov-report=term-missing --cov-fail-under=90
```

```bash
uv run python tools/check_traceability.py
```

All six must pass before opening a PR.

## Testing

- No test talks to real Trello or to any non-loopback host; a network guard fails the test if it tries.
- Unit tests (`tests/unit/`) use the transport stub. Acceptance tests (`tests/acceptance/test_mNN_*.py`) run against the fake Trello server (`tests/fakes/trello/`, one module per resource) by pointing `http.base_url` at it.
- Every automated TC carries `@pytest.mark.tc("TC-Mxx-nnn")` with the id from its milestone table. The traceability checker fails the build if an `in_review` or `done` milestone has a TC without a test, or a marker points to an unknown TC.
- Coverage: at least 90 % of lines and branches for the full suite, on every OS and Python version in CI.
- Each test gets a temporary `TRELLOCTL_HOME`, working directory and user home, and no `TRELLO_*` variables from the real environment. Use the injectable clock for TTLs, never real sleeps, except in MCP-level tests, which run a real server process with a one-second TTL.
- When a manual TC against real Trello disagrees with the fake, fix the fake first, then the code.

## Git workflow

- Git flow: `main` (releases only), `develop` (integration), `feature/mNN-<slug>` per milestone, branched from an up-to-date `develop`.
- Every milestone lands through a PR into `develop`, merged with a merge commit (`--no-ff`) only when CI is green on Windows, Linux and macOS with Python 3.12, 3.13 and 3.14.
- The only direct commits to `develop` are the orchestrator's `docs(plan): …` status updates to `docs/plan/00-plan-maestro.md` and to the status fields (frontmatter and phase table) of the affected milestone file.
- Never commit to `main`, never force-push shared branches, never skip hooks.
- Commit messages: Conventional Commits in English (`feat(cache): …`, `test(m07): …`, `docs(plan): …`).

## Executing a milestone

Follow the subagent protocol in `docs/plan/00-plan-maestro.md`:

1. Read this file, your milestone file, the PRD sections it lists and the clarifications it cites.
2. Create your feature branch from `develop`.
3. Keep the traffic lights in your milestone file current: frontmatter `status` (`pending`, `in_progress`, `in_review`, `done`, `blocked`) and the phase table (⚪ 🟡 🔵 🟢 🔴). One commit per phase at least.
4. Stay inside your scope and never edit the files your milestone lists under "No toca", the PRD or the master plan. If you need to, set 🔴, explain why in the milestone log and stop.
5. Open the PR when local verification passes and report: PR link, TCs automated, coverage, deviations.

## Platform notes

- Windows and macOS are the supported platforms; Linux works without formal support but runs in CI.
- Platform-specific code is limited to HOME resolution, console encoding and file permissions (AC‑27.1).
- Use `pathlib` for paths; never build paths with string concatenation or hardcoded separators.

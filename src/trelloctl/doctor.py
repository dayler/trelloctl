"""`doctor`: environment checks (AC-5.2) and the Trello contract check (`--api`, AC-5.5).

Every check yields one `Check` with `OK`, `WARN` or `FAIL`, a one-line explanation and, when
something is wrong, a suggested action. The module never fixes anything: it only creates the
missing working subdirectories (AC-5.6) and removes every temporary probe file it writes.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import re
import socket
import sqlite3
import ssl
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from trelloctl import __version__
from trelloctl.app.commands.context import CommandContext, Outcome
from trelloctl.app.commands.skills import SKILL_FILE, SKILL_NAME, parse_frontmatter, skill_text
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.cache import SCHEMA_VERSION
from trelloctl.infra.paths import ATTACHMENTS_DIR, CACHE_DB, LOGS_DIR, ensure_home
from trelloctl.infra.settings import require_credentials, select_board
from trelloctl.infra.trello_client import (
    Transport,
    TransportRequest,
    TrelloClient,
    urllib_transport,
)

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

MIN_PYTHON = (3, 12)
# Value of this module that in-process tests replace with the URL of the fake Trello.
OPENAPI_URL = "https://developer.atlassian.com/cloud/trello/swagger.v3.json"
MAX_REDIRECTS = 5
MACOS_CERTIFICATES_HINT = (
    "On macOS, python.org installers ship without root certificates: run "
    "'/Applications/Python 3.x/Install Certificates.command' (or `pip install --upgrade "
    "certifi`), then retry."
)

# Hooks that tests replace to simulate an environment.
find_spec: Callable[[str], Any] = importlib.util.find_spec
package_version: Callable[[str], str] = importlib.metadata.version


def python_version() -> tuple[int, int, int]:
    return (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)


def resolve_host(host: str, port: int) -> None:
    """Resolve `host` or raise `OSError`."""
    socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def tls_handshake(host: str, port: int, timeout: float) -> None:
    """Open a TLS connection verified against the system certificate store."""
    context = ssl.create_default_context()
    with (
        socket.create_connection((host, port), timeout=timeout) as sock,
        context.wrap_socket(sock, server_hostname=host),
    ):
        pass


# --- endpoint inventory --------------------------------------------------------------------


@dataclass(frozen=True)
class Endpoint:
    """A Trello endpoint the tool calls: method, path template and the query parameters it sends.

    `spec=False` keeps it out of the contract comparison (file downloads are not API
    endpoints); `smoke` marks the read endpoints that get a smoke read.
    """

    method: str
    path: str
    params: tuple[str, ...] = ()
    spec: bool = True
    smoke: bool = False


# The single inventory of what the tool uses (AC-5.5); the fake Trello routes mirror it.
INVENTORY: tuple[Endpoint, ...] = (
    Endpoint("GET", "/members/me", ("fields",), smoke=True),
    Endpoint("GET", "/members/me/boards", ("fields", "filter"), smoke=True),
    Endpoint(
        "GET",
        "/boards/{id}",
        ("fields", "lists", "list_fields", "labels", "label_fields", "members", "member_fields"),
        smoke=True,
    ),
    Endpoint("GET", "/members/{id}", ("fields",), smoke=True),
    Endpoint("GET", "/boards/{id}/actions", ("filter", "limit", "before", "since"), smoke=True),
    Endpoint(
        "GET",
        "/boards/{id}/cards/{filter}",
        ("fields", "limit", "before", "modifiedSince", "attachments", "checklists"),
        smoke=True,
    ),
    Endpoint("GET", "/lists/{id}", smoke=True),
    Endpoint("GET", "/lists/{id}/cards", ("fields", "attachments", "checklists"), smoke=True),
    Endpoint("GET", "/labels/{id}", smoke=True),
    Endpoint("GET", "/cards/{id}", ("attachments", "checklists"), smoke=True),
    Endpoint("GET", "/cards/{id}/actions", ("filter", "limit", "before"), smoke=True),
    Endpoint("GET", "/cards/{id}/attachments", smoke=True),
    Endpoint("GET", "/cards/{id}/attachments/{idAttachment}/download/{filename}", spec=False),
    Endpoint("GET", "/checklists/{id}", smoke=True),
    Endpoint("GET", "/checklists/{id}/checkItems/{idCheckItem}", smoke=True),
    Endpoint(
        "GET",
        "/search",
        ("query", "idBoards", "modelTypes", "partial", "cards_limit", "card_fields"),
        smoke=True,
    ),
    Endpoint(
        "POST",
        "/cards",
        ("idList", "name", "desc", "due", "idLabels", "idMembers", "pos", "idCardSource"),
    ),
    Endpoint(
        "PUT",
        "/cards/{id}",
        (
            *("name", "desc", "due", "dueComplete", "closed"),
            *("idLabels", "idMembers", "pos", "idList", "idBoard"),
        ),
    ),
    Endpoint("POST", "/cards/{id}/attachments", ("url", "name", "file")),
    Endpoint("POST", "/cards/{id}/idLabels", ("value",)),
    Endpoint("DELETE", "/cards/{id}/idLabels/{idLabel}"),
    Endpoint("POST", "/cards/{id}/idMembers", ("value",)),
    Endpoint("DELETE", "/cards/{id}/idMembers/{idMember}"),
    Endpoint("POST", "/cards/{id}/checklists", ("name",)),
    Endpoint("PUT", "/checklists/{id}", ("name",)),
    Endpoint("DELETE", "/checklists/{id}"),
    Endpoint("POST", "/checklists/{id}/checkItems", ("name",)),
    Endpoint("DELETE", "/checklists/{id}/checkItems/{idCheckItem}"),
    Endpoint("PUT", "/cards/{id}/checkItem/{idCheckItem}", ("state", "name")),
    Endpoint("POST", "/cards/{id}/actions/comments", ("text",)),
    Endpoint("PUT", "/cards/{id}/actions/{idAction}/comments", ("text",)),
    Endpoint("DELETE", "/cards/{id}/actions/{idAction}/comments"),
    Endpoint("POST", "/labels", ("name", "color", "idBoard")),
    Endpoint("PUT", "/labels/{id}", ("name", "color")),
    Endpoint("DELETE", "/labels/{id}"),
    Endpoint("POST", "/lists", ("name", "idBoard", "pos")),
    Endpoint("PUT", "/lists/{id}", ("name", "pos")),
    Endpoint("PUT", "/lists/{id}/closed", ("value",)),
    Endpoint("POST", "/lists/{id}/moveAllCards", ("idBoard", "idList")),
    Endpoint("POST", "/lists/{id}/archiveAllCards"),
)


def shape(path: str) -> str:
    """A path template with its parameter names erased, to compare templates of different names."""
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")


# --- report --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    """One diagnostic result."""

    name: str
    status: str
    message: str
    action: str | None = None

    def as_dict(self) -> dict[str, Any]:
        item: dict[str, Any] = {"name": self.name, "status": self.status, "message": self.message}
        if self.action:
            item["action"] = self.action
        return item


def _ok(name: str, message: str) -> Check:
    return Check(name, OK, message)


def _warn(name: str, message: str, action: str | None = None) -> Check:
    return Check(name, WARN, message, action)


def _fail(name: str, message: str, action: str | None = None) -> Check:
    return Check(name, FAIL, message, action)


def summarize(checks: Sequence[Check]) -> dict[str, int]:
    return {s.lower(): sum(1 for c in checks if c.status == s) for s in (OK, WARN, FAIL)}


def render_text(checks: Sequence[Check]) -> str:
    """The human-readable report: one line per check, the suggested action below it."""
    width = max((len(c.name) for c in checks), default=0)
    lines = ["trelloctl doctor", ""]
    for check in checks:
        lines.append(f"[{check.status:<4}] {check.name:<{width}}  {check.message}")
        if check.action:
            lines.append(f"       {'':<{width}}  action: {check.action}")
    counts = summarize(checks)
    lines.append("")
    lines.append(f"{counts['ok']} OK, {counts['warn']} WARN, {counts['fail']} FAIL")
    return "\n".join(lines) + "\n"


# --- environment checks ----------------------------------------------------------------------


def check_python() -> Check:
    found = python_version()
    text = ".".join(str(part) for part in found)
    if found[:2] < MIN_PYTHON:
        return _fail(
            "python",
            f"Python {text} is older than {MIN_PYTHON[0]}.{MIN_PYTHON[1]}.",
            "Install Python 3.12 or newer (`uv tool install trelloctl` downloads it).",
        )
    return _ok("python", f"Python {text}")


def _probe_dir(path: Path) -> None:
    """Write and remove a temporary file in `path`; raises `OSError`."""
    probe = path / ".doctor-write-test"
    try:
        probe.write_bytes(b"")
    finally:
        probe.unlink(missing_ok=True)


def check_working_directory(home: Path) -> Check:
    name = "working_directory"
    action = f"Make {home} and its logs/ and attachments/ folders writable, or set TRELLOCTL_HOME."
    try:
        ensure_home(home)
        for folder in (home, home / LOGS_DIR, home / ATTACHMENTS_DIR):
            _probe_dir(folder)
    except TrelloctlError as exc:
        return _fail(name, exc.message, action)
    except OSError as exc:
        return _fail(name, f"Working directory is not usable: {exc.strerror or exc}.", action)
    return _ok(name, f"{home} is writable (logs/ and attachments/ present)")


def check_config(ctx: CommandContext) -> Check:
    settings = ctx.settings
    if not settings.config_found:
        return _fail(
            "config",
            f"{settings.config_path} does not exist.",
            "Run `trelloctl config init` to create it.",
        )
    error = ctx.config_error
    if error is not None:
        where = ""
        line, column = error.details.get("line"), error.details.get("column")
        if line is not None and column is not None:
            where = f" (line {line}, column {column})"
        return _fail(
            "config",
            f"{_one_line(error.message)}{where}",
            f"Fix {settings.config_path}; `trelloctl config validate` lists every problem.",
        )
    return _ok("config", f"{settings.config_path} is valid")


def check_credentials(ctx: CommandContext) -> Check:
    try:
        require_credentials(ctx.settings)
    except TrelloctlError as exc:
        return _fail(
            "credentials",
            _one_line(exc.message),
            "Set [auth] api_key and token in config.toml, or TRELLO_API_KEY and TRELLO_TOKEN.",
        )
    return _ok("credentials", "api_key and token are set")


def _host_port(base_url: str) -> tuple[str, int, bool]:
    split = urlsplit(base_url)
    secure = split.scheme == "https"
    try:
        port = split.port
    except ValueError:
        port = None
    return split.hostname or "", port or (443 if secure else 80), secure


def check_dns(base_url: str) -> Check:
    host, port, _ = _host_port(base_url)
    try:
        resolve_host(host, port)
    except OSError as exc:
        return _fail(
            "dns",
            f"Cannot resolve {host}: {exc}.",
            "Check the network connection, the DNS settings and http.base_url.",
        )
    return _ok("dns", f"{host} resolves")


def check_tls(base_url: str, timeout: float) -> Check:
    host, port, secure = _host_port(base_url)
    if not secure:
        return _warn("tls", "not applicable: http.base_url is not HTTPS.")
    try:
        tls_handshake(host, port, timeout)
    except (OSError, ssl.SSLError) as exc:
        return _fail(
            "tls",
            f"TLS connection to {host}:{port} failed: {exc}.",
            f"Check the network, proxy and system certificates. {MACOS_CERTIFICATES_HINT}",
        )
    return _ok("tls", f"TLS connection to {host}:{port} verified")


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _api_failure(name: str, exc: TrelloctlError, what: str) -> Check:
    message = f"{what}: {_one_line(exc.message)}"
    if exc.code == "AUTH_INVALID":
        return _fail(
            name,
            message,
            "Create a new token (https://trello.com/app-key) and update [auth] in config.toml.",
        )
    if exc.code == "RATE_LIMITED":
        return _warn(name, message, "Wait a few seconds and run doctor again.")
    if exc.code in ("NETWORK_ERROR", "TIMEOUT"):
        return _fail(name, message, "Check the network connection and http.base_url.")
    return _fail(name, f"{message} ({exc.code})", "Check the Trello status and your credentials.")


def check_token(client: TrelloClient) -> Check:
    try:
        me = client.get("/members/me", {"fields": "username"})
    except TrelloctlError as exc:
        return _api_failure("token", exc, "Token validation failed")
    username = me.get("username") if isinstance(me, Mapping) else None
    return _ok("token", f"token is valid{f' (user {username})' if username else ''}")


_ID = re.compile(r"^[0-9a-fA-F]{24}$")
_SHORT = re.compile(r"^[A-Za-z0-9]{8}$")
_BOARD_URL = re.compile(r"/b/([A-Za-z0-9]{8})(?:/|$)")


def _board_reference(client: TrelloClient, ref: str) -> str:
    """The id or shortLink to ask Trello for; names are looked up among the user's boards."""
    found = _BOARD_URL.search(ref)
    if found:
        return found.group(1)
    if _ID.match(ref) or _SHORT.match(ref):
        return ref
    boards = client.get("/members/me/boards", {"fields": "name,closed", "filter": "open"})
    wanted = ref.casefold()
    for board in boards if isinstance(boards, list) else []:
        if isinstance(board, Mapping) and str(board.get("name", "")).casefold() == wanted:
            return str(board["id"])
    raise TrelloctlError(
        "BOARD_NOT_FOUND", f"No board named {ref!r}.", {"board": ref, "status": 404}
    )


def check_board(
    ctx: CommandContext, client: TrelloClient | None, token_ok: bool
) -> tuple[Check, str | None]:
    """Access to the default board; returns the check and the board id when it is reachable."""
    try:
        ref = select_board(ctx.settings, ctx.board_flag)
    except TrelloctlError:
        return _warn(
            "board",
            "no default board configured; board checks skipped.",
            ("Run `trelloctl config board set <board>` to choose one."),
        ), None
    if client is None or not token_ok:
        return _warn(
            "board", f"skipped: Trello is not reachable with valid credentials ({ref})."
        ), None
    try:
        board = client.get(f"/boards/{_board_reference(client, ref)}", {"fields": "name,closed"})
    except TrelloctlError as exc:
        status = exc.details.get("status")
        if exc.code == "BOARD_NOT_FOUND" or (exc.code == "API_ERROR" and status in (400, 404)):
            return (
                _fail(
                    "board",
                    f"default board {ref!r} was not found or is not accessible.",
                    "Run `trelloctl config board set <board>` with a board you can access.",
                ),
                None,
            )
        return _api_failure("board", exc, f"Board {ref!r} is not accessible"), None
    if not isinstance(board, Mapping) or not board.get("id"):
        return _fail("board", f"Trello returned an unexpected answer for {ref!r}."), None
    return _ok("board", f"default board {board.get('name') or ref!r} is accessible"), str(
        board["id"]
    )


def check_cache(home: Path) -> Check:
    """`PRAGMA integrity_check` and schema version of `cache.db` (read only)."""
    name = "cache"
    db = home / CACHE_DB
    action = "Run `trelloctl cache clear` to rebuild the cache."
    if not db.exists():
        return _ok(name, "cache.db does not exist yet")
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        return _fail(name, f"cache.db cannot be opened: {exc}.", action)
    try:
        rows = conn.execute("PRAGMA integrity_check").fetchall()
        detail = "; ".join(str(r[0]) for r in rows)
        if detail != "ok":
            return _fail(name, f"cache.db failed the integrity check: {detail}.", action)
        row = conn.execute("SELECT value FROM schema WHERE key = 'version'").fetchone()
    except sqlite3.Error as exc:
        return _fail(name, f"cache.db is damaged: {exc}.", action)
    finally:
        conn.close()
    version = None if row is None else str(row[0])
    if version != SCHEMA_VERSION:
        return _fail(
            name, f"cache.db has schema version {version}, expected {SCHEMA_VERSION}.", action
        )
    return _ok(name, f"integrity ok, schema version {version}")


def check_mcp() -> Check:
    name = "mcp"
    try:
        spec = find_spec("mcp")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return _ok(name, "not installed (optional)")
    try:
        version = package_version("mcp")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown version"
    return _ok(name, f"mcp {version}")


def packaged_skill_version() -> str:
    return parse_frontmatter(skill_text()).get("version", __version__)


def check_skill(scope: str, base: Path) -> Check:
    name = f"skill_{scope}"
    target = base / ".claude" / "skills" / SKILL_NAME / SKILL_FILE
    install = f"Run `trelloctl skills install --harness claude --scope {scope} --force`."
    try:
        installed = parse_frontmatter(target.read_text(encoding="utf-8")).get("version")
    except FileNotFoundError:
        return _warn(name, f"not installed in the {scope} scope ({target}).", install)
    except (OSError, UnicodeDecodeError) as exc:
        return _warn(name, f"cannot read {target}: {exc}.", install)
    expected = packaged_skill_version()
    if installed != expected:
        return _warn(
            name,
            f"installed version {installed or 'unknown'} differs from packaged {expected}.",
            install,
        )
    return _ok(name, f"version {installed} matches the packaged skill ({target})")


def check_inbox(client: TrelloClient | None, token_ok: bool) -> Check:
    name = "inbox"
    action = "The Inbox commands will not work; use a work board instead."
    if client is None or not token_ok:
        return _warn(name, "skipped: Trello is not reachable with valid credentials.")
    try:
        data = client.get("/members/me", {"fields": "inbox"})
    except TrelloctlError as exc:
        return _warn(
            name, f"Inbox availability could not be checked: {_one_line(exc.message)}", action
        )
    inbox = data.get("inbox") if isinstance(data, Mapping) else None
    if isinstance(inbox, Mapping) and inbox.get("idBoard") and inbox.get("idList"):
        return _ok(name, "the Inbox is available")
    return _warn(
        name, "the Inbox is not available for this account (no idBoard or idList).", action
    )


# --- --api: contract and smoke reads ---------------------------------------------------------


def download_spec(transport: Transport | None, timeout: float) -> dict[str, Any]:
    """The OpenAPI document at `OPENAPI_URL`; raises `ValueError` with the reason if unavailable."""
    send = transport or urllib_transport
    url = OPENAPI_URL
    for _ in range(MAX_REDIRECTS + 1):
        try:
            response = send(TransportRequest("GET", url, {"Accept": "application/json"}), timeout)
        except Exception as exc:
            raise ValueError(
                f"request failed: {_one_line(str(exc) or type(exc).__name__)}"
            ) from None
        if response.status in (301, 302, 303, 307, 308) and response.headers.get("location"):
            url = urljoin(url, response.headers["location"])
            if urlsplit(url).scheme not in ("http", "https"):
                raise ValueError("redirected to an unsupported URL")
            continue
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}")
        try:
            spec = json.loads(response.body)
        except ValueError:
            raise ValueError("the response is not valid JSON") from None
        if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
            raise ValueError("the document has no `paths`")
        return spec
    raise ValueError("too many redirects")


def _operation_parameters(
    spec: Mapping[str, Any], item: Mapping[str, Any], op: Mapping[str, Any]
) -> dict[str, bool]:
    """Query parameters of an operation: name -> deprecated."""
    found: dict[str, bool] = {}
    for raw in (*item.get("parameters", []), *op.get("parameters", [])):
        param = raw
        if isinstance(raw, Mapping) and "$ref" in raw:
            param = spec
            for part in str(raw["$ref"]).lstrip("#/").split("/"):
                param = param.get(part, {}) if isinstance(param, Mapping) else {}
        if isinstance(param, Mapping) and param.get("in") == "query" and param.get("name"):
            found[str(param["name"])] = bool(param.get("deprecated"))
    return found


def compare_contract(spec: Mapping[str, Any]) -> list[Check]:
    """Compare the inventory with the specification: endpoints and methods, then parameters."""
    paths: dict[str, Mapping[str, Any]] = {}
    for template, item in spec["paths"].items():
        if isinstance(item, Mapping):
            key = shape(template)
            paths[key[2:] if key.startswith("/1/") else key] = item
    missing: list[str] = []
    deprecated: list[str] = []
    undocumented: list[str] = []
    for endpoint in INVENTORY:
        if not endpoint.spec:
            continue
        item = paths.get(shape(endpoint.path))
        operation = item.get(endpoint.method.lower()) if item is not None else None
        if item is None or not isinstance(operation, Mapping):
            missing.append(f"{endpoint.method} {endpoint.path}")
            continue
        declared = _operation_parameters(spec, item, operation)
        for param in endpoint.params:
            if param not in declared:
                undocumented.append(f"{param} ({endpoint.method} {endpoint.path})")
            elif declared[param]:
                deprecated.append(f"{param} ({endpoint.method} {endpoint.path})")
    checked = sum(1 for e in INVENTORY if e.spec)
    contract = (
        _fail(
            "api_contract",
            "endpoints missing from the specification: " + ", ".join(missing),
            "Trello changed its API: update trelloctl or report the change.",
        )
        if missing
        else _ok("api_contract", f"all {checked} endpoints and methods exist in the specification")
    )
    problems = [f"deprecated: {', '.join(deprecated)}"] if deprecated else []
    if undocumented:
        problems.append(f"not documented: {', '.join(undocumented)}")
    parameters = (
        _warn(
            "api_parameters",
            "; ".join(problems),
            "Update trelloctl before Trello removes these parameters.",
        )
        if problems
        else _ok("api_parameters", "every parameter in use is documented and current")
    )
    return [contract, parameters]


class _Samples(dict[str, str]):
    """Ids found while reading, used by later smoke reads."""


def _first(items: Any) -> Mapping[str, Any] | None:
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping) and item.get("id"):
            return item
    return None


def _smoke_name(template: str) -> str:
    return f"smoke GET {template}"


def smoke_reads(
    client: TrelloClient | None,
    board_id: str | None,
    skip: str | None,
    board_reason: str = "no default board",
) -> list[Check]:
    """One read per read endpoint of the inventory; later reads use ids found by earlier ones."""
    samples = _Samples()
    if board_id:
        samples["board"] = board_id
    checks: list[Check] = []

    def run(
        template: str,
        needs: Sequence[str],
        build: Callable[[_Samples], tuple[str, dict[str, Any]]],
        collect: Callable[[Any, _Samples], None] | None = None,
    ) -> None:
        name = _smoke_name(template)
        if client is None or skip:
            checks.append(_warn(name, f"skipped: {skip or 'no client'}."))
            return
        absent = next((key for key in needs if key not in samples), None)
        if absent is not None:
            reason = board_reason if absent == "board" else f"no {absent} found to read"
            checks.append(_warn(name, f"skipped: {reason}."))
            return
        path, params = build(samples)
        try:
            data = client.get(path, params)
        except TrelloctlError as exc:
            checks.append(
                _fail(
                    name,
                    f"{_one_line(exc.message)} ({exc.code})",
                    "Check the Trello status and the API contract.",
                )
            )
            return
        checks.append(_ok(name, f"GET {path} answered"))
        if collect is not None:
            collect(data, samples)

    def board(data: Any, s: _Samples) -> None:
        if isinstance(data, Mapping):
            for key, field in (("list", "lists"), ("label", "labels"), ("member", "members")):
                found = _first(data.get(field))
                if found:
                    s[key] = str(found["id"])

    def cards(data: Any, s: _Samples) -> None:
        for item in data if isinstance(data, list) else []:
            if not isinstance(item, Mapping) or not item.get("id"):
                continue
            s.setdefault("card", str(item["id"]))
            for checklist in item.get("checklists") or []:
                sample = (
                    _first(checklist.get("checkItems")) if isinstance(checklist, Mapping) else None
                )
                if isinstance(checklist, Mapping) and checklist.get("id") and "checklist" not in s:
                    s["checklist"] = str(checklist["id"])
                if sample and "checkitem" not in s and s.get("checklist") == checklist.get("id"):
                    s["checkitem"] = str(sample["id"])

    run("/members/me", (), lambda s: ("/members/me", {"fields": "username"}))
    run(
        "/members/me/boards",
        (),
        lambda s: ("/members/me/boards", {"fields": "name", "filter": "open"}),
    )
    run(
        "/boards/{id}",
        ("board",),
        lambda s: (
            f"/boards/{s['board']}",
            {
                "fields": "name",
                "lists": "open",
                "list_fields": "name",
                "labels": "all",
                "label_fields": "name",
                "members": "all",
                "member_fields": "username",
            },
        ),
        board,
    )
    run(
        "/members/{id}",
        ("member",),
        lambda s: (f"/members/{s['member']}", {"fields": "username"}),
    )
    run(
        "/boards/{id}/actions",
        ("board",),
        lambda s: (f"/boards/{s['board']}/actions", {"limit": 1}),
    )
    run(
        "/boards/{id}/cards/{filter}",
        ("board",),
        lambda s: (
            f"/boards/{s['board']}/cards/open",
            {"fields": "name", "limit": 100, "attachments": True, "checklists": "all"},
        ),
        cards,
    )
    run("/lists/{id}", ("list",), lambda s: (f"/lists/{s['list']}", {}))
    run("/lists/{id}/cards", ("list",), lambda s: (f"/lists/{s['list']}/cards", {"fields": "name"}))
    run("/labels/{id}", ("label",), lambda s: (f"/labels/{s['label']}", {}))
    run("/cards/{id}", ("card",), lambda s: (f"/cards/{s['card']}", {"fields": "name"}))
    run(
        "/cards/{id}/actions",
        ("card",),
        lambda s: (f"/cards/{s['card']}/actions", {"filter": "commentCard", "limit": 1}),
    )
    run("/cards/{id}/attachments", ("card",), lambda s: (f"/cards/{s['card']}/attachments", {}))
    run("/checklists/{id}", ("checklist",), lambda s: (f"/checklists/{s['checklist']}", {}))
    run(
        "/checklists/{id}/checkItems/{idCheckItem}",
        ("checklist", "checkitem"),
        lambda s: (f"/checklists/{s['checklist']}/checkItems/{s['checkitem']}", {}),
    )
    run(
        "/search",
        ("board",),
        lambda s: (
            "/search",
            {
                "query": "trelloctl",
                "idBoards": s["board"],
                "modelTypes": "cards",
                "partial": True,
                "cards_limit": 1,
                "card_fields": "name",
            },
        ),
    )
    return checks


# --- orchestration -------------------------------------------------------------------------


def collect_checks(ctx: CommandContext, *, api: bool) -> list[Check]:
    """Run every check in the order of AC-5.2; with `api`, add the contract and smoke reads."""
    settings = ctx.settings
    checks = [
        check_python(),
        check_working_directory(settings.home),
        check_config(ctx),
    ]
    credentials = check_credentials(ctx)
    checks += [
        credentials,
        check_dns(settings.base_url),
        check_tls(settings.base_url, settings.timeout_seconds),
    ]
    client: TrelloClient | None = None
    if credentials.status == OK:
        client = ctx.client()
        token = check_token(client)
    else:
        token = _warn("token", "skipped: no credentials.")
    checks.append(token)
    token_ok = token.status == OK
    board_check, board_id = check_board(ctx, client, token_ok)
    checks += [
        board_check,
        check_cache(settings.home),
        check_mcp(),
        check_skill("user", Path.home()),
        check_skill("project", Path.cwd()),
        check_inbox(client, token_ok),
    ]
    if api:
        reason = (
            "no default board" if board_check.status == WARN else "default board not accessible"
        )
        checks += api_checks(ctx, client if token_ok else None, board_id, reason)
    return checks


def api_checks(
    ctx: CommandContext, client: TrelloClient | None, board_id: str | None, board_reason: str
) -> list[Check]:
    checks: list[Check] = []
    try:
        spec = download_spec(ctx.transport, ctx.settings.timeout_seconds)
    except ValueError as exc:
        checks.append(
            _warn(
                "api_spec",
                f"the OpenAPI specification is not available ({exc}); contract not compared.",
                "Retry later or check access to developer.atlassian.com.",
            )
        )
    else:
        checks.append(_ok("api_spec", f"downloaded ({len(spec['paths'])} paths)"))
        checks += compare_contract(spec)
    skip = None
    if client is None:
        skip = "no valid credentials"
    checks += smoke_reads(client, board_id, skip, board_reason)
    return checks


def run(ctx: CommandContext, args: Mapping[str, Any]) -> Outcome:
    """Handler of `doctor`: the report as text, or as JSON with `--json` (A-2)."""
    checks = collect_checks(ctx, api=bool(args.get("api")))
    summary = summarize(checks)
    data = {"checks": [c.as_dict() for c in checks], "summary": summary}
    envelope = ctx.success(data)
    text = None if args.get("json") else render_text(checks)
    return Outcome(envelope, text=text, exit_code=1 if summary["fail"] else 0)

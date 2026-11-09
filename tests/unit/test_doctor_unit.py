"""Unit tests of `doctor`: pure helpers, contract comparison and checks with a stub transport."""

from __future__ import annotations

import importlib.metadata
import json
import socket
import ssl
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.transport import StubTransport, response
from tests.fakes.trello.openapi import load_fixture

from trelloctl import doctor
from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.trello_client import TrelloClient

KEY = "k" * 8
TOKEN = "t" * 12
BOARD = "5f1b2c3d4e5f6a7b8c9d0e1f"


def client(stub: StubTransport) -> TrelloClient:
    return TrelloClient("https://api.trello.com/1", KEY, TOKEN, 5, transport=stub)


def context(home: Path, *, board: str | None = None, flag: str | None = None) -> CommandContext:
    lines = ['[auth]\napi_key = "kkkkkkkk"\ntoken = "tttttttttttt"\n']
    if board:
        lines.append(f'[board]\ndefault = "{board}"\n')
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    settings, error = load_settings_lenient({}, home)
    return CommandContext(settings=settings, config_error=error, environ={}, board_flag=flag)


def test_shape_erases_parameter_names() -> None:
    assert doctor.shape("/cards/{card_id}/checkItem/{x}") == "/cards/{}/checkItem/{}"
    assert doctor.shape("/search/") == "/search"
    assert doctor.shape("") == "/"


def test_inventory_has_unique_entries_and_a_smoke_read_per_documented_read() -> None:
    keys = [(e.method, doctor.shape(e.path)) for e in doctor.INVENTORY]
    assert len(keys) == len(set(keys))
    for endpoint in doctor.INVENTORY:
        if endpoint.method == "GET" and endpoint.spec:
            assert endpoint.smoke, endpoint.path
        if endpoint.smoke:
            assert endpoint.method == "GET"


def test_render_text_and_summary() -> None:
    checks = [
        doctor.Check("a", doctor.OK, "fine"),
        doctor.Check("long_name", doctor.FAIL, "broken", "fix it"),
        doctor.Check("c", doctor.WARN, "meh"),
    ]
    text = doctor.render_text(checks)
    assert text.splitlines()[0] == "trelloctl doctor"
    assert "[OK  ] a          fine" in text
    assert "action: fix it" in text
    assert text.endswith("1 OK, 1 WARN, 1 FAIL\n")
    assert doctor.summarize(checks) == {"ok": 1, "warn": 1, "fail": 1}
    assert checks[1].as_dict()["action"] == "fix it"
    assert "action" not in checks[0].as_dict()


def test_python_check(monkeypatch: pytest.MonkeyPatch) -> None:
    assert doctor.check_python().status == doctor.OK
    monkeypatch.setattr(doctor, "python_version", lambda: (3, 11, 0))
    assert doctor.check_python().status == doctor.FAIL


def test_default_python_version_matches_the_interpreter() -> None:
    import sys

    assert doctor.python_version() == tuple(sys.version_info[:3])


def test_resolve_host_resolves_loopback_and_fails_for_garbage() -> None:
    doctor.resolve_host("127.0.0.1", 80)
    with pytest.raises(socket.gaierror):
        doctor.resolve_host("host name with spaces.invalid", 80)


def test_tls_handshake_wraps_a_verified_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Sock:
        def __enter__(self) -> Sock:
            return self

        def __exit__(self, *exc: object) -> None:
            events.append("closed")

    class Context:
        def wrap_socket(self, sock: Sock, server_hostname: str) -> Sock:
            events.append(f"wrap {server_hostname}")
            return Sock()

    monkeypatch.setattr(socket, "create_connection", lambda address, timeout: Sock())
    monkeypatch.setattr(ssl, "create_default_context", lambda: Context())
    doctor.tls_handshake("example.test", 443, 3)
    assert events == ["wrap example.test", "closed", "closed"]


def test_dns_and_tls_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)
    assert doctor.check_dns("https://api.trello.com/1").status == doctor.OK
    assert doctor.check_tls("https://api.trello.com/1", 5).status == doctor.OK
    assert doctor.check_tls("https://api.trello.com:8443/1", 5).message.endswith("verified")
    http = doctor.check_tls("http://127.0.0.1:1/1", 5)
    assert (http.status, "not applicable" in http.message) == (doctor.WARN, True)

    def boom(*args: Any) -> None:
        raise OSError("boom")

    monkeypatch.setattr(doctor, "resolve_host", boom)
    monkeypatch.setattr(doctor, "tls_handshake", boom)
    assert doctor.check_dns("https://api.trello.com/1").status == doctor.FAIL
    failed = doctor.check_tls("https://api.trello.com/1", 5)
    assert failed.status == doctor.FAIL
    assert failed.action is not None
    assert "macOS" in failed.action
    # An unusable port in base_url falls back to the scheme default.
    assert doctor._host_port("https://host:bad/1") == ("host", 443, True)


def test_token_check_outcomes() -> None:
    ok = doctor.check_token(client(StubTransport(response(200, '{"username": "ana"}'))))
    assert ok.status == doctor.OK
    assert "ana" in ok.message
    anonymous = doctor.check_token(client(StubTransport(response(200, "[]"))))
    assert anonymous.message == "token is valid"
    invalid = doctor.check_token(client(StubTransport(response(401, "invalid token"))))
    assert invalid.status == doctor.FAIL
    assert invalid.action is not None
    assert "token" in invalid.action
    limited = doctor.check_token(client(StubTransport(response(429, "API_TOKEN_LIMIT_EXCEEDED"))))
    assert limited.status == doctor.WARN
    network = doctor.check_token(client(StubTransport(OSError("down"))))
    assert network.status == doctor.FAIL
    assert network.action is not None
    assert "network" in network.action
    other = doctor.check_token(client(StubTransport(response(500, "oops"))))
    assert other.status == doctor.FAIL
    assert "API_ERROR" in other.message


def test_board_reference_resolution() -> None:
    plain = client(StubTransport())
    assert doctor._board_reference(plain, BOARD) == BOARD
    assert doctor._board_reference(plain, "abcd1234") == "abcd1234"
    assert doctor._board_reference(plain, "https://trello.com/b/abcd1234/work") == "abcd1234"
    boards = json.dumps([{"id": BOARD, "name": "Work"}, {"id": "x", "name": "Other"}, 5])
    named = client(StubTransport(response(200, boards)))
    assert doctor._board_reference(named, "work") == BOARD
    with pytest.raises(TrelloctlError) as info:
        doctor._board_reference(client(StubTransport(response(200, "{}"))), "Missing")
    assert info.value.code == "BOARD_NOT_FOUND"


def test_board_check_variants(tmp_path: Path) -> None:
    no_board, board_id = doctor.check_board(context(tmp_path), client(StubTransport()), True)
    assert (no_board.status, board_id) == (doctor.WARN, None)
    ctx = context(tmp_path, board=BOARD)
    skipped, _ = doctor.check_board(ctx, None, False)
    assert skipped.status == doctor.WARN
    found, board_id = doctor.check_board(
        ctx, client(StubTransport(response(200, json.dumps({"id": BOARD, "name": "Work"})))), True
    )
    assert (found.status, board_id) == (doctor.OK, BOARD)
    assert "Work" in found.message
    unexpected, board_id = doctor.check_board(ctx, client(StubTransport(response(200, "[]"))), True)
    assert (unexpected.status, board_id) == (doctor.FAIL, None)
    missing, _ = doctor.check_board(ctx, client(StubTransport(response(404, "nope"))), True)
    assert missing.status == doctor.FAIL
    assert "not accessible" in missing.message
    named = context(tmp_path, board="No such board")
    lookup = client(StubTransport(response(200, "[]")))
    absent, _ = doctor.check_board(named, lookup, True)
    assert absent.status == doctor.FAIL
    down, _ = doctor.check_board(ctx, client(StubTransport(OSError("down"))), True)
    assert down.status == doctor.FAIL
    assert "Board" in down.message


def test_board_flag_overrides_the_default(tmp_path: Path) -> None:
    ctx = context(tmp_path, board="other", flag=BOARD)
    check, board_id = doctor.check_board(
        ctx, client(StubTransport(response(200, json.dumps({"id": BOARD})))), True
    )
    assert (check.status, board_id) == (doctor.OK, BOARD)
    assert BOARD in check.message


def test_cache_check_states(tmp_path: Path) -> None:
    assert "does not exist" in doctor.check_cache(tmp_path).message
    import sqlite3

    db = tmp_path / "cache.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE schema (key TEXT, value TEXT)")
    conn.commit()
    assert "schema version None" in doctor.check_cache(tmp_path).message
    conn.execute("INSERT INTO schema VALUES ('version', '99')")
    conn.commit()
    wrong = doctor.check_cache(tmp_path)
    assert wrong.status == doctor.FAIL
    assert "99" in wrong.message
    conn.execute("DELETE FROM schema")
    conn.execute("INSERT INTO schema VALUES ('version', '1')")
    conn.commit()
    conn.close()
    assert doctor.check_cache(tmp_path).status == doctor.OK
    other = tmp_path / "other"
    other.mkdir()
    (other / "cache.db").write_bytes(b"x" * 4096)
    assert doctor.check_cache(other).status == doctor.FAIL
    bare = tmp_path / "bare"
    bare.mkdir()
    sqlite3.connect(bare / "cache.db").close()
    assert doctor.check_cache(bare).status == doctor.FAIL  # valid file, no schema table


def test_cache_check_reports_an_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "cache.db").write_bytes(b"")

    class Conn:
        def execute(self, sql: str) -> Any:
            class Rows:
                def fetchall(self) -> list[tuple[str]]:
                    return [("row 1 missing from index",)]

            return Rows()

        def close(self) -> None:
            pass

    monkeypatch.setattr(doctor.sqlite3, "connect", lambda *args, **kwargs: Conn())
    result = doctor.check_cache(tmp_path)
    assert result.status == doctor.FAIL
    assert "integrity" in result.message
    monkeypatch.setattr(
        doctor.sqlite3,
        "connect",
        lambda *args, **kwargs: (_ for _ in ()).throw(doctor.sqlite3.OperationalError("locked")),
    )
    assert "cannot be opened" in doctor.check_cache(tmp_path).message


def test_mcp_check_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "find_spec", lambda name: object())

    def missing(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor, "package_version", missing)
    assert doctor.check_mcp().message == "mcp unknown version"

    def broken(name: str) -> None:
        raise ValueError(name)

    monkeypatch.setattr(doctor, "find_spec", broken)
    assert doctor.check_mcp().message == "not installed (optional)"


def test_skill_check_unreadable_and_versionless(tmp_path: Path) -> None:
    target = tmp_path / ".claude" / "skills" / "trelloctl" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("no frontmatter", encoding="utf-8")
    versionless = doctor.check_skill("user", tmp_path)
    assert versionless.status == doctor.WARN
    assert "unknown" in versionless.message
    target.write_bytes(b"\xff\xfe\x00")
    broken = doctor.check_skill("user", tmp_path)
    assert broken.status == doctor.WARN
    assert "cannot read" in broken.message


def test_inbox_check_variants() -> None:
    inbox = json.dumps({"inbox": {"idBoard": "b", "idList": "l"}})
    assert doctor.check_inbox(client(StubTransport(response(200, inbox))), True).status == doctor.OK
    partial = json.dumps({"inbox": {"idBoard": "b"}})
    assert (
        doctor.check_inbox(client(StubTransport(response(200, partial))), True).status
        == doctor.WARN
    )
    assert (
        doctor.check_inbox(client(StubTransport(response(200, "[]"))), True).status == doctor.WARN
    )
    assert doctor.check_inbox(client(StubTransport(response(500, "x"))), True).status == doctor.WARN
    assert doctor.check_inbox(None, False).status == doctor.WARN
    assert doctor.check_inbox(client(StubTransport()), False).status == doctor.WARN


def test_working_directory_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert doctor.check_working_directory(tmp_path / "new").status == doctor.OK
    assert not list((tmp_path / "new").rglob(".doctor*"))
    home = tmp_path / "file-home"
    home.write_text("x")
    assert doctor.check_working_directory(home).status == doctor.FAIL

    def not_a_dir(path: Path) -> None:
        raise OSError("probe failed")

    monkeypatch.setattr(doctor, "_probe_dir", not_a_dir)
    failed = doctor.check_working_directory(tmp_path / "other")
    assert failed.status == doctor.FAIL
    assert "probe failed" in failed.message


def test_config_check_variants(tmp_path: Path) -> None:
    settings, _ = load_settings_lenient({}, tmp_path)
    missing = CommandContext(settings=settings, config_error=None, environ={})
    assert doctor.check_config(missing).status == doctor.FAIL
    (tmp_path / "config.toml").write_text("[auth]\napi_key = 5\n", encoding="utf-8")
    settings, error = load_settings_lenient({}, tmp_path)
    assert error is not None
    invalid = doctor.check_config(CommandContext(settings=settings, config_error=error, environ={}))
    assert invalid.status == doctor.FAIL
    assert "line" not in invalid.message
    ctx = context(tmp_path)
    assert doctor.check_config(ctx).status == doctor.OK
    assert doctor.check_credentials(ctx).status == doctor.OK
    assert (
        doctor.check_credentials(CommandContext(settings=missing.settings, environ={})).status
        == doctor.FAIL
    )


# --- --api -----------------------------------------------------------------------------------


def spec_with(mutate: Any) -> dict[str, Any]:
    spec = load_fixture()
    mutate(spec)
    return spec


def test_contract_passes_with_the_fixture() -> None:
    contract, parameters = doctor.compare_contract(load_fixture())
    assert (contract.status, parameters.status) == (doctor.OK, doctor.OK)


def test_contract_reports_missing_methods_and_endpoints() -> None:
    def mutate(spec: dict[str, Any]) -> None:
        del spec["paths"]["/cards/{id}"]["put"]
        del spec["paths"]["/labels"]
        spec["paths"]["/not/ours"] = "ignored"

    contract, _ = doctor.compare_contract(spec_with(mutate))
    assert contract.status == doctor.FAIL
    assert "PUT /cards/{id}" in contract.message
    assert "POST /labels" in contract.message


def test_contract_reports_deprecated_and_undocumented_parameters() -> None:
    def mutate(spec: dict[str, Any]) -> None:
        post = spec["paths"]["/cards"]["post"]
        post["parameters"] = [
            {**p, "deprecated": True} if p["name"] == "name" else p
            for p in post["parameters"]
            if p["name"] != "desc"
        ]

    _, parameters = doctor.compare_contract(spec_with(mutate))
    assert parameters.status == doctor.WARN
    assert "deprecated: name (POST /cards)" in parameters.message
    assert "not documented: desc (POST /cards)" in parameters.message


def test_contract_accepts_prefixed_paths_and_path_level_parameters() -> None:
    def mutate(spec: dict[str, Any]) -> None:
        spec["paths"] = {"/1" + path: item for path, item in spec["paths"].items()}
        item = spec["paths"]["/1/labels"]
        item["parameters"] = item["post"].pop("parameters")

    contract, parameters = doctor.compare_contract(spec_with(mutate))
    assert (contract.status, parameters.status) == (doctor.OK, doctor.OK)


def test_download_spec_outcomes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "OPENAPI_URL", "https://spec.test/a.json")
    good = '{"paths": {"/x": {}}}'
    assert doctor.download_spec(StubTransport(response(200, good)), 5)["paths"] == {"/x": {}}
    redirect = response(301, b"", {"Location": "/b.json"})
    stub = StubTransport(redirect, response(200, good))
    doctor.download_spec(stub, 5)
    assert [r.url for r in stub.requests] == [
        "https://spec.test/a.json",
        "https://spec.test/b.json",
    ]
    assert "Authorization" not in stub.requests[0].headers
    cases = {
        "HTTP 404": StubTransport(response(404, "x")),
        "request failed": StubTransport(OSError("down")),
        "not valid JSON": StubTransport(response(200, "<html>")),
        "no `paths`": StubTransport(response(200, "[]")),
        "unsupported URL": StubTransport(response(302, b"", {"location": "file:///etc/passwd"})),
        "too many redirects": StubTransport(*[response(302, b"", {"location": "/l"})] * 6),
    }
    for reason, transport in cases.items():
        with pytest.raises(ValueError, match=reason):
            doctor.download_spec(transport, 5)


def smoke_stub(*bodies: str) -> StubTransport:
    return StubTransport(*[response(200, body) for body in bodies])


def test_smoke_reads_skip_reasons() -> None:
    assert len(doctor.smoke_reads(None, None, "no valid credentials")) == sum(
        1 for e in doctor.INVENTORY if e.smoke
    )
    checks = doctor.smoke_reads(None, None, "no valid credentials")
    assert {c.status for c in checks} == {doctor.WARN}
    assert all("no valid credentials" in c.message for c in checks)


def test_smoke_reads_without_samples_warn_per_missing_sample() -> None:
    bodies = ['{"id": "me"}', "[]", "{}", "[]", "[]", "[]"]
    stub = smoke_stub(*bodies)
    checks = {c.name: c for c in doctor.smoke_reads(client(stub), BOARD, None)}
    assert checks["smoke GET /members/me"].status == doctor.OK
    assert checks["smoke GET /boards/{id}"].status == doctor.OK
    assert "no member found to read" in checks["smoke GET /members/{id}"].message
    assert "no list found" in checks["smoke GET /lists/{id}"].message
    assert "no checklist found" in checks["smoke GET /checklists/{id}"].message
    assert (
        "no checklist found"
        in checks["smoke GET /checklists/{id}/checkItems/{idCheckItem}"].message
    )
    reads = [r.url.split("?")[0].rsplit("/1", 1)[1] for r in stub.requests]
    assert reads[0] == "/members/me"
    no_board = doctor.smoke_reads(client(smoke_stub('{"id": "me"}', "[]")), None, None, "no board")
    by_name = {c.name: c for c in no_board}
    for template in ("/boards/{id}", "/boards/{id}/actions", "/search"):
        assert by_name[f"smoke GET {template}"].message == "skipped: no board."


def test_smoke_reads_follow_sample_ids_and_report_failures() -> None:
    board = json.dumps(
        {
            "lists": [{"id": "L1"}],
            "labels": [{"id": "B1"}],
            "members": [{"id": "M1"}],
        }
    )
    cards = json.dumps(
        [
            {"id": "C0", "checklists": []},
            "junk",
            {"id": "C1", "checklists": [{"id": "K1", "checkItems": [{"id": "I1"}]}, "junk"]},
            {"id": "C2", "checklists": [{"id": "K2", "checkItems": [{"id": "I2"}]}]},
        ]
    )
    ok = response(200, "{}")
    stub = StubTransport(
        response(200, '{"id": "me"}'),
        response(200, "[]"),
        response(200, board),
        ok,  # members/{id}
        ok,  # actions
        response(200, cards),
        response(500, "broken"),  # lists/{id}
        *[ok] * 20,
    )
    checks = {c.name: c for c in doctor.smoke_reads(client(stub), BOARD, None)}
    urls = [r.url for r in stub.requests]
    assert any("/members/M1" in url for url in urls)
    assert any("/checklists/K1/checkItems/I1" in url for url in urls)
    assert not any("K2" in url for url in urls)
    assert checks["smoke GET /lists/{id}"].status == doctor.FAIL
    assert checks["smoke GET /lists/{id}/cards"].status == doctor.OK


def test_api_checks_without_a_client_skip_the_smoke_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doctor, "OPENAPI_URL", "https://spec.test/a.json")
    ctx = context(tmp_path)
    ctx.transport = StubTransport(response(404, "x"))
    checks = doctor.api_checks(ctx, None, None, "no default board")
    assert checks[0].name == "api_spec"
    assert checks[0].status == doctor.WARN
    assert all(c.status == doctor.WARN for c in checks)
    assert len(checks) == 1 + sum(1 for e in doctor.INVENTORY if e.smoke)


def test_run_exit_codes_and_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)
    ctx = context(tmp_path)
    ctx.transport = StubTransport(response(401, "invalid token"), response(401, "invalid token"))
    ctx.command = "doctor"
    outcome = doctor.run(ctx, {"json": False})
    assert outcome.exit_code == 1
    assert outcome.text is not None
    assert outcome.envelope["ok"] is True
    as_json = doctor.run(ctx, {"json": True})
    assert as_json.text is None
    assert as_json.exit_code == 1

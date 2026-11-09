"""M14 acceptance tests: `doctor` and `doctor --api`."""

from __future__ import annotations

import json
import re
import socket
import ssl
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m14_support import (
    BOARD,
    ORDER,
    close_caches,  # noqa: F401
    doctor_json,
    doctor_text,
    healthy,
    hide_inbox,
    paths_of,
    requests_of,
    smoke_names,
    spec_on_fake,  # noqa: F401
    write_config,
)
from tests.fakes.trello import FakeTrello
from tests.fakes.trello.openapi import load_fixture

from trelloctl import doctor
from trelloctl.app.commands.context import CommandContext, load_settings_lenient
from trelloctl.app.commands.skills import SKILL_FILE, SKILL_NAME
from trelloctl.cli import main
from trelloctl.infra.cache import Cache
from trelloctl.registry import default_registry, execute


@pytest.mark.tc("TC-M14-001")
def test_doctor_prints_a_text_report_and_exits_zero(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    code, out = doctor_text(capsys)
    assert code == 0
    with pytest.raises(ValueError, match="Expecting"):
        json.loads(out)
    lines = out.splitlines()
    assert lines[0] == "trelloctl doctor"
    assert any(line.startswith("[OK") and " python " in line for line in lines)
    assert lines[-1].endswith("0 FAIL")


@pytest.mark.tc("TC-M14-002")
def test_doctor_json_reports_every_check_with_status_and_message(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    code, envelope, checks = doctor_json(capsys)
    assert code == 0
    assert envelope["ok"] is True
    assert envelope["meta"]["command"] == "doctor"
    for check in checks.values():
        assert check["status"] in ("OK", "WARN", "FAIL")
        assert check["message"]
    assert envelope["data"]["summary"]["fail"] == 0
    assert checks["token"]["status"] == "OK"
    assert checks["board"]["status"] == "OK"
    assert checks["inbox"]["status"] == "OK"


@pytest.mark.tc("TC-M14-003")
def test_checks_follow_the_order_of_ac_5_2(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    _, envelope, _ = doctor_json(capsys)
    assert [c["name"] for c in envelope["data"]["checks"]] == ORDER


@pytest.mark.tc("TC-M14-004")
def test_every_check_is_one_line_and_a_fail_carries_an_action(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    write_config(trelloctl_home, fake_trello.base_url, fake_trello, key="", token="")
    code, out = doctor_text(capsys)
    assert code == 1
    lines = out.splitlines()
    statuses = [line for line in lines if line.startswith("[")]
    assert len(statuses) == len(ORDER)
    assert all(line[1:5].strip() in ("OK", "WARN", "FAIL") for line in statuses)
    failing = lines.index(next(line for line in statuses if line.startswith("[FAIL")))
    assert lines[failing + 1].strip().startswith("action:")
    _, _, checks = doctor_json(capsys)
    for check in checks.values():
        assert "\n" not in check["message"]
        assert ("action" in check) or check["status"] != "FAIL"


@pytest.mark.tc("TC-M14-005")
def test_only_warnings_exit_zero(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello, board=None)
    code, envelope, checks = doctor_json(capsys)
    assert checks["board"]["status"] == "WARN"
    assert checks["skill_user"]["status"] == "WARN"
    summary = envelope["data"]["summary"]
    assert summary["warn"] >= 3
    assert summary["fail"] == 0
    assert code == 0


@pytest.mark.tc("TC-M14-006")
def test_invalid_token_exits_one_and_json_stays_ok(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    write_config(trelloctl_home, fake_trello.base_url, fake_trello, token="wrong", board=BOARD)
    fake_trello.requests.clear()
    code, out = doctor_text(capsys)
    assert code == 1
    assert "[FAIL] token" in out
    code, envelope, checks = doctor_json(capsys)
    assert code == 1
    assert envelope["ok"] is True
    assert checks["token"]["status"] == "FAIL"
    assert envelope["data"]["summary"]["fail"] == 1
    assert checks["board"]["status"] == "WARN"
    assert not requests_of(fake_trello, "/boards")


@pytest.mark.tc("TC-M14-007")
def test_old_interpreter_fails_with_an_action(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    monkeypatch.setattr(doctor, "python_version", lambda: (3, 11, 9))
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["python"]["status"] == "FAIL"
    assert "3.11.9" in checks["python"]["message"]
    assert "3.12" in checks["python"]["action"]


@pytest.mark.tc("TC-M14-008")
def test_missing_config_runs_and_suggests_config_init(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, envelope, checks = doctor_json(capsys)
    assert code == 1
    assert envelope["ok"] is True
    assert [c["name"] for c in envelope["data"]["checks"]] == ORDER
    assert checks["config"]["status"] == "FAIL"
    assert "config init" in checks["config"]["action"]
    assert checks["credentials"]["status"] == "FAIL"


@pytest.mark.tc("TC-M14-009")
def test_malformed_config_reports_line_and_column(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (trelloctl_home / "config.toml").write_text('[auth]\napi_key = "x"\ntoken = = "y"\n')
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["config"]["status"] == "FAIL"
    assert "line 3, column" in checks["config"]["message"]


@pytest.mark.tc("TC-M14-010")
def test_missing_credentials_fail(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    write_config(trelloctl_home, fake_trello.base_url, fake_trello, key="", token="")
    fake_trello.requests.clear()
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["credentials"]["status"] == "FAIL"
    assert "TRELLO_API_KEY" in checks["credentials"]["action"]
    assert checks["config"]["status"] == "OK"
    assert not requests_of(fake_trello, "/members")


@pytest.mark.tc("TC-M14-011")
def test_dns_and_tls_failures_fail_and_tls_suggests_macos_certificates(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    https = f"https://127.0.0.1:{fake_trello.port}/1"
    write_config(trelloctl_home, https, fake_trello, board=BOARD)

    def no_dns(host: str, port: int) -> None:
        raise socket.gaierror(11001, "getaddrinfo failed")

    def bad_cert(host: str, port: int, timeout: float) -> None:
        raise ssl.SSLCertVerificationError("certificate verify failed")

    monkeypatch.setattr(doctor, "resolve_host", no_dns)
    monkeypatch.setattr(doctor, "tls_handshake", lambda host, port, timeout: None)
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["dns"]["status"] == "FAIL"
    assert "127.0.0.1" in checks["dns"]["message"]
    assert checks["tls"]["status"] == "OK"

    monkeypatch.setattr(doctor, "resolve_host", lambda host, port: None)
    monkeypatch.setattr(doctor, "tls_handshake", bad_cert)
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["dns"]["status"] == "OK"
    assert checks["tls"]["status"] == "FAIL"
    assert "macOS" in checks["tls"]["action"]
    assert "Install Certificates" in checks["tls"]["action"]


@pytest.mark.tc("TC-M14-012")
def test_unwritable_working_directory_fails(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    # The CLI would stop on its own working-directory check first; skip it to reach doctor.
    monkeypatch.setattr("trelloctl.cli.ensure_home", lambda home: home)
    (trelloctl_home / "logs").mkdir(exist_ok=True)
    attachments = trelloctl_home / "attachments"
    attachments.write_text("not a directory")
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["working_directory"]["status"] == "FAIL"
    assert str(trelloctl_home) in checks["working_directory"]["message"]
    assert checks["working_directory"]["action"]

    attachments.unlink()
    attachments.mkdir()
    real_probe = doctor._probe_dir

    def probe(path: Path) -> None:
        if path.name == "logs":
            raise PermissionError(13, "Permission denied")
        real_probe(path)

    monkeypatch.setattr(doctor, "_probe_dir", probe)
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["working_directory"]["status"] == "FAIL"
    assert "Permission denied" in checks["working_directory"]["message"]


@pytest.mark.tc("TC-M14-013")
def test_no_default_board_warns_without_touching_the_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello, board=None)
    fake_trello.requests.clear()
    code, _, checks = doctor_json(capsys)
    assert code == 0
    assert checks["board"]["status"] == "WARN"
    assert "no default board" in checks["board"]["message"]
    assert not requests_of(fake_trello, "/boards")
    assert not requests_of(fake_trello, "/lists")


@pytest.mark.tc("TC-M14-014")
def test_inaccessible_default_board_fails(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello, board="9f1b2c3d4e5f6a7b8c9d0e99")
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["board"]["status"] == "FAIL"
    assert "not accessible" in checks["board"]["message"]
    assert "config board set" in checks["board"]["action"]


@pytest.mark.tc("TC-M14-015")
def test_cache_integrity_and_schema_version(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    Cache.open(trelloctl_home).close()
    code, _, checks = doctor_json(capsys)
    assert code == 0
    assert checks["cache"]["status"] == "OK"
    assert "schema version 1" in checks["cache"]["message"]
    for leftover in trelloctl_home.glob("cache.db-*"):
        leftover.unlink()
    (trelloctl_home / "cache.db").write_bytes(b"this is not an sqlite database" * 100)
    code, _, checks = doctor_json(capsys)
    assert code == 1
    assert checks["cache"]["status"] == "FAIL"
    assert "cache clear" in checks["cache"]["action"]


@pytest.mark.tc("TC-M14-016")
def test_mcp_extra_present_and_absent(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    monkeypatch.setattr(doctor, "find_spec", lambda name: object())
    monkeypatch.setattr(doctor, "package_version", lambda name: "1.9.4")
    _, _, checks = doctor_json(capsys)
    assert (checks["mcp"]["status"], checks["mcp"]["message"]) == ("OK", "mcp 1.9.4")
    monkeypatch.setattr(doctor, "find_spec", lambda name: None)
    _, _, checks = doctor_json(capsys)
    assert checks["mcp"]["status"] == "OK"
    assert checks["mcp"]["message"] == "not installed (optional)"


def _install(base: Path, text: str | None) -> None:
    target = base / ".claude" / "skills" / SKILL_NAME / SKILL_FILE
    if text is None:
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


@pytest.mark.tc("TC-M14-017")
def test_skill_version_in_user_and_project_scopes(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    packaged = doctor.packaged_skill_version()
    states = {
        "equal": (f"---\nname: trelloctl\nversion: {packaged}\n---\n", "OK"),
        "different": ("---\nname: trelloctl\nversion: 0.0.1\n---\n", "WARN"),
        "absent": (None, "WARN"),
    }
    bases = {"skill_user": Path.home(), "skill_project": Path.cwd()}
    for name, base in bases.items():
        for state, (text, expected) in states.items():
            for other in bases.values():
                _install(other, None)
            _install(base, text)
            code, _, checks = doctor_json(capsys)
            assert code == 0, (name, state)
            assert checks[name]["status"] == expected, (name, state)
            if state != "equal":
                assert "skills install" in checks[name]["action"]
            if state == "different":
                assert "0.0.1" in checks[name]["message"]
            other_name = next(n for n in bases if n != name)
            assert checks[other_name]["status"] == "WARN"


@pytest.mark.tc("TC-M14-018")
def test_missing_inbox_warns_without_changing_the_other_checks(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    _, _, with_inbox = doctor_json(capsys)
    assert with_inbox["inbox"]["status"] == "OK"
    hide_inbox(fake_trello.state)
    fake_trello.requests.clear()
    code, _, without = doctor_json(capsys)
    assert code == 0
    assert without["inbox"]["status"] == "WARN"
    assert "Inbox" in without["inbox"]["message"]
    assert any(
        r.path == "/members/me" and r.param("fields") == "inbox" for r in fake_trello.requests
    )
    assert {k: v for k, v in without.items() if k != "inbox"} == {
        k: v for k, v in with_inbox.items() if k != "inbox"
    }


@pytest.mark.tc("TC-M14-019")
def test_api_check_passes_and_reads_every_read_endpoint(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    fake_trello.requests.clear()
    code, envelope, checks = doctor_json(capsys, "--api")
    assert code == 0
    assert checks["api_spec"]["status"] == "OK"
    assert checks["api_contract"]["status"] == "OK"
    assert checks["api_parameters"]["status"] == "OK"
    names = [c["name"] for c in envelope["data"]["checks"]]
    assert names[: len(ORDER)] == ORDER
    assert names[-len(smoke_names()) :] == smoke_names()
    assert all(checks[name]["status"] == "OK" for name in smoke_names()), [
        checks[n] for n in smoke_names() if checks[n]["status"] != "OK"
    ]
    reads = [r.path for r in fake_trello.requests if r.method == "GET"]
    for endpoint in doctor.INVENTORY:
        if endpoint.smoke:
            pattern = re.compile(
                "^" + re.sub(r"\\{[^}]*\\}", "[^/]+", re.escape(endpoint.path)) + "$"
            )
            assert any(pattern.match(path) for path in reads), endpoint.path
    assert all(r.method == "GET" for r in fake_trello.requests)


@pytest.mark.tc("TC-M14-020")
def test_api_check_names_missing_endpoints_and_deprecated_parameters(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    spec = load_fixture()
    del spec["paths"]["/cards/{id}/checklists"]
    fake_trello.state.extra["openapi"] = spec
    code, _, checks = doctor_json(capsys, "--api")
    assert code == 1
    assert checks["api_contract"]["status"] == "FAIL"
    assert "POST /cards/{id}/checklists" in checks["api_contract"]["message"]
    assert checks["api_parameters"]["status"] == "OK"

    spec = load_fixture()
    for param in spec["paths"]["/cards"]["post"]["parameters"]:
        if param["name"] == "idLabels":
            param["deprecated"] = True
    fake_trello.state.extra["openapi"] = spec
    code, _, checks = doctor_json(capsys, "--api")
    assert code == 0
    assert checks["api_contract"]["status"] == "OK"
    assert checks["api_parameters"]["status"] == "WARN"
    assert "idLabels (POST /cards)" in checks["api_parameters"]["message"]


@pytest.mark.tc("TC-M14-021")
def test_unavailable_specification_warns_and_smoke_reads_still_run(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    fake_trello.fail_next("GET", "/swagger.v3.json", 404, "not found")
    code, _, checks = doctor_json(capsys, "--api")
    assert code == 0
    assert checks["api_spec"]["status"] == "WARN"
    assert "404" in checks["api_spec"]["message"]
    assert "api_contract" not in checks
    assert all(checks[name]["status"] == "OK" for name in smoke_names())
    # Network failure: nothing listens on the specification URL.
    monkeypatch.setattr(doctor, "OPENAPI_URL", "http://127.0.0.1:1/swagger.v3.json")
    code, _, checks = doctor_json(capsys, "--api")
    assert code == 0
    assert checks["api_spec"]["status"] == "WARN"
    assert all(checks[name]["status"] == "OK" for name in smoke_names())


@pytest.mark.tc("TC-M14-022")
def test_inventory_matches_the_routes_of_the_fake_trello() -> None:
    routes = {
        (method, doctor.shape(template))
        for method, template in FakeTrello().routes
        if template != "/swagger.v3.json"
    }
    inventory = {(e.method, doctor.shape(e.path)) for e in doctor.INVENTORY}
    assert routes - inventory == set(), "routes of the fake missing from the inventory"
    assert inventory - routes == set(), "inventory entries without a route in the fake"
    assert len(inventory) == len(doctor.INVENTORY)


def probes(home: Path) -> list[Path]:
    """Temporary write-probe files left under the working directory."""
    return [p for p in home.rglob("*") if p.name.startswith((".write-test", ".doctor"))]


@pytest.mark.tc("TC-M14-023")
def test_doctor_creates_missing_subdirectories_and_leaves_no_temporary_file(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    assert not (trelloctl_home / "logs").exists()
    assert not (trelloctl_home / "attachments").exists()
    settings, error = load_settings_lenient({}, trelloctl_home)
    ctx = CommandContext(settings=settings, config_error=error, environ={})
    operation = default_registry().get(("doctor",))
    assert operation is not None
    outcome = execute(operation, {"api": True}, ctx)
    checks = {c["name"]: c for c in outcome.envelope["data"]["checks"]}
    assert checks["working_directory"]["status"] == "OK"
    assert (trelloctl_home / "logs").is_dir()
    assert (trelloctl_home / "attachments").is_dir()
    assert not [
        p for p in trelloctl_home.rglob("*") if p.name.startswith((".write-test", ".doctor"))
    ]
    # The same through the CLI, which prepares the directories before doctor runs.
    code, _, _ = doctor_json(capsys, "--api")
    assert code == 0
    leftovers = [
        p for p in trelloctl_home.rglob("*") if p.name.startswith((".write-test", ".doctor"))
    ]
    assert leftovers == []
    assert list((trelloctl_home / "attachments").iterdir()) == []


@pytest.mark.tc("TC-M14-024")
def test_api_check_contacts_only_base_url_and_the_specification_url(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    healthy(trelloctl_home, fake_trello)
    with FakeTrello() as spec_server:
        monkeypatch.setattr(
            doctor, "OPENAPI_URL", f"http://127.0.0.1:{spec_server.port}/swagger.v3.json"
        )
        seen: list[Any] = []
        hosts: list[str] = []
        real_connect = socket.socket.connect
        real_resolve = doctor.resolve_host

        def connect(self: socket.socket, address: Any) -> None:
            seen.append(address)
            real_connect(self, address)

        def resolve(host: str, port: int) -> None:
            hosts.append(host)
            real_resolve(host, port)

        monkeypatch.setattr(socket.socket, "connect", connect)
        monkeypatch.setattr(doctor, "resolve_host", resolve)
        code, _, checks = doctor_json(capsys, "--api")
        assert code == 0
        assert checks["api_contract"]["status"] == "OK"
        assert set(paths_of(spec_server)) == {"/swagger.v3.json"}
        assert "/swagger.v3.json" not in paths_of(fake_trello)
        assert {addr[1] for addr in seen} == {fake_trello.port, spec_server.port}
        assert {addr[0] for addr in seen} == {"127.0.0.1"}
        assert hosts == ["127.0.0.1"]


@pytest.mark.tc("TC-M14-025")
def test_http_base_url_makes_tls_not_applicable(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    healthy(trelloctl_home, fake_trello)
    assert fake_trello.base_url.startswith("http://")
    code, _, checks = doctor_json(capsys)
    assert code == 0
    assert checks["tls"]["status"] == "WARN"
    assert "not applicable" in checks["tls"]["message"]
    assert checks["dns"]["status"] == "OK"
    assert main(["doctor"]) == 0
    assert "not applicable" in capsys.readouterr().out

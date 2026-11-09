"""Automated test cases of milestone M00: fake Trello, isolation, guard and checker."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from tests.fakes.trello import FakeTrello, Request, json_response
from tests.harness import NetworkGuard, NetworkGuardError

import check_traceability as ct


def fetch(url: str, data: bytes | None = None) -> tuple[int, str, dict[str, str]]:
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode(), dict(err.headers)


def auth(fake: FakeTrello, path: str, key: str | None = None, token: str | None = None) -> str:
    k = fake.api_key if key is None else key
    t = fake.token if token is None else token
    return f"{fake.base_url}{path}?key={k}&token={t}"


def add_ping(fake: FakeTrello) -> None:
    @fake.router.route("GET", "/ping/{name}")
    @fake.router.route("POST", "/ping/{name}")
    def ping(req: Request) -> object:
        return json_response({"name": req.params["name"]})


@pytest.mark.tc("TC-M00-001")
def test_fake_starts_on_loopback_and_stops_cleanly() -> None:
    before = set(threading.enumerate())
    fake = FakeTrello()
    add_ping(fake)
    fake.start()
    assert fake.base_url.startswith("http://127.0.0.1:")
    assert fake.port > 0
    status, body, _ = fetch(auth(fake, "/ping/a"))
    assert (status, json.loads(body)) == (200, {"name": "a"})
    fake.stop()
    assert set(threading.enumerate()) <= before


@pytest.mark.tc("TC-M00-002")
def test_unregistered_route_is_404_text(fake_trello: FakeTrello) -> None:
    status, body, headers = fetch(auth(fake_trello, "/nope"))
    assert status == 404
    assert headers["Content-Type"].startswith("text/plain")
    assert body


@pytest.mark.tc("TC-M00-003")
def test_bad_credentials_are_401(fake_trello: FakeTrello) -> None:
    add_ping(fake_trello)
    base = f"{fake_trello.base_url}/ping/a"
    assert fetch(base)[:2] == (401, "invalid key")
    assert fetch(auth(fake_trello, "/ping/a", key="other"))[:2] == (401, "invalid key")
    assert fetch(auth(fake_trello, "/ping/a", token="other"))[:2] == (401, "invalid token")
    assert fetch(f"{base}?key={fake_trello.api_key}")[:2] == (401, "invalid token")


@pytest.mark.tc("TC-M00-004")
def test_faults_apply_once_to_next_matching_request(fake_trello: FakeTrello) -> None:
    add_ping(fake_trello)
    url = auth(fake_trello, "/ping/a")
    fake_trello.rate_limit("GET", "/ping/{name}")
    status, _, headers = fetch(url)
    assert status == 429
    assert headers["x-rate-limit-api-token-remaining"] == "0"
    assert fetch(url)[0] == 200
    fake_trello.fail_next("GET", "/ping/{name}", status=503)
    assert fetch(auth(fake_trello, "/other"))[0] == 404  # not matching, fault kept
    assert fetch(url)[0] == 503
    assert fetch(url)[0] == 200
    fake_trello.fail_next("GET", "/ping/a", status=401, body="invalid token")
    assert fetch(url)[:2] == (401, "invalid token")
    fake_trello.fail_next("GET", "/ping/a", status=None, delay=0.3)
    start = time.monotonic()
    assert fetch(url)[0] == 200
    assert time.monotonic() - start >= 0.25
    fake_trello.fail_next("GET", "/ping/a", status=200, body="<html>not json")
    assert fetch(url)[1] == "<html>not json"


@pytest.mark.tc("TC-M00-005")
def test_request_log_keeps_order_and_details(fake_trello: FakeTrello) -> None:
    add_ping(fake_trello)
    fetch(auth(fake_trello, "/ping/a") + "&x=1", data=b'{"a": 1}')
    fetch(auth(fake_trello, "/missing"))
    first, second = fake_trello.requests
    assert (first.method, first.path, first.raw_path) == ("POST", "/ping/a", "/1/ping/a")
    assert first.param("x") == "1"
    assert first.json() == {"a": 1}
    assert first.headers["user-agent"]
    assert first.query["key"] == [fake_trello.api_key]
    assert (second.method, second.path) == ("GET", "/missing")


@pytest.mark.tc("TC-M00-006")
def test_network_guard_names_external_host(
    network_guard: NetworkGuard, pytester: pytest.Pytester
) -> None:
    with pytest.raises(NetworkGuardError, match=r"203\.0\.113\.1"):
        socket.create_connection(("203.0.113.1", 80), timeout=1)
    assert network_guard.violations == ["203.0.113.1"]
    network_guard.clear()

    pytester.makeconftest('pytest_plugins = ["tests.harness"]')
    pytester.makepyfile(
        """
        import socket

        def test_swallows():
            try:
                socket.create_connection(("203.0.113.9", 80), timeout=1)
            except Exception:
                pass
        """
    )
    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*203.0.113.9*"])


@pytest.mark.tc("TC-M00-007")
def test_each_test_gets_own_home_and_clean_env(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = tmp_path / "record.jsonl"
    monkeypatch.setenv("M00_RECORD", str(record))
    for name in ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_BOARD"):
        monkeypatch.setenv(name, "real-value")
    monkeypatch.setenv("TRELLOCTL_HOME", "/real/home")
    pytester.makeconftest('pytest_plugins = ["tests.harness"]')
    pytester.makepyfile(
        """
        import json, os

        def _dump():
            env = {k: v for k, v in os.environ.items() if k.startswith("TRELLO")}
            with open(os.environ["M00_RECORD"], "a") as fh:
                fh.write(json.dumps({"cwd": os.getcwd(), "env": env}) + "\\n")

        def test_one():
            _dump()

        def test_two():
            _dump()
        """
    )
    pytester.runpytest_inprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)
    rows = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(rows) == 2
    homes = [r["env"]["TRELLOCTL_HOME"] for r in rows]
    assert homes[0] != homes[1]
    assert all(h != "/real/home" for h in homes)
    for row in rows:
        assert set(row["env"]) == {"TRELLOCTL_HOME"}


# Traceability checker ----------------------------------------------------

PRD = """# PRD
- AC‑1.1 First criterion.
- AC‑1.2 Second criterion.

| RNF‑1 | No dependencies |
"""
MASTER = """# Master
| [[M00-x\\|M00]] | 0 | - | w | w | w | w | w | {m00} | 1 | - |
| [[M01-y\\|M01]] | 1 | M00 | w | w | w | w | w | {m01} | 1 | - |

| A-1  | decidida | Topic | Rule |
"""
MANUAL = "| TC-MAN-01 | Thing | AC-1.1 a AC-1.2, RNF-1 | ~1 | x |\n"
ROW_M00 = "| TC-M00-001 | AC-1.1, A-1 | P | C | s | r |\n"
ROW_M01 = "| TC-M01-001 | AC-1.2 | P | C | s | r |\n"
YELLOW, WHITE, GREEN = "\U0001f7e1", "⚪", "\U0001f7e2"


def milestone(mid: str, status: str, deps: str, rows: str) -> str:
    return (
        f"---\nid: {mid}\nstatus: {status}\ndepends_on: [{deps}]\n---\n\n"
        "| TC | AC | Tipo | Nivel | Escenario | Resultado |\n| - | - | - | - | - | - |\n"
        f"{rows}"
    )


def make_repo(
    root: Path,
    *,
    prd: str = PRD,
    manual: str = MANUAL,
    m00: tuple[str, str] = ("in_progress", ROW_M00),
    m01: tuple[str, str, str] = ("pending", "M00", ROW_M01),
    tests: str = "",
    plan_status: tuple[str, str] = (YELLOW, WHITE),
) -> Path:
    plan = root / "docs" / "plan"
    plan.mkdir(parents=True)
    (root / "docs" / "PRD.md").write_text(prd, encoding="utf-8")
    (root / "docs" / "tc-manuales-trello.md").write_text(manual, encoding="utf-8")
    master = MASTER.format(m00=plan_status[0], m01=plan_status[1])
    (plan / "00-plan-maestro.md").write_text(master, encoding="utf-8")
    (plan / "M00-x.md").write_text(milestone("M00", m00[0], "", m00[1]), encoding="utf-8")
    (plan / "M01-y.md").write_text(milestone("M01", m01[0], m01[1], m01[2]), encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text(tests, encoding="utf-8")
    return root


@pytest.mark.tc("TC-M00-008")
def test_checker_fails_naming_uncovered_ac(tmp_path: Path) -> None:
    only_rnf = "| TC-MAN-01 | Thing | RNF-1 | ~1 | x |\n"
    assert ct.check(make_repo(tmp_path / "a", manual=only_rnf)).ok
    # M00 covers AC-1.1 and M01 covers AC-1.2: dropping M01's row leaves AC-1.2 uncovered.
    other = tmp_path / "b"
    make_repo(other, manual=only_rnf, m01=("pending", "M00", ""))
    errors = ct.check(other).errors
    assert any("AC-1.2" in e for e in errors)
    assert not any("AC-1.1" in e for e in errors)


@pytest.mark.tc("TC-M00-009")
def test_checker_fails_for_unmarked_tc_of_done_milestone(tmp_path: Path) -> None:
    make_repo(tmp_path, m00=("done", ROW_M00), plan_status=(GREEN, WHITE))
    result = ct.check(tmp_path)
    assert any("TC-M00-001" in e and "no test is marked" in e for e in result.errors)
    (tmp_path / "tests" / "test_a.py").write_text(
        '@pytest.mark.tc("TC-M00-001")\ndef test_x(): ...\n', encoding="utf-8"
    )
    assert ct.check(tmp_path).ok


@pytest.mark.tc("TC-M00-010")
def test_checker_fails_for_orphan_marker(tmp_path: Path) -> None:
    make_repo(tmp_path, tests='@pytest.mark.tc("TC-M99-001")\ndef test_x(): ...\n')
    result = ct.check(tmp_path)
    assert any("TC-M99-001" in e and "orphan" in e for e in result.errors)


@pytest.mark.tc("TC-M00-011")
def test_checker_normalises_non_breaking_hyphen(tmp_path: Path) -> None:
    assert "‑" in PRD
    make_repo(tmp_path)
    result = ct.check(tmp_path)
    assert result.ok, result.errors


@pytest.mark.tc("TC-M00-012")
def test_checker_status_mismatch_is_warning_and_done_needs_done_deps(tmp_path: Path) -> None:
    make_repo(tmp_path, plan_status=(GREEN, WHITE))
    result = ct.check(tmp_path)
    assert result.ok
    assert any("M00" in w for w in result.warnings)
    bad = tmp_path / "b"
    marked = '@pytest.mark.tc("TC-M00-001")\n@pytest.mark.tc("TC-M01-001")\ndef test_x(): ...\n'
    make_repo(bad, m01=("done", "M00", ROW_M01), tests=marked)
    result = ct.check(bad)
    assert any("M01 is done but depends on M00" in e for e in result.errors)


@pytest.mark.tc("TC-M00-013")
def test_python_m_trelloctl_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "trelloctl", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.startswith("trelloctl ")
    assert "Traceback" not in proc.stderr

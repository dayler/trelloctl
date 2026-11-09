"""M07 acceptance tests: board selection by name, `config board set` and `cache` commands."""

from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    OTHER,
    SHORT,
    URL,
    calls,
    close_caches,
    make_resolver,
    prime,
    run_json,
    seed_board,
    seed_standard,
    write_config,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

MEMBER_BOARDS = "/members/me/boards"


def reads_of_user_boards(fake: FakeTrello) -> int:
    return len(calls(fake, MEMBER_BOARDS))


def reload_archived(
    capsys: pytest.CaptureFixture[str], *flags: str
) -> tuple[int, dict[str, object]]:
    return run_json(capsys, *flags, "cache", "reload", "--archived")


# --- selecting the board (AC-1.8, AC-1.9, AC-7.1) ---


@pytest.mark.tc("TC-M07-017")
@pytest.mark.parametrize("reference", [BOARD, SHORT, URL, "wOrK", "WORK", "work"])
def test_board_by_id_short_link_url_and_name_in_any_case(
    reference: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", reference)
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["meta"]["board_id"] == BOARD  # type: ignore[index]


@pytest.mark.tc("TC-M07-018")
@pytest.mark.parametrize("reference", [BOARD, URL])
def test_board_by_id_or_url_never_reads_the_user_boards(
    reference: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", reference)
    assert (code, envelope["ok"]) == (0, True)
    assert reads_of_user_boards(fake_trello) == 0


@pytest.mark.tc("TC-M07-019")
def test_a_name_shared_by_two_boards_is_ambiguous(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello, BOARD, "Same", "sameAAA1")
    seed_board(fake_trello, OTHER, "same", "sameBBB2")
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", "SAME")
    assert (code, envelope["error"]["code"]) == (5, "AMBIGUOUS_BOARD")  # type: ignore[index]
    candidates = envelope["error"]["details"]["candidates"]  # type: ignore[index]
    assert {c["id"] for c in candidates} == {BOARD, OTHER}
    assert all({"id", "name", "context"} <= set(c) for c in candidates)
    assert calls(fake_trello, prefix=True, path="/boards/") == []


@pytest.mark.tc("TC-M07-020")
def test_an_unknown_board_name_reloads_the_user_boards_once(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", "Nope")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    assert envelope["error"]["details"]["board"] == "Nope"  # type: ignore[index]
    assert reads_of_user_boards(fake_trello) == 1  # cold cache: the load is the reload
    code, envelope = reload_archived(capsys, "--board", "Work")
    assert (code, envelope["ok"]) == (0, True)
    assert reads_of_user_boards(fake_trello) == 1  # warm cache: served from member_boards
    code, envelope = reload_archived(capsys, "--board", "Nope")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    assert reads_of_user_boards(fake_trello) == 2  # warm cache miss: one reload, one retry


@pytest.mark.tc("TC-M07-021")
def test_trello_board_variable_is_used_and_the_flag_wins(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    write_config(trelloctl_home, fake_trello)
    monkeypatch.setenv("TRELLO_BOARD", "Work")
    code, envelope = reload_archived(capsys)
    assert (code, envelope["meta"]["board_id"]) == (0, BOARD)  # type: ignore[index]
    code, envelope = reload_archived(capsys, "--board", "Other")
    assert (code, envelope["meta"]["board_id"]) == (0, OTHER)  # type: ignore[index]


# --- config board set (AC-1.10, AC-1.2) ---


@pytest.mark.tc("TC-M07-022")
@pytest.mark.parametrize("reference", [SHORT, URL, "work"])
def test_config_board_set_resolves_and_stores_the_id(
    reference: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    write_config(trelloctl_home, fake_trello, board=OTHER)
    code, envelope = run_json(capsys, "config", "board", "set", reference)
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["default"] == BOARD  # type: ignore[index]
    parsed = tomllib.loads((trelloctl_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["board"]["default"] == BOARD


@pytest.mark.tc("TC-M07-023")
@pytest.mark.parametrize("reference", ["Work", SHORT, URL])
def test_config_board_set_by_name_shortlink_or_url_needs_credentials(
    reference: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, auth=False)
    code, envelope = run_json(capsys, "config", "board", "set", reference)
    assert (code, envelope["error"]["code"]) == (4, "AUTH_MISSING")  # type: ignore[index]
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M07-024")
@pytest.mark.parametrize("status", [401, 403])
def test_a_denied_board_with_valid_credentials_is_board_not_found(
    status: int,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello, board=BOARD)
    fake_trello.fail_next("GET", f"/boards/{BOARD}", status, "unauthorized board")
    code, envelope = run_json(capsys, "cache", "reload")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    assert envelope["error"]["details"]["board"] == BOARD  # type: ignore[index]


# --- --no-cache and cache reuse ---


@pytest.mark.tc("TC-M07-041")
def test_no_cache_resolves_a_name_in_memory_without_creating_cache_db(
    trelloctl_home: Path, fake_trello: FakeTrello
) -> None:
    seed_standard(fake_trello)
    resolver = make_resolver(trelloctl_home, fake_trello, no_cache=True, board_flag="work")
    with resolver:
        assert resolver.selected_board() == BOARD
        assert calls(fake_trello, MEMBER_BOARDS) != []
    assert not (trelloctl_home / "cache.db").exists()


@pytest.mark.tc("TC-M07-043")
def test_a_second_reload_by_board_name_inside_the_ttl_skips_the_user_boards(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello)
    for expected in (1, 0):
        fake_trello.requests.clear()
        code, envelope = run_json(capsys, "--board", "Work", "cache", "reload", "--list", "Todo")
        assert (code, envelope["ok"]) == (0, True)
        assert reads_of_user_boards(fake_trello) == expected


@pytest.mark.tc("TC-M07-044")
def test_a_name_wins_over_a_short_link_and_the_short_link_is_the_fallback(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello, BOARD, "Personal", "zeta0001")
    seed_board(fake_trello, OTHER, "Other", "Personal")
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", "Personal")
    assert (code, envelope["meta"]["board_id"]) == (0, BOARD)  # type: ignore[index]
    del fake_trello.state.boards[BOARD]
    assert run_json(capsys, "cache", "clear")[0] == 0
    fake_trello.requests.clear()
    code, envelope = reload_archived(capsys, "--board", "Personal")
    assert (code, envelope["meta"]["board_id"]) == (0, OTHER)  # type: ignore[index]
    assert reads_of_user_boards(fake_trello) == 1
    assert calls(fake_trello, "/boards/Personal") == []


@pytest.mark.tc("TC-M07-049")
def test_a_short_link_is_found_in_the_cached_user_boards_without_extra_calls(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    write_config(trelloctl_home, fake_trello)
    for expected in (1, 0):
        fake_trello.requests.clear()
        code, envelope = reload_archived(capsys, "--board", SHORT)
        assert (code, envelope["meta"]["board_id"]) == (0, BOARD)  # type: ignore[index]
        assert reads_of_user_boards(fake_trello) == expected
        assert calls(fake_trello, f"/boards/{SHORT}") == []


# --- archived boards (A-24) ---


@pytest.mark.tc("TC-M07-045")
def test_an_archived_board_by_name_needs_the_include_option(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello, BOARD, "Viejo", "viejo001", closed=True)
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", "Viejo")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    assert reads_of_user_boards(fake_trello) == 1
    code, envelope = reload_archived(capsys, "--include-archived-boards", "--board", "Viejo")
    assert (code, envelope["meta"]["board_id"]) == (0, BOARD)  # type: ignore[index]
    code, envelope = reload_archived(capsys, "--board", "Viejo")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    assert reads_of_user_boards(fake_trello) == 2  # the miss on a warm cache reloaded once


@pytest.mark.tc("TC-M07-046")
def test_an_active_and_an_archived_board_with_one_name(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello, BOARD, "Proyecto", "activo01")
    seed_board(fake_trello, OTHER, "Proyecto", "archiv01", closed=True)
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", "Proyecto")
    assert (code, envelope["meta"]["board_id"]) == (0, BOARD)  # type: ignore[index]
    code, envelope = reload_archived(capsys, "--include-archived-boards", "--board", "Proyecto")
    assert (code, envelope["error"]["code"]) == (5, "AMBIGUOUS_BOARD")  # type: ignore[index]
    candidates = envelope["error"]["details"]["candidates"]  # type: ignore[index]
    assert {(c["id"], c["context"]) for c in candidates} == {
        (BOARD, "open"),
        (OTHER, "archived"),
    }


@pytest.mark.tc("TC-M07-047")
@pytest.mark.parametrize("reference", [OTHER, "archiv01", "https://trello.com/b/archiv01/old"])
def test_an_archived_board_is_reached_by_id_short_link_or_url(
    reference: str,
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seed_board(fake_trello, BOARD, "Active", "activo01")
    seed_board(fake_trello, OTHER, "Old", "archiv01", closed=True)
    write_config(trelloctl_home, fake_trello)
    code, envelope = reload_archived(capsys, "--board", reference)
    assert (code, envelope["meta"]["board_id"]) == (0, OTHER)  # type: ignore[index]


@pytest.mark.tc("TC-M07-048")
def test_config_board_set_with_the_include_option_stores_an_archived_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_board(fake_trello, BOARD, "Viejo", "viejo001", closed=True)
    write_config(trelloctl_home, fake_trello)
    code, envelope = run_json(capsys, "config", "board", "set", "Viejo")
    assert (code, envelope["error"]["code"]) == (5, "BOARD_NOT_FOUND")  # type: ignore[index]
    code, envelope = run_json(
        capsys, "--include-archived-boards", "config", "board", "set", "Viejo"
    )
    assert (code, envelope["data"]["default"]) == (0, BOARD)  # type: ignore[index]
    parsed = tomllib.loads((trelloctl_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["board"]["default"] == BOARD


# --- cache status and clear (AC-22.15, A-4) ---


@pytest.mark.tc("TC-M07-039")
def test_cache_status_reports_age_and_size_per_board_and_attachment_space(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_standard(fake_trello)
    seed_board(fake_trello, OTHER, "Other", "other001")
    write_config(trelloctl_home, fake_trello)
    prime(trelloctl_home, fake_trello, datetime.now(UTC))
    prime(trelloctl_home, fake_trello, datetime.now(UTC), OTHER)
    folder = trelloctl_home / "attachments" / BOARD / "card"
    folder.mkdir(parents=True)
    (folder / "file.bin").write_bytes(b"12345")
    code, envelope = run_json(capsys, "cache", "status")
    assert (code, envelope["ok"]) == (0, True)
    data = envelope["data"]  # type: ignore[index]
    assert {b["board_id"] for b in data["boards"]} == {BOARD, OTHER}
    work = next(b for b in data["boards"] if b["board_id"] == BOARD)
    assert work["name"] == "Work"
    assert work["metadata"]["lists"] == 2
    assert work["metadata"]["labels"] == 1
    assert work["metadata"]["members"] == 1
    assert work["cards"]["count"] == 2
    assert work["archived"]["count"] == 0
    assert all(isinstance(work[k]["age_seconds"], int) for k in ("metadata", "cards"))
    assert work["archived"]["age_seconds"] is None
    assert data["attachments_bytes"] == 5
    assert data["member_boards"]["count"] == 2
    assert data["database_bytes"] > 0
    assert fake_trello.requests != []  # only the priming above; status itself adds none
    before = len(fake_trello.requests)
    run_json(capsys, "cache", "status")
    assert len(fake_trello.requests) == before


@pytest.mark.tc("TC-M07-040")
def test_cache_status_and_clear_work_without_config_and_keep_attachments(
    trelloctl_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = Cache.open(trelloctl_home)
    cache.replace_snapshot(
        BOARD,
        {"name": "Work"},
        [{"id": "a" * 24, "name": "Todo", "pos": 1}],
        [],
        [],
        [],
        datetime.now(UTC),
    )
    folder = trelloctl_home / "attachments" / BOARD / "card"
    folder.mkdir(parents=True)
    kept = folder / "file.bin"
    kept.write_bytes(b"data")
    assert not (trelloctl_home / "config.toml").exists()
    code, envelope = run_json(capsys, "cache", "status")
    assert (code, envelope["ok"]) == (0, True)
    assert [b["board_id"] for b in envelope["data"]["boards"]] == [BOARD]  # type: ignore[index]
    code, envelope = run_json(capsys, "cache", "clear")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["attachments_kept"] is True  # type: ignore[index]
    assert cache.list_boards() == []
    assert kept.read_bytes() == b"data"
    code, envelope = run_json(capsys, "cache", "clear", "--attachments")
    assert (code, envelope["data"]["attachments_removed"]) == (0, 1)  # type: ignore[index]
    assert not kept.exists()
    assert (trelloctl_home / "attachments").is_dir()

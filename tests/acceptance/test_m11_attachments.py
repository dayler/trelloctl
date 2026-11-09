"""M11 acceptance tests: `card attachments list|download|add|clear` against the fake Trello."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from tests.acceptance.m07_support import (  # noqa: F401
    BOARD,
    OTHER,
    add_card,
    add_list,
    calls,
    close_caches,
    new_id,
    paths,
    prime,
    run_json,
    seed_board,
    seed_standard,
    write_config,
)
from tests.fakes.trello import FakeTrello

from trelloctl.infra.cache import Cache

DATE = "2026-01-01T00:00:00.000Z"
LATER = "2026-02-01T00:00:00.000Z"


def now() -> datetime:
    return datetime.now(UTC)


def add_att(
    fake: FakeTrello,
    card_id: str,
    name: str,
    content: bytes = b"data",
    *,
    upload: bool = True,
    date: str = DATE,
) -> str:
    """Seed an attachment of the fake; the download url is a Trello url, as in the real API."""
    ident = new_id(fake)
    if upload:
        url = (
            f"https://trello.com/1/cards/{card_id}/attachments/{ident}/download/"
            f"{quote(name, safe='')}"
        )
    else:
        url = "https://example.com/spec"
    fake.state.attachments[ident] = {
        "id": ident,
        "idCard": card_id,
        "name": name,
        "bytes": len(content) if upload else 0,
        "mimeType": "text/plain" if upload else "",
        "date": date,
        "url": url,
        "isUpload": upload,
        "content": content,
    }
    return ident


def download_template(card_id: str, att_id: str, name: str) -> str:
    return f"/cards/{card_id}/attachments/{att_id}/download/{name}"


def downloads(fake: FakeTrello) -> list[str]:
    return [p for p in paths(fake, "GET") if "/download/" in p]


def attachment_dir(home: Path, card_id: str, board_id: str = BOARD) -> Path:
    return home / "attachments" / board_id / card_id


def setup(
    home: Path, fake: FakeTrello, *, extra: str = "", timeout: int | None = None
) -> dict[str, str]:
    """Standard board, config with the default board, and a primed cache of the board."""
    ids = seed_standard(fake)
    write_config(home, fake, board=BOARD, extra=extra, timeout=timeout)
    return ids


def prime_and_reset(home: Path, fake: FakeTrello) -> None:
    prime(home, fake, now())
    fake.requests.clear()


def cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    return run_json(capsys, "card", "attachments", *argv)


# --- list (AC-21.1) ---


@pytest.mark.tc("TC-M11-001")
def test_list_serves_the_cache_and_fresh_reads_the_card(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    file_id = add_att(fake_trello, ids["c1"], "report.txt", b"12345")
    link_id = add_att(fake_trello, ids["c1"], "spec", upload=False)
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(capsys, "list", ids["c1"])
    assert (code, envelope["ok"]) == (0, True)
    assert fake_trello.requests == []
    by_id = {a["id"]: a for a in envelope["data"]["attachments"]}
    assert by_id[file_id] == {
        "id": file_id,
        "name": "report.txt",
        "bytes": 5,
        "mimeType": "text/plain",
        "date": DATE,
        "url": fake_trello.state.attachments[file_id]["url"],
        "isUpload": True,
        "local_path": None,
    }
    assert by_id[link_id]["isUpload"] is False
    assert by_id[link_id]["url"] == "https://example.com/spec"
    assert by_id[link_id]["local_path"] is None
    assert envelope["meta"]["cache"]["hit"] is True

    code, envelope = cli(capsys, "list", ids["c1"], "--fresh")
    assert (code, envelope["ok"]) == (0, True)
    assert paths(fake_trello) == [f"/cards/{ids['c1']}"]
    assert len(envelope["data"]["attachments"]) == 2
    assert envelope["meta"]["cache"]["hit"] is False


# --- download (AC-21.2) ---


@pytest.mark.tc("TC-M11-002")
def test_download_by_id_writes_the_file_with_the_oauth_header(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "report.txt", b"hello world")
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(capsys, "download", ids["c1"], "--id", att)
    assert (code, envelope["ok"]) == (0, True)
    target = attachment_dir(trelloctl_home, ids["c1"]) / f"{att}_report.txt"
    assert target.read_bytes() == b"hello world"
    assert [d["id"] for d in envelope["data"]["downloaded"]] == [att]
    request = calls(fake_trello, prefix=True, path=f"/cards/{ids['c1']}/attachments/")[0]
    header = request.headers["authorization"]
    assert header == (
        f'OAuth oauth_consumer_key="{fake_trello.api_key}", oauth_token="{fake_trello.token}"'
    )
    assert "key" not in request.query
    assert "token" not in request.query
    code, listing = cli(capsys, "list", ids["c1"])
    assert Path(listing["data"]["attachments"][0]["local_path"]) == target


@pytest.mark.tc("TC-M11-003")
def test_download_of_an_external_link_is_skipped(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    link = add_att(fake_trello, ids["c1"], "spec", upload=False)
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(capsys, "download", ids["c1"], "--id", link)
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["downloaded"] == []
    assert [(s["id"], s["url"]) for s in envelope["data"]["skipped"]] == [
        (link, "https://example.com/spec")
    ]
    assert downloads(fake_trello) == []


# --- download --all (AC-21.3) ---


@pytest.mark.tc("TC-M11-004")
def test_download_all_synchronizes_new_changed_equal_and_orphans(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    card = ids["c1"]
    equal = add_att(fake_trello, card, "equal.txt", b"same")
    changed = add_att(fake_trello, card, "changed.txt", b"old")
    orphan = add_att(fake_trello, card, "orphan.txt", b"gone")
    link = add_att(fake_trello, card, "spec", upload=False)
    prime_and_reset(trelloctl_home, fake_trello)
    code, first = cli(capsys, "download", card, "--all")
    assert (code, first["ok"]) == (0, True)
    folder = attachment_dir(trelloctl_home, card)
    assert len(list(folder.iterdir())) == 3

    del fake_trello.state.attachments[orphan]
    fake_trello.state.attachments[changed].update(content=b"newer bytes", bytes=11)
    fresh = add_att(fake_trello, card, "fresh.txt", b"brand new")
    fake_trello.requests.clear()
    mtime = (folder / f"{equal}_equal.txt").stat().st_mtime_ns

    code, envelope = cli(capsys, "download", card, "--all")
    assert (code, envelope["ok"]) == (0, True)
    data = envelope["data"]
    assert [p for p in paths(fake_trello, "GET") if "/download/" not in p] == [f"/cards/{card}"]
    assert len(downloads(fake_trello)) == 2
    assert {d["id"] for d in data["downloaded"]} == {changed, fresh}
    assert [k["id"] for k in data["kept"]] == [equal]
    assert [s["id"] for s in data["skipped"]] == [link]
    assert [d["id"] for d in data["deleted"]] == [orphan]
    assert (folder / f"{changed}_changed.txt").read_bytes() == b"newer bytes"
    assert (folder / f"{fresh}_fresh.txt").read_bytes() == b"brand new"
    assert (folder / f"{equal}_equal.txt").stat().st_mtime_ns == mtime
    assert not (folder / f"{orphan}_orphan.txt").exists()


@pytest.mark.tc("TC-M11-005")
def test_same_bytes_with_a_different_date_is_downloaded_again(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "a.txt", b"identical")
    prime_and_reset(trelloctl_home, fake_trello)
    assert cli(capsys, "download", ids["c1"], "--all")[0] == 0
    fake_trello.state.attachments[att]["date"] = LATER
    fake_trello.requests.clear()

    code, envelope = cli(capsys, "download", ids["c1"], "--all")
    assert (code, envelope["ok"]) == (0, True)
    assert len(downloads(fake_trello)) == 1
    assert [d["id"] for d in envelope["data"]["downloaded"]] == [att]
    assert envelope["data"]["kept"] == []


@pytest.mark.tc("TC-M11-006")
def test_rate_limit_stops_the_serial_download_and_reports_the_partial_result(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    card = ids["c1"]
    first = add_att(fake_trello, card, "one.txt", b"1")
    second = add_att(fake_trello, card, "two.txt", b"2")
    third = add_att(fake_trello, card, "three.txt", b"3")
    prime_and_reset(trelloctl_home, fake_trello)
    fake_trello.rate_limit("GET", download_template(card, second, "two.txt"))

    code, envelope = cli(capsys, "download", card, "--all")
    assert code == 7
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "RATE_LIMITED"
    partial = envelope["error"]["details"]["partial"]
    assert [d["id"] for d in partial["downloaded"]] == [first]
    assert partial["kept"] == []
    assert partial["skipped"] == []
    assert partial["deleted"] == []
    assert len(downloads(fake_trello)) == 2
    assert not any(third in p for p in downloads(fake_trello))
    assert (attachment_dir(trelloctl_home, card) / f"{first}_one.txt").is_file()


@pytest.mark.tc("TC-M11-007")
def test_download_of_an_attachment_deleted_in_trello_removes_the_local_copy(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "doomed.txt", b"bytes")
    prime_and_reset(trelloctl_home, fake_trello)
    assert cli(capsys, "download", ids["c1"], "--id", att)[0] == 0
    local = attachment_dir(trelloctl_home, ids["c1"]) / f"{att}_doomed.txt"
    assert local.is_file()
    del fake_trello.state.attachments[att]

    code, envelope = cli(capsys, "download", ids["c1"], "--id", att)
    assert code == 5
    assert envelope["error"]["code"] == "ATTACHMENT_GONE"
    assert not local.exists()
    assert not attachment_dir(trelloctl_home, ids["c1"]).exists()


@pytest.mark.tc("TC-M11-008")
def test_download_of_an_unknown_attachment_is_not_found(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    add_att(fake_trello, ids["c1"], "a.txt")
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(capsys, "download", ids["c1"], "--id", "f" * 24)
    assert code == 5
    assert envelope["error"]["code"] == "ATTACHMENT_NOT_FOUND"
    assert downloads(fake_trello) == []


@pytest.mark.tc("TC-M11-009")
def test_download_needs_exactly_one_of_all_and_id(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "a.txt")
    prime_and_reset(trelloctl_home, fake_trello)

    for argv in (("download", ids["c1"]), ("download", ids["c1"], "--all", "--id", att)):
        code, envelope = cli(capsys, *argv)
        assert code == 2
        assert envelope["error"]["code"] == "USAGE"
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M11-010")
def test_hostile_attachment_names_stay_inside_the_card_directory(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    first = add_att(fake_trello, ids["c1"], "../../x", b"one")
    second = add_att(fake_trello, ids["c1"], "a:b*?.txt", b"two")
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(capsys, "download", ids["c1"], "--all")
    assert (code, envelope["ok"]) == (0, True)
    folder = attachment_dir(trelloctl_home, ids["c1"])
    saved = {p.name: p.read_bytes() for p in folder.iterdir()}
    assert len(saved) == 2
    assert sorted(saved.values()) == [b"one", b"two"]
    assert all(n.startswith((f"{first}_", f"{second}_")) for n in saved)
    assert not any(c in n for n in saved for c in '/\\:*?"<>|')
    assert ".." not in "".join(saved)
    outside = [
        p
        for p in trelloctl_home.parent.rglob("*")
        if p.is_file() and folder not in p.parents and trelloctl_home not in p.parents
    ]
    assert outside == []
    in_home = [
        p
        for p in trelloctl_home.rglob("*")
        if p.is_file()
        and folder not in p.parents
        and p.name != "config.toml"
        and "logs" not in p.relative_to(trelloctl_home).parts
        and not p.name.startswith("cache.db")
    ]
    assert in_home == []


# --- add (AC-21.5) ---


@pytest.mark.tc("TC-M11-011")
def test_add_file_posts_multipart_and_updates_the_cache(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    prime_and_reset(trelloctl_home, fake_trello)
    report = tmp_path / "informe.pdf"
    report.write_bytes(b"%PDF-1.4 fake")

    code, envelope = cli(capsys, "add", ids["c1"], "--file", str(report), "--name", "Informe")
    assert (code, envelope["ok"]) == (0, True)
    posts = calls(fake_trello, f"/cards/{ids['c1']}/attachments", "POST")
    assert len(posts) == 1
    assert posts[0].headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b"%PDF-1.4 fake" in posts[0].body
    created = envelope["data"]["attachment"]
    assert (created["name"], created["bytes"], created["isUpload"]) == ("Informe", 13, True)
    stored = {r["id"]: r for r in Cache.open(trelloctl_home).list_attachments(BOARD, ids["c1"])}
    assert stored[created["id"]]["name"] == "Informe"
    assert fake_trello.state.attachments[created["id"]]["content"] == b"%PDF-1.4 fake"


@pytest.mark.tc("TC-M11-012")
def test_add_url_attaches_a_link(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    prime_and_reset(trelloctl_home, fake_trello)

    code, envelope = cli(
        capsys, "add", ids["c1"], "--url", "https://example.com", "--name", "Enlace"
    )
    assert (code, envelope["ok"]) == (0, True)
    post = calls(fake_trello, f"/cards/{ids['c1']}/attachments", "POST")[0]
    assert post.param("url") == "https://example.com"
    assert post.param("name") == "Enlace"
    created = envelope["data"]["attachment"]
    assert (created["name"], created["url"], created["isUpload"]) == (
        "Enlace",
        "https://example.com",
        False,
    )
    stored = Cache.open(trelloctl_home).list_attachments(BOARD, ids["c1"])
    assert [(r["id"], r["is_upload"]) for r in stored] == [(created["id"], 0)]


LIMIT = "\n[attachments]\nmax_upload_bytes = 10\n"


@pytest.mark.tc("TC-M11-013")
def test_a_file_over_the_limit_is_refused_without_calling_the_api(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ids = setup(trelloctl_home, fake_trello, extra=LIMIT)
    prime_and_reset(trelloctl_home, fake_trello)
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * 11)

    code, envelope = cli(capsys, "add", ids["c1"], "--file", str(big))
    assert code == 6
    assert envelope["error"]["code"] == "ATTACHMENT_TOO_LARGE"
    assert envelope["error"]["details"]["max_upload_bytes"] == 10
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M11-014")
def test_a_file_of_exactly_the_limit_is_uploaded(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ids = setup(trelloctl_home, fake_trello, extra=LIMIT)
    prime_and_reset(trelloctl_home, fake_trello)
    exact = tmp_path / "exact.bin"
    exact.write_bytes(b"x" * 10)

    code, envelope = cli(capsys, "add", ids["c1"], "--file", str(exact))
    assert (code, envelope["ok"]) == (0, True)
    assert len(calls(fake_trello, f"/cards/{ids['c1']}/attachments", "POST")) == 1
    assert envelope["data"]["attachment"]["bytes"] == 10


@pytest.mark.tc("TC-M11-015")
def test_add_rejects_both_sources_and_a_missing_file(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    prime_and_reset(trelloctl_home, fake_trello)
    existing = tmp_path / "a.txt"
    existing.write_bytes(b"a")

    both = cli(capsys, "add", ids["c1"], "--file", str(existing), "--url", "https://example.com")
    missing = cli(capsys, "add", ids["c1"], "--file", str(tmp_path / "missing.txt"))
    folder = cli(capsys, "add", ids["c1"], "--file", str(tmp_path))
    for code, envelope in (both, missing, folder):
        assert code == 2
        assert envelope["error"]["code"] == "USAGE"
    assert fake_trello.requests == []


# --- clear (AC-21.6, AC-22.15, A-4) ---


@pytest.mark.tc("TC-M11-016")
def test_clear_deletes_files_nulls_the_path_and_prunes_empty_directories(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    only = add_att(fake_trello, ids["c1"], "only.txt", b"1")
    keep = add_att(fake_trello, ids["c2"], "keep.txt", b"2")
    drop = add_att(fake_trello, ids["c2"], "drop.txt", b"3")
    prime_and_reset(trelloctl_home, fake_trello)
    for card in (ids["c1"], ids["c2"]):
        assert cli(capsys, "download", card, "--all")[0] == 0
    fake_trello.requests.clear()
    cache = Cache.open(trelloctl_home)

    code, envelope = cli(capsys, "clear", ids["c1"])
    assert (code, envelope["ok"]) == (0, True)
    assert [c["id"] for c in envelope["data"]["cleared"]] == [only]
    assert not attachment_dir(trelloctl_home, ids["c1"]).exists()
    assert cache.list_attachments(BOARD, ids["c1"])[0]["local_path"] is None
    assert attachment_dir(trelloctl_home, ids["c2"]).is_dir()

    code, envelope = cli(capsys, "clear", ids["c2"], "--id", drop)
    assert (code, envelope["ok"]) == (0, True)
    folder = attachment_dir(trelloctl_home, ids["c2"])
    assert [p.name for p in folder.iterdir()] == [f"{keep}_keep.txt"]
    rows = {r["id"]: r for r in cache.list_attachments(BOARD, ids["c2"])}
    assert rows[drop]["local_path"] is None
    assert rows[keep]["local_path"] is not None

    code, envelope = cli(capsys, "clear", ids["c2"], "--id", keep)
    assert (code, envelope["ok"]) == (0, True)
    assert not (trelloctl_home / "attachments" / BOARD).exists()
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M11-017")
def test_cache_clear_attachments_removes_files_of_every_board(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    seed_board(fake_trello, OTHER, "Other", "efgh5678")
    other_list = add_list(fake_trello, "Inbox", 1, OTHER)
    other_card = add_card(fake_trello, other_list, "Elsewhere", id_short=1, board_id=OTHER)
    add_att(fake_trello, ids["c1"], "one.txt", b"1")
    add_att(fake_trello, other_card, "two.txt", b"22")
    prime(trelloctl_home, fake_trello, now())
    prime(trelloctl_home, fake_trello, now(), OTHER)
    assert cli(capsys, "download", ids["c1"], "--all")[0] == 0
    assert cli(capsys, "download", other_card, "--all")[0] == 0
    root = trelloctl_home / "attachments"
    assert len([p for p in root.rglob("*") if p.is_file()]) == 2

    code, envelope = run_json(capsys, "cache", "clear", "--attachments")
    assert (code, envelope["ok"]) == (0, True)
    assert list(root.iterdir()) == []


@pytest.mark.tc("TC-M11-018")
def test_clear_works_without_config_toml(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "one.txt", b"1")
    prime_and_reset(trelloctl_home, fake_trello)
    assert cli(capsys, "download", ids["c1"], "--all")[0] == 0
    (trelloctl_home / "config.toml").unlink()
    fake_trello.requests.clear()

    code, envelope = run_json(capsys, "--board", BOARD, "card", "attachments", "clear", ids["c1"])
    assert (code, envelope["ok"]) == (0, True)
    assert [c["id"] for c in envelope["data"]["cleared"]] == [att]
    assert not attachment_dir(trelloctl_home, ids["c1"]).exists()
    assert fake_trello.requests == []


@pytest.mark.tc("TC-M11-019")
def test_cache_status_reports_the_bytes_of_the_downloaded_files(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    sizes = [3, 10, 25]
    for index, size in enumerate(sizes):
        add_att(fake_trello, ids["c1"], f"f{index}.bin", b"x" * size)
    prime_and_reset(trelloctl_home, fake_trello)
    assert cli(capsys, "download", ids["c1"], "--all")[0] == 0

    code, envelope = run_json(capsys, "cache", "status")
    assert (code, envelope["ok"]) == (0, True)
    assert envelope["data"]["attachments_bytes"] == sum(sizes)


@pytest.mark.tc("TC-M11-020")
def test_clear_never_deletes_a_file_it_did_not_download(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello)
    att = add_att(fake_trello, ids["c1"], "mine.txt", b"1")
    prime_and_reset(trelloctl_home, fake_trello)
    assert cli(capsys, "download", ids["c1"], "--all")[0] == 0
    folder = attachment_dir(trelloctl_home, ids["c1"])
    foreign = folder / "notes-from-the-user.txt"
    foreign.write_text("keep me", encoding="utf-8")

    code, envelope = cli(capsys, "clear", ids["c1"])
    assert (code, envelope["ok"]) == (0, True)
    assert foreign.read_text(encoding="utf-8") == "keep me"
    assert not (folder / f"{att}_mine.txt").exists()
    assert [p.name for p in folder.iterdir()] == [foreign.name]


# --- timeouts and logging (AC-21.2) ---


@pytest.mark.tc("TC-M11-021")
def test_a_slow_download_times_out_without_retry_and_logs_with_the_token_masked(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = setup(trelloctl_home, fake_trello, timeout=1)
    slow = add_att(fake_trello, ids["c1"], "slow.txt", b"zzz")
    quick = add_att(fake_trello, ids["c1"], "quick.txt", b"qqq")
    prime_and_reset(trelloctl_home, fake_trello)
    fake_trello.fail_next(
        "GET", download_template(ids["c1"], slow, "slow.txt"), status=None, delay=3
    )

    code, envelope = run_json(
        capsys,
        "--log-level",
        "debug",
        "card",
        "attachments",
        "download",
        ids["c1"],
        "--id",
        slow,
    )
    assert code == 8
    assert envelope["error"]["code"] == "TIMEOUT"
    assert len(downloads(fake_trello)) == 1

    code, envelope = run_json(
        capsys,
        "--log-level",
        "debug",
        "card",
        "attachments",
        "download",
        ids["c1"],
        "--id",
        quick,
    )
    assert (code, envelope["ok"]) == (0, True)
    text = "".join(p.read_text(encoding="utf-8") for p in (trelloctl_home / "logs").glob("*"))
    lines = [line for line in text.splitlines() if "/download/" in line]
    assert len(lines) == 2
    assert any("status=200" in line for line in lines)
    assert any("status=error" in line for line in lines)
    assert fake_trello.token not in text
    assert json.dumps(envelope).count(fake_trello.token) == 0

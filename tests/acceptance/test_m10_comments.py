"""M10 acceptance tests: comments of a card (RF-17)."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.acceptance.m10_support import (
    ME,
    FakeTrello,
    calls,
    card,
    close_caches,  # noqa: F401
    comment,
    error_code,
    finish,
    request_params,
    run,
    seed_world,
    writes,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "m10_markdown_comment.md"


def world(fake: FakeTrello, **kwargs: object) -> tuple[dict[str, str], str]:
    ids = seed_world(fake)
    return ids, card(fake, ids["todo"], "X", 1, **kwargs)  # type: ignore[arg-type]


def reads(fake: FakeTrello, target: str) -> list[object]:
    return calls(fake, f"/cards/{target}/actions")  # type: ignore[return-value]


@pytest.mark.tc("TC-M10-001")
def test_comments_are_limited_paged_and_projected(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    for index in range(1200):
        comment(fake_trello, target, f"c{index}")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comments", target)
    assert (code, out["ok"]) == (0, True)
    assert len(out["data"]["comments"]) == 50
    assert out["meta"]["truncated"] is True
    first = out["data"]["comments"][0]
    assert set(first) == {"id", "text", "date", "memberId", "member"}
    assert first["text"] == "c1199"
    assert first["date"]
    assert first["memberId"]
    fake_trello.requests.clear()
    code, out = run(capsys, "card", "comments", target, "--limit", "0")
    assert (code, out["ok"]) == (0, True)
    assert len(out["data"]["comments"]) == 1200
    assert len({c["id"] for c in out["data"]["comments"]}) == 1200
    assert out["meta"]["truncated"] is False
    assert len(reads(fake_trello, target)) == 2
    code, out = run(capsys, "card", "comments", target, "--fields", "id,text", "--limit", "2")
    assert (code, out["ok"]) == (0, True)
    assert all(set(c) == {"id", "text"} for c in out["data"]["comments"])


@pytest.mark.tc("TC-M10-002")
def test_comments_are_never_served_from_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    comment(fake_trello, target, "hello")
    finish(trelloctl_home, fake_trello)
    for _ in range(2):
        code, out = run(capsys, "card", "comments", target)
        assert (code, out["ok"]) == (0, True)
        assert [c["text"] for c in out["data"]["comments"]] == ["hello"]
    assert len(reads(fake_trello, target)) == 2


@pytest.mark.tc("TC-M10-003")
def test_add_sends_the_text_literally_with_the_mention(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "add", target, "--text", "hola @ana")
    assert (code, out["ok"]) == (0, True)
    new_id = out["data"]["id"]
    assert new_id in fake_trello.state.actions
    assert fake_trello.state.actions[new_id]["data"]["text"] == "hola @ana"
    (post,) = calls(fake_trello, f"/cards/{target}/actions/comments", "POST")
    assert request_params(post) == {"text": "hola @ana"}
    assert out["data"]["text"] == "hola @ana"
    assert out["data"]["memberId"] == ME


@pytest.mark.tc("TC-M10-004")
def test_text_comes_from_a_file_or_from_standard_input(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    text = "línea uno ñ\n日本語 €\n\nlast"
    path = tmp_path / "comment.txt"
    path.write_bytes(text.encode("utf-8"))
    code, out = run(capsys, "card", "comment", "add", target, "--text-file", str(path))
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.state.actions[out["data"]["id"]]["data"]["text"] == text
    code, out = run(
        capsys, "card", "comment", "add", target, "--text", "-", stdin=text.encode("utf-8")
    )
    assert (code, out["ok"]) == (0, True)
    assert fake_trello.state.actions[out["data"]["id"]]["data"]["text"] == text


@pytest.mark.tc("TC-M10-005")
def test_conflicting_or_unreadable_text_sources_are_usage_errors(
    trelloctl_home: Path,
    fake_trello: FakeTrello,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    both = ("card", "comment", "add", target, "--text", "a", "--text-file", str(tmp_path / "f"))
    code, out = run(capsys, *both)
    assert (code, error_code(out)) == (2, "USAGE")
    missing = ("card", "comment", "add", target, "--text-file", str(tmp_path / "nope.txt"))
    code, out = run(capsys, *missing)
    assert (code, error_code(out)) == (2, "USAGE")
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-006")
def test_edit_replaces_the_text_of_an_own_comment(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    own = comment(fake_trello, target, "old", member=ME)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "edit", target, own, "--text", "nuevo")
    assert (code, out["ok"]) == (0, True)
    (put,) = calls(fake_trello, f"/cards/{target}/actions/{own}/comments", "PUT")
    assert request_params(put) == {"text": "nuevo"}
    assert fake_trello.state.actions[own]["data"]["text"] == "nuevo"
    assert out["data"]["text"] == "nuevo"


@pytest.mark.tc("TC-M10-007")
def test_remove_deletes_an_own_comment(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    own = comment(fake_trello, target, "bye", member=ME)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "remove", target, own, "--yes")
    assert (code, out["ok"]) == (0, True)
    assert out["data"]["deleted"] == own
    assert own not in fake_trello.state.actions
    assert len(calls(fake_trello, f"/cards/{target}/actions/{own}/comments", "DELETE")) == 1


@pytest.mark.tc("TC-M10-008")
def test_comments_of_other_users_are_refused_by_trello(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    foreign = comment(fake_trello, target, "theirs")
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "edit", target, foreign, "--text", "mine")
    assert (code, error_code(out)) == (7, "API_ERROR")
    assert "unauthorized" in out["error"]["details"]["upstream"]
    code, out = run(capsys, "card", "comment", "remove", target, foreign, "--yes")
    assert (code, error_code(out)) == (7, "API_ERROR")
    assert "unauthorized" in out["error"]["details"]["upstream"]
    assert fake_trello.state.actions[foreign]["data"]["text"] == "theirs"


@pytest.mark.tc("TC-M10-009")
def test_remove_without_yes_asks_for_confirmation(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    own = comment(fake_trello, target, "keep", member=ME)
    finish(trelloctl_home, fake_trello)
    code, out = run(capsys, "card", "comment", "remove", target, own)
    assert error_code(out) == "CONFIRMATION_REQUIRED"
    assert code != 0
    assert writes(fake_trello) == []
    assert own in fake_trello.state.actions


@pytest.mark.tc("TC-M10-010")
def test_a_comment_is_never_found_by_its_text(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    comment(fake_trello, target, "texto del comentario", member=ME)
    finish(trelloctl_home, fake_trello)
    code, out = run(
        capsys, "card", "comment", "edit", target, "texto del comentario", "--text", "x"
    )
    assert error_code(out) == "COMMENT_NOT_FOUND"
    assert code != 0
    assert writes(fake_trello) == []


@pytest.mark.tc("TC-M10-011")
def test_the_comment_count_is_kept_up_to_date_in_the_cache(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    existing = comment(fake_trello, target, "one", member=ME)
    fake_trello.state.cards[target]["badges"]["comments"] = 1
    finish(trelloctl_home, fake_trello)

    def badge() -> int:
        fake_trello.requests.clear()
        code, out = run(capsys, "card", "get", target)
        assert (code, out["ok"]) == (0, True)
        assert fake_trello.requests == []
        return int(out["data"]["badges"]["comments"])

    assert badge() == 1
    code, out = run(capsys, "card", "comment", "add", target, "--text", "two")
    assert (code, out["ok"]) == (0, True)
    assert badge() == 2
    code, out = run(capsys, "card", "comment", "edit", target, existing, "--text", "uno")
    assert (code, out["ok"]) == (0, True)
    assert badge() == 2
    code, out = run(capsys, "card", "comment", "remove", target, existing, "--yes")
    assert (code, out["ok"]) == (0, True)
    assert badge() == 1


@pytest.mark.tc("TC-M10-039")
def test_markdown_is_sent_and_returned_byte_for_byte(
    trelloctl_home: Path, fake_trello: FakeTrello, capsys: pytest.CaptureFixture[str]
) -> None:
    _, target = world(fake_trello)
    finish(trelloctl_home, fake_trello)
    text = FIXTURE.read_bytes().decode("utf-8")
    code, out = run(capsys, "card", "comment", "add", target, "--text-file", str(FIXTURE))
    assert (code, out["ok"]) == (0, True)
    new_id = out["data"]["id"]
    (post,) = calls(fake_trello, f"/cards/{target}/actions/comments", "POST")
    assert request_params(post)["text"] == text
    assert fake_trello.state.actions[new_id]["data"]["text"] == text
    code, out = run(capsys, "card", "comments", target)
    assert out["data"]["comments"][0]["text"] == text
    edited = text + "\nEdited: **again** @ana\n"
    code, out = run(
        capsys,
        "card",
        "comment",
        "edit",
        target,
        new_id,
        "--text",
        "-",
        stdin=edited.encode("utf-8"),
    )
    assert (code, out["ok"]) == (0, True)
    (put,) = calls(fake_trello, f"/cards/{target}/actions/{new_id}/comments", "PUT")
    assert request_params(put)["text"] == edited
    assert fake_trello.state.actions[new_id]["data"]["text"] == edited
    code, out = run(capsys, "card", "comments", target)
    assert out["data"]["comments"][0]["text"] == edited

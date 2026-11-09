"""M16 acceptance tests: the error catalog end to end, exit codes and English messages."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from tests.acceptance.m07_support import close_caches, prime, probe_registry  # noqa: F401
from tests.acceptance.m08_support import ago
from tests.acceptance.m09_support import BOARD as BOARD_ID
from tests.acceptance.m09_support import (
    add_checklist,
    add_label,
    add_list,
    add_member,
    card,
    seed_board,
)
from tests.acceptance.m16_support import (
    CORE,
    DONE,
    REVIEW,
    TODO,
    UPLOAD,
    Env,
    assert_english,
    environment,
)
from tests.fakes.trello.inbox import hide_inbox

from trelloctl.cli import main
from trelloctl.domain.errors import CATALOG, TrelloctlError

# PRD 10.5: exit code of every error code.
TABLE = {
    1: ["INTERNAL"],
    2: ["USAGE"],
    3: ["CONFIG_NOT_FOUND", "CONFIG_INVALID", "BOARD_NOT_CONFIGURED", "HOME_NOT_WRITABLE"],
    4: ["AUTH_MISSING", "AUTH_INVALID"],
    5: [
        "BOARD_NOT_FOUND",
        "LIST_NOT_FOUND",
        "CARD_NOT_FOUND",
        "LABEL_NOT_FOUND",
        "MEMBER_NOT_FOUND",
        "CHECKLIST_NOT_FOUND",
        "CHECKITEM_NOT_FOUND",
        "COMMENT_NOT_FOUND",
        "ATTACHMENT_NOT_FOUND",
        "ATTACHMENT_GONE",
        "AMBIGUOUS_BOARD",
        "AMBIGUOUS_LIST",
        "AMBIGUOUS_CARD",
        "AMBIGUOUS_LABEL",
        "AMBIGUOUS_MEMBER",
        "AMBIGUOUS_CHECKLIST",
        "AMBIGUOUS_CHECKITEM",
        "INBOX_UNAVAILABLE",
    ],
    6: ["MOVE_OUT_OF_RANGE", "CONFIRMATION_REQUIRED", "ATTACHMENT_TOO_LARGE"],
    7: ["API_ERROR", "RATE_LIMITED"],
    8: ["NETWORK_ERROR", "TIMEOUT"],
    9: ["CACHE_STALE"],
}
EXIT_OF = {code: exit_code for exit_code, codes in TABLE.items() for code in codes}
Case = Callable[[Env, Path, pytest.MonkeyPatch], tuple[int, dict[str, Any]]]
CASES: dict[str, Case] = {}


def case(code: str) -> Callable[[Case], Case]:
    def register(fn: Case) -> Case:
        assert code not in CASES
        CASES[code] = fn
        return fn

    return register


def card_create(env: Env) -> tuple[int, dict[str, Any]]:
    return env.run("card", "create", "--list", "To Do", "--name", "Probe")


@case("INTERNAL")
def _internal(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    def boom(ctx: object, args: object) -> dict[str, Any]:
        raise RuntimeError("kaboom")

    registry = probe_registry(boom)
    code = main(["probe", "read"], registry=registry)
    import json

    return code, json.loads(env.capsys.readouterr().out)


@case("USAGE")
def _usage(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "move", REVIEW, "--to", "Done", "--right", "1")


@case("CONFIG_NOT_FOUND")
def _config_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    (env.home / "config.toml").unlink()
    return env.run("board", "show")


@case("CONFIG_INVALID")
def _config_invalid(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    (env.home / "config.toml").write_text("[auth\nbroken", encoding="utf-8")
    return env.run("board", "show")


@case("BOARD_NOT_CONFIGURED")
def _board_not_configured(
    env: Env, tmp: Path, mp: pytest.MonkeyPatch
) -> tuple[int, dict[str, Any]]:
    (env.home / "config.toml").write_text(
        f'[auth]\napi_key = "{env.fake.api_key}"\ntoken = "{env.fake.token}"\n'
        f'[http]\nbase_url = "{env.fake.base_url}"\n',
        encoding="utf-8",
    )
    return env.run("board", "show")


@case("HOME_NOT_WRITABLE")
def _home_not_writable(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    target = tmp / "a-file"
    target.write_text("x", encoding="utf-8")
    mp.setenv("TRELLOCTL_HOME", str(target))
    return env.run("config", "show")


@case("AUTH_MISSING")
def _auth_missing(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    env.write(auth=False)
    return env.run("board", "show")


@case("AUTH_INVALID")
def _auth_invalid(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    mp.setenv("TRELLO_TOKEN", "not-the-token")
    return env.run("board", "show", "--fresh")


@case("BOARD_NOT_FOUND")
def _board_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("--board", "6f" + "0" * 22, "board", "show")


@case("LIST_NOT_FOUND")
def _list_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("board", "list", "archive", "No such list")


@case("CARD_NOT_FOUND")
def _card_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "get", "No such card")


@case("LABEL_NOT_FOUND")
def _label_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "label", "add", REVIEW, "no-such-label")


@case("MEMBER_NOT_FOUND")
def _member_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "member", "add", REVIEW, "nobody")


@case("CHECKLIST_NOT_FOUND")
def _checklist_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "checklist", "rename", REVIEW, "No such checklist", "--name", "x")


@case("CHECKITEM_NOT_FOUND")
def _checkitem_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "checklist", "item", "check", REVIEW, "QA", "no such item")


@case("COMMENT_NOT_FOUND")
def _comment_not_found(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "comment", "edit", REVIEW, "6c" + "0" * 22, "--text", "x")


@case("ATTACHMENT_NOT_FOUND")
def _attachment_not_found(
    env: Env, tmp: Path, mp: pytest.MonkeyPatch
) -> tuple[int, dict[str, Any]]:
    return env.run("card", "attachments", "download", REVIEW, "--id", "no-such-attachment")


@case("ATTACHMENT_GONE")
def _attachment_gone(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    code, _ = env.run("card", "attachments", "download", REVIEW, "--id", UPLOAD)
    assert code == 0
    del env.fake.state.attachments[UPLOAD]
    return env.run("card", "attachments", "download", REVIEW, "--id", UPLOAD)


@case("AMBIGUOUS_BOARD")
def _ambiguous_board(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    seed_board(env.fake, "6a" + "1" * 22, "Same", "sameAAA1")
    seed_board(env.fake, "6a" + "2" * 22, "same", "sameBBB2")
    return env.run("--board", "SAME", "board", "show")


@case("AMBIGUOUS_LIST")
def _ambiguous_list(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    add_list(env.fake, "Twin", 8)
    add_list(env.fake, "twin", 9)
    return env.run("board", "list", "archive", "Twin")


@case("AMBIGUOUS_CARD")
def _ambiguous_card(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    card(env.fake, TODO, "Fix", 90)
    card(env.fake, DONE, "fix", 91)
    return env.run("card", "move", "Fix", "--right", "1")


@case("AMBIGUOUS_LABEL")
def _ambiguous_label(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    add_label(env.fake, "dup", "red")
    add_label(env.fake, "dup", "blue")
    return env.run("card", "label", "add", REVIEW, "dup")


@case("AMBIGUOUS_MEMBER")
def _ambiguous_member(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    add_member(env.fake, "sam1", "Sam Lee")
    add_member(env.fake, "sam2", "Sam Lee")
    return env.run("card", "member", "add", REVIEW, "Sam Lee")


@case("AMBIGUOUS_CHECKLIST")
def _ambiguous_checklist(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    add_checklist(env.fake, REVIEW, "QA", ("x",), pos=2)
    return env.run("card", "checklist", "rename", REVIEW, "QA", "--name", "New")


@case("AMBIGUOUS_CHECKITEM")
def _ambiguous_checkitem(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    add_checklist(env.fake, CORE, "Dup", ("e2e", "e2e"))
    return env.run("card", "checklist", "item", "check", CORE, "Dup", "e2e")


@case("INBOX_UNAVAILABLE")
def _inbox_unavailable(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    hide_inbox(env.fake.state)
    return env.run("inbox", "list")


@case("MOVE_OUT_OF_RANGE")
def _move_out_of_range(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return env.run("card", "move", REVIEW, "--right", "9")


@case("CONFIRMATION_REQUIRED")
def _confirmation_required(
    env: Env, tmp: Path, mp: pytest.MonkeyPatch
) -> tuple[int, dict[str, Any]]:
    return env.run("board", "label", "remove", "bug")


@case("ATTACHMENT_TOO_LARGE")
def _attachment_too_large(
    env: Env, tmp: Path, mp: pytest.MonkeyPatch
) -> tuple[int, dict[str, Any]]:
    with (env.home / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write("\n[attachments]\nmax_upload_bytes = 10\n")
    big = tmp / "big.bin"
    big.write_bytes(b"x" * 11)
    return env.run("card", "attachments", "add", REVIEW, "--file", str(big))


@case("API_ERROR")
def _api_error(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    env.fake.fail_next("POST", "/cards", 500, "boom")
    return card_create(env)


@case("RATE_LIMITED")
def _rate_limited(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    env.fake.rate_limit("POST", "/cards")
    return card_create(env)


@case("NETWORK_ERROR")
def _network_error(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    env.fake.fail_next("POST", "/cards", drop=True)
    return card_create(env)


@case("TIMEOUT")
def _timeout(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    with (env.home / "config.toml").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    text = (env.home / "config.toml").read_text(encoding="utf-8")
    (env.home / "config.toml").write_text(
        text.replace("[http]\n", "[http]\ntimeout_seconds = 1\n"), encoding="utf-8"
    )
    env.fake.fail_next("POST", "/cards", None, delay=3)
    return card_create(env)


@case("CACHE_STALE")
def _cache_stale(env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    prime(env.home, env.fake, ago(7200), BOARD_ID)
    env.fake.stop()
    return env.run("board", "lists")


def provoke(code: str, env: Env, tmp: Path, mp: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    return CASES[code](env, tmp, mp)


@pytest.mark.tc("TC-M16-005")
def test_the_cases_cover_the_whole_catalog_and_the_table_of_the_prd() -> None:
    assert set(CASES) == set(CATALOG) == set(EXIT_OF)
    for code, exit_code in EXIT_OF.items():
        assert int(CATALOG[code]) == exit_code, code
    assert TrelloctlError("USAGE", "x").exit_code == 2


@pytest.mark.tc("TC-M16-005")
@pytest.mark.parametrize("code", sorted(CASES))
def test_every_error_code_ends_with_the_exit_code_of_the_table(
    code: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with environment(tmp_path, monkeypatch, capsys, "case") as env:
        exit_code, envelope = provoke(code, env, tmp_path, monkeypatch)
    assert exit_code == EXIT_OF[code], envelope
    if code == "CACHE_STALE":
        # The one class that carries data: ok is true and the refresh error is in meta.cache.
        assert envelope["ok"] is True
        assert envelope["meta"]["cache"]["stale"] is True
        assert envelope["meta"]["cache"]["error"]["code"] == "NETWORK_ERROR"
    else:
        assert envelope["ok"] is False
        assert envelope["error"]["code"] == code
    assert envelope["meta"]["schema_version"] == "1"


# --- TC-M16-010: the messages of the whole catalog are English ---------------------------


@pytest.mark.tc("TC-M16-010")
def test_the_messages_of_every_error_code_are_english(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen = 0
    for code in sorted(CASES):
        with environment(tmp_path, monkeypatch, capsys, code.lower()) as env:
            _, envelope = provoke(code, env, tmp_path, monkeypatch)
        error = envelope.get("error")
        if error is None:  # CACHE_STALE: the message is the refresh error carried in meta
            error = envelope["meta"]["cache"]["error"]
        assert error["message"], code
        assert_english(error["message"], code)
        seen += 1
    assert seen == len(CATALOG)

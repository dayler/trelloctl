"""Unit tests for the fake Trello member and attachment routes."""

from __future__ import annotations

import pytest
from tests.fakes.trello import FakeTrello

from trelloctl.domain.errors import TrelloctlError
from trelloctl.infra.trello_client import TrelloClient


def client(fake: FakeTrello) -> TrelloClient:
    return TrelloClient(fake.base_url, fake.api_key, fake.token, 5)


def test_members_me_filters_fields_and_lookup(fake_trello: FakeTrello) -> None:
    fake_trello.state.members["me"] = {"id": "m1", "username": "ann", "inbox": {"idBoard": "b"}}
    c = client(fake_trello)
    assert c.get("/members/me", {"fields": "inbox"}) == {"id": "m1", "inbox": {"idBoard": "b"}}
    assert c.get("/members/ann")["username"] == "ann"
    assert c.get("/members/m1", {"fields": "all"})["username"] == "ann"


def test_missing_member_is_404(fake_trello: FakeTrello) -> None:
    with pytest.raises(TrelloctlError) as info:
        client(fake_trello).get("/members/nobody")
    assert info.value.details["status"] == 404


def test_missing_attachment_is_404(fake_trello: FakeTrello) -> None:
    with pytest.raises(TrelloctlError) as info:
        client(fake_trello).download("https://trello.com/1/cards/c/attachments/zz/download/f")
    assert info.value.details["status"] == 404


def test_url_attachment_and_upload(fake_trello: FakeTrello) -> None:
    c = client(fake_trello)
    link = c.post("/cards/c/attachments", {"url": "https://example.com", "name": "Site"})
    assert link["isUpload"] is False
    assert link["name"] == "Site"
    stored = c.upload("/cards/c/attachments", filename="a.txt", content=b"x")
    assert stored["name"] == "a.txt"


def test_post_without_file_is_400(fake_trello: FakeTrello) -> None:
    with pytest.raises(TrelloctlError) as info:
        client(fake_trello).post("/cards/c/attachments")
    assert info.value.details["status"] == 400

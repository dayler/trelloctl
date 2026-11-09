"""Unit tests for domain models."""

from __future__ import annotations

from trelloctl.domain.models import (
    Attachment,
    Badges,
    Board,
    Card,
    Checklist,
    Comment,
    Label,
    Member,
    TrelloList,
    classify_card_ref,
)


def test_board_kind_and_fields() -> None:
    board = Board.from_json({"id": "b", "name": "N", "shortLink": "s", "url": "u", "closed": True})
    assert (board.kind, board.short_link, board.url, board.closed) == ("board", "s", "u", True)
    assert Board.from_json({"id": "i", "name": "Inbox"}, kind="inbox").kind == "inbox"


def test_list_label_member_tolerate_missing_fields() -> None:
    assert TrelloList.from_json({"id": "l"}) == TrelloList(id="l", name="", pos=0.0, closed=False)
    assert Label.from_json({"id": "x"}).color is None
    assert Member.from_json({"id": "m", "fullName": "A B"}).full_name == "A B"


def test_comment_reads_nested_text() -> None:
    nested = Comment.from_json(
        {"id": "c", "idMemberCreator": "m", "date": "d", "data": {"text": "hi"}}
    )
    assert (nested.text, nested.member_id, nested.date) == ("hi", "m", "d")
    flat = Comment.from_json({"id": "c", "text": "yo", "memberId": "m2"})
    assert (flat.text, flat.member_id) == ("yo", "m2")


def test_checklist_items() -> None:
    cl = Checklist.from_json(
        {
            "id": "k",
            "name": "T",
            "pos": 3,
            "checkItems": [{"id": "i", "name": "a", "state": "complete"}, 5],
        }
    )
    assert [(i.name, i.state) for i in cl.items] == [("a", "complete")]
    assert Checklist.from_json({"id": "k"}).items == ()


def test_attachment_fields() -> None:
    att = Attachment.from_json({"id": "a", "bytes": 10, "isUpload": True, "mimeType": "x/y"})
    assert (att.bytes, att.is_upload, att.mime_type, att.local_path) == (10, True, "x/y", None)
    assert Attachment.from_json({"id": "a", "bytes": None}).bytes is None


def test_badges_non_mapping_and_bool_numbers() -> None:
    assert Badges.from_json(None) == Badges()
    assert Badges.from_json({"comments": True}).comments == 0


def test_card_ids_fall_back_to_embedded_objects() -> None:
    card = Card.from_json(
        {"id": "c", "labels": [{"id": "l1"}], "members": [{"id": "m1"}], "idShort": 3}
    )
    assert card.label_ids == ("l1",)
    assert card.member_ids == ("m1",)
    assert card.id_short == 3


def test_classify_is_case_preserving_and_rejects_foreign_urls() -> None:
    assert classify_card_ref("https://example.com/c/Ab3dE6gH")[0].kind == "name"
    assert classify_card_ref("ABCDEF0123456789ABCDEF01")[0].kind == "id"

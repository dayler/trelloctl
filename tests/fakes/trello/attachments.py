"""Fake Trello routes for attachments: listing, authenticated download and creation.

``state.attachments[id]`` holds ``{"id", "idCard", "name", "bytes", "mimeType", "date", "url",
"isUpload", "content"}``; ``content`` (bytes) is what the download route serves and is never
part of a serialized attachment. Uploads and links receive ``date``, ``mimeType`` and ``url``
as Trello would assign them.
"""

from __future__ import annotations

import mimetypes
from datetime import UTC, datetime
from email import message_from_bytes
from typing import Any

from tests.fakes.trello.core import Request, Response, Router, text_response


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict[str, Any]]:
    """Parse a multipart/form-data body into ``{field: {"filename", "content"}}``."""
    raw = b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + body
    message = message_from_bytes(raw)
    fields: dict[str, dict[str, Any]] = {}
    payload = message.get_payload()
    for part in payload if isinstance(payload, list) else []:
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str):
            continue
        content = part.get_payload(decode=True)
        fields[name] = {
            "filename": part.get_filename(),
            "content": content if isinstance(content, bytes) else b"",
        }
    return fields


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "content"}


def register(router: Router) -> None:
    @router.route("GET", "/cards/{card}/attachments")
    def list_attachments(req: Request) -> Response | list[dict[str, Any]]:
        card = req.params["card"]
        known = req.state.cards.get(card) or next(
            (c for c in req.state.cards.values() if c.get("shortLink") == card), None
        )
        if known is None:
            return text_response("The requested resource was not found.", 404)
        return [public(a) for a in req.state.attachments.values() if a.get("idCard") == known["id"]]

    @router.route("GET", "/cards/{card}/attachments/{att}/download/{name}", auth="oauth")
    def download(req: Request) -> Response:
        attachment = req.state.attachments.get(req.params["att"])
        if attachment is None:
            return text_response("The requested resource was not found.", 404)
        return Response(200, bytes(attachment.get("content", b"")), {}, "application/octet-stream")

    @router.route("POST", "/cards/{card}/attachments")
    def upload(req: Request) -> Response | dict[str, Any]:
        attachment_id = req.state.new_id()
        if req.param("url") is not None:
            link: dict[str, Any] = {
                "id": attachment_id,
                "idCard": req.params["card"],
                "name": req.param("name") or req.param("url"),
                "url": req.param("url"),
                "bytes": None,
                "mimeType": "",
                "date": _now(),
                "isUpload": False,
            }
            req.state.attachments[attachment_id] = link
            return link
        content_type = req.headers.get("content-type", "")
        if not content_type.startswith("multipart/form-data"):
            return text_response("invalid value for file", 400)
        fields = parse_multipart(content_type, req.body)
        file = fields.get("file")
        if file is None:
            return text_response("invalid value for file", 400)
        name_field = fields.get("name")
        name = name_field["content"].decode("utf-8") if name_field else file["filename"]
        record: dict[str, Any] = {
            "id": attachment_id,
            "idCard": req.params["card"],
            "name": name,
            "fileName": file["filename"],
            "bytes": len(file["content"]),
            "mimeType": mimetypes.guess_type(str(file["filename"] or ""))[0] or "",
            "date": _now(),
            "url": (
                f"https://trello.com/1/cards/{req.params['card']}/attachments/{attachment_id}"
                f"/download/{file['filename']}"
            ),
            "isUpload": True,
            "content": file["content"],
        }
        req.state.attachments[attachment_id] = record
        return public(record)

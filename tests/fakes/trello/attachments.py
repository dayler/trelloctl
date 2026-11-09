"""Fake Trello routes for attachments: authenticated download and multipart upload.

Only what M04 needs; M11 completes the rest of the attachment endpoints.
"""

from __future__ import annotations

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


def register(router: Router) -> None:
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
            "isUpload": True,
            "content": file["content"],
        }
        req.state.attachments[attachment_id] = record
        return {k: v for k, v in record.items() if k != "content"}

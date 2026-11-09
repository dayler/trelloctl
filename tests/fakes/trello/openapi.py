"""Fake Trello route that serves the OpenAPI specification (``GET /swagger.v3.json``).

The document is the reduced fixture in ``tests/fixtures/openapi/`` unless a test stores another
one in ``state.extra["openapi"]`` (a dict). The route is outside the ``/1`` API prefix and needs
no credentials, like the real specification.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tests.fakes.trello.core import Request, Response, Router, json_response

SPEC_PATH = "/swagger.v3.json"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "openapi" / "swagger.v3.json"


def load_fixture() -> dict[str, Any]:
    """The reduced specification that contains every endpoint of the doctor inventory."""
    loaded: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return loaded


def register(router: Router) -> None:
    @router.route("GET", SPEC_PATH, auth="none")
    def spec(req: Request) -> Response:
        return json_response(req.state.extra.get("openapi") or load_fixture())

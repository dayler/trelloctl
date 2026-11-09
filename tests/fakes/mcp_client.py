"""Minimal MCP client over stdio for the tests: a real `trelloctl mcp serve` subprocess.

It speaks raw line-delimited JSON-RPC, so the tests also see every byte of the server stdout
and can assert that it carries protocol messages only.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
TIMEOUT = 30.0


class McpClient:
    """Drives one server process; always `close()` it (or use it as a context manager)."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        args: Sequence[str] = ("mcp", "serve"),
        cwd: Path | None = None,
    ) -> None:
        full_env = dict(os.environ if env is None else env)
        full_env.setdefault("PYTHONIOENCODING", "utf-8")
        self.stdout_lines: list[str] = []
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._next_id = 1
        self.proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "trelloctl", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=cwd,
        )
        self._stderr: list[bytes] = []
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def __enter__(self) -> McpClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in self.proc.stdout:
            self._lines.put(raw.decode("utf-8").rstrip("\r\n"))
        self._lines.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in self.proc.stderr:
            self._stderr.append(raw)

    @property
    def stderr(self) -> str:
        return b"".join(self._stderr).decode("utf-8", errors="replace")

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def _read_message(self) -> dict[str, Any] | None:
        line = self._lines.get(timeout=TIMEOUT)
        if line is None:
            return None
        self.stdout_lines.append(line)
        message: dict[str, Any] = json.loads(line)
        return message

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a request and return the whole JSON-RPC response (`result` or `error`)."""
        ident = self._next_id
        self._next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": ident, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        while True:
            reply = self._read_message()
            if reply is None:
                raise EOFError(f"server closed stdout; stderr: {self.stderr}")
            if reply.get("id") == ident:
                return reply

    def initialize(self) -> dict[str, Any]:
        reply = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "tests", "version": "0"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return reply

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = self.request("tools/list")["result"]["tools"]
        return tools

    def call_raw(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return self.request("tools/call", params)

    def call(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> tuple[bool, dict[str, Any]]:
        """Call a tool: returns `isError` and the envelope parsed from the text content."""
        reply = self.call_raw(name, arguments)
        result = reply["result"]
        content = result["content"]
        assert len(content) == 1
        assert content[0]["type"] == "text"
        envelope: dict[str, Any] = json.loads(content[0]["text"])
        return bool(result.get("isError", False)), envelope

    def close(self) -> int:
        """Close stdin like a harness does and wait for the process; kills it on a timeout."""
        try:
            if self.proc.stdin is not None and not self.proc.stdin.closed:
                self.proc.stdin.close()
        except OSError:
            pass
        try:
            code = self.proc.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            code = self.proc.wait()
        for reader in self._readers:
            reader.join(timeout=5)
        while True:
            try:
                line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is not None:
                self.stdout_lines.append(line)
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream is not None:
                stream.close()
        return code

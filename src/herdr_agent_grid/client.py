"""Bounded socket requests, with CLI transport when no socket is available."""
from __future__ import annotations

import json
import os
import socket
import subprocess
from typing import Any


class HerdrError(RuntimeError):
    pass


class Client:
    def __init__(self, timeout: float = 2.0):
        self.socket_path = os.environ.get("HERDR_SOCKET_PATH")
        candidate = os.environ.get("HERDR_BIN_PATH", "")
        self.bin = candidate if candidate and os.access(candidate, os.X_OK) else "herdr"
        self.timeout = timeout

    def call(self, method: str, params: dict, cli: list[str]) -> dict[str, Any]:
        try:
            if self.socket_path:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                    conn.settimeout(self.timeout)
                    conn.connect(self.socket_path)
                    request = {"id": "grid", "method": method, "params": params}
                    conn.sendall((json.dumps(request) + "\n").encode())
                    chunks = bytearray()
                    while b"\n" not in chunks:
                        block = conn.recv(65536)
                        if not block:
                            raise HerdrError("Herdr closed the connection")
                        chunks.extend(block)
                        if len(chunks) > 32 * 1024 * 1024:
                            raise HerdrError("Herdr response exceeds 32 MiB")
                    response = json.loads(chunks.split(b"\n", 1)[0])
            else:
                proc = subprocess.run([self.bin, *cli], capture_output=True,
                                      timeout=self.timeout, check=False)
                output = proc.stdout if proc.returncode == 0 else proc.stderr
                try:
                    response = json.loads(output)
                except ValueError:
                    raise HerdrError(output.decode("utf-8", "replace").strip()
                                     or "Herdr returned no JSON") from None
            if not isinstance(response, dict):
                raise HerdrError("Invalid Herdr response")
            if response.get("error"):
                error = response["error"]
                raise HerdrError(error.get("message", str(error))
                                 if isinstance(error, dict) else str(error))
            result = response.get("result")
            if not isinstance(result, dict):
                raise HerdrError("Herdr response is missing its result")
            return result
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            # Never retry a possibly delivered focus command through another
            # transport. A read failure is retried on the next refresh.
            raise HerdrError(str(error)) from error

    def snapshot(self) -> dict:
        result = self.call("session.snapshot", {}, ["api", "snapshot"])
        snapshot = result.get("snapshot")
        if not isinstance(snapshot, dict):
            raise HerdrError("Herdr response is missing the snapshot")
        return snapshot

    def read(self, pane_id: str, lines: int) -> str:
        result = self.call("pane.read", {"pane_id": pane_id, "source": "visible",
                                        "format": "text", "lines": lines},
                           ["pane", "read", pane_id, "--source", "visible",
                            "--lines", str(lines)])
        read = result.get("read")
        if not isinstance(read, dict) or not isinstance(read.get("text"), str):
            raise HerdrError("Herdr response is missing the terminal preview")
        return read["text"]

    def focus(self, pane_id: str) -> None:
        self.call("agent.focus", {"target": pane_id}, ["agent", "focus", pane_id])

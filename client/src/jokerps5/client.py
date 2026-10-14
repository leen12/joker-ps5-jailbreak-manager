"""Low-level and high-level TCP client for jokerd.elf."""

from __future__ import annotations

import json
import os
import socket
import threading
from typing import Any

from .buttons import format_mask, parse_buttons
from .images import Screenshot

MAX_LINE_BYTES = 1024
MAX_SEQ_STEPS = 64


class JokerError(Exception):
    """Raised when the payload returns an {"ok": false} reply."""

    def __init__(self, message: str, reply: dict[str, Any]) -> None:
        super().__init__(message)
        self.reply = reply


def _quote_path(path: str) -> str:
    if " " in path:
        raise ValueError(f"path must not contain spaces (protocol is space-separated): {path!r}")
    if not path.startswith("/"):
        raise ValueError(f"path must be absolute: {path!r}")
    return path


def _check_ms(ms: int) -> int:
    if not 1 <= ms <= 10000:
        raise ValueError(f"ms must be in 1..10000, got {ms}")
    return ms


def _check_axis(value: int, name: str) -> int:
    if not 0 <= value <= 255:
        raise ValueError(f"{name} must be in 0..255, got {value}")
    return value


class JokerClient:
    """Persistent, thread-safe TCP client for the jokerd line protocol."""

    def __init__(self, host: str | None = None, port: int = 9310, timeout: float = 15.0) -> None:
        self.host = host or os.environ.get("PS5_HOST")
        if not self.host:
            raise ValueError("host not provided and PS5_HOST is not set")
        self.port = port
        self.timeout = timeout
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None

    # -- connection management -------------------------------------------------

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        sock.settimeout(self.timeout)
        return sock

    def close(self) -> None:
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                finally:
                    self._sock = None

    def _ensure_connected(self) -> socket.socket:
        if self._sock is None:
            self._sock = self._connect()
        return self._sock

    # -- framing ------------------------------------------------------------

    def _send_line(self, sock: socket.socket, line: str) -> None:
        encoded = (line + "\n").encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise ValueError(f"command line exceeds {MAX_LINE_BYTES} bytes: {line!r}")
        sock.sendall(encoded)

    def _recv_line(self, sock: socket.socket) -> str:
        buf = bytearray()
        while True:
            chunk = sock.recv(1)
            if not chunk:
                raise ConnectionError("connection closed while reading reply line")
            if chunk == b"\n":
                break
            buf += chunk
        return buf.decode("utf-8")

    def _recv_exact(self, sock: socket.socket, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(min(65536, n - len(buf)))
            if not chunk:
                raise ConnectionError("connection closed while reading raw bytes")
            buf += chunk
        return bytes(buf)

    def _do_request(self, line: str, upload: bytes | None) -> tuple[dict[str, Any], bytes | None]:
        sock = self._ensure_connected()
        if upload is not None:
            self._send_line(sock, line)
            sock.sendall(upload)
        else:
            self._send_line(sock, line)
        reply_line = self._recv_line(sock)
        reply = json.loads(reply_line)
        payload: bytes | None = None
        if "bytes" in reply:
            payload = self._recv_exact(sock, int(reply["bytes"]))
        if not reply.get("ok", False):
            raise JokerError(reply.get("error", "unknown error"), reply)
        return reply, payload

    def request(self, line: str, upload: bytes | None = None) -> tuple[dict[str, Any], bytes | None]:
        """Send one command line, optionally followed by raw upload bytes.

        Returns (reply_dict, raw_bytes_or_None). Raises JokerError on {"ok": false}.
        Reconnects once automatically if the socket was broken.
        """
        with self._lock:
            try:
                return self._do_request(line, upload)
            except (ConnectionError, OSError):
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                self._sock = self._connect()
                return self._do_request(line, upload)

    # -- hello / pad ----------------------------------------------------------

    def hello(self) -> dict[str, Any]:
        reply, _ = self.request("hello")
        return reply

    def pad_open(self, user_id: int | None = None) -> dict[str, Any]:
        line = "pad open" if user_id is None else f"pad open {user_id}"
        reply, _ = self.request(line)
        return reply

    def pad_close(self) -> dict[str, Any]:
        reply, _ = self.request("pad close")
        return reply

    def pad_status(self) -> dict[str, Any]:
        reply, _ = self.request("pad status")
        return reply

    def pad_set(self, buttons: str | int, lx: int, ly: int, rx: int, ry: int, l2: int, r2: int) -> None:
        mask = buttons if isinstance(buttons, int) else parse_buttons(buttons)
        for name, value in (("lx", lx), ("ly", ly), ("rx", rx), ("ry", ry), ("l2", l2), ("r2", r2)):
            _check_axis(value, name)
        line = f"pad set {format_mask(mask)} {lx} {ly} {rx} {ry} {l2} {r2}"
        self.request(line)

    def pad_press(self, buttons: str | int, ms: int = 120) -> None:
        mask = buttons if isinstance(buttons, int) else parse_buttons(buttons)
        _check_ms(ms)
        self.request(f"pad press {format_mask(mask)} {ms}")

    def pad_seq(self, steps: list[str]) -> dict[str, Any]:
        if len(steps) > MAX_SEQ_STEPS:
            raise ValueError(f"seq supports at most {MAX_SEQ_STEPS} steps, got {len(steps)}")
        reply, _ = self.request("pad seq " + " ".join(steps))
        return reply

    def pad_release(self) -> None:
        self.request("pad release")

    # -- screenshots ----------------------------------------------------------

    def shot(self, hold_ms: int = 1200) -> Screenshot:
        _check_ms(hold_ms)
        reply, data = self.request(f"shot {hold_ms}")
        if data is None:
            raise JokerError("shot reply did not include raw bytes", reply)
        return Screenshot(data=data, format=reply["format"], path=reply["path"])

    def shots(self) -> list[dict[str, Any]]:
        reply, _ = self.request("shots")
        return reply["files"]

    # -- apps -----------------------------------------------------------------

    def app_list(self) -> list[dict[str, Any]]:
        reply, _ = self.request("app list")
        return reply["apps"]

    def app_launch(self, title_id: str) -> dict[str, Any]:
        reply, _ = self.request(f"app launch {title_id}")
        return reply

    def app_home(self) -> None:
        self.request("app home")

    def app_close(self) -> None:
        self.request("app close")

    def app_running(self) -> str | int:
        reply, _ = self.request("app running")
        return reply["app_id"]

    # -- filesystem -------------------------------------------------------------

    def fs_ls(self, path: str) -> list[dict[str, Any]]:
        reply, _ = self.request(f"fs ls {_quote_path(path)}")
        return reply["entries"]

    def fs_stat(self, path: str) -> dict[str, Any]:
        reply, _ = self.request(f"fs stat {_quote_path(path)}")
        return reply

    def fs_get(self, path: str) -> bytes:
        reply, data = self.request(f"fs get {_quote_path(path)}")
        if data is None:
            raise JokerError("fs get reply did not include raw bytes", reply)
        return data

    def fs_put(self, remote: str, data: bytes, mode: int | None = None) -> dict[str, Any]:
        line = f"fs put {_quote_path(remote)} {len(data)}"
        if mode is not None:
            line += f" {mode:o}"
        reply, _ = self.request(line, upload=data)
        return reply

    def fs_mkdir(self, path: str) -> None:
        self.request(f"fs mkdir {_quote_path(path)}")

    def fs_rm(self, path: str) -> None:
        self.request(f"fs rm {_quote_path(path)}")

    def fs_df(self) -> list[dict[str, Any]]:
        reply, _ = self.request("fs df")
        return reply["mounts"]

    # -- system -----------------------------------------------------------------

    def notify(self, text: str) -> None:
        self.request(f"sys notify {text}")

    def sys_info(self) -> dict[str, Any]:
        reply, _ = self.request("sys info")
        return reply

    def quit(self) -> None:
        self.request("quit")

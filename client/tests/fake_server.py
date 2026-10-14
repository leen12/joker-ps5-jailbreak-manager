"""A threaded fake jokerd server for tests. Implements enough of the protocol."""

from __future__ import annotations

import io
import json
import socket
import threading


def make_tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class FakeJokerServer:
    """Real TCP server on 127.0.0.1:0, handling a tiny protocol subset in a thread."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(5)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.drop_next_connection = False
        self.fs_files: dict[str, bytes] = {}
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            if self.drop_next_connection:
                self.drop_next_connection = False
                conn.close()
                continue
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    buf = self._dispatch(conn, line.decode("utf-8"), buf)

    def _dispatch(self, conn: socket.socket, line: str, rest: bytes) -> bytes:
        parts = line.split(" ")
        cmd = parts[0]

        if cmd == "hello":
            self._reply(conn, {"ok": True, "name": "jokerd", "version": "1", "proto": 1, "fw": "0x1", "pid": 1, "pad": "closed"})
        elif cmd == "pad" and parts[1] == "set":
            self._reply(conn, {"ok": True})
        elif cmd == "pad" and parts[1] == "press":
            self._reply(conn, {"ok": True})
        elif cmd == "pad" and parts[1] == "seq":
            self._reply(conn, {"ok": True, "steps_run": len(parts) - 2})
        elif cmd == "pad" and parts[1] == "release":
            self._reply(conn, {"ok": True})
        elif cmd == "pad" and parts[1] == "open":
            self._reply(conn, {"ok": True, "backend": "vdev", "handle": 1, "user": 1, "steps": []})
        elif cmd == "pad" and parts[1] == "close":
            self._reply(conn, {"ok": True, "steps": []})
        elif cmd == "shot":
            png = make_tiny_png()
            self._reply(conn, {"ok": True, "bytes": len(png), "path": "/data/shot.png", "format": "png", "deleted": "0x1"})
            conn.sendall(png)
        elif cmd == "fs" and parts[1] == "get":
            path = parts[2]
            data = self.fs_files.get(path, b"")
            self._reply(conn, {"ok": True, "bytes": len(data)})
            conn.sendall(data)
        elif cmd == "fs" and parts[1] == "put":
            path = parts[2]
            size = int(parts[3])
            while len(rest) < size:
                rest += conn.recv(65536)
            data, rest = rest[:size], rest[size:]
            self.fs_files[path] = data
            self._reply(conn, {"ok": True, "written": size})
            return rest
        elif cmd == "fail":
            self._reply(conn, {"ok": False, "error": "boom"})
        else:
            self._reply(conn, {"ok": False, "error": f"unknown command {cmd}"})
        return rest

    def _reply(self, conn: socket.socket, obj: dict) -> None:
        conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))

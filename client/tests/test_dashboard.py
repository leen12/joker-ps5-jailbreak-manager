import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from jokerps5.client import JokerClient
from jokerps5.dashboard import make_handler, serve


def _request(url: str, *, token: str | None = None, data: bytes | None = None, content_type: str | None = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.read(), response.headers


def test_dashboard_authenticated_file_workflow(server):
    server.fs_files["/data/config.ini"] = b"mode=old\n"
    factory = lambda: JokerClient("127.0.0.1", server.port, timeout=5)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler("unused", 0, token="secret", client_factory=factory))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        body, _ = _request(base + "/")
        assert b"Filesystem Workspace" in body
        with pytest.raises(urllib.error.HTTPError) as error:
            _request(base + "/api/files?path=%2Fdata")
        assert error.value.code == 401

        body, _ = _request(base + "/api/text?path=%2Fdata%2Fconfig.ini", token="secret")
        opened = json.loads(body)
        assert opened["text"] == "mode=old\n"
        update = json.dumps({
            "path": "/data/config.ini", "text": "mode=new\n", "expected_sha256": opened["sha256"],
        }).encode()
        body, _ = _request(base + "/api/text", token="secret", data=update, content_type="application/json")
        result = json.loads(body)
        assert server.fs_files["/data/config.ini"] == b"mode=new\n"
        assert server.fs_files[result["backup"]] == b"mode=old\n"

        body, _ = _request(
            base + "/api/file?path=%2Fdata%2Fupload.bin", token="secret", data=b"payload",
            content_type="application/octet-stream",
        )
        assert json.loads(body)["bytes"] == 7
        downloaded, headers = _request(base + "/api/file?path=%2Fdata%2Fupload.bin", token="secret")
        assert downloaded == b"payload"
        assert "attachment" in headers["Content-Disposition"]

        screenshot, headers = _request(base + "/api/screenshot?backend=latest", token="secret")
        assert screenshot.startswith(b"\x89PNG\r\n\x1a\n")
        assert headers["Content-Type"] == "image/png"

        delete = json.dumps({"path": "/data/upload.bin"}).encode()
        _request(base + "/api/files/delete", token="secret", data=delete, content_type="application/json")
        assert "/data/upload.bin" not in server.fs_files
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_remote_dashboard_requires_token():
    with pytest.raises(ValueError, match="token is required"):
        serve("ps5", 9310, bind="0.0.0.0", listen=0)

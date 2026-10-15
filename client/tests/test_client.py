import pytest

from jokerps5.client import JokerClient, JokerError
from fake_server import FakeJokerServer


@pytest.fixture
def server():
    srv = FakeJokerServer()
    yield srv
    srv.stop()


@pytest.fixture
def client(server):
    c = JokerClient(host="127.0.0.1", port=server.port, timeout=5.0)
    yield c
    c.close()


def test_hello(client):
    reply = client.hello()
    assert reply["ok"] is True
    assert reply["name"] == "jokerd"


def test_pad_press_validates_ms(client):
    with pytest.raises(ValueError):
        client.pad_press("cross", ms=0)
    with pytest.raises(ValueError):
        client.pad_press("cross", ms=10001)


def test_pad_set_validates_axes(client):
    with pytest.raises(ValueError):
        client.pad_set("cross", 256, 128, 128, 128, 0, 0)


def test_pad_seq_max_steps(client):
    with pytest.raises(ValueError):
        client.pad_seq(["cross:100"] * 65)


def test_shot_roundtrip(client):
    shot = client.shot()
    assert shot.format == "png"
    assert len(shot.data) > 0
    png = shot.to_png(max_width=800)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_shot_backends(client):
    assert client.shot_latest().backend == "gallery"
    assert client.shot_grab("/data/app/request", "/data/app/shots").backend == "title-grab"


def test_shot_grab_validates_timeout(client):
    with pytest.raises(ValueError, match="100..30000"):
        client.shot_grab("/data/app/request", "/data/app/shots", wait_ms=50)


def test_fs_put_get_roundtrip(client):
    client.fs_put("/data/test.txt", b"hello world")
    data = client.fs_get("/data/test.txt")
    assert data == b"hello world"


def test_fs_path_must_be_absolute(client):
    with pytest.raises(ValueError):
        client.fs_ls("relative/path")


def test_fs_path_rejects_spaces(client):
    with pytest.raises(ValueError):
        client.fs_get("/data/has space.txt")


def test_error_reply_raises_joker_error(client):
    with pytest.raises(JokerError):
        client.request("fail")


def test_reconnect_after_drop(server, client):
    client.hello()
    server.drop_next_connection = False
    client.close()
    reply = client.hello()
    assert reply["ok"] is True


def test_reconnect_after_server_resets_connection(server, client):
    client.hello()
    # Force the underlying socket closed to simulate a broken pipe, then
    # verify the next request transparently reconnects.
    client._sock.close()
    reply = client.hello()
    assert reply["ok"] is True


def test_missing_host_raises():
    import os

    saved = os.environ.pop("PS5_HOST", None)
    try:
        with pytest.raises(ValueError):
            JokerClient()
    finally:
        if saved is not None:
            os.environ["PS5_HOST"] = saved

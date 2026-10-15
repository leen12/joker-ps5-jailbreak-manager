import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fake_server import FakeJokerServer
from jokerps5.client import JokerClient


@pytest.fixture
def server():
    srv = FakeJokerServer()
    yield srv
    srv.stop()


@pytest.fixture
def client(server):
    instance = JokerClient(host="127.0.0.1", port=server.port, timeout=5.0)
    yield instance
    instance.close()

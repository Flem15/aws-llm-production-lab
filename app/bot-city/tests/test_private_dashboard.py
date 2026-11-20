import http.client
import threading
from pathlib import Path

import pytest
from bots.dashboard import make_server


@pytest.fixture
def server(tmp_path):
    (tmp_path / "analytics.html").write_text("private analytics")
    (tmp_path / "secret.txt").write_text("not served")
    (tmp_path / "analytics.json").symlink_to(tmp_path / "secret.txt")
    instance = make_server(0, str(tmp_path))
    thread = threading.Thread(target=instance.serve_forever)
    thread.start()
    yield instance
    instance.shutdown(); instance.server_close(); thread.join(timeout=5)
    assert not thread.is_alive()


def request(server, path, method="GET", headers=None):
    client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        client.request(method, path, headers=headers or {})
        response = client.getresponse(); response.read()
        return response.status, dict(response.getheaders())
    finally:
        client.close()


def test_local_read_only_route(server):
    status, headers = request(server, "/analytics.html")
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert request(server, "/secret.txt")[0] == 404
    assert request(server, "/analytics.json")[0] == 403
    assert request(server, "/../secret.txt")[0] == 404
    assert request(server, "/analytics.html", "POST")[0] == 501


@pytest.mark.parametrize("headers", [{"Host": "attacker.test"}, {"Origin": "https://attacker.test"}, {"Origin": "null"}])
def test_dns_rebinding_and_cross_origin_blocked(server, headers):
    assert request(server, "/analytics.html", headers=headers)[0] == 403

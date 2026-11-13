"""ui.server: the local HTTP layer, its guards, and the SSE endpoint."""
import json
import threading
import urllib.error
import urllib.request

import pytest

from workbay.ui.server import make_server


@pytest.fixture
def http_server(api_env):
    api, root, single = api_env
    server = make_server(api, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host = f"127.0.0.1:{server.server_address[1]}"
    try:
        yield host, api
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


def request(host, path, data=None, headers=None):
    """-> (status, body bytes, content type). HTTP errors are returned, not raised."""
    url = f"http://{host}{path}"
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers.get("Content-Type", "")


def json_of(body):
    return json.loads(body.decode("utf-8"))


def test_index_page(http_server):
    host, _ = http_server
    status, body, ctype = request(host, "/")
    assert status == 200
    assert "text/html" in ctype
    assert b"Workbay" in body


def test_api_workspaces(http_server):
    host, _ = http_server
    status, body, ctype = request(host, "/api/workspaces")
    assert status == 200
    assert "application/json" in ctype
    assert len(json_of(body)["workspaces"]) == 2


def test_api_unknown_is_404(http_server):
    host, _ = http_server
    status, body, _ = request(host, "/api/nope")
    assert status == 404
    assert "error" in json_of(body)


def test_post_rescan(http_server):
    host, _ = http_server
    status, body, _ = request(host, "/api/rescan", data={})
    assert status == 200
    assert len(json_of(body)["workspaces"]) == 2


def test_post_from_a_foreign_origin_is_403(http_server):
    host, _ = http_server
    status, body, _ = request(host, "/api/rescan", data={}, headers={"Origin": "http://evil.com"})
    assert status == 403
    assert json_of(body)["error"]


def test_post_with_a_foreign_host_is_403(http_server):
    host, _ = http_server
    status, _, _ = request(host, "/api/rescan", data={}, headers={"Host": "evil.com"})
    assert status == 403


def test_post_body_not_json_is_400(http_server):
    host, api = http_server
    status, body, _ = request(host, "/api/rescan", data=b"this is not json",
                              headers={"Content-Type": "application/json"})
    assert status == 400
    assert "JSON" in json_of(body)["error"]


def test_post_to_a_get_only_endpoint_is_404(http_server):
    host, _ = http_server
    status, _, _ = request(host, "/api/repo", data={})
    assert status == 404


def test_post_fetch_without_ws_is_400(http_server):
    host, _ = http_server
    status, body, _ = request(host, "/api/fetch", data={})
    assert status == 400
    assert "缺少参数" in json_of(body)["error"]


def read_stream(host, path, timeout=5.0):
    """GET an endless reply: what arrives within timeout, plus whether the peer closed."""
    import socket

    port = int(host.rsplit(":", 1)[1])
    sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    try:
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode())
        sock.settimeout(timeout)
        buf, closed = b"", False
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    closed = True
                    break
                buf += chunk
        except socket.timeout:
            pass
        head, _, body = buf.partition(b"\r\n\r\n")
        return head.decode("latin-1"), body, closed
    finally:
        sock.close()


def test_events_once_ends_the_reply(http_server):
    host, _ = http_server
    head, body, closed = read_stream(host, "/api/events?from=0&once=1")
    assert "200" in head.splitlines()[0]
    assert "text/event-stream" in head
    assert b"event: index" in body
    assert closed is True


def test_events_once_without_a_value_ends_the_reply(http_server):
    """The page asks for ?once with no value: a blank query value must still count."""
    host, _ = http_server
    head, body, closed = read_stream(host, "/api/events?from=0&once")
    assert "text/event-stream" in head
    assert b"event: index" in body
    assert closed is True


def test_events_without_once_keeps_streaming(http_server):
    """No &once: the reply stays open waiting for job events (it only writes once there
    are some, so nothing more is expected within the short probe window)."""
    host, _ = http_server
    head, body, closed = read_stream(host, "/api/events?from=0", timeout=3.0)
    assert "text/event-stream" in head
    assert closed is False



def test_static_cannot_escape_its_directory(http_server):
    host, _ = http_server
    for path in ("/static/../server.py", "/static/..%2fserver.py", "/static/../api.py"):
        status, body, _ = request(host, path)
        assert status == 404, path
        assert b"make_server" not in body


def test_api_status_and_settings(http_server):
    host, api = http_server
    status, body, _ = request(host, "/api/status")
    assert status == 200
    assert "summary" in next(iter(json_of(body)["status"].values()))

    status, body, _ = request(host, "/api/settings")
    assert status == 200
    assert "settings" in json_of(body)

    status, body, _ = request(host, "/api/settings", data={"refresh_s": "15", "terminal": "bogus"})
    assert status == 200
    assert json_of(body)["settings"]["refresh_s"] == 15

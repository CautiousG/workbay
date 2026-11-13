"""The local HTTP server of the UI: 127.0.0.1 only, standard library only.

    GET  /                       the page (PAGE below; app.js / app.css under /static/)
    GET  /api/<name>?a=b         Api.<name>(a=b), the read calls in _GET
    POST /api/<name>  {json}     Api.<name>(**json), the calls in _POST
    GET  /api/events?from=N      server-sent events: every job event from N on
                                 (&once: what is there now, then the reply ends)

Every /api reply is JSON; a refused request is {"error": "..."} with its status. A POST
must come from the page itself (Host / Origin checked), so another web page in the
same browser cannot drive the local git through it.
"""
import json
import mimetypes
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .api import Api, ApiError

STATIC = Path(__file__).resolve().parent / "static"
# Not from the registry: on some Windows boxes it maps .js to text/plain.
_TYPES = {".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}

PAGE = """<!doctype html>
<html lang="zh-cn" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Apiary · Workspace Manager</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>🐝</text></svg>">
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<header class="top">
  <div class="brand"><span class="logo">⬢</span> Apiary <span class="sub">· Workspace Manager</span></div>
  <nav id="tabs"></nav>
  <div class="spacer"></div>
  <span id="jobs-pill" class="pill idle"></span>
  <button id="theme" class="ghost" title="深色 / 浅色">◐</button>
</header>
<div id="banner" hidden></div>
<main id="views"></main>
<aside id="drawer" hidden></aside>
<div id="modal" hidden></div>
<div id="toast"></div>
<script src="/static/app.js"></script>
<script src="/static/ws.js"></script>
<script src="/static/dialogs.js"></script>
<script src="/static/build.js"></script>
<script src="/static/drawer.js"></script>
<script src="/static/jobs.js"></script>
<script src="/static/history.js"></script>
<script src="/static/settings.js"></script>
</body>
</html>
"""

# name -> how to call Api with the request's arguments (a dict).
_GET = {
    "workspaces": lambda api, a: api.workspaces(),
    "status": lambda api, a: api.status(_list(a.get("ws"))),
    "repo": lambda api, a: api.repo(a["ws"], a["repo"]),
    "diff": lambda api, a: api.diff(a["ws"], a["repo"], a["path"], a.get("staged"), a.get("untracked")),
    "manifest": lambda api, a: api.manifest(a["ws"]),
    "manifest_raw": lambda api, a: api.manifest_raw(a["ws"], a.get("file")),
    "stuck": lambda api, a: api.stuck(a["ws"]),
    "open_files": lambda api, a: api.open_files(a["ws"]),
    "jobs": lambda api, a: api.jobs_list(),
    "build_targets": lambda api, a: api.build_targets(a["ws"], a.get("rescan", False)),
    "build_result": lambda api, a: api.build_result(a["ws"], a["target"]),
    "claude_sessions": lambda api, a: api.claude_sessions(),
    "claude_preview": lambda api, a: api.claude_preview(a["id"]),
    "claude_trash": lambda api, a: api.claude_trash(),
    "settings": lambda api, a: api.get_settings(),
    "env": lambda api, a: api.env(),
}
_POST = {
    "rescan": lambda api, a: api.rescan(),
    "status": lambda api, a: api.status(a.get("ws")),
    "fetch": lambda api, a: api.fetch(a["ws"], a.get("repos")),
    "fetch_all": lambda api, a: api.fetch_all(),
    "pull": lambda api, a: api.pull(a["ws"], a.get("repos"), a.get("confirm", False)),
    "update_check": lambda api, a: api.update_check(a["ws"], a.get("projects"), a.get("keep_descendants", True),
                                                    a.get("rebase", False)),
    "update": lambda api, a: api.update(a["ws"], a.get("projects"), a.get("keep_descendants", True),
                                        a.get("rebase", False), a.get("narrow", False), a.get("confirm", False)),
    "sync_check": lambda api, a: api.sync_check(a["ws"], a.get("projects"), a.get("autostash")),
    "sync": lambda api, a: api.sync(a["ws"], a.get("projects"), a.get("autostash"), a.get("confirm", False)),
    "align": lambda api, a: api.align(a["ws"], a.get("repos"), a.get("confirm", False)),
    "unstick": lambda api, a: api.unstick(a["ws"], a["repo"], a.get("action", "")),
    "backup": lambda api, a: api.restore_backup(a["ws"], a["repo"], a["ref"], a.get("drop", False)),
    "set_manifest": lambda api, a: api.set_manifest(a["ws"], a["file"]),
    "forall": lambda api, a: api.forall(a["ws"], a.get("cmd", ""), a.get("projects")),
    "git": lambda api, a: api.git(a["ws"], a.get("cmd", ""), a.get("repos")),
    "stage": lambda api, a: api.stage(a["ws"], a["repo"], a.get("files")),
    "unstage": lambda api, a: api.stage(a["ws"], a["repo"], a.get("files"), unstage=True),
    "commit": lambda api, a: api.commit(a["ws"], a["repo"], a.get("message", ""), a.get("amend", False),
                                        a.get("confirm", False)),
    "install_hook": lambda api, a: api.install_hook(a["ws"], a["repo"]),
    "open": lambda api, a: api.open(a.get("what", "explorer"), a["ws"], a.get("repo"), a.get("file")),
    "build": lambda api, a: api.build(a["ws"], a.get("targets"), a.get("action", "build"), a.get("reconfigure", False)),
    "build_clean": lambda api, a: api.build_clean(a["ws"], a.get("targets"), a.get("confirm", False)),
    "build_ic": lambda api, a: api.set_build_ic(a["ws"], a.get("ic", "")),
    "build_fav": lambda api, a: api.build_fav(a["ws"], a["target"], a.get("on", True)),
    "goto": lambda api, a: api.goto(a["ws"], a["file"], a.get("line")),
    "reveal": lambda api, a: api.reveal(a["ws"], a["path"]),
    "claude_resume": lambda api, a: api.claude_resume(a["id"]),
    "claude_delete": lambda api, a: api.claude_delete(a.get("ids"), a.get("confirm", False)),
    "claude_restore": lambda api, a: api.claude_restore(a["batch"]),
    "claude_empty_trash": lambda api, a: api.claude_empty_trash(a.get("batch"), a.get("confirm", False)),
    "claude_open_trash": lambda api, a: api.claude_open_trash(),
    "cancel": lambda api, a: api.cancel(a["job"]),
    "settings": lambda api, a: api.save_settings(a),
    "hide": lambda api, a: api.hide(a["ws"]),
}


def _list(value):
    return [v for v in str(value).split(",") if v] if value else None


def _handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version = "apiary-ui"

        def log_message(self, fmt, *args):
            pass

        def _send(self, status, body: bytes, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, status=200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _local(self):
            """The request comes from our own page: Host is us, Origin (if any) is us."""
            port = self.server.server_address[1]
            ours = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.headers.get("Host") not in ours:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlparse(origin).netloc in ours

        def _call(self, table, name, args):
            try:
                return self._json(table[name](api, args))
            except ApiError as exc:
                return self._json({"error": str(exc)}, exc.status)
            except KeyError as exc:
                return self._json({"error": f"缺少参数 {exc}"}, 400)
            except Exception as exc:                      # a bug: say it, keep serving
                return self._json({"error": f"内部错误：{type(exc).__name__}: {exc}"}, 500)

        def do_GET(self):
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            if url.path in ("/", "/index.html"):
                return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            if url.path.startswith("/static/"):
                path = (STATIC / unquote(url.path[len("/static/"):])).resolve()
                if STATIC not in path.parents or not path.is_file():
                    return self._json({"error": "not found"}, 404)
                ctype = _TYPES.get(path.suffix) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                return self._send(200, path.read_bytes(), ctype + "; charset=utf-8")
            if not self._local():
                return self._json({"error": "forbidden"}, 403)
            if url.path == "/api/events":
                return self._events(int(query.get("from", 0)), once="once" in query)
            name = url.path.removeprefix("/api/")
            if url.path.startswith("/api/") and name in _GET:
                return self._call(_GET, name, query)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            url = urlparse(self.path)
            name = url.path.removeprefix("/api/")
            if not self._local():
                return self._json({"error": "forbidden"}, 403)
            if not url.path.startswith("/api/") or name not in _POST:
                return self._json({"error": "not found"}, 404)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            except ValueError:
                return self._json({"error": "请求体不是 JSON"}, 400)
            if not isinstance(body, dict):
                return self._json({"error": "请求体必须是 JSON 对象"}, 400)
            return self._call(_POST, name, body)

        def _events(self, index, once=False):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    index, batch = api.events_after(index, timeout=0 if once else 10)
                    if not batch:
                        self.wfile.write(b": keepalive\n\n")
                    for event in batch:
                        self.wfile.write(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n")
                    self.wfile.write(f"event: index\ndata: {index}\n\n".encode())
                    self.wfile.flush()
                    if once:
                        self.close_connection = True
                        return
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                return

    return Handler


class _Server(ThreadingHTTPServer):
    # Not SO_REUSEADDR: on Windows it lets a second UI bind the same port.
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def make_server(api: Api, port=0):
    """A server on 127.0.0.1:port (0: any free one). OSError when the port is taken."""
    return _Server(("127.0.0.1", port), _handler(api))


def serve(port=8780, open_browser=True):
    api = Api()
    try:
        server = make_server(api, port)
    except OSError:
        server = make_server(api, 0)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Apiary · Workspace Manager at {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        api.close()
        server.server_close()
    return 0

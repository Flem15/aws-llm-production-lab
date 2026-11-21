"""Private read-only dashboard. Loopback only; no trading or AWS endpoints."""
import functools
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")
ROUTES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/state.json": "state.json",
    "/analytics.html": "analytics.html",
    "/analytics.js": "analytics.js",
    "/analytics.css": "analytics.css",
    "/analytics.json": "analytics.json",
}


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _allowed(self):
        self._approved_path = None
        hosts = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
        host = self.headers.get("Host", "")
        if host not in hosts or self.headers.get("Origin", f"http://{host}") != f"http://{host}":
            self.send_error(403)
            return False
        route = urlsplit(self.path).path
        filename = ROUTES.get(route)
        if filename is None:
            self.send_error(404)
            return False
        # Filesystem paths contain only trusted directory + literal route values.
        target = Path(self.directory) / filename
        if target.is_symlink() or target.resolve().parent != Path(self.directory).resolve():
            self.send_error(403)
            return False
        if not target.is_file():
            self.send_error(404)
            return False
        self._approved_path = str(target)
        return True

    def translate_path(self, path):
        # Never let the parent server reinterpret request text as a file path.
        if self._approved_path is None:
            raise ValueError("dashboard path was not approved")
        return self._approved_path

    def do_GET(self):
        if self._allowed():
            super().do_GET()

    def do_HEAD(self):
        if self._allowed():
            super().do_HEAD()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        # The pre-existing live view uses inline JS/CSS. Analytics uses only local assets.
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        super().end_headers()


def make_server(port=8050, directory=ROOT):
    return ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=directory))


def serve(port=8050):
    with make_server(port) as server:
        server.serve_forever()


def serve_in_background(port=8050):
    threading.Thread(target=serve, args=(port,), daemon=True).start()


if __name__ == "__main__":
    print("Private dashboard: http://127.0.0.1:8050/analytics.html")
    serve()

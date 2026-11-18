"""Tiny static server for dashboard/ (index.html polls state.json)."""
import functools
import os
import threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dashboard")


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(port=8050):
    ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=ROOT)).serve_forever()


def serve_in_background(port=8050):
    threading.Thread(target=serve, args=(port,), daemon=True).start()


if __name__ == "__main__":
    print(f"Dashboard on http://localhost:8050")
    serve()

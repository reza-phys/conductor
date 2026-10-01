"""Local live dashboard: serves .conductor/status.html and status.json on 127.0.0.1.

The page polls status.json and re-renders in place; hooks keep the files fresh, so the
server itself is a dumb, read-only static file server limited to those two files.
"""
from __future__ import annotations

import http.server
from functools import partial
from pathlib import Path

FILES = {"/": ("status.html", "text/html; charset=utf-8"),
         "/status.html": ("status.html", "text/html; charset=utf-8"),
         "/status.json": ("status.json", "application/json; charset=utf-8")}


class Handler(http.server.BaseHTTPRequestHandler):
    def __init__(self, *a, state: Path, **kw):
        self.state = state
        super().__init__(*a, **kw)

    def do_GET(self):  # noqa: N802
        entry = FILES.get(self.path.split("?")[0])
        if not entry:
            self.send_error(404)
            return
        try:
            body = (self.state / entry[0]).read_bytes()
        except OSError:
            self.send_error(503, "not rendered yet")
            return
        self.send_response(200)
        self.send_header("Content-Type", entry[1])
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def run(state: Path, port: int = 8765) -> None:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, state=state))
    print(f"Conductor dashboard: http://127.0.0.1:{port}  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

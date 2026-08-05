#!/usr/bin/env python3
"""server.py — serves the public SPP3 stream-health page.

Standard library only. Reads the committed data files on each request and
renders; there is no database and no chain call in the request path, so the
page cannot be slower or less available than static hosting. Fresh data
arrives the way everything else does: the monitor commits status.json, the
push triggers a redeploy.
"""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "scripts"))

import render as R  # noqa: E402

PROVIDERS = ROOT / "data" / "providers.json"
STATUS = ROOT / "data" / "streams" / "status.json"


def _load():
    status = json.loads(STATUS.read_text())
    providers = json.loads(PROVIDERS.read_text())
    try:
        import stream_monitor as M
        status["findings"] = M.findings(status)
    except Exception:            # noqa: BLE001 - page must render regardless
        status.setdefault("findings", [])
    return status, providers


class Handler(BaseHTTPRequestHandler):
    server_version = "spp3-streams"

    def _send(self, code, body, ctype="text/html; charset=utf-8", cache="no-store"):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/":
                status, providers = _load()
                self._send(200, R.render(status, providers, time.time()))
            elif path == "/status.json":
                self._send(200, STATUS.read_text(), "application/json")
            elif path == "/healthz":
                self._send(200, "ok\n", "text/plain; charset=utf-8")
            else:
                self._send(404, "not found\n", "text/plain; charset=utf-8")
        except Exception as e:            # noqa: BLE001
            sys.stderr.write("error serving %s: %r\n" % (path, e))
            self._send(500, "temporarily unavailable\n",
                       "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write("serving on :%d\n" % port)
    srv.serve_forever()


if __name__ == "__main__":
    main()

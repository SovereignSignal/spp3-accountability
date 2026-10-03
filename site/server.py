#!/usr/bin/env python3
"""Serve committed accountability snapshots without network calls on requests."""
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "scripts"))

import render as R
import grails_view as G
import grails_chain_view as H

PROVIDERS = ROOT / "data" / "providers.json"
STATUS = ROOT / "data" / "streams" / "status.json"
BOARD = ROOT / "data" / "notion" / "board.json"
CALENDAR = ROOT / "data" / "calendar.json"
COMMITMENTS = ROOT / "data" / "commitments.json"
LEDGER = ROOT / "data" / "onchain" / "ledger.json"
GRAILS = ROOT / "data" / "grails" / "observations.json"
GRAILS_SALES = ROOT / "data" / "grails" / "sales.json"
GRAILS_CHAIN = ROOT / "data" / "grails" / "chain.json"
CHAIN_FEEDS = {
    "/grails-chain.json": GRAILS_CHAIN,
    "/grails-settlements.json": ROOT / "data" / "grails" / "settlements.json",
    "/grails-registrar.json": ROOT / "data" / "grails" / "registrar.json",
}


def _marketplace(ctx):
    return R.page_marketplace(ctx) + G.marketplace_summary(ctx)


def _measurements(ctx):
    return H.chain_section(ctx) + G.page_measurements(ctx)


R.ROUTES["/marketplace"] = ("Marketplace", _marketplace)
R.ROUTES["/marketplace/measurements"] = ("Grails measurements", _measurements)
if not any(path == "/marketplace/measurements" for path, _ in R.NAV):
    R.NAV.insert(3, ("/marketplace/measurements", "Measurements"))


def _optional(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _load():
    status = json.loads(STATUS.read_text())
    providers = json.loads(PROVIDERS.read_text())
    try:
        import stream_monitor as M
        status["findings"] = M.findings(status)
    except Exception:
        status.setdefault("findings", [])
    return {
        "status": status, "providers": providers, "board": _optional(BOARD),
        "calendar": _optional(CALENDAR), "commitments": _optional(COMMITMENTS),
        "ledger": _optional(LEDGER), "grails": _optional(GRAILS),
        "grails_chain": _optional(GRAILS_CHAIN),
    }


FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
    '<rect width="32" height="32" rx="7" fill="#1B5CF0"/>'
    '<text x="16" y="22" text-anchor="middle" fill="#fff" '
    'font-family="ui-monospace,SFMono-Regular,Menlo,monospace" '
    'font-size="15" font-weight="700">3</text></svg>'
)
ROBOTS_TXT = "User-agent: *\nAllow: /\nDisallow: /healthz\n"


class Handler(BaseHTTPRequestHandler):
    server_version = "spp3-streams"

    def _send(self, code, body, ctype="text/html; charset=utf-8", cache="no-store"):
        payload = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        try:
            if path == "/status.json":
                self._send(200, STATUS.read_text(), "application/json")
            elif path == "/ledger.json":
                self._send(200, LEDGER.read_text(), "application/json")
            elif path in CHAIN_FEEDS:
                doc = _optional(CHAIN_FEEDS[path])
                self._send(200 if doc else 503, json.dumps(doc or {"status": "unavailable"}), "application/json")
            elif path in ("/grails.json", "/grails-sales.json"):
                doc = _optional(GRAILS if path == "/grails.json" else GRAILS_SALES)
                self._send(200 if doc else 503, json.dumps(doc or {"status": "unavailable"}), "application/json")
            elif path == "/healthz":
                self._send(200, "ok\n", "text/plain; charset=utf-8")
            elif path == "/robots.txt":
                self._send(200, ROBOTS_TXT, "text/plain; charset=utf-8", cache="public, max-age=86400")
            elif path in ("/favicon.svg", "/favicon.ico"):
                self._send(200, FAVICON_SVG, "image/svg+xml", cache="public, max-age=86400")
            else:
                ctx = _load()
                ctx["now"] = time.time()
                html = R.render(ctx, path)
                if html is None:
                    self._send(404, "not found\n", "text/plain; charset=utf-8")
                else:
                    self._send(200, html)
        except Exception as e:
            sys.stderr.write("error serving %s: %r\n" % (path, e))
            self._send(500, "temporarily unavailable\n", "text/plain; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(os.environ.get("PORT", "8080"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write("serving on :%d\n" % port)
    srv.serve_forever()


if __name__ == "__main__":
    main()

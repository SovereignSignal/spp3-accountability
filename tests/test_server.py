import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "site"))
sys.path.insert(0, str(ROOT / "scripts"))
import server as S  # noqa: E402
import render as R  # noqa: E402


class TestPublicSurface(unittest.TestCase):
    def test_robots_allows_crawling_and_hides_healthz(self):
        self.assertIn("Allow: /", S.ROBOTS_TXT)
        self.assertIn("Disallow: /healthz", S.ROBOTS_TXT)

    def test_favicon_is_inline_svg(self):
        self.assertIn("<svg", S.FAVICON_SVG)
        self.assertIn("#1B5CF0", S.FAVICON_SVG)

    def test_old_marketplace_links_redirect_to_the_provider_page(self):
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer
        srv = ThreadingHTTPServer(("127.0.0.1", 0), S.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)

        class NoFollow(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoFollow)
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        for old, new in [("/marketplace", "/provider/nomentum"),
                         ("/marketplace/measurements", "/provider/nomentum/measurements")]:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                opener.open(base + old)
            self.assertEqual((cm.exception.code, cm.exception.headers["Location"]), (301, new))

    def test_board_json_is_not_a_public_route(self):
        # Committee process flags used to be served verbatim at /board.json.
        src = Path(S.__file__).read_text()
        self.assertNotIn('path == "/board.json"', src)
        self.assertIsNone(R.render({
            "status": {"overall": "healthy", "checked_at": "2026-08-20T15:00:01Z",
                       "block_number": 1, "streams": [], "retired": [],
                       "net_flow": {"ok": True, "unaccounted_wei_s": 0},
                       "runway": {"ok": True, "combined_days": 1, "level": "ok"}},
            "providers": {"providers": [], "spp3_stream_start": 1,
                          "master_stream_wei_s": 1},
            "board": {}, "calendar": {}, "commitments": {}, "now": 1,
        }, "/board.json"))


if __name__ == "__main__":
    unittest.main()

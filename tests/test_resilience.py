"""Resilience and security tests for the local HTTP server and storage layer.

Covers Fase 1 of the handover hardening: request-size limits, Origin
checking, concurrency limits on expensive routes, and storage corruption
recovery. Uses a real ThreadingHTTPServer bound to 127.0.0.1:0 (OS-assigned
port) so the tests exercise the actual wire protocol, not just the Python
functions in isolation.
"""

from __future__ import annotations

import http.client
import socket
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from src.server import AnalysisRunner, make_handler


class _LiveServerTestCase(unittest.TestCase):
    """Spins up the real Handler on a throwaway port for each test."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        dashboard_path = Path(self.tmp_dir.name) / "index.html"
        dashboard_path.write_text("<html><body>dashboard</body></html>", encoding="utf-8")
        self.runner = AnalysisRunner(runner=lambda: {"ok": True})
        handler_cls = make_handler(self.runner, dashboard_path)
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        self.tmp_dir.cleanup()

    def post(self, path: str, body: bytes = b"{}", extra_headers: dict | None = None) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {"Content-Type": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            conn.request("POST", path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def raw_get_with_header(self, path: str, header_lines: list[str]) -> bytes:
        """Send a GET with hand-built headers so a test can forge Host/Origin."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            has_host = any(line.lower().startswith("host:") for line in header_lines)
            request = f"GET {path} HTTP/1.1\r\n"
            if not has_host:
                request += "Host: 127.0.0.1\r\n"
            request += "\r\n".join(header_lines) + "\r\n" if header_lines else ""
            request += "\r\n"
            sock.sendall(request.encode("utf-8"))
            sock.settimeout(3)
            try:
                return sock.recv(4096)
            except socket.timeout:
                return b""
        finally:
            sock.close()

    def raw_post_with_header(self, path: str, header_lines: list[str], body: bytes) -> bytes:
        """Send a POST with hand-built headers, bypassing http.client's own
        Content-Length computation, so we can send an oversized or negative
        Content-Length without actually transmitting that many bytes."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        try:
            # Only send a default Host header if the caller didn't supply
            # their own (some tests deliberately override it).
            has_host = any(line.lower().startswith("host:") for line in header_lines)
            request = f"POST {path} HTTP/1.1\r\n"
            if not has_host:
                request += "Host: 127.0.0.1\r\n"
            request += "\r\n".join(header_lines) + "\r\n" if header_lines else ""
            request += "\r\n"
            sock.sendall(request.encode("utf-8") + body)
            sock.settimeout(3)
            try:
                return sock.recv(4096)
            except socket.timeout:
                return b""
        finally:
            sock.close()


class OriginHostCheckTest(_LiveServerTestCase):
    def test_rejects_forged_host_header(self) -> None:
        # Simulates DNS rebinding: the browser's Host header reflects the
        # hostname it originally navigated to, not 127.0.0.1, even once that
        # hostname's DNS record has been repointed at this server.
        response = self.raw_post_with_header(
            "/api/run",
            ["Host: evil.example.com", "Content-Length: 2", "Connection: close"],
            body=b"{}",
        )
        self.assertIn(b"403", response[:15])

    def test_rejects_cross_origin_fetch(self) -> None:
        # A hostile page's fetch()/<form> POST would carry its own Origin.
        response = self.raw_post_with_header(
            "/api/run",
            ["Origin: https://evil.example.com", "Content-Length: 2", "Connection: close"],
            body=b"{}",
        )
        self.assertIn(b"403", response[:15])

    def test_accepts_matching_localhost_origin(self) -> None:
        status, _ = self.post("/api/run", body=b"{}", extra_headers={"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertIn(status, (202, 409))

    def test_accepts_request_with_no_origin_header(self) -> None:
        # Same-origin requests, curl, and this app's own server-to-server
        # calls don't always send Origin; only Host is required to match.
        status, _ = self.post("/api/run", body=b"{}")
        self.assertIn(status, (202, 409))


class ReadApiOriginCheckTest(_LiveServerTestCase):
    """The read APIs return personal data (saved articles, feedback, the
    collected news). A hostile page that can reach 127.0.0.1 must not be able
    to read them any more than it can POST to them."""

    def test_rejects_forged_host_on_read_api(self) -> None:
        response = self.raw_get_with_header(
            "/api/sources", ["Host: evil.example.com", "Connection: close"]
        )
        self.assertIn(b"403", response[:15])

    def test_rejects_cross_origin_read(self) -> None:
        response = self.raw_get_with_header(
            "/api/saved", ["Origin: https://evil.example.com", "Connection: close"]
        )
        self.assertIn(b"403", response[:15])

    def test_allows_same_origin_read(self) -> None:
        response = self.raw_get_with_header(
            "/api/status", [f"Origin: http://127.0.0.1:{self.port}", "Connection: close"]
        )
        self.assertIn(b"200", response[:15])

    def test_dashboard_page_itself_is_still_served(self) -> None:
        # Only /api/* is gated: a plain navigation to the dashboard, which
        # carries no Origin header, must keep working.
        response = self.raw_get_with_header("/", ["Connection: close"])
        self.assertIn(b"200", response[:15])


class ContentLengthLimitTest(_LiveServerTestCase):
    def test_oversized_content_length_is_rejected_without_hanging(self) -> None:
        response = self.raw_post_with_header(
            "/api/run",
            ["Content-Length: 999999999999", "Connection: close"],
            body=b"{}",
        )
        # The point of the fix is that the server responds promptly instead
        # of trying to read ~1TB from the socket. Any prompt HTTP response
        # (it need not be a specific status) proves it didn't hang.
        self.assertTrue(response.startswith(b"HTTP/1.0") or response.startswith(b"HTTP/1.1"), response[:60])

    def test_negative_content_length_is_rejected_without_hanging(self) -> None:
        response = self.raw_post_with_header(
            "/api/run",
            ["Content-Length: -1", "Connection: close"],
            body=b"{}",
        )
        self.assertTrue(response.startswith(b"HTTP/1.0") or response.startswith(b"HTTP/1.1"), response[:60])

    def test_normal_sized_request_still_works(self) -> None:
        status, body = self.post("/api/run", body=b'{"source_id": "x"}')
        self.assertIn(status, (202, 409))


class TypedLoaderCorruptionTest(unittest.TestCase):
    """A truncated/corrupt data file (e.g. left behind by a crash mid-write,
    which save_json's atomic replace now prevents going forward, but old
    files or external interference could still produce) must make every
    typed loader degrade to its safe default, never raise, and never keep
    breaking on the next call (the corrupt file gets quarantined)."""

    def _write_corrupt(self, tmp_dir: str, name: str) -> Path:
        path = Path(tmp_dir) / name
        path.write_text("{this is not valid json", encoding="utf-8")
        return path

    def test_every_typed_loader_degrades_on_corrupt_file(self) -> None:
        from src.storage import json_storage as js

        cases = [
            (js.load_read_urls, set()),
            (js.load_trend_state, {"repositories": {}, "updated_at": ""}),
            (js.load_llm_insights, {}),
            (js.load_saved_articles, {}),
            (js.load_feedback, {}),
            (js.load_preferences, {"topics": {}, "keywords": {}, "sources": {}, "vote_count": 0}),
            (js.load_deep_dives, {}),
            (js.load_video_transcripts, {}),
            (js.load_llm_usage_log, []),
        ]
        for loader, expected_default in cases:
            with self.subTest(loader=loader.__name__):
                with tempfile.TemporaryDirectory() as tmp_dir:
                    corrupt_path = self._write_corrupt(tmp_dir, "state.json")

                    result = loader(corrupt_path)  # must not raise

                    self.assertEqual(result, expected_default)
                    # The corrupt file must be quarantined, not left in place
                    # to break every future call the same way.
                    self.assertFalse(corrupt_path.exists())
                    quarantined = list(Path(tmp_dir).glob("state.json.corrupt-*"))
                    self.assertEqual(len(quarantined), 1)

    def test_load_sources_config_degrades_on_corrupt_file(self) -> None:
        from src.storage.json_storage import load_sources_config

        with tempfile.TemporaryDirectory() as tmp_dir:
            corrupt_path = Path(tmp_dir) / "sources.json"
            corrupt_path.write_text("{this is not valid json", encoding="utf-8")
            missing_legacy = Path(tmp_dir) / "no_such_sources.yaml"

            result = load_sources_config(corrupt_path, legacy_yaml_path=missing_legacy)

            self.assertEqual(result, {"categories": [], "sources": []})
            self.assertFalse(corrupt_path.exists())

    def test_load_all_collection_reports_skips_corrupt_dates_not_the_whole_scan(self) -> None:
        from src.storage.json_storage import load_all_collection_reports, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-06-14").mkdir()
            (base / "2026-06-15").mkdir()
            save_json(base / "2026-06-14" / "collection_report.json", {"per_source": [{"source_id": "a"}]})
            (base / "2026-06-15" / "collection_report.json").write_text("{broken", encoding="utf-8")

            reports = load_all_collection_reports(base)

            self.assertEqual(set(reports.keys()), {"2026-06-14"})

    def test_load_recent_processed_news_skips_corrupt_dates_not_the_whole_scan(self) -> None:
        from datetime import date

        from src.storage.json_storage import load_recent_processed_news, save_json

        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-06-14").mkdir()
            (base / "2026-06-15").mkdir()
            save_json(base / "2026-06-14" / "news.json", [{"url": "https://a.com/1"}])
            (base / "2026-06-15" / "news.json").write_text("{broken", encoding="utf-8")

            items = load_recent_processed_news(base_dir=base, date=date(2026, 6, 15), days=2)

            self.assertEqual([i["url"] for i in items], ["https://a.com/1"])


if __name__ == "__main__":
    unittest.main()

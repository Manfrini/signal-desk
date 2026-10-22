"""Tests for the SSRF guard used on every server-side outbound fetch."""

from __future__ import annotations

import socket
import unittest
from unittest import mock

from src.net_guard import is_safe_public_url


class IsSafePublicUrlTest(unittest.TestCase):
    def test_rejects_non_http_schemes(self) -> None:
        for url in ("file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)"):
            safe, reason = is_safe_public_url(url)
            self.assertFalse(safe, url)
            self.assertTrue(reason)

    def test_rejects_missing_host(self) -> None:
        safe, reason = is_safe_public_url("http:///path")
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_rejects_loopback_ip(self) -> None:
        safe, reason = is_safe_public_url("http://127.0.0.1:8000/admin")
        self.assertFalse(safe)
        self.assertIn("privato", reason)

    def test_rejects_loopback_hostname(self) -> None:
        safe, reason = is_safe_public_url("http://localhost/admin")
        self.assertFalse(safe)

    def test_rejects_private_ip_literal(self) -> None:
        for host in ("10.0.0.5", "192.168.1.1", "172.16.0.1"):
            safe, reason = is_safe_public_url(f"http://{host}/x")
            self.assertFalse(safe, host)

    def test_rejects_link_local_metadata_ip(self) -> None:
        safe, reason = is_safe_public_url("http://169.254.169.254/latest/meta-data/")
        self.assertFalse(safe)

    def test_file_url_with_youtube_looking_fragment_is_rejected(self) -> None:
        # The exact bypass attempt from the audit: a non-http(s) URL whose
        # text merely contains "youtube.com/@..." somewhere in it.
        safe, reason = is_safe_public_url("file:///Users/x/.ssh/id_rsa#youtube.com/@a")
        self.assertFalse(safe)
        self.assertIn("schema", reason)

    def test_accepts_public_https_url(self) -> None:
        safe, reason = is_safe_public_url("https://example.com/feed")
        self.assertTrue(safe, reason)
        self.assertEqual(reason, "")

    def test_dns_resolution_failure_is_rejected(self) -> None:
        with mock.patch("socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            safe, reason = is_safe_public_url("https://this-does-not-resolve.example/x")
        self.assertFalse(safe)
        self.assertTrue(reason)

    def test_resolved_private_ip_is_rejected_even_for_a_public_looking_host(self) -> None:
        # Simulates a hostname that resolves to a private/loopback address.
        with mock.patch(
            "socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 80))],
        ):
            safe, reason = is_safe_public_url("http://sneaky.example/x")
        self.assertFalse(safe)


if __name__ == "__main__":
    unittest.main()

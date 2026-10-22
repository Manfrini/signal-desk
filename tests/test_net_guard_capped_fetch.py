"""Tests for the shared response-size cap used by every collector fetch."""

from __future__ import annotations

import unittest
from unittest import mock

from src.net_guard import fetch_capped_text


def _mock_response(chunks: list[bytes]) -> mock.Mock:
    response = mock.Mock()
    response.raise_for_status.return_value = None
    response.encoding = "utf-8"
    response.apparent_encoding = "utf-8"
    response.iter_content.return_value = iter(chunks)
    return response


class FetchCappedTextTest(unittest.TestCase):
    def test_returns_decoded_text_for_normal_response(self) -> None:
        with mock.patch("requests.get", return_value=_mock_response([b"hello ", b"world"])):
            text = fetch_capped_text("https://example.com/x")
        self.assertEqual(text, "hello world")

    def test_raises_when_body_exceeds_max_bytes(self) -> None:
        big_chunk = b"x" * 1000
        with mock.patch("requests.get", return_value=_mock_response([big_chunk, big_chunk])):
            with self.assertRaises(ValueError):
                fetch_capped_text("https://example.com/huge", max_bytes=1500)

    def test_stops_reading_as_soon_as_cap_is_exceeded(self) -> None:
        # A hostile/huge response must not be fully buffered before the cap
        # kicks in - iter_content should be consumed lazily.
        chunks_yielded = []

        def chunk_generator():
            for _ in range(1000):
                chunk = b"x" * 1000
                chunks_yielded.append(chunk)
                yield chunk

        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.encoding = "utf-8"
        response.apparent_encoding = "utf-8"
        response.iter_content.return_value = chunk_generator()

        with mock.patch("requests.get", return_value=response):
            with self.assertRaises(ValueError):
                fetch_capped_text("https://example.com/huge", max_bytes=2500)

        # Should have stopped a few chunks past the cap, nowhere near 1000.
        self.assertLess(len(chunks_yielded), 10)


if __name__ == "__main__":
    unittest.main()


class EncodingResolutionTest(unittest.TestCase):
    """A server that omits the charset must still decode.

    Regression: the code used `response.apparent_encoding` as the fallback,
    but that property reads `response.content`, which raises once
    `iter_content` has consumed the stream. Every feed served as e.g.
    `Content-Type: application/xml` with no `charset=` therefore failed
    permanently with "content for this response was already consumed"
    (found on posthog.com/rss.xml while verifying the source catalogue).
    """

    @staticmethod
    def _streamed_response(chunks: list[bytes], encoding: str | None) -> mock.Mock:
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.encoding = encoding
        type(response).apparent_encoding = mock.PropertyMock(
            side_effect=RuntimeError("The content for this response was already consumed")
        )
        response.iter_content.return_value = iter(chunks)
        return response

    def test_decodes_when_server_sends_no_charset(self) -> None:
        body = "ciao più".encode("utf-8")
        with mock.patch("requests.get", return_value=self._streamed_response([body], None)):
            text = fetch_capped_text("https://example.com/feed.xml")
        self.assertEqual(text, "ciao più")

    def test_never_touches_apparent_encoding(self) -> None:
        # Explicit guard: reaching for it at all re-introduces the crash.
        response = self._streamed_response([b"hi"], None)
        with mock.patch("requests.get", return_value=response):
            fetch_capped_text("https://example.com/feed.xml")  # must not raise

    def test_header_charset_still_wins_when_present(self) -> None:
        body = "caffè".encode("latin-1")
        with mock.patch("requests.get", return_value=self._streamed_response([body], "latin-1")):
            text = fetch_capped_text("https://example.com/feed.xml")
        self.assertEqual(text, "caffè")

"""Tests for deep-dive content fetching and generation."""

from __future__ import annotations

import threading
import time
import unittest
from unittest import mock

from src.processors import content_fetcher
from src.processors.content_fetcher import (
    REASON_FETCH_ERROR,
    REASON_NO_CAPTIONS,
    fetch_full_content,
    fetch_full_content_with_reason,
    youtube_video_id,
)
from src.processors.deep_dive import generate_deep_dive
from src.server import DeepDiveManager


class VideoIdTest(unittest.TestCase):
    def test_extracts_id_from_common_forms(self) -> None:
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=abc123XYZ"), "abc123XYZ")
        self.assertEqual(youtube_video_id("https://youtu.be/abc123XYZ"), "abc123XYZ")
        self.assertEqual(youtube_video_id("https://www.youtube.com/shorts/abc123XYZ"), "abc123XYZ")
        self.assertEqual(youtube_video_id("https://example.com/post"), "")


class _NoDiskTranscriptCacheMixin:
    """Keep the video-transcript cache in-memory only during these tests, so
    a video_id reused across tests never leaks a cached outcome via the real
    data/video_transcripts.json file."""

    def setUp(self) -> None:
        super().setUp()  # type: ignore[misc]
        self._transcript_cache: dict[str, dict] = {}
        load_patch = mock.patch.object(
            content_fetcher, "load_video_transcripts", side_effect=lambda: dict(self._transcript_cache)
        )
        save_patch = mock.patch.object(
            content_fetcher,
            "save_video_transcript",
            side_effect=lambda video_id, entry: self._transcript_cache.__setitem__(video_id, entry),
        )
        load_patch.start()
        save_patch.start()
        self.addCleanup(load_patch.stop)  # type: ignore[attr-defined]
        self.addCleanup(save_patch.stop)  # type: ignore[attr-defined]


class FetchFullContentTest(_NoDiskTranscriptCacheMixin, unittest.TestCase):
    def test_youtube_uses_transcript_when_available(self) -> None:
        item = {"url": "https://www.youtube.com/watch?v=abc123XYZ", "title": "Video"}
        with mock.patch.object(content_fetcher, "_youtube_transcript", return_value=("parola " * 100, "")):
            text, complete = fetch_full_content(item)
        self.assertTrue(complete)
        self.assertIn("parola", text)

    def test_youtube_without_transcript_is_partial(self) -> None:
        item = {"url": "https://www.youtube.com/watch?v=abc123XYZ", "title": "Video", "summary": "Riassunto"}
        with mock.patch.object(content_fetcher, "_youtube_transcript", return_value=("", REASON_NO_CAPTIONS)):
            text, complete = fetch_full_content(item)
        self.assertFalse(complete)
        self.assertIn("Riassunto", text)

    def test_article_full_content(self) -> None:
        item = {"url": "https://example.com/post", "title": "Articolo"}
        fake = mock.Mock(content_quality="full", raw_content="x" * 500, excerpt="e")
        with mock.patch("src.collectors.article_extractor.fetch_article_content", return_value=fake):
            text, complete = fetch_full_content(item)
        self.assertTrue(complete)
        self.assertEqual(len(text), 500)

    def test_article_fetch_failure_falls_back_partial(self) -> None:
        item = {"url": "https://example.com/post", "title": "Articolo", "summary": "Sintesi"}
        with mock.patch("src.collectors.article_extractor.fetch_article_content", side_effect=RuntimeError("403")):
            text, complete = fetch_full_content(item)
        self.assertFalse(complete)
        self.assertIn("Sintesi", text)


class FetchFullContentWithReasonTest(_NoDiskTranscriptCacheMixin, unittest.TestCase):
    def test_distinguishes_no_captions_from_fetch_error(self) -> None:
        item = {"url": "https://www.youtube.com/watch?v=abc123XYZ", "title": "Video", "summary": "s"}
        with mock.patch.object(content_fetcher, "_youtube_transcript", return_value=("", REASON_NO_CAPTIONS)):
            _, complete, reason = fetch_full_content_with_reason(item)
        self.assertFalse(complete)
        self.assertEqual(reason, REASON_NO_CAPTIONS)

        self._transcript_cache.clear()  # REASON_NO_CAPTIONS above was cached; reset for the next case.
        with mock.patch.object(content_fetcher, "_youtube_transcript", return_value=("", REASON_FETCH_ERROR)):
            _, complete, reason = fetch_full_content_with_reason(item)
        self.assertFalse(complete)
        self.assertEqual(reason, REASON_FETCH_ERROR)

    def test_article_failure_reason_is_fetch_error(self) -> None:
        item = {"url": "https://example.com/post", "title": "Articolo", "summary": "s"}
        with mock.patch("src.collectors.article_extractor.fetch_article_content", side_effect=RuntimeError("403")):
            _, complete, reason = fetch_full_content_with_reason(item)
        self.assertFalse(complete)
        self.assertEqual(reason, REASON_FETCH_ERROR)

    def test_transcript_library_error_is_classified_correctly(self) -> None:
        # A real CouldNotRetrieveTranscript subclass -> REASON_NO_CAPTIONS;
        # RequestBlocked/IpBlocked or any other exception -> REASON_FETCH_ERROR.
        from youtube_transcript_api import RequestBlocked, TranscriptsDisabled

        with mock.patch(
            "youtube_transcript_api.YouTubeTranscriptApi.fetch",
            side_effect=TranscriptsDisabled("abc123XYZ"),
        ):
            text, reason = content_fetcher._youtube_transcript("abc123XYZ")
        self.assertEqual(text, "")
        self.assertEqual(reason, REASON_NO_CAPTIONS)

        with mock.patch(
            "youtube_transcript_api.YouTubeTranscriptApi.fetch",
            side_effect=RequestBlocked("abc123XYZ"),
        ):
            text, reason = content_fetcher._youtube_transcript("abc123XYZ")
        self.assertEqual(text, "")
        self.assertEqual(reason, REASON_FETCH_ERROR)

        with mock.patch(
            "youtube_transcript_api.YouTubeTranscriptApi.fetch",
            side_effect=RuntimeError("boom"),
        ):
            text, reason = content_fetcher._youtube_transcript("abc123XYZ")
        self.assertEqual(text, "")
        self.assertEqual(reason, REASON_FETCH_ERROR)


class YoutubeTranscriptCachedTest(_NoDiskTranscriptCacheMixin, unittest.TestCase):
    def test_fetches_and_caches_definitive_outcome(self) -> None:
        with mock.patch.object(
            content_fetcher, "_youtube_transcript", return_value=("testo", "")
        ) as fetch:
            text, reason = content_fetcher._youtube_transcript_cached("vid1")
        self.assertEqual((text, reason), ("testo", ""))
        fetch.assert_called_once_with("vid1")
        self.assertEqual(self._transcript_cache["vid1"], {"text": "testo", "reason": ""})

    def test_second_call_hits_cache_without_refetching(self) -> None:
        with mock.patch.object(content_fetcher, "_youtube_transcript", return_value=("testo", "")):
            content_fetcher._youtube_transcript_cached("vid1")

        with mock.patch.object(content_fetcher, "_youtube_transcript") as fetch:
            text, reason = content_fetcher._youtube_transcript_cached("vid1")
        fetch.assert_not_called()
        self.assertEqual((text, reason), ("testo", ""))

    def test_no_captions_is_also_cached(self) -> None:
        with mock.patch.object(
            content_fetcher, "_youtube_transcript", return_value=("", REASON_NO_CAPTIONS)
        ):
            content_fetcher._youtube_transcript_cached("vid2")

        with mock.patch.object(content_fetcher, "_youtube_transcript") as fetch:
            text, reason = content_fetcher._youtube_transcript_cached("vid2")
        fetch.assert_not_called()
        self.assertEqual((text, reason), ("", REASON_NO_CAPTIONS))

    def test_fetch_error_is_not_cached_and_is_retried(self) -> None:
        with mock.patch.object(
            content_fetcher, "_youtube_transcript", return_value=("", REASON_FETCH_ERROR)
        ) as fetch:
            content_fetcher._youtube_transcript_cached("vid3")
            content_fetcher._youtube_transcript_cached("vid3")
        self.assertEqual(fetch.call_count, 2)
        self.assertNotIn("vid3", self._transcript_cache)


class GenerateDeepDiveTest(unittest.TestCase):
    class _StubAnalyzer:
        calls = 1

        def run_structured(self, prompt, schema):
            return {
                "overview": "Panoramica del contenuto.",
                "points": [{"title": "Punto", "detail": "Dettaglio del punto."}, {"title": "", "detail": ""}],
                "insights": ["Insight uno", ""],
            }

        def usage_summary(self):
            return {"calls": 1, "total_cost_usd": 0.05, "input_tokens": 100, "output_tokens": 60}

    def test_builds_dive_and_marks_complete(self) -> None:
        item = {"url": "https://example.com/x", "title": "T"}
        with mock.patch(
            "src.processors.deep_dive.fetch_full_content_with_reason",
            return_value=("contenuto lungo " * 50, True, ""),
        ):
            dive = generate_deep_dive(item, goal="g", analyzer=self._StubAnalyzer())
        self.assertEqual(dive["overview"], "Panoramica del contenuto.")
        self.assertEqual(len(dive["points"]), 1)  # empty point dropped
        self.assertEqual(dive["insights"], ["Insight uno"])  # empty dropped
        self.assertTrue(dive["content_complete"])
        self.assertNotIn("partial_note", dive)
        self.assertIn("contenuto lungo", dive["source_text"])

    def test_partial_content_flag_and_note_for_no_captions(self) -> None:
        item = {"url": "https://example.com/x", "title": "T", "summary": "s"}
        with mock.patch(
            "src.processors.deep_dive.fetch_full_content_with_reason",
            return_value=("estratto", False, REASON_NO_CAPTIONS),
        ):
            dive = generate_deep_dive(item, goal="", analyzer=self._StubAnalyzer())
        self.assertFalse(dive["content_complete"])
        self.assertIn("sottotitoli", dive["partial_note"].lower())
        self.assertEqual(dive["source_text"], "estratto")

    def test_partial_content_note_for_fetch_error(self) -> None:
        item = {"url": "https://example.com/x", "title": "T", "summary": "s"}
        with mock.patch(
            "src.processors.deep_dive.fetch_full_content_with_reason",
            return_value=("estratto", False, REASON_FETCH_ERROR),
        ):
            dive = generate_deep_dive(item, goal="", analyzer=self._StubAnalyzer())
        self.assertFalse(dive["content_complete"])
        self.assertIn("blocco", dive["partial_note"].lower())

    def test_no_content_returns_error_dive(self) -> None:
        item = {"url": "https://example.com/x"}
        with mock.patch(
            "src.processors.deep_dive.fetch_full_content_with_reason",
            return_value=("", False, REASON_FETCH_ERROR),
        ):
            dive = generate_deep_dive(item, goal="", analyzer=self._StubAnalyzer())
        self.assertEqual(dive["points"], [])
        self.assertIn("recuperare", dive["overview"].lower())


def _wait(manager: DeepDiveManager, url: str, target: str, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if manager.get(url)["status"] == target:
            return True
        time.sleep(0.01)
    return False


class DeepDiveManagerTest(unittest.TestCase):
    def test_runs_and_caches_result(self) -> None:
        manager = DeepDiveManager(generator=lambda item: {"overview": "ok", "points": [], "insights": [], "content_complete": True})
        manager._results.clear()  # ignore any on-disk cache for the test
        url = "https://example.com/x"
        self.assertEqual(manager.start(url, {"url": url}), "running")
        self.assertTrue(_wait(manager, url, "done"))
        self.assertEqual(manager.get(url)["result"]["overview"], "ok")
        # Second start hits cache.
        self.assertEqual(manager.start(url, {"url": url}), "done")

    def test_rejects_new_jobs_as_busy_once_at_the_concurrency_limit(self) -> None:
        gate = threading.Event()
        manager = DeepDiveManager(generator=lambda item: gate.wait(2) and {"overview": "ok", "points": [], "insights": [], "content_complete": True})
        manager._results.clear()

        from src.server import MAX_CONCURRENT_DEEP_DIVES

        for i in range(MAX_CONCURRENT_DEEP_DIVES):
            url = f"https://example.com/busy-{i}"
            self.assertEqual(manager.start(url, {"url": url}), "running")

        overflow_url = "https://example.com/overflow"
        self.assertEqual(manager.start(overflow_url, {"url": overflow_url}), "busy")

        gate.set()  # let the in-flight jobs finish so the thread doesn't leak past the test
        for i in range(MAX_CONCURRENT_DEEP_DIVES):
            self.assertTrue(_wait(manager, f"https://example.com/busy-{i}", "done"))


if __name__ == "__main__":
    unittest.main()

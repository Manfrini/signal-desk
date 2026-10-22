"""Transcript requests are spaced out and stop once YouTube blocks us."""

from __future__ import annotations

import pytest

from src.processors import content_fetcher as cf


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(cf, "load_video_transcripts", lambda *a, **k: {})
    monkeypatch.setattr(cf, "save_video_transcript", lambda *a, **k: None)
    monkeypatch.setattr(cf, "_transcripts_blocked_until", 0.0)
    monkeypatch.setattr(cf, "_last_transcript_request", 0.0)


def test_after_a_block_no_further_request_reaches_youtube(monkeypatch):
    from youtube_transcript_api import RequestBlocked

    calls = []

    class Blocked:
        def fetch(self, video_id, languages):
            calls.append(video_id)
            raise RequestBlocked(video_id)

    monkeypatch.setattr("youtube_transcript_api.YouTubeTranscriptApi", Blocked)
    monkeypatch.setattr(cf.time, "sleep", lambda s: None)

    assert cf._youtube_transcript_cached("aaa") == ("", cf.REASON_FETCH_ERROR)
    assert cf._youtube_transcript_cached("bbb") == ("", cf.REASON_FETCH_ERROR)
    assert calls == ["aaa"]


def test_live_requests_are_spaced_by_the_minimum_interval(monkeypatch):
    sleeps = []
    monkeypatch.setattr(cf, "_youtube_transcript", lambda vid: ("x" * 300, cf.REASON_NONE))
    monkeypatch.setattr(cf.time, "sleep", sleeps.append)

    cf._youtube_transcript_cached("aaa")
    cf._youtube_transcript_cached("bbb")

    assert len(sleeps) >= 1 and 0 < sleeps[-1] <= cf.TRANSCRIPT_MIN_INTERVAL_SECONDS


def test_items_older_than_the_cutoff_are_not_enriched():
    from datetime import date

    from src.collectors.base import SourceConfig
    from src.collectors.rss_collector import RssCollector

    fetched = []
    collector = RssCollector(article_fetcher=lambda url: fetched.append(url))
    collector.set_cutoff_date(date(2026, 10, 1))

    assert collector._is_before_cutoff({"published_at": "Mon, 01 Jun 2026 10:00:00 GMT"})
    assert not collector._is_before_cutoff({"published_at": "Tue, 06 Oct 2026 10:00:00 GMT"})
    assert not collector._is_before_cutoff({"published_at": ""})

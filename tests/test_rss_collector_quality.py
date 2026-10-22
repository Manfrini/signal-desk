"""Tests for RSS collector content quality behavior."""

from __future__ import annotations

from src.collectors.base import SourceConfig
from src.collectors.rss_collector import RssCollector


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "utf-8"
        self.apparent_encoding = "utf-8"

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 65536):
        yield self.text.encode("utf-8")

    def close(self) -> None:
        return None


def test_rss_collector_uses_metadata_excerpt_when_article_fetch_fails(monkeypatch) -> None:
    rss = """
    <rss version="2.0">
      <channel>
        <item>
          <title>OpenAI introduces a new model for developers</title>
          <link>https://openai.com/index/model-update</link>
          <pubDate>Tue, 05 May 2026 10:00:00 GMT</pubDate>
          <description>OpenAI introduced a model update for developers using the API with improved reliability and latency.</description>
        </item>
      </channel>
    </rss>
    """
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(rss),
    )

    source = SourceConfig(
        id="openai_news",
        name="OpenAI News",
        company="OpenAI",
        url="https://openai.com/news/rss.xml",
        type="rss",
        category_hint="general_ai",
        enabled=True,
        allowed_domains=("openai.com",),
    )

    def failing_fetcher(url: str):
        raise RuntimeError("403")

    items = RssCollector(article_fetcher=failing_fetcher).collect(source)

    assert len(items) == 1
    assert items[0]["url"] == "https://openai.com/index/model-update"
    assert items[0]["content_quality"] == "excerpt"
    assert "developers using the API" in items[0]["excerpt"]


def test_rss_collector_never_fetches_wrong_domain_items(monkeypatch) -> None:
    # A compromised or typo'd feed can list a link to any host; the fetcher
    # must never be called for a URL outside the source's allowed_domains,
    # instead of relying only on the post-collect() domain filter.
    rss = """
    <rss version="2.0">
      <channel>
        <item>
          <title>Suspicious redirect entry</title>
          <link>http://127.0.0.1:9200/_cat/indices</link>
          <pubDate>Tue, 05 May 2026 10:00:00 GMT</pubDate>
          <description>This should never be fetched because the host is not allowed.</description>
        </item>
      </channel>
    </rss>
    """
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(rss),
    )
    source = SourceConfig(
        id="openai_news",
        name="OpenAI News",
        company="OpenAI",
        url="https://openai.com/news/rss.xml",
        type="rss",
        category_hint="general_ai",
        enabled=True,
        allowed_domains=("openai.com",),
    )

    def fetcher_that_must_not_be_called(url: str):
        raise AssertionError(f"article_fetcher must not be called for wrong-domain url: {url}")

    items = RssCollector(article_fetcher=fetcher_that_must_not_be_called).collect(source)

    assert len(items) == 1
    assert items[0]["content_quality"] == "excerpt"


def _youtube_rss(video_id: str) -> str:
    return f"""
    <rss version="2.0">
      <channel>
        <item>
          <title>Nuovo video sull'AI</title>
          <link>https://www.youtube.com/watch?v={video_id}</link>
          <pubDate>Tue, 05 May 2026 10:00:00 GMT</pubDate>
          <description>Breve descrizione del video dal feed RSS.</description>
        </item>
      </channel>
    </rss>
    """


def _youtube_source() -> SourceConfig:
    return SourceConfig(
        id="yt_channel",
        name="Canale YouTube",
        company="Canale YouTube",
        url="https://www.youtube.com/feeds/videos.xml?channel_id=UC1",
        type="rss",
        category_hint="general_ai",
        enabled=True,
        allowed_domains=("youtube.com",),
        source_group="opinion_video",
        signal_type="video",
    )


def test_rss_collector_uses_transcript_for_video_items_when_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(_youtube_rss("abcDEF123")),
    )
    monkeypatch.setattr(
        "src.processors.content_fetcher._youtube_transcript_cached",
        lambda video_id: ("parola " * 100, ""),
    )

    items = RssCollector().collect(_youtube_source())

    assert len(items) == 1
    assert items[0]["content_quality"] == "full"
    assert "parola" in items[0]["raw_content"]


def test_rss_collector_falls_back_to_description_when_no_transcript(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(_youtube_rss("abcDEF123")),
    )
    monkeypatch.setattr(
        "src.processors.content_fetcher._youtube_transcript_cached",
        lambda video_id: ("", "no_captions"),
    )

    items = RssCollector().collect(_youtube_source())

    assert len(items) == 1
    assert items[0]["content_quality"] == "excerpt"
    assert "Breve descrizione del video" in items[0]["excerpt"]
    assert items[0]["raw_content"] == ""

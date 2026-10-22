"""Tests for HTML collector content quality filtering."""

from __future__ import annotations

from src.collectors.article_extractor import ArticleContent
from src.collectors.base import SourceConfig
from src.collectors.html_collector import HtmlCollector


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


def source() -> SourceConfig:
    return SourceConfig(
        id="anthropic_news",
        name="Anthropic News",
        company="Anthropic",
        url="https://www.anthropic.com/news",
        type="html",
        category_hint="general_ai",
        enabled=True,
        notes="test",
        allowed_domains=("anthropic.com",),
    )


def test_html_collector_rejects_mailto_and_generic_newsroom(monkeypatch) -> None:
    html = """
    <html><body>
      <article>
        <a href="mailto:press@anthropic.com">press</a>
        <h2>Newsroom</h2>
      </article>
      <article>
        <a href="/news/claude-opus-4-7">Read</a>
        <h2>Introducing Claude Opus 4.7</h2>
        <p>Our latest model improves coding and agentic work.</p>
      </article>
    </body></html>
    """
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(html),
    )

    collector = HtmlCollector(
        article_fetcher=lambda url: ArticleContent(
            clean_title="Introducing Claude Opus 4.7",
            excerpt="Our latest model improves coding and agentic work.",
            raw_content="Our latest model improves coding and agentic work. It is available for teams.",
            content_quality="excerpt",
        )
    )

    items = collector.collect(source())

    assert len(items) == 1
    assert items[0]["url"] == "https://www.anthropic.com/news/claude-opus-4-7"
    assert items[0]["clean_title"] == "Introducing Claude Opus 4.7"
    assert items[0]["content_quality"] == "excerpt"


def test_html_collector_marks_title_only_when_article_fetch_fails(monkeypatch) -> None:
    html = """
    <html><body>
      <article>
        <a href="/news/finance-agents">Read</a>
        <h2>Agents for financial services</h2>
      </article>
    </body></html>
    """
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(html),
    )

    def failing_fetcher(url: str) -> ArticleContent:
        raise RuntimeError("blocked")

    items = HtmlCollector(article_fetcher=failing_fetcher).collect(source())

    assert len(items) == 1
    assert items[0]["url"] == "https://www.anthropic.com/news/finance-agents"
    assert items[0]["content_quality"] == "title_only"


def test_html_collector_rejects_external_wrong_domain_links(monkeypatch) -> None:
    html = """
    <html><body>
      <article>
        <a href="https://nvidia.github.io/NeMo/blogs/parakeet/">External</a>
        <h2>NVIDIA NeMo audio research update</h2>
      </article>
      <article>
        <a href="/blog/suno-v5-update">Read</a>
        <h2>Suno v5 update for music creation</h2>
      </article>
    </body></html>
    """
    monkeypatch.setattr(
        "src.net_guard.requests.get",
        lambda *args, **kwargs: FakeResponse(html),
    )

    suno_source = SourceConfig(
        id="suno_blog",
        name="Suno Blog",
        company="Suno",
        url="https://suno.com/blog/",
        type="html",
        category_hint="music_generation",
        enabled=True,
        notes="test",
        allowed_domains=("suno.com",),
    )

    collector = HtmlCollector(
        article_fetcher=lambda url: ArticleContent(
            clean_title="Suno v5 update for music creation",
            excerpt="Suno updates its music creation model with new audio controls.",
            raw_content="",
            content_quality="excerpt",
        )
    )

    items = collector.collect(suno_source)

    assert [item["url"] for item in items] == ["https://suno.com/blog/suno-v5-update"]
    assert collector.excluded_wrong_domain_items == 1

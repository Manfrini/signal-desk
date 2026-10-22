"""Dates on link-based HTML sources.

The collector has two extraction paths. The `<article>` one reads a `<time>`
element; the link-based fallback hardcoded `published_at=""`. Sources whose
cards are plain divs -- anthropic.com/news among them -- therefore produced
items with no date at all: 43 of them were sitting in the dashboard, unable
to earn a recency bonus and sorted last.

Extracting the text is not enough on its own: "Sep 18, 2026" is not a format
the pipeline's date parser accepts, so the collector normalises it to ISO
before handing it on.
"""

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


def _collect(html: str, monkeypatch):
    monkeypatch.setattr("src.net_guard.requests.get", lambda *a, **k: FakeResponse(html))
    collector = HtmlCollector(
        article_fetcher=lambda url: ArticleContent(text="Testo completo.", quality="full")
    )
    return collector.collect(source())


def test_reads_the_date_from_a_time_element_beside_the_link(monkeypatch):
    # anthropic.com's featured block, simplified: a <time> in the card.
    html = """
    <html><body><div>
      <div class="gridItem">
        <a href="/news/claude-opus-5-5">
          <span>Announcements</span>
          <time>Sep 22, 2026</time>
          <h2>Introducing Claude Opus 5.5</h2>
          <p>Opus 5.5 performs at the level of Fable 5.1 on most work.</p>
        </a>
      </div>
    </div></body></html>
    """
    items = _collect(html, monkeypatch)

    assert items, "the card should be collected"
    assert items[0]["published_at"].startswith("2026-09-22")


def test_reads_the_date_from_the_card_text_when_there_is_no_time_element(monkeypatch):
    html = """
    <html><body><div>
      <div class="gridItem">
        <a href="/news/accenture-embedded-evaluation">
          Sep 18, 2026 Announcements Partnering with Accenture on embedded evaluation
        </a>
      </div>
    </div></body></html>
    """
    items = _collect(html, monkeypatch)

    assert items
    assert items[0]["published_at"].startswith("2026-09-18")


def test_the_emitted_date_is_understood_by_the_ranking_parser(monkeypatch):
    # A date the pipeline cannot parse is the same as no date at all.
    from src.processors.intelligence import _parse_date

    html = """
    <html><body><div><div class="gridItem">
      <a href="/news/life-sciences"><time>Sep 17, 2026</time>
      <h2>Introducing the Life Sciences Verification Program</h2></a>
    </div></div></body></html>
    """
    items = _collect(html, monkeypatch)

    assert _parse_date(items[0]["published_at"]) is not None


def test_a_card_without_any_date_still_collects(monkeypatch):
    # Missing dates must not cost us the article. The collector stamps the run
    # date as an upper bound so the item can still age out of the window.
    html = """
    <html><body><div><div class="gridItem">
      <a href="/news/no-date-here"><h2>Un annuncio senza data</h2></a>
    </div></div></body></html>
    """
    items = _collect(html, monkeypatch)

    assert items
    assert items[0]["date_quality"] == "estimated"


# --- The featured article ---------------------------------------------------
#
# anthropic.com/news puts its lead story in a FeaturedGrid block whose link is
# a top-level path (/claude-opus-5-5), not /news/something. The "an article URL
# has at least two path segments" rule -- which is what keeps /pricing and
# /about out -- rejected it, so the one post announcing a new model never
# reached the dashboard.


def test_collects_a_featured_article_at_a_top_level_path(monkeypatch):
    html = """
    <html><body><div class="FeaturedGrid">
      <div class="featuredItem">
        <a href="/claude-opus-5-5">
          <h2>Introducing Claude Opus 5.5</h2>
          <span>Announcements</span><time>Sep 22, 2026</time>
          <p>Opus 5.5 performs at the level of Fable 5.1 on most work.</p>
        </a>
      </div>
    </div></body></html>
    """
    items = _collect(html, monkeypatch)

    urls = [i["url"] for i in items]
    assert "https://www.anthropic.com/claude-opus-5-5" in urls
    assert items[0]["published_at"].startswith("2026-09-22")


def test_still_rejects_undated_navigation_links(monkeypatch):
    # The date is what distinguishes an article from a menu entry; without one
    # a single-segment path stays out, as before.
    html = """
    <html><body><nav>
      <a href="/pricing">Pricing and plans for every team</a>
      <a href="/about">About the company and our mission</a>
    </nav></body></html>
    """
    items = _collect(html, monkeypatch)

    assert items == []


def test_ignores_in_page_anchors_on_the_source_page(monkeypatch):
    # Accessibility skip-links: the same page as the source, plus a fragment.
    # The old two-segment rule hid them; once a date could waive that rule they
    # had to be rejected on their own merits.
    html = """
    <html><body>
      <a href="/news#main-content">Skip to main content</a>
      <a href="/news#footer">Skip to footer of this page</a>
      <div class="featuredItem">
        <a href="/claude-opus-5-5"><h2>Introducing Claude Opus 5.5</h2>
        <time>Sep 22, 2026</time></a>
      </div>
    </body></html>
    """
    items = _collect(html, monkeypatch)

    assert [i["url"] for i in items] == ["https://www.anthropic.com/claude-opus-5-5"]


def test_does_not_borrow_a_date_from_a_neighbouring_card(monkeypatch):
    # A wrapper holding several links is a grid, not a card: its <time>
    # belongs to one of the children, not to all of them.
    html = """
    <html><body><div class="grid">
      <div class="card"><a href="/news/with-date"><h2>Un articolo con la sua data</h2>
        <time>Sep 22, 2026</time></a></div>
      <div class="card"><a href="/news/without-date"><h2>Un articolo senza data propria</h2></a></div>
    </div></body></html>
    """
    items = _collect(html, monkeypatch)

    by_url = {i["url"].rsplit("/", 1)[-1]: i for i in items}
    assert by_url["with-date"]["published_at"].startswith("2026-09-22")
    # The sibling gets an estimate from the run, never the neighbour's date.
    assert not by_url["without-date"]["published_at"].startswith("2026-09-22")
    assert by_url["without-date"]["date_quality"] == "estimated"

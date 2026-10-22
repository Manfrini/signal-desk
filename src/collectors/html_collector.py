"""Generic HTML source collector."""

from __future__ import annotations

import logging
import re
from datetime import date as date_type
from datetime import datetime
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from src.net_guard import fetch_capped_text
from .base import BaseCollector, SourceConfig, normalize_item
from .article_extractor import ArticleContent, fetch_article_content

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "AI-News-Intelligence-Agent/0.1"
GENERIC_TITLES = {"newsroom", "blog", "updates", "home", "news"}
MIN_MEANINGFUL_TITLE_LENGTH = 8
MAX_ITEMS_TO_ENRICH = 10


class HtmlCollector(BaseCollector):
    """Collect items from a generic HTML page."""

    def __init__(
        self,
        article_fetcher: Callable[[str], ArticleContent] = fetch_article_content,
    ) -> None:
        self.article_fetcher = article_fetcher
        self.excluded_wrong_domain_items = 0

    def collect(self, source: SourceConfig) -> list[dict[str, str]]:
        self.excluded_wrong_domain_items = 0
        text = fetch_capped_text(
            source.url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            headers={"User-Agent": USER_AGENT},
        )

        soup = BeautifulSoup(text, "html.parser")
        meta_summary = _meta_description(soup)
        wrong_domain_count = [0]
        items = _extract_article_items(source, soup, meta_summary, wrong_domain_count)
        self.excluded_wrong_domain_items = wrong_domain_count[0]
        if items:
            return [
                _enrich_with_article_content(item, self.article_fetcher)
                for item in items[:MAX_ITEMS_TO_ENRICH]
            ]
        link_wrong_domain_count = [0]
        link_items = _extract_link_items(source, soup, meta_summary, link_wrong_domain_count)
        self.excluded_wrong_domain_items += link_wrong_domain_count[0]
        return [
            _enrich_with_article_content(item, self.article_fetcher)
            for item in link_items[:MAX_ITEMS_TO_ENRICH]
        ]


def _meta_description(soup: BeautifulSoup) -> str:
    meta = soup.find("meta", attrs={"name": "description"})
    if isinstance(meta, Tag):
        content = meta.get("content")
        if content:
            return str(content).strip()
    return ""


def _extract_article_items(
    source: SourceConfig,
    soup: BeautifulSoup,
    meta_summary: str,
    wrong_domain_count: list[int],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for article in soup.find_all("article"):
        if not isinstance(article, Tag):
            continue

        link = article.find("a", href=True)
        href = link.get("href") if isinstance(link, Tag) else ""
        title_node = article.find(["h1", "h2", "h3"])
        if title_node is None:
            title_node = link
        summary_node = article.find("p")
        time_node = article.find("time")

        published_at = ""
        if isinstance(time_node, Tag):
            published_at = _to_iso_date(
                str(time_node.get("datetime") or time_node.get_text(" ", strip=True))
            )

        title = title_node.get_text(" ", strip=True) if isinstance(title_node, Tag) else ""
        absolute_url = urljoin(source.url, str(href or ""))
        if _is_wrong_domain(source, absolute_url):
            wrong_domain_count[0] += 1
            logger.info("Skipping wrong-domain HTML article candidate from source %s: %s", source.id, absolute_url)
            continue
        if not _is_valid_candidate(source, title, absolute_url):
            logger.info("Skipping low-quality HTML article candidate from source %s: %s", source.id, title)
            continue

        normalized = normalize_item(
            source,
            title=title,
            url=absolute_url,
            published_at=published_at,
            raw_summary=summary_node.get_text(" ", strip=True)
            if isinstance(summary_node, Tag)
            else meta_summary,
            raw_content="",
        )
        if normalized is None:
            logger.info("Skipping HTML article without URL from source %s", source.id)
            continue
        items.append(normalized)

    return items


_TEXT_DATE_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b",
    re.I,
)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1
)}


def _to_iso_date(value: str) -> str:
    """Normalise a human date to ISO, or return it unchanged if already usable.

    "Sep 18, 2026" is what these cards print, and it is not a format the
    pipeline's date parser accepts -- extracting it without converting would
    leave the item as dateless as before.
    """
    text = (value or "").strip()
    if not text:
        return ""
    try:  # already ISO / datetime attribute
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    match = _TEXT_DATE_RE.search(text)
    if not match:
        return text  # leave it to the downstream parser to judge
    month = _MONTHS.get(match.group(1)[:3].lower())
    if not month:
        return text
    try:
        return date_type(int(match.group(3)), month, int(match.group(2))).isoformat()
    except ValueError:
        return text


def _published_at_for_link(link: Tag) -> str:
    """Find the date belonging to a link-based card.

    Looks for a <time> inside the link or in the small block around it, then
    falls back to a date written in the card's own text.
    """
    scopes = [link]
    parent = link.parent
    for _ in range(3):  # the card wrapper is rarely deeper than this
        if not isinstance(parent, Tag):
            break
        # Once the block holds more than this link it is a grid, not a card:
        # its <time> belongs to one child, and borrowing it would date every
        # sibling — and any stray link nearby — with someone else's date.
        if len(parent.find_all("a", href=True)) > 1:
            break
        scopes.append(parent)
        parent = parent.parent
    for scope in scopes:
        time_node = scope.find("time")
        if isinstance(time_node, Tag):
            raw = str(time_node.get("datetime") or time_node.get_text(" ", strip=True))
            if raw.strip():
                return _to_iso_date(raw)
    for scope in scopes:
        found = _TEXT_DATE_RE.search(scope.get_text(" ", strip=True))
        if found:
            return _to_iso_date(found.group(0))
    return ""


def _extract_link_items(
    source: SourceConfig,
    soup: BeautifulSoup,
    meta_summary: str,
    wrong_domain_count: list[int],
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for link in soup.find_all("a", href=True):
        if not isinstance(link, Tag):
            continue

        title = link.get_text(" ", strip=True)
        href = str(link.get("href") or "").strip()
        absolute_url = urljoin(source.url, href)
        if _is_wrong_domain(source, absolute_url):
            wrong_domain_count[0] += 1
            continue
        published_at = _published_at_for_link(link)
        if not _is_valid_candidate(source, title, absolute_url, published_at):
            continue

        if absolute_url in seen_urls:
            continue
        seen_urls.add(absolute_url)

        normalized = normalize_item(
            source,
            title=title,
            url=absolute_url,
            published_at=published_at,
            raw_summary=meta_summary,
            raw_content="",
        )
        if normalized is None:
            logger.info("Skipping HTML link without URL from source %s", source.id)
            continue
        items.append(normalized)

    return items


def _enrich_with_article_content(
    item: dict[str, str],
    article_fetcher: Callable[[str], ArticleContent],
) -> dict[str, str]:
    try:
        article = article_fetcher(item["url"])
    except Exception as exc:  # noqa: BLE001 - one article fetch must not stop source collection.
        logger.info("Article content fetch failed for %s: %s", item["url"], exc)
        if _meaningful_metadata(item.get("raw_summary", "")):
            return {
                **item,
                "excerpt": item["raw_summary"],
                "content_quality": "excerpt",
            }
        return {**item, "content_quality": "title_only"}

    clean_title = article.clean_title or item["clean_title"] or item["title"]
    excerpt = article.excerpt or item["raw_summary"]
    return {
        **item,
        "clean_title": clean_title,
        "excerpt": excerpt,
        "raw_content": article.raw_content,
        "content_quality": article.content_quality,
    }


def _meaningful_metadata(value: str) -> bool:
    text = " ".join(str(value).split())
    if len(text) < 40:
        return False
    lowered = text.lower()
    boilerplate = (
        "your source for the latest",
        "latest news and updates",
        "skip to content",
    )
    return not any(fragment in lowered for fragment in boilerplate)


def _is_valid_candidate(
    source: SourceConfig, title: str, absolute_url: str, published_at: str = ""
) -> bool:
    normalized_title = " ".join(title.split()).strip()
    if len(normalized_title) < MIN_MEANINGFUL_TITLE_LENGTH:
        return False
    if normalized_title.lower() in GENERIC_TITLES and not _looks_like_specific_article_url(source, absolute_url):
        return False
    if _is_rejected_url(source, absolute_url):
        return False
    return _looks_like_specific_article_url(source, absolute_url, dated=bool(published_at))


def _is_rejected_url(source: SourceConfig, absolute_url: str) -> bool:
    parsed = urlparse(absolute_url)
    if parsed.scheme.lower() == "mailto":
        return True
    source_url = source.url.rstrip("/")
    candidate_url = absolute_url.rstrip("/")
    if candidate_url == source_url:
        return True
    # An in-page anchor is navigation wherever it points: the same document
    # plus a fragment is the source page itself, not one of its articles.
    # (Accessibility skip-links look exactly like this.)
    if parsed.fragment:
        without_fragment = parsed._replace(fragment="").geturl().rstrip("/")
        if not parsed.path.strip("/") or without_fragment == source_url:
            return True
    return False


def _is_wrong_domain(source: SourceConfig, absolute_url: str) -> bool:
    parsed = urlparse(absolute_url)
    if parsed.scheme not in {"http", "https"}:
        return False
    source_host = urlparse(source.url).netloc.removeprefix("www.")
    candidate_host = parsed.netloc.removeprefix("www.")
    if not source_host or not candidate_host or source_host == candidate_host:
        return False
    allowed_domains = source.allowed_domains or (source_host,)
    return not any(
        candidate_host == domain.removeprefix("www.")
        or candidate_host.endswith("." + domain.removeprefix("www."))
        for domain in allowed_domains
    )


def _looks_like_specific_article_url(
    source: SourceConfig, absolute_url: str, dated: bool = False
) -> bool:
    parsed = urlparse(absolute_url)
    if parsed.scheme not in {"http", "https"}:
        return False

    source_host = urlparse(source.url).netloc.removeprefix("www.")
    candidate_host = parsed.netloc.removeprefix("www.")
    if source_host and candidate_host and source_host != candidate_host:
        allowed_domains = source.allowed_domains or (source_host,)
        if not any(candidate_host == domain.removeprefix("www.") or candidate_host.endswith("." + domain.removeprefix("www.")) for domain in allowed_domains):
            return False

    path_parts = [part for part in parsed.path.split("/") if part]
    # Two segments is the usual shape of an article path (/news/<slug>), and
    # requiring it is what keeps /pricing and /about out. A lead story can sit
    # at a top-level path, though -- anthropic.com puts its new-model
    # announcement at /claude-opus-5-5 -- so a card carrying a real
    # publication date is taken as an article whatever its depth. Navigation
    # links have no date, which is what makes the distinction hold.
    if len(path_parts) < 2 and not (dated and path_parts):
        return False
    if any(part in {"tag", "tags", "category", "label", "author"} for part in path_parts):
        return False
    return True

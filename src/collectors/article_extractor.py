"""Article page content extraction helpers."""

from __future__ import annotations

import logging
import json
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from bs4.element import Tag

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15
MAX_RAW_CONTENT_LENGTH = 6000
USER_AGENT = "AI-News-Intelligence-Agent/0.1"
MIN_FULL_CONTENT_LENGTH = 240


@dataclass(frozen=True)
class ArticleContent:
    """Clean content extracted from an article page."""

    clean_title: str
    excerpt: str
    raw_content: str
    content_quality: str


def fetch_article_content(url: str) -> ArticleContent:
    """Fetch one article URL and extract clean article content."""
    from src.net_guard import fetch_capped_text, is_safe_public_url

    safe, reason = is_safe_public_url(url)
    if not safe:
        raise ValueError(f"URL non sicuro da recuperare ({reason}): {url}")

    text = fetch_capped_text(url, timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT})
    return extract_article_content(text)


def extract_article_content(html: str) -> ArticleContent:
    """Extract title, excerpt, and body text from article HTML."""
    soup = BeautifulSoup(html, "html.parser")
    json_ld_description = _extract_json_ld_description(soup)
    _remove_noise(soup)

    clean_title = _extract_title(soup)
    excerpt = json_ld_description or _extract_excerpt(soup)
    raw_content = _extract_body_text(soup)

    if len(raw_content) > MAX_RAW_CONTENT_LENGTH:
        raw_content = raw_content[:MAX_RAW_CONTENT_LENGTH].rsplit(" ", 1)[0].strip()

    if len(raw_content) >= MIN_FULL_CONTENT_LENGTH:
        content_quality = "full"
    elif excerpt:
        content_quality = "excerpt"
    else:
        content_quality = "title_only"

    return ArticleContent(
        clean_title=clean_title,
        excerpt=excerpt,
        raw_content=raw_content,
        content_quality=content_quality,
    )


def _remove_noise(soup: BeautifulSoup) -> None:
    noisy_selectors = (
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "form",
        "aside",
        "[role='navigation']",
        "[aria-label*='cookie' i]",
        "[class*='cookie' i]",
        "[id*='cookie' i]",
        "[class*='banner' i]",
        "[id*='banner' i]",
    )
    for node in soup.select(", ".join(noisy_selectors)):
        node.decompose()


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in (
        "meta[property='og:title']",
        "meta[name='twitter:title']",
    ):
        meta = soup.select_one(selector)
        if isinstance(meta, Tag):
            content = meta.get("content")
            if content:
                return _clean_text(str(content))

    title_node = soup.find("h1") or soup.find("title")
    if isinstance(title_node, Tag):
        return _clean_text(title_node.get_text(" ", strip=True))
    return ""


def _extract_excerpt(soup: BeautifulSoup) -> str:
    json_ld_description = _extract_json_ld_description(soup)
    if json_ld_description:
        return json_ld_description

    for selector in (
        "meta[name='description']",
        "meta[property='og:description']",
        "meta[name='twitter:description']",
    ):
        meta = soup.select_one(selector)
        if isinstance(meta, Tag):
            content = meta.get("content")
            if content:
                return _clean_text(str(content))

    lead = soup.select_one("article p, main p")
    if isinstance(lead, Tag):
        return _clean_text(lead.get_text(" ", strip=True))
    return ""


def _extract_json_ld_description(soup: BeautifulSoup) -> str:
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        if not isinstance(script, Tag):
            continue
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except json.JSONDecodeError:
            continue
        description = _find_json_ld_value(payload, "description")
        if description:
            return _clean_text(description)
    return ""


def _find_json_ld_value(payload: object, key: str) -> str:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        for nested in payload.values():
            found = _find_json_ld_value(nested, key)
            if found:
                return found
    if isinstance(payload, list):
        for nested in payload:
            found = _find_json_ld_value(nested, key)
            if found:
                return found
    return ""


def _extract_body_text(soup: BeautifulSoup) -> str:
    container = soup.find("article") or soup.find("main") or soup.body
    if not isinstance(container, Tag):
        return ""

    paragraphs = [
        _clean_text(node.get_text(" ", strip=True))
        for node in container.find_all(["p", "li"])
        if isinstance(node, Tag)
    ]
    meaningful = [
        paragraph
        for paragraph in paragraphs
        if len(paragraph) >= 40 and not _looks_like_boilerplate(paragraph)
    ]
    return "\n\n".join(meaningful).strip()


def _looks_like_boilerplate(text: str) -> bool:
    lowered = text.lower()
    boilerplate_fragments = (
        "accept cookies",
        "privacy policy",
        "terms of use",
        "all rights reserved",
        "subscribe",
        "sign up",
    )
    return any(fragment in lowered for fragment in boilerplate_fragments)


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()

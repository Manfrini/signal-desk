"""Rule-based V1 news processor."""

from __future__ import annotations

import re
from typing import Any

from src.processors.intelligence import build_intelligence_fields, calculate_signal_score


KEYWORD_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("coding_ai", ("code", "coding", "developer", "copilot", "agent", "github", "ide", "cli")),
    (
        "image_generation",
        ("image", "video", "diffusion", "firefly", "midjourney", "stable diffusion"),
    ),
    ("music_generation", ("music", "audio", "song", "voice", "suno", "udio")),
    ("research", ("paper", "benchmark", "eval", "model card", "research")),
    (
        "business_product",
        ("pricing", "enterprise", "product", "launch", "subscription", "partnership"),
    ),
    ("general_ai", ("model", "assistant", "chat", "api", "multimodal", "reasoning")),
)

PROCESSED_ITEM_KEYS = (
    "title",
    "clean_title",
    "url",
    "source_id",
    "source_name",
    "company",
    "category",
    "summary",
    "key_points",
    "published_at",
    "collected_at",
    "tags",
    "content_quality",
    "date_quality",
    "relevance_score",
    "summary_quality",
    "why_it_matters",
    "possible_action",
    "primary_topic",
    "secondary_topic",
    "signal_score",
    "source_group",
    "signal_type",
    "trend_score",
)


class RuleBasedProcessor:
    """Process raw news items using deterministic local rules."""

    def process_item(self, raw_item: dict[str, Any]) -> dict[str, Any]:
        """Return one V1 processed item."""
        title = _normalize_title(_choose_title(raw_item))
        company = str(raw_item.get("company", "")).strip()
        category = self.classify(raw_item)
        relevance_score = self.relevance_score_for(raw_item, title)
        content_quality = str(raw_item.get("content_quality", "title_only")).strip() or "title_only"
        if relevance_score == 1:
            content_quality = "invalid"
        intelligence = build_intelligence_fields(
            raw_item,
            title=title,
            category=category,
            relevance_score=relevance_score,
            content_quality=content_quality,
        )
        summary_quality = str(intelligence["summary_quality"])
        if summary_quality == "low":
            content_quality = "title_only" if content_quality != "invalid" else content_quality
            intelligence["signal_score"] = calculate_signal_score(
                raw_item,
                title=title,
                summary=str(intelligence["summary"]),
                relevance_score=relevance_score,
                content_quality=content_quality,
            )

        return {
            "title": title,
            "clean_title": title,
            "url": str(raw_item.get("url", "")).strip(),
            "source_id": str(raw_item.get("source_id", "")).strip(),
            "source_name": str(raw_item.get("source_name", "")).strip(),
            "company": company,
            "category": category,
            # TODO: Replace this deterministic extractive summary with LLM summarization in a later version.
            "summary": intelligence["summary"],
            "key_points": self.key_points_for(raw_item),
            "published_at": str(raw_item.get("published_at", "")).strip(),
            "collected_at": str(raw_item.get("collected_at", "")).strip(),
            "tags": self.tags_for(raw_item),
            "content_quality": content_quality,
            "date_quality": str(raw_item.get("date_quality", "unknown")).strip() or "unknown",
            "relevance_score": relevance_score,
            "summary_quality": summary_quality,
            "why_it_matters": intelligence["why_it_matters"],
            "possible_action": intelligence["possible_action"],
            "primary_topic": intelligence["primary_topic"],
            "secondary_topic": intelligence["secondary_topic"],
            "signal_score": intelligence["signal_score"],
            "source_group": str(raw_item.get("source_group", "foundation_models")).strip()
            or "foundation_models",
            "signal_type": str(raw_item.get("signal_type", "official_news")).strip()
            or "official_news",
            "trend_score": trend_score_for(raw_item),
        }

    def summarize(self, raw_item: dict[str, Any], title: str) -> tuple[str, str]:
        """Build a short Italian extractive summary and quality label."""
        from src.processors.intelligence import summarize_item

        return summarize_item(raw_item, title)

    def key_points_for(self, raw_item: dict[str, Any]) -> list[str]:
        """Return up to three short extractive key points."""
        sentences = _meaningful_sentences(_key_point_source_text(raw_item))
        return sentences[:3] if len(sentences) >= 2 else []

    def classify(self, raw_item: dict[str, Any]) -> str:
        """Classify a raw item using category hints first, then keywords."""
        category_hint = str(raw_item.get("category_hint", "")).strip()
        if category_hint:
            return category_hint

        matched_categories = self._matched_categories(raw_item)
        if matched_categories:
            return matched_categories[0]
        return "general_ai"

    def tags_for(self, raw_item: dict[str, Any]) -> list[str]:
        """Return deterministic tags from matched keyword categories."""
        tags = self._matched_categories(raw_item)
        category_hint = str(raw_item.get("category_hint", "")).strip()
        if category_hint and category_hint not in tags:
            tags.insert(0, category_hint)
        return tags[:3]

    def relevance_score_for(self, raw_item: dict[str, Any], title: str) -> int:
        """Return basic V1.2 relevance score from 1 to 5."""
        text = _classification_text(raw_item, title)
        if _matches_any(text, ("model launch", "system card", "api", "benchmark", "research", "agent", "coding agent", "copilot", "multimodal", "reasoning", "open model", "developer tool")):
            return 5
        if _matches_any(text, ("enterprise", "partnership", "workflow", "product update", "launch", "pricing", "customer", "adoption", "business", "security")):
            return 4
        if _matches_any(text, ("startup", "founder", "cofounder", "co-founder", "solopreneur", "fundraising", "fundraise", "pitch", "go-to-market", "go to market", "venture capital", "seed round", "bootstrapped", "product market fit", "pmf")):
            return 4
        if _matches_any(text, ("product management", "product manager", "product analytics", "analytics", "metric", "metrics", "kpi", "a/b test", "experiment", "experimentation", "retention", "conversion", "funnel", "data analysis", "data analyst", "data science", "dashboard", "sql", "data pipeline", "data warehouse", "discovery", "roadmap")):
            return 4
        if _matches_any(text, ("ai", "chatgpt", "claude", "gemini", "voice", "image", "music", "audio", "video")):
            return 3
        if _matches_any(text, ("event", "conference", "community", "marketing", "campaign")):
            return 2
        return 1

    def _matched_categories(self, raw_item: dict[str, Any]) -> list[str]:
        text = _classification_text(raw_item, str(raw_item.get("title", "")))

        matches: list[str] = []
        for category, keywords in KEYWORD_CATEGORIES:
            if any(_keyword_matches(text, keyword) for keyword in keywords):
                matches.append(category)
        return matches


# Titles that come from a cookie/consent wall instead of the real content.
_CONSENT_TITLE_FRAGMENTS = (
    "prima di continuare",
    "before you continue",
    "accedi per confermare",
    "consent.youtube",
)


def _choose_title(raw_item: dict[str, Any]) -> str:
    """Prefer clean_title, but fall back to title if it's a consent-page title."""
    clean = str(raw_item.get("clean_title") or "").strip()
    original = str(raw_item.get("title") or "").strip()
    if clean and not _is_consent_title(clean):
        return clean
    return original or clean


def _is_consent_title(title: str) -> bool:
    lowered = title.lower()
    return any(fragment in lowered for fragment in _CONSENT_TITLE_FRAGMENTS)


def _keyword_matches(text: str, keyword: str) -> bool:
    """Return whether a keyword appears as a word or phrase."""
    pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text) is not None


def _matches_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(_keyword_matches(text, keyword) for keyword in keywords)


def _normalize_title(title: str) -> str:
    """Remove date-only or changelog-only title prefixes where possible."""
    cleaned = re.sub(r"\s+", " ", title).strip()
    cleaned = re.sub(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}\s+", "", cleaned)
    cleaned = re.sub(r"^\d{4}[-/]\d{2}[-/]\d{2}\s+", "", cleaned)
    return cleaned or title


def _content_source_text(raw_item: dict[str, Any]) -> str:
    for key in ("excerpt", "raw_content", "raw_summary"):
        value = _strip_html(str(raw_item.get(key, "")).strip())
        if value and not _looks_like_boilerplate(value):
            return value
    return ""


def _key_point_source_text(raw_item: dict[str, Any]) -> str:
    for key in ("raw_content", "excerpt", "raw_summary"):
        value = _strip_html(str(raw_item.get(key, "")).strip())
        if value:
            return value
    return ""


def _classification_text(raw_item: dict[str, Any], title: str) -> str:
    raw_content = str(raw_item.get("raw_content", ""))[:1000]
    return " ".join(
        (
            title,
            str(raw_item.get("clean_title", "")),
            str(raw_item.get("excerpt", "")),
            str(raw_item.get("raw_summary", "")),
            raw_content,
        )
    ).lower()


def _meaningful_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []

    candidates = re.split(r"(?<=[.!?])\s+", normalized)
    sentences: list[str] = []
    for candidate in candidates:
        sentence = candidate.strip(" -•\t\n")
        if len(sentence) < 30:
            continue
        if _looks_like_boilerplate(sentence):
            continue
        sentences.append(_shorten_sentence(sentence))
        if len(sentences) >= 3:
            break
    return sentences


def _shorten_sentence(sentence: str, max_length: int = 220) -> str:
    if len(sentence) <= max_length:
        return sentence
    return sentence[:max_length].rsplit(" ", 1)[0].rstrip(".,;:") + "."


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _looks_like_boilerplate(sentence: str) -> bool:
    lowered = sentence.lower()
    fragments = (
        "your source for the latest features",
        "skip to content",
        "privacy policy",
        "terms of use",
        "all rights reserved",
        "is an ai safety and research company",
        "is an artificial intelligence research",
        "anthropic is an ai",
        "openai is an ai",
        "hugging face is",
    )
    return any(fragment in lowered for fragment in fragments)


def trend_score_for(raw_item: dict[str, Any]) -> int:
    """Return deterministic trend score from raw trend fields."""
    explicit_score = int(raw_item.get("trend_score") or 0)
    if explicit_score:
        return max(0, min(100, explicit_score))
    if str(raw_item.get("signal_type", "")) in {"open_source_trend", "community_signal"}:
        base = 20
        if raw_item.get("github_stars"):
            base += min(35, int(raw_item.get("github_stars") or 0) // 500)
        if raw_item.get("github_star_growth"):
            base += min(35, int(raw_item.get("github_star_growth") or 0) * 5)
        if raw_item.get("hn_points"):
            base += min(40, int(raw_item.get("hn_points") or 0) // 4)
        if raw_item.get("hn_comments"):
            base += min(25, int(raw_item.get("hn_comments") or 0) // 3)
        return max(0, min(100, base))
    return 0

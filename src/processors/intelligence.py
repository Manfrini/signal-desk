"""Deterministic local intelligence enrichment rules."""

from __future__ import annotations

import re
from collections import Counter
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

TOPICS = (
    "AI Coding Agents",
    "Enterprise AI Adoption",
    "Open-source LLMs",
    "AI Infrastructure",
    "Regulation & Policy",
    "Multimodal AI",
    "AI Search",
    "AI Video Generation",
    "AI Music & Voice",
    "Foundation Models",
    "General AI",
)

STRATEGIC_KEYWORDS = (
    "agent",
    "copilot",
    "enterprise",
    "governance",
    "api",
    "benchmark",
    "open model",
    "multimodal",
    "infrastructure",
    "pricing",
    "partnership",
    "workflow",
)

SOURCE_AUTHORITY = {
    "openai_news": 8,
    "anthropic_news": 8,
    "google_deepmind_blog": 8,
    "huggingface_blog": 8,
    "github_copilot_changelog": 8,
    "google_ai_blog": 6,
    "microsoft_ai_blog": 6,
    "github_blog": 6,
    "stability_ai_news": 6,
    "suno_blog": 4,
    "elevenlabs_blog": 4,
}

QUALITY_POINTS = {
    "full": 12,
    "excerpt": 7,
    "title_only": 1,
    "invalid": 0,
}

TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("AI Coding Agents", ("copilot", "coding agent", "code agent", "developer", "ide", "cli", "pull request", "mcp")),
    ("Enterprise AI Adoption", ("enterprise", "customer", "adoption", "workflow", "governance", "admin", "managed", "security", "finance")),
    ("Open-source LLMs", ("open model", "open-source", "open source", "hugging face", "weights", "model card", "granite", "gemma")),
    ("AI Infrastructure", ("infrastructure", "supercomputer", "latency", "compute", "gpu", "tpu", "inference", "webhook", "deployment")),
    ("Regulation & Policy", ("policy", "safety", "governance", "regulation", "responsible", "privacy", "election", "security")),
    ("Multimodal AI", ("multimodal", "voice", "audio", "image", "video", "vision", "speech")),
    ("AI Search", ("search", "answer engine", "perplexity", "retrieval", "ranking")),
    ("AI Video Generation", ("video", "runway", "film", "generation model")),
    ("AI Music & Voice", ("music", "song", "suno", "udio", "voice", "audio", "elevenlabs", "tts")),
    ("Foundation Models", ("model", "gpt", "claude", "gemini", "reasoning", "system card", "api")),
)

BOILERPLATE_FRAGMENTS = (
    "your source for the latest",
    "updates, ideas, and inspiration",
    "latest news and updates",
    "read the latest research, product, and company updates",
    "skip to content",
    "privacy policy",
    "terms of use",
    "all rights reserved",
    "subscribe to",
    "sign up for",
    "anthropic is an ai safety and research company",
    "openai is an ai research and deployment company",
    "is a voice ai research",
    "mission to make content universally accessible",
    "hugging face is",
    "github is where",
)

GENERIC_NOISE_TERMS = (
    "event",
    "conference",
    "webinar",
    "marketing",
    "campaign",
    "recap",
    "roundup",
)


def build_intelligence_fields(
    raw_item: dict[str, Any],
    *,
    title: str,
    category: str,
    relevance_score: int,
    content_quality: str,
) -> dict[str, Any]:
    """Build V1.5 deterministic intelligence fields for one processed item."""
    summary, summary_quality = summarize_item(raw_item, title)
    primary_topic, secondary_topic = cluster_topics(raw_item, title=title, summary=summary, category=category)
    signal_score = calculate_signal_score(
        raw_item,
        title=title,
        summary=summary,
        relevance_score=relevance_score,
        content_quality=content_quality,
    )
    return {
        "summary": summary,
        "summary_quality": summary_quality,
        "why_it_matters": why_it_matters_for(primary_topic, category, relevance_score, summary),
        "possible_action": possible_action_for(primary_topic, category, relevance_score),
        "primary_topic": primary_topic,
        "secondary_topic": secondary_topic,
        "signal_score": signal_score,
    }


def summarize_item(raw_item: dict[str, Any], title: str) -> tuple[str, str]:
    """Return a concise, cleaned summary and quality label."""
    source_text = _best_source_text(raw_item)
    sentences = _meaningful_sentences(source_text)
    if sentences:
        selected = sentences[:2]
        summary = " ".join(selected)
        quality = "high" if _looks_concrete(summary) else "medium"
        return summary, quality
    return title, "low"


def cluster_topics(raw_item: dict[str, Any], *, title: str, summary: str, category: str) -> tuple[str, str]:
    """Assign primary and optional secondary strategic topics."""
    text = _topic_text(raw_item, title, summary, category)
    matches: list[str] = []
    for topic, keywords in TOPIC_RULES:
        if any(_keyword_matches(text, keyword) for keyword in keywords):
            matches.append(topic)

    if not matches:
        fallback = {
            "coding_ai": "AI Coding Agents",
            "image_generation": "AI Video Generation",
            "music_generation": "AI Music & Voice",
            "research": "Foundation Models",
            "business_product": "Enterprise AI Adoption",
        }.get(category, "General AI")
        matches.append(fallback)

    primary = matches[0]
    secondary = next((topic for topic in matches[1:] if topic != primary), "")
    return primary, secondary


def calculate_signal_score(
    raw_item: dict[str, Any],
    *,
    title: str,
    summary: str,
    relevance_score: int,
    content_quality: str,
    today: date | None = None,
) -> int:
    """Return a 0-100 local signal score derived from item fields."""
    if relevance_score <= 1 or content_quality == "invalid":
        return 0
    text = _scoring_text(raw_item, title, summary)
    score = relevance_score * 12
    score += SOURCE_AUTHORITY.get(str(raw_item.get("source_id", "")), 4)
    score += QUALITY_POINTS.get(content_quality, 1)
    score += _novelty_points(str(raw_item.get("published_at", "")), today=today)
    score += min(12, sum(3 for keyword in STRATEGIC_KEYWORDS if _keyword_matches(text, keyword)))
    if any(_keyword_matches(text, term) for term in GENERIC_NOISE_TERMS):
        score -= 20
    # Demote items we couldn't read in full: a notizia judged only on its
    # excerpt should not outrank one read end-to-end.
    if content_quality != "full":
        score -= 12
    return max(0, min(100, score))


def top_signals(items: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    """Return top valid items by signal score."""
    return sorted(_valid_items(items), key=_ranking_key, reverse=True)[:limit]


def daily_intelligence_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Generate a compact strategic daily summary from processed items."""
    valid = _valid_items(items)
    selected = top_signals(valid, limit=5)
    topic_counts = Counter(str(item.get("primary_topic") or "General AI") for item in valid)
    top_topics = [topic for topic, _ in topic_counts.most_common(5)]
    strongest_topic = top_topics[0] if top_topics else "General AI"
    lead = selected[0] if selected else {}
    lead_title = str(lead.get("title") or "No high-signal AI updates")
    headline = f"{strongest_topic} leads today's AI signal mix"
    main_shift = (
        f"The strongest signal is '{lead_title}', pointing to movement in {strongest_topic.lower()}."
        if selected
        else "No new useful signals were collected for this run."
    )
    return {
        "headline": headline,
        "main_shift": main_shift,
        "top_topics": top_topics,
        "key_takeaway_for_pm": _takeaway_for_pm(strongest_topic, lead),
        "key_takeaway_for_data": _takeaway_for_data(strongest_topic, lead),
        "suggested_focus": _suggested_focus(strongest_topic, selected),
    }


def signal_score_buckets(items: list[dict[str, Any]]) -> dict[str, int]:
    """Return signal score distribution buckets."""
    buckets = {"80-100": 0, "60-79": 0, "40-59": 0, "20-39": 0, "0-19": 0}
    for item in items:
        score = int(item.get("signal_score", 0) or 0)
        if score >= 80:
            buckets["80-100"] += 1
        elif score >= 60:
            buckets["60-79"] += 1
        elif score >= 40:
            buckets["40-59"] += 1
        elif score >= 20:
            buckets["20-39"] += 1
        else:
            buckets["0-19"] += 1
    return buckets


def _best_source_text(raw_item: dict[str, Any]) -> str:
    for key in ("excerpt", "raw_content", "raw_summary"):
        text = _strip_html(str(raw_item.get(key, "")))
        text = _clean_text(text)
        if not text or _is_boilerplate(text):
            continue
        return text
    return ""


def _meaningful_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for candidate in re.split(r"(?<=[.!?])\s+", _clean_text(text)):
        sentence = candidate.strip(" -•\t\n")
        if len(sentence) < 35 or _is_boilerplate(sentence):
            continue
        sentences.append(_shorten(sentence))
        if len(sentences) >= 2:
            break
    return sentences


def _looks_concrete(summary: str) -> bool:
    text = summary.lower()
    concrete_terms = (
        "launch",
        "introduc",
        "support",
        "allow",
        "adds",
        "improves",
        "available",
        "api",
        "model",
        "agent",
        "benchmark",
        "enterprise",
        "partnership",
        "governance",
    )
    return len(summary) >= 80 and any(term in text for term in concrete_terms)


def why_it_matters_for(topic: str, category: str, relevance_score: int, summary: str) -> str:
    """Return nothing: only a real analysis can say why an item matters.

    This used to answer with one pre-written sentence per topic. Every
    article sharing a topic then carried the same line — on a real page, 80
    of 165 items, one sentence repeated nineteen times. Filler that reads the
    same everywhere is worse than an empty field: the reader cannot tell a
    thought from a template, and stops trusting the ones that are real.

    The dashboard renders an honest note in its place, saying the analysis
    has not run for this item and that re-running it fills the field in.
    """
    return ""


def possible_action_for(topic: str, category: str, relevance_score: int) -> str:
    """Return nothing, for the same reason as ``why_it_matters_for``."""
    return ""


def _takeaway_for_pm(topic: str, lead: dict[str, Any]) -> str:
    if not lead:
        return "No immediate product planning action is suggested today."
    return f"PM focus: use the strongest {topic.lower()} signal to reassess roadmap assumptions and stakeholder messaging."


def _takeaway_for_data(topic: str, lead: dict[str, Any]) -> str:
    if not lead:
        return "No immediate data instrumentation change is suggested today."
    return f"Data focus: verify whether existing metrics can capture adoption, quality, and workflow impact for {topic.lower()}."


def _suggested_focus(topic: str, selected: list[dict[str, Any]]) -> str:
    if not selected:
        return "Wait for new useful signals before changing priorities."
    return f"Focus on {topic.lower()} and review the top {min(3, len(selected))} source items before the next planning discussion."


def _valid_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("content_quality") != "invalid" and int(item.get("relevance_score", 3) or 3) > 1
    ]


def _ranking_key(item: dict[str, Any]) -> tuple[int, int, str]:
    return (
        int(item.get("signal_score", 0) or 0),
        QUALITY_POINTS.get(str(item.get("content_quality", "")), 0),
        str(item.get("published_at", "")),
    )


def _topic_text(raw_item: dict[str, Any], title: str, summary: str, category: str) -> str:
    raw_content = str(raw_item.get("raw_content", ""))[:1000]
    return " ".join(
        (
            title,
            summary,
            str(raw_item.get("excerpt", "")),
            str(raw_item.get("raw_summary", "")),
            " ".join(str(tag) for tag in raw_item.get("tags", []) if tag),
            category,
            raw_content,
        )
    ).lower()


def _scoring_text(raw_item: dict[str, Any], title: str, summary: str) -> str:
    return " ".join((title, summary, str(raw_item.get("excerpt", "")), str(raw_item.get("raw_summary", "")))).lower()


def _novelty_points(value: str, today: date | None = None) -> int:
    parsed = _parse_date(value)
    if parsed is None:
        return 0
    current = today or datetime.now(timezone.utc).date()
    age = max(0, (current - parsed).days)
    # Wide gap on purpose: without it a strong 3-day-old item always buries
    # today's news, since relevance/authority/quality dominate the score.
    if age <= 1:
        return 18
    if age <= 3:
        return 8
    if age <= 7:
        return 2
    return 0


def _parse_date(value: str) -> date | None:
    normalized = value.strip()
    if not normalized:
        return None
    try:
        parsed_email_date = parsedate_to_datetime(normalized)
        if parsed_email_date.tzinfo is None:
            parsed_email_date = parsed_email_date.replace(tzinfo=timezone.utc)
        return parsed_email_date.date()
    except (TypeError, ValueError, IndexError, OverflowError):
        pass
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _keyword_matches(text: str, keyword: str) -> bool:
    pattern = r"\b" + re.escape(keyword).replace(r"\ ", r"\s+") + r"\b"
    return re.search(pattern, text) is not None


def _is_boilerplate(text: str) -> bool:
    lowered = text.lower()
    return any(fragment in lowered for fragment in BOILERPLATE_FRAGMENTS)


def _strip_html(value: str) -> str:
    return re.sub(r"<[^>]+>", " ", value)


def _clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    cleaned = re.sub(r"(?i)^in breve:\s*", "", cleaned)
    return cleaned


def _shorten(sentence: str, max_length: int = 220) -> str:
    if len(sentence) <= max_length:
        return sentence
    return sentence[:max_length].rsplit(" ", 1)[0].rstrip(".,;:") + "."

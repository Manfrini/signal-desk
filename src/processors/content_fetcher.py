"""On-demand full-content retrieval for deep dives.

Reads the actual source so a deep dive evaluates the real content, not the
catchy excerpt:
- YouTube URLs → transcript (via youtube-transcript-api).
- Other URLs → full article text (reusing the collector's extractor).

Returns ``(text, complete)`` where ``complete`` is False when only partial
content (excerpt/summary) could be obtained — e.g. paywalls or videos with no
captions. ``fetch_full_content_with_reason`` additionally reports *why* the
content is partial, so the UI can tell "no captions on this video" apart from
"the fetch failed" (often YouTube rate-limiting/blocking the request) instead
of showing the same generic message for both.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.storage.json_storage import (
    load_transcript_status,
    load_video_transcripts,
    save_transcript_status,
    save_video_transcript,
)

logger = logging.getLogger(__name__)

MIN_FULL_LENGTH = 240

# Reason codes returned by fetch_full_content_with_reason when complete=False.
REASON_NONE = ""
REASON_NO_CAPTIONS = "no_captions"
REASON_FETCH_ERROR = "fetch_error"
REASON_NO_SOURCE = "no_source"

# YouTube rate-limits transcript requests per IP. Space live requests out, and
# once it blocks us stop asking for a while: every extra request made during a
# block can prolong it. State is per process (a daily run, or the dashboard
# server), so a fresh run always gets one probe.
TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.5
TRANSCRIPT_BLOCK_COOLDOWN_SECONDS = 3600
_last_transcript_request = 0.0
_transcripts_blocked_until = 0.0


def fetch_full_content(item: dict[str, Any]) -> tuple[str, bool]:
    """Return (text, complete) for a deep dive on this item."""
    text, complete, _reason = fetch_full_content_with_reason(item)
    return text, complete


def fetch_full_content_with_reason(item: dict[str, Any]) -> tuple[str, bool, str]:
    """Return (text, complete, reason). ``reason`` is '' when complete=True,
    otherwise one of REASON_NO_CAPTIONS / REASON_FETCH_ERROR / REASON_NO_SOURCE.
    """
    url = str(item.get("url", "")).strip()
    if not url:
        return _fallback_text(item), False, REASON_NO_SOURCE

    if _is_youtube(url):
        video_id = youtube_video_id(url)
        if video_id:
            transcript, reason = _youtube_transcript_cached(video_id)
            if len(transcript) >= MIN_FULL_LENGTH:
                return transcript, True, REASON_NONE
            return _fallback_text(item), False, reason or REASON_NO_CAPTIONS
        return _fallback_text(item), False, REASON_NO_CAPTIONS

    try:
        from src.collectors.article_extractor import fetch_article_content

        content = fetch_article_content(url)
    except Exception as exc:  # noqa: BLE001 - any fetch failure → partial
        logger.warning("Article fetch failed for %s: %s", url, exc)
        return _fallback_text(item), False, REASON_FETCH_ERROR

    if content.content_quality == "full" and len(content.raw_content) >= MIN_FULL_LENGTH:
        return content.raw_content, True, REASON_NONE
    partial = content.raw_content or content.excerpt or _fallback_text(item)
    return partial, False, REASON_FETCH_ERROR


def _is_youtube(url: str) -> bool:
    lowered = url.lower()
    return "youtube.com" in lowered or "youtu.be" in lowered


def youtube_video_id(url: str) -> str:
    """Extract the YouTube video id from common URL forms."""
    for pattern in (
        r"[?&]v=([A-Za-z0-9_-]{6,})",
        r"youtu\.be/([A-Za-z0-9_-]{6,})",
        r"youtube\.com/(?:shorts|embed|live)/([A-Za-z0-9_-]{6,})",
    ):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return ""


def _youtube_transcript(video_id: str) -> tuple[str, str]:
    """Fetch and join a YouTube transcript.

    Returns (text, reason). ``reason`` distinguishes a genuine "no captions on
    this video" (REASON_NO_CAPTIONS, e.g. TranscriptsDisabled/NoTranscriptFound
    from the library) from any other failure (REASON_FETCH_ERROR) — the latter
    is most often YouTube rate-limiting/blocking the request from this
    machine's IP rather than a property of the video itself (RequestBlocked/
    IpBlocked, or any other network/parse error).
    """
    try:
        from youtube_transcript_api import CouldNotRetrieveTranscript, YouTubeTranscriptApi
    except ImportError:
        logger.warning("youtube-transcript-api not installed; skipping transcript for %s.", video_id)
        return "", REASON_FETCH_ERROR
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["it", "en"])
    except CouldNotRetrieveTranscript as exc:
        # Library-known case, but IpBlocked/RequestBlocked are network issues,
        # not a property of the video (e.g. no captions, disabled, unavailable).
        from youtube_transcript_api import RequestBlocked

        if isinstance(exc, RequestBlocked):
            logger.warning(
                "Transcript fetch blocked by YouTube for %s: %s. Pausing transcript requests for %d min.",
                video_id,
                exc,
                TRANSCRIPT_BLOCK_COOLDOWN_SECONDS // 60,
            )
            global _transcripts_blocked_until
            _transcripts_blocked_until = time.monotonic() + TRANSCRIPT_BLOCK_COOLDOWN_SECONDS
            now = datetime.now(timezone.utc)
            save_transcript_status(
                {
                    "blocked_at": now.isoformat(),
                    "resume_at": (now + timedelta(seconds=TRANSCRIPT_BLOCK_COOLDOWN_SECONDS)).isoformat(),
                }
            )
            return "", REASON_FETCH_ERROR
        logger.warning("No transcript available for %s: %s", video_id, exc)
        return "", REASON_NO_CAPTIONS
    except Exception as exc:  # noqa: BLE001 - network/parse errors, etc.
        logger.warning(
            "Transcript fetch failed for %s (likely a network/rate-limit issue, "
            "not a property of the video): %s",
            video_id,
            exc,
        )
        return "", REASON_FETCH_ERROR
    text = " ".join(str(snippet.text).strip() for snippet in fetched if str(snippet.text).strip())
    return re.sub(r"\s+", " ", text).strip(), REASON_NONE


# Outcomes that will never change on a re-fetch: a definitive answer either
# way, worth persisting so we don't hit YouTube again for the same video.
_DEFINITIVE_REASONS = (REASON_NONE, REASON_NO_CAPTIONS)


def _youtube_transcript_cached(video_id: str) -> tuple[str, str]:
    """Cached wrapper around ``_youtube_transcript``.

    Shared by the daily collection enrichment and the on-demand deep dive, so
    a video's transcript is fetched from YouTube at most once (a definitive
    "found" or "no captions" outcome is cached forever); a transient
    REASON_FETCH_ERROR is never cached, so it can be retried on a later run.
    Live requests are spaced out and stop for a cooldown after YouTube blocks us.
    """
    cached = load_video_transcripts().get(video_id)
    if isinstance(cached, dict) and cached.get("reason", "") in _DEFINITIVE_REASONS:
        return str(cached.get("text", "")), str(cached.get("reason", ""))

    if time.monotonic() < _transcripts_blocked_until:
        return "", REASON_FETCH_ERROR
    global _last_transcript_request
    wait = _last_transcript_request + TRANSCRIPT_MIN_INTERVAL_SECONDS - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_transcript_request = time.monotonic()

    text, reason = _youtube_transcript(video_id)
    if reason in _DEFINITIVE_REASONS:
        save_video_transcript(video_id, {"text": text, "reason": reason})
        if load_transcript_status():
            save_transcript_status({})  # YouTube answered again: the block is over.
    return text, reason


def _fallback_text(item: dict[str, Any]) -> str:
    """Best available text from the processed item (used when source is closed)."""
    parts = [
        str(item.get("title") or item.get("clean_title") or "").strip(),
        str(item.get("summary", "")).strip(),
        " ".join(str(point) for point in item.get("key_points", []) if point),
    ]
    return " ".join(part for part in parts if part).strip()

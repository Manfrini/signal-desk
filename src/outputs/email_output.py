"""Executive daily email output generation."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date as date_type
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.processors.intelligence import daily_intelligence_summary, top_signals
from src.storage.json_storage import (
    daily_intelligence_summary_path,
    load_json,
    preview_daily_intelligence_summary_path,
    today_utc,
)

logger = logging.getLogger(__name__)


def _safe_href(url: str) -> str:
    """Escape a URL for use in an <a href>, or return '#' if unsafe.

    Collected item URLs come from external feeds; without a scheme check, a
    hostile feed could publish a `javascript:` URL that runs when the
    rendered link is clicked (an email client that renders HTML links).
    """
    try:
        scheme = urlsplit(str(url).strip()).scheme.lower()
    except ValueError:
        return "#"
    if scheme not in ("http", "https"):
        return "#"
    return escape(str(url), quote=True)


DEFAULT_DATA_DIR = Path("data")
DEFAULT_EMAIL_DIR = Path("outputs/email")
DEFAULT_TOP_SIGNAL_LIMIT = 5

# Email-safe inline palette (Marco Manfrini "Data-crisp" design system, light
# mode only — email clients cannot rely on prefers-color-scheme reliably, and
# HTML email cannot load design/mm/tokens.css). No CSS files, no web fonts, no
# CDN: every color below is inlined per element, per email-client convention.
EMAIL_BG = "#FAF9F7"
EMAIL_SURFACE = "#FFFFFF"
EMAIL_TEXT = "#1A1B1E"
EMAIL_TEXT_SECONDARY = "#5E6169"
EMAIL_BRAND = "#0F4C81"
EMAIL_BRAND_SOFT = "#E7EFF7"
EMAIL_ACCENT_TEXT = "#B54708"
EMAIL_BORDER = "#E4E1DB"
EMAIL_FONT = "Arial, Helvetica, sans-serif"


@dataclass(frozen=True)
class EmailOutputPaths:
    """Generated email output paths."""

    html_path: Path
    text_path: Path


class EmailOutput:
    """Generate local executive intelligence brief files from processed news."""

    def __init__(
        self,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        output_dir: str | Path = DEFAULT_EMAIL_DIR,
        date: date_type | None = None,
        max_items_per_category: int = DEFAULT_TOP_SIGNAL_LIMIT,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.date = date or today_utc()
        self.max_items_per_category = max_items_per_category

    def generate_from_file(self, preview: bool = False) -> EmailOutputPaths:
        """Load processed news from disk and generate email outputs."""
        items = load_json(self._news_path(), default=[])
        if not isinstance(items, list):
            items = []
        return self.generate(items, preview=preview, daily_summary=self._load_daily_summary(preview))

    def generate(
        self,
        items: list[dict[str, Any]],
        preview: bool = False,
        daily_summary: dict[str, Any] | None = None,
    ) -> EmailOutputPaths:
        """Generate HTML and plain-text newsletter files."""
        paths = self.paths(preview=preview)
        summary = daily_summary or daily_intelligence_summary(_valid_items(items))
        self._save_text(paths.html_path, self.render_html(items, daily_summary=summary))
        self._save_text(paths.text_path, self.render_text(items, daily_summary=summary))
        return paths

    def render_html(
        self,
        items: list[dict[str, Any]],
        daily_summary: dict[str, Any] | None = None,
    ) -> str:
        """Render the executive brief as email-safe HTML (table layout).

        No external CSS/JS/SVG/web fonts: everything is inline, in nested
        `role="presentation"` tables, so it renders consistently across email
        clients (many of which strip <style> blocks and ignore <header>/
        <main>/<article> and border-radius/auto-margin centering).
        """
        items = _sort_items(_valid_items(items))
        summary = daily_summary or daily_intelligence_summary(items)
        title = self._title()
        top_items = top_signals(items, limit=self.max_items_per_category)
        theme_html = self._render_theme_sections(items)
        worth_watching = [item for item in items if 40 <= _signal_score(item) < 70][:6]
        noise = [item for item in items if 20 <= _signal_score(item) < 40][:5]

        header_html = f"""<tr><td style="padding:24px; font-family:{EMAIL_FONT};">
        <p style="margin:0 0 8px; color:{EMAIL_BRAND}; font-size:12px; font-weight:bold; letter-spacing:1px; text-transform:uppercase;">Executive AI Brief</p>
        <h1 style="margin:0; font-size:26px; line-height:1.25; color:{EMAIL_TEXT}; font-family:{EMAIL_FONT};">{escape(title)}</h1>
        <h2 style="margin:18px 0 6px; font-size:18px; color:{EMAIL_TEXT}; font-family:{EMAIL_FONT};">{escape(str(summary.get("headline", "")))}</h2>
        <p style="margin:0; color:{EMAIL_TEXT_SECONDARY}; font-size:14px;">{escape(str(summary.get("main_shift", "")))}</p>
      </td></tr>
      <tr><td style="padding:0 24px 24px;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_BRAND_SOFT}" style="background-color:{EMAIL_BRAND_SOFT};">
          <tr><td style="padding:14px; font-family:{EMAIL_FONT};">
            <p style="margin:0 0 8px; font-size:12px; font-weight:bold; letter-spacing:1px; text-transform:uppercase; color:{EMAIL_BRAND};">Suggested focus</p>
            <p style="margin:0; color:{EMAIL_TEXT}; font-size:14px;">{escape(str(summary.get("suggested_focus", "")))}</p>
          </td></tr>
        </table>
      </td></tr>"""

        return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)}</title>
</head>
<body style="margin:0; padding:0; background-color:{EMAIL_BG};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_BG}" style="background-color:{EMAIL_BG};">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_SURFACE}" style="width:600px; max-width:600px; background-color:{EMAIL_SURFACE}; border:1px solid {EMAIL_BORDER};">
          {header_html}
        </table>
        {_render_section("Top Signals Today", [self._render_signal_html(item) for item in top_items])}
        {theme_html}
        {_render_section("Worth Watching", [self._render_compact_html(item) for item in worth_watching])}
        {_render_section("Noise / Low Priority", [self._render_compact_html(item) for item in noise]) if noise else ""}
      </td>
    </tr>
  </table>
</body>
</html>
"""

    def render_text(
        self,
        items: list[dict[str, Any]],
        daily_summary: dict[str, Any] | None = None,
    ) -> str:
        """Render the executive brief as plain text."""
        items = _sort_items(_valid_items(items))
        summary = daily_summary or daily_intelligence_summary(items)
        top_items = top_signals(items, limit=self.max_items_per_category)
        worth_watching = [item for item in items if 40 <= _signal_score(item) < 70][:6]
        noise = [item for item in items if 20 <= _signal_score(item) < 40][:5]
        chunks = [
            self._title(),
            "",
            str(summary.get("headline", "")).strip(),
            str(summary.get("main_shift", "")).strip(),
            "",
            f"Suggested focus: {str(summary.get('suggested_focus', '')).strip()}",
            "",
            "TOP SIGNALS TODAY",
        ]
        chunks.extend(self._render_signal_text(item) for item in top_items)

        grouped = _group_by_topic(items)
        if grouped:
            chunks.extend(["", "STRATEGIC THEMES"])
            for topic, topic_items in grouped.items():
                chunks.append(f"\n{topic}")
                for item in topic_items[:3]:
                    chunks.append(f"- {item.get('title')} ({item.get('source_name') or item.get('company')}) [{_signal_score(item)}] {item.get('url')}")

        if worth_watching:
            chunks.extend(["", "WORTH WATCHING"])
            chunks.extend(self._render_compact_text(item) for item in worth_watching)
        if noise:
            chunks.extend(["", "NOISE / LOW PRIORITY"])
            chunks.extend(self._render_compact_text(item) for item in noise)
        return "\n".join(chunk for chunk in chunks if chunk is not None).rstrip() + "\n"

    def paths(self, preview: bool = False) -> EmailOutputPaths:
        """Return output paths for the configured date."""
        prefix = "preview_" if preview else ""
        date_text = self.date.isoformat()
        return EmailOutputPaths(
            html_path=self.output_dir / f"{prefix}{date_text}.html",
            text_path=self.output_dir / f"{prefix}{date_text}.txt",
        )

    def _news_path(self) -> Path:
        return self.data_dir / self.date.isoformat() / "news.json"

    def _load_daily_summary(self, preview: bool) -> dict[str, Any] | None:
        path = (
            preview_daily_intelligence_summary_path(self.data_dir, self.date)
            if preview
            else daily_intelligence_summary_path(self.data_dir, self.date)
        )
        if path.exists():
            return load_json(path)
        return None

    def _title(self) -> str:
        return f"AI Daily Intelligence Brief — {self.date.isoformat()}"

    def _render_theme_sections(self, items: list[dict[str, Any]]) -> str:
        grouped = _group_by_topic(items)
        theme_blocks = []
        for topic, topic_items in grouped.items():
            rendered_items = "".join(self._render_theme_item_html(item) for item in topic_items[:3])
            theme_blocks.append(
                f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_SURFACE}" style="background-color:{EMAIL_SURFACE}; border:1px solid {EMAIL_BORDER}; margin:0 0 12px;">
          <tr><td style="padding:16px; font-family:{EMAIL_FONT};">
            <p style="margin:0 0 10px; font-size:16px; color:{EMAIL_TEXT};">{escape(topic)}</p>
            {rendered_items}
          </td></tr>
        </table>"""
            )
        return _render_section("Strategic Themes", theme_blocks)

    @staticmethod
    def _render_signal_html(item: dict[str, Any]) -> str:
        title = escape(str(item.get("title", "")))
        url = _safe_href(item.get("url", ""))
        source = escape(str(item.get("source_name") or item.get("company") or "Source"))
        why = escape(str(item.get("why_it_matters", "")))
        action = escape(str(item.get("possible_action", "")))
        score = escape(str(_signal_score(item)))
        topic = escape(str(item.get("primary_topic") or "General AI"))
        return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_SURFACE}" style="background-color:{EMAIL_SURFACE}; border:1px solid {EMAIL_BORDER}; margin:0 0 14px;">
          <tr><td style="padding:18px; font-family:{EMAIL_FONT};">
            <p style="margin:0 0 8px; color:{EMAIL_ACCENT_TEXT}; font-size:12px; font-weight:bold;">{topic} &middot; Signal {score}</p>
            <p style="margin:0 0 8px; font-size:18px; line-height:1.3;"><a href="{url}" style="color:{EMAIL_TEXT}; text-decoration:none;">{title}</a></p>
            <p style="margin:0 0 10px; color:{EMAIL_TEXT_SECONDARY}; font-size:13px;">{source}</p>
            <p style="margin:0 0 8px; color:{EMAIL_TEXT}; font-size:14px;"><strong>Why it matters:</strong> {why}</p>
            <p style="margin:0 0 12px; color:{EMAIL_TEXT}; font-size:14px;"><strong>Possible action:</strong> {action}</p>
            <p style="margin:0;"><a href="{url}" style="color:{EMAIL_BRAND}; font-size:13px;">Open source link</a></p>
          </td></tr>
        </table>"""

    @staticmethod
    def _render_compact_html(item: dict[str, Any]) -> str:
        title = escape(str(item.get("title", "")))
        url = _safe_href(item.get("url", ""))
        source = escape(str(item.get("source_name") or item.get("company") or "Source"))
        summary = escape(str(item.get("summary", "")))
        return f"""<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{EMAIL_SURFACE}" style="background-color:{EMAIL_SURFACE}; border:1px solid {EMAIL_BORDER}; margin:0 0 10px;">
          <tr><td style="padding:14px; font-family:{EMAIL_FONT};">
            <p style="margin:0 0 6px; font-size:15px;"><a href="{url}" style="color:{EMAIL_TEXT}; text-decoration:none;">{title}</a></p>
            <p style="margin:0 0 6px; color:{EMAIL_TEXT_SECONDARY}; font-size:13px;">{source} &middot; Signal {_signal_score(item)}</p>
            <p style="margin:0; color:{EMAIL_TEXT}; font-size:14px;">{summary}</p>
          </td></tr>
        </table>"""

    @staticmethod
    def _render_theme_item_html(item: dict[str, Any]) -> str:
        title = escape(str(item.get("title", "")))
        url = _safe_href(item.get("url", ""))
        source = escape(str(item.get("source_name") or item.get("company") or "Source"))
        return (
            f'<p style="margin:0 0 8px; font-size:14px; font-family:{EMAIL_FONT};">'
            f'<a href="{url}" style="color:{EMAIL_BRAND}; text-decoration:none;">{title}</a> '
            f'<span style="color:{EMAIL_TEXT_SECONDARY};">&middot; {source} &middot; Signal {_signal_score(item)}</span></p>'
        )

    @staticmethod
    def _render_signal_text(item: dict[str, Any]) -> str:
        return (
            f"\n- {item.get('title')}\n"
            f"  Source: {item.get('source_name') or item.get('company')} | Signal: {_signal_score(item)} | Topic: {item.get('primary_topic') or 'General AI'}\n"
            f"  Why it matters: {item.get('why_it_matters')}\n"
            f"  Possible action: {item.get('possible_action')}\n"
            f"  Link: {item.get('url')}"
        )

    @staticmethod
    def _render_compact_text(item: dict[str, Any]) -> str:
        return f"- {item.get('title')} | {item.get('source_name') or item.get('company')} | Signal {_signal_score(item)} | {item.get('url')}"

    @staticmethod
    def _save_text(path: Path, content: str) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            logger.warning("Overwriting existing email file: %s", path)
        path.write_text(content, encoding="utf-8")
        logger.info("Saved email file: %s", path)
        return path


def _render_section(title: str, rendered_items: list[str]) -> str:
    if not rendered_items:
        return ""
    return f"""<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" style="width:600px; max-width:600px; margin-top:24px;">
          <tr><td style="padding:0 24px; font-family:{EMAIL_FONT};">
            <p style="margin:0 0 12px; font-size:20px; color:{EMAIL_TEXT};">{escape(title)}</p>
            {''.join(rendered_items)}
          </td></tr>
        </table>"""


def _valid_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("content_quality") != "invalid"
        and int(item.get("relevance_score", 3) or 3) > 1
        and _signal_score(item) > 0
    ]


def _sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: (_signal_score(item), str(item.get("published_at", ""))), reverse=True)


def _group_by_topic(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if _signal_score(item) < 50:
            continue
        grouped[str(item.get("primary_topic") or "General AI")].append(item)
    return dict(sorted(grouped.items(), key=lambda pair: max(_signal_score(item) for item in pair[1]), reverse=True))


def _signal_score(item: dict[str, Any]) -> int:
    return int(item.get("signal_score") or (int(item.get("relevance_score", 1) or 1) * 12))

"""Static local dashboard output generation.

The dashboard is built on a small design system (defined as CSS tokens in
``_render_head``) instead of a utility framework, so colours, typography and
spacing live in one place and adapt to a light/dark theme.

UX model:
- **Brief** (default): three curated sections — what's big in the AI world
  today, what's relevant for you, OSS/emerging to follow.
- **Esplora**: the full filterable stream with minimal controls and topic chips.

Each item is a soft card in a single column: a source anchor (initial), a
topic eyebrow, an explicit Save button, a discreet importance indicator, and
the "why it matters / action" insight collapsed behind a click.
"""

from __future__ import annotations

import json
import logging
from datetime import date as date_type
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from src.processors.intelligence import _novelty_points, _parse_date, daily_intelligence_summary
from src.processors.sections import (
    is_mainstream as _is_mainstream,
    is_opinion as _is_opinion,
    is_tool as _is_tool,
    personal_score as _personal_score,
)
from src.storage.json_storage import (
    daily_intelligence_summary_path,
    load_sources_config,
    latest_news_by_source,
    load_all_collection_reports,
    load_json,
    load_llm_usage_log,
    preview_daily_intelligence_summary_path,
    preview_section_briefs_path,
    section_briefs_path,
    today_utc,
)

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path("data")
DEFAULT_DASHBOARD_DIR = Path("outputs/dashboard")

_ITALIAN_MONTHS = (
    "gen", "feb", "mar", "apr", "mag", "giu",
    "lug", "ago", "set", "ott", "nov", "dic",
)

# Minimal inline SVG icon set (stroke, currentColor) so the dashboard needs no
# icon CDN and works offline.
_ICONS: dict[str, str] = {
    "bookmark": '<path d="M6 3h12v18l-6-4-6 4z"/>',
    "bolt": '<path d="M13 3 4 14h7l-1 7 9-11h-7z"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "moon": '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>',
    "check": '<path d="M5 12l4 4 11-11"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "alert": '<circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/>',
    "up": '<path d="M7 22V11l5-9a2.5 2.5 0 0 1 2.5 3L13 10h5.5a2 2 0 0 1 2 2.4l-1.6 7A2 2 0 0 1 17 21H7z"/><path d="M3 11h4v11H3z"/>',
    "down": '<path d="M17 2v11l-5 9a2.5 2.5 0 0 1-2.5-3L11 14H5.5a2 2 0 0 1-2-2.4l1.6-7A2 2 0 0 1 7 3h10z"/><path d="M21 13h-4V2h4z"/>',
}


def _icon(name: str, size: int = 16) -> str:
    return (
        f'<svg viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" '
        f'stroke="currentColor" stroke-width="1.7" stroke-linecap="round" '
        f'stroke-linejoin="round" aria-hidden="true">{_ICONS[name]}</svg>'
    )


def _format_run_label(value: str) -> str:
    """Format a collection-report date for the compact dashboard stat."""
    try:
        parsed = date_type.fromisoformat(value)
    except (TypeError, ValueError):
        return "Nessun run registrato"
    return f"{parsed.day} {_ITALIAN_MONTHS[parsed.month - 1]} {parsed.year}"


class DashboardOutput:
    """Generate a self-contained local HTML intelligence dashboard."""

    def __init__(
        self,
        data_dir: str | Path = DEFAULT_DATA_DIR,
        output_dir: str | Path = DEFAULT_DASHBOARD_DIR,
        date: date_type | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.output_dir = Path(output_dir)
        self.date = date or today_utc()

    def generate_from_file(self, preview: bool = False) -> Path:
        """Load processed news from disk and generate the dashboard."""
        items = load_json(self._news_path(), default=[])
        if not isinstance(items, list):
            items = []
        return self.generate(
            items,
            preview=preview,
            daily_summary=self._load_daily_summary(preview),
            section_briefs=self._load_section_briefs(preview),
        )

    def generate(
        self,
        items: list[dict[str, Any]],
        preview: bool = False,
        daily_summary: dict[str, Any] | None = None,
        section_briefs: dict[str, str] | None = None,
    ) -> Path:
        """Generate the dashboard HTML file."""
        path = self.path(preview=preview)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            logger.warning("Overwriting existing dashboard file: %s", path)
        path.write_text(
            self.render_html(items, daily_summary=daily_summary, section_briefs=section_briefs),
            encoding="utf-8",
        )
        logger.info("Saved dashboard file: %s", path)
        return path

    def path(self, preview: bool = False) -> Path:
        """Return the dashboard output path."""
        filename = "preview_index.html" if preview else "index.html"
        return self.output_dir / filename

    def render_html(
        self,
        items: list[dict[str, Any]],
        daily_summary: dict[str, Any] | None = None,
        section_briefs: dict[str, str] | None = None,
    ) -> str:
        """Render the dashboard as standalone HTML."""
        valid_items = _valid_items(items)
        valid_items = [_with_current_score(item, self.date) for item in valid_items]
        brief_items = _sort_dashboard_items(valid_items, today=self.date)
        items = _sort_recent_items(valid_items)
        summary = (
            daily_summary
            or self._load_daily_summary(preview=False)
            or daily_intelligence_summary(brief_items)
        )
        if section_briefs is None:
            section_briefs = self._load_section_briefs(preview=False)

        big_items = [item for item in brief_items if _is_mainstream(item)][:6]
        per_te_items = [
            item for item in sorted(brief_items, key=_personal_score, reverse=True)
            if _personal_score(item) > 0
        ][:6]
        opinion_items = [item for item in brief_items if _is_opinion(item)][:6]
        tools_items = sorted(
            [item for item in brief_items if _is_tool(item)],
            key=lambda item: int(item.get("trend_score", 0) or 0),
            reverse=True,
        )[:6]

        big_cards = _render_cards(big_items, "Nessuna novità dalle big oggi.")
        per_te_cards = _render_cards(per_te_items, "Nessun segnale rilevante per product & data oggi.")
        opinion_cards = _render_cards(opinion_items, "Nessuna opinione o video oggi. Aggiungi fonti o canali YouTube nel config.")
        tools_cards = _render_cards(tools_items, "Nessuno strumento o API emergente oggi.")
        stream_cards = _render_cards(items, "Nessun segnale disponibile.")
        brief_references = {
            "big": _render_brief_references(big_items),
            "perte": _render_brief_references(per_te_items),
            "opinion": _render_brief_references(opinion_items),
            "tools": _render_brief_references(tools_items),
        }

        categories = sorted({str(item.get("category", "")).strip() for item in items if item.get("category")})
        topic_chips = "\n".join(
            f'<button type="button" data-chip="{escape(category, quote=True)}" class="chip">'
            f'{escape(_category_label(category))}</button>'
            for category in categories
        )

        usage_log = load_llm_usage_log(self.data_dir / "llm_usage_log.json")
        reports = load_all_collection_reports(self.data_dir)
        catalog = _source_catalog()
        latest_run_date = max(reports) if reports else ""
        latest_per_source = (
            reports[latest_run_date].get("per_source", []) if latest_run_date else []
        )
        active_source_ids = {
            str(source.get("id")) for source in catalog if source.get("enabled")
        }
        # A source disabled after a past run must not keep the dashboard in an
        # error state. Its historical failure stays inspectable in Fonti.
        sources_error_count = sum(
            1
            for row in latest_per_source
            if row.get("status") != "ok" and str(row.get("source_id")) in active_source_ids
        )
        sources_total = sum(1 for src in catalog if src.get("enabled"))
        # Chips from the catalogue's own categories, so they cover every
        # source available, not only those that produced items today.
        source_categories = sorted(
            {str(src.get("category_hint", "")).strip() for src in catalog if src.get("category_hint")}
        )
        source_category_chips = "\n".join(
            f'<button type="button" data-src-chip="{escape(category, quote=True)}" class="chip">'
            f'{escape(_category_label(category))}</button>'
            for category in source_categories
        )

        head = _render_head()
        body = _render_body(
            summary=summary,
            total=len(items),
            last_run_label=_format_run_label(latest_run_date),
            sources_total=sources_total,
            sources_error_count=sources_error_count,
            big_cards=big_cards,
            per_te_cards=per_te_cards,
            opinion_cards=opinion_cards,
            tools_cards=tools_cards,
            stream_cards=stream_cards,
            topic_chips=topic_chips,
            source_category_chips=source_category_chips,
            section_briefs=section_briefs or {},
            brief_references=brief_references,
        )
        sources_data = {
            day: {
                "per_source": rep.get("per_source", []),
                "failed": rep.get("failed_sources", []),
            }
            for day, rep in reports.items()
        }
        latest_by_source = latest_news_by_source(self.data_dir)
        # The Fonti page is driven by the catalogue, not by run reports: a
        # fresh install has no reports yet, and used to show an empty table
        # even with 100+ sources configured. Injecting it here also keeps the
        # page usable as a static file:// export, with no server to query.
        data = (
            f'<script id="news-data" type="application/json">{_safe_json(items)}</script>\n'
            f'<script id="usage-data" type="application/json">{_safe_json(usage_log)}</script>\n'
            f'<script id="sources-catalog" type="application/json">{_safe_json(catalog)}</script>\n'
            f'<script id="sources-data" type="application/json">{_safe_json_object(sources_data)}</script>\n'
            f'<script id="source-latest-data" type="application/json">{_safe_json_object(latest_by_source)}</script>\n'
            f'<script id="daily-summary-data" type="application/json">{_safe_json_object(summary)}</script>\n'
        )
        return head + body + data + _DASHBOARD_SCRIPT + "\n</body>\n</html>\n"

    def _news_path(self) -> Path:
        return self.data_dir / self.date.isoformat() / "news.json"

    def _brief_dates(self) -> list[date_type]:
        """This dashboard's date, then earlier run dates, newest first.

        Collection runs once a day, so on any day without a run the brief of
        the last one is still the current brief. Looking only at today made
        the page fall back to the deterministic summary and to generic
        section labels from the day after every run.
        """
        dates = [self.date]
        base = Path(self.data_dir)
        if not base.exists():
            return dates
        for folder in sorted(base.iterdir(), reverse=True):
            if not folder.is_dir():
                continue
            try:
                parsed = date_type.fromisoformat(folder.name)
            except ValueError:
                continue
            if parsed < self.date:
                dates.append(parsed)
        return dates

    def _load_daily_summary(self, preview: bool) -> dict[str, Any] | None:
        for day in self._brief_dates():
            path = (
                preview_daily_intelligence_summary_path(base_dir=self.data_dir, date=day)
                if preview
                else daily_intelligence_summary_path(base_dir=self.data_dir, date=day)
            )
            if path.exists():
                payload = load_json(path)
                if isinstance(payload, dict):
                    return payload
        return None

    def _load_section_briefs(self, preview: bool) -> dict[str, str]:
        for day in self._brief_dates():
            path = (
                preview_section_briefs_path(base_dir=self.data_dir, date=day)
                if preview
                else section_briefs_path(base_dir=self.data_dir, date=day)
            )
            if path.exists():
                payload = load_json(path)
                if isinstance(payload, dict):
                    return {str(k): str(v) for k, v in payload.items()}
        return {}


def _render_head() -> str:
    """Document head: design tokens (light/dark), component styles, theme init."""
    return """<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI News Intelligence Dashboard</title>
  <link rel="stylesheet" href="../../design/mm/tokens.css">
  <link rel="stylesheet" href="../../design/mm/ui-kit.css">
  <script>
    (function () {
      try {
        var saved = localStorage.getItem('ai-news-theme');
        var dark = saved ? saved === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
        if (dark) document.documentElement.setAttribute('data-theme', 'dark');
      } catch (e) {}
    })();
    window.dashboardPendingView = 'brief';
    window.dashboardSetView = function (view) {
      window.dashboardPendingView = view || 'brief';
      if (typeof window.dashboardRenderView === 'function') window.dashboardRenderView(window.dashboardPendingView);
    };
  </script>
  <style>
    /* I token (colore, tipografia, spaziatura, raggi) vengono da
       design/mm/tokens.css; i componenti mm- da design/mm/ui-kit.css, linkati
       sopra. Il tema segue [data-theme="light"] (default) / [data-theme="dark"]
       sul documento. Qui sotto: solo i componenti specifici di questo prodotto
       (segnali, insight, deep-dive, newsletter, fonti), scritti sui token —
       niente valori letterali di colore o spaziatura. Gli alias mappano i nomi
       storici dell'app sui token MM; --accent/--accent-soft restano l'arancio
       "accento dati" per i segnali calcolati (Per te), ogni altro uso storico
       di --accent come azione/link è passato a --primary. */
    :root {
      --text: var(--ink);
      --text-2: var(--ink-dim);
      --text-3: var(--ink-dim);   /* era --ink-faint: sotto AA per testo informativo */
      --border-2: var(--border-strong);
      --anchor: var(--surface-2);
    }

    /* --- Rail: intestazione di prodotto sopra la navigazione del kit --- */
    .rail-tag { margin:0 0 var(--space-3); font-size:var(--text-xs); color:var(--ink-dim); line-height:1.5; }
    .rail-nav { display:flex; flex-direction:column; gap:2px; }
    /* Sotto i 900px il rail del kit si stringe a sole icone: nascondi i testi di prodotto */
    @media (max-width: 900px) { .rail-tag { display:none; } }

    /* --- Header di pagina: occhiello sopra l'h1 del kit --- */
    .mm-page-head .eyebrow { margin:0 0 var(--space-1); font-size:var(--text-xs); font-weight:var(--weight-bold); letter-spacing:.06em; text-transform:uppercase; color:var(--ink-dim); }

    /* --- Banner stato LLM: superficie dal kit (mm-card), qui solo il layout --- */
    .llm-banner { display:flex; flex-wrap:wrap; gap:var(--space-3); align-items:center; justify-content:space-between; border-left:3px solid var(--warn); font-size:var(--text-sm); line-height:1.5; }
    .llm-banner.ok { border-left-color:var(--ok); }
    .llm-banner .llm-actions { display:flex; gap:var(--space-2); flex-shrink:0; }
    .llm-banner code { font-size:var(--text-xs); background:var(--surface-2); padding:1px 4px; border-radius:var(--radius-sm); }

    /* --- Intestazioni di sezione / testi di raccordo --- */
    .sec-head { display:flex; align-items:baseline; justify-content:space-between; gap:var(--space-3); margin:var(--space-6) 0 var(--space-3); }
    .sec-head h3 { font-size:var(--text-lg); font-weight:var(--weight-semibold); margin:0; }
    .sec-head .count { font-size:var(--text-sm); color:var(--ink-dim); }
    .group-desc { font-size:var(--text-sm); color:var(--ink-dim); margin:0 0 var(--space-4); }
    .section-brief { font-size:var(--text-sm); line-height:1.6; color:var(--text-2); background:var(--primary-soft); border:1px solid var(--primary); border-radius:var(--radius-md); padding:var(--space-3) var(--space-4); margin:0 0 var(--space-4); }
    .brief-references { display:flex; align-items:baseline; flex-wrap:wrap; gap:var(--space-2); margin:-2px 0 var(--space-4); font-size:var(--text-xs); }
    .brief-references .lab { color:var(--ink-dim); font-weight:var(--weight-semibold); }
    .brief-references a { color:var(--primary); text-decoration:none; border-bottom:1px solid transparent; }
    .brief-references a:hover, .brief-references a:focus-visible { border-bottom-color:currentColor; }
    .tab-info { position:relative; display:inline-flex; align-items:center; justify-content:center; width:15px; height:15px; margin-left:4px; border:1px solid currentColor; border-radius:50%; font-size:10px; font-weight:var(--weight-bold); line-height:1; vertical-align:1px; opacity:.72; }
    .tab-info::after { content:attr(data-tooltip); position:absolute; z-index:4; top:calc(100% + 9px); left:50%; width:230px; transform:translateX(-50%); padding:var(--space-2) var(--space-3); border-radius:var(--radius-md); background:var(--ink); color:var(--surface); box-shadow:var(--shadow-2); font-size:var(--text-xs); font-weight:var(--weight-regular); line-height:1.45; text-align:left; opacity:0; pointer-events:none; transition:opacity .15s ease; }
    .tab-info:hover::after, .mm-tab:focus-visible .tab-info::after { opacity:1; }

    /* --- Card segnale (superficie da mm-card) --- */
    .signal { display:flex; gap:var(--space-3); padding:var(--space-4); }
    .signal.me { border:1.5px solid var(--accent); }
    .anchor { width:38px; height:38px; flex-shrink:0; border-radius:var(--radius-md); background:var(--anchor); display:flex; align-items:center; justify-content:center; font-size:var(--text-md); font-weight:var(--weight-semibold); color:var(--ink-dim); }
    .signal.me .anchor { background:var(--accent-soft); color:var(--accent-text); }
    .signal .body { flex:1; min-width:0; }
    .toprow { display:flex; align-items:center; justify-content:space-between; gap:var(--space-2); margin-bottom:var(--space-1); }
    .eyebrow-topic { font-size:var(--text-xs); color:var(--primary); }
    .signal h4 { font-size:var(--text-lg); font-weight:var(--weight-semibold); line-height:1.35; margin:0 0 var(--space-1); color:var(--ink); }
    .metarow { font-size:var(--text-sm); color:var(--ink-dim); margin:0 0 var(--space-2); display:flex; align-items:center; gap:var(--space-2); flex-wrap:wrap; }
    .metarow .imp { display:inline-flex; align-items:center; gap:3px; }
    .metarow .pte { font-size:var(--text-xs); padding:2px var(--space-2); border-radius:var(--radius-pill); background:var(--accent-soft); color:var(--accent-text); }
    .metarow .trd { font-size:var(--text-xs); padding:2px var(--space-2); border-radius:var(--radius-pill); background:var(--surface-2); color:var(--ink-dim); }
    .signal p.sum { font-size:var(--text-sm); line-height:1.6; color:var(--text-2); margin:0 0 var(--space-2); }
    .actions { display:flex; align-items:center; gap:var(--space-2); flex-shrink:0; }

    /* --- Salva / voto (controlli di card) --- */
    .save { display:inline-flex; align-items:center; gap:5px; padding:var(--space-1) var(--space-3); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:var(--surface-2); color:var(--ink-dim); font-size:var(--text-xs); font-weight:var(--weight-semibold); cursor:pointer; font-family:inherit; }
    .save:hover { border-color:var(--primary); color:var(--primary); }
    .save.on { background:var(--primary-soft); border-color:var(--primary); color:var(--primary); }
    .vote-btn { display:none; align-items:center; justify-content:center; width:30px; height:28px; padding:0; border-radius:var(--radius-md); border:1px solid var(--border-strong); background:transparent; color:var(--ink-dim); cursor:pointer; }
    body.served .vote-btn { display:inline-flex; }
    .vote-btn:hover { border-color:var(--primary); color:var(--primary); }
    .vote-btn.on-up { background:var(--ok-soft); border-color:var(--ok); color:var(--ok); }
    .vote-btn.on-down { background:var(--error-soft); border-color:var(--error); color:var(--error); }

    /* --- Deep-dive --- */
    .deepdive-btn { display:none; align-items:center; gap:5px; padding:var(--space-1) var(--space-3); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:transparent; color:var(--ink-dim); font:inherit; font-size:var(--text-xs); font-weight:var(--weight-semibold); cursor:pointer; }
    body.served .deepdive-btn { display:inline-flex; }
    .deepdive-btn:hover { border-color:var(--primary); color:var(--primary); }
    .deepdive-btn:disabled { opacity:0.6; cursor:default; }
    .partial { font-size:var(--text-xs); padding:2px var(--space-2); border-radius:var(--radius-pill); background:var(--surface-2); color:var(--ink-dim); border:1px solid var(--border-strong); }
    .deepdive-panel { margin-top:var(--space-3); padding-top:var(--space-3); border-top:1px solid var(--border); }
    .deepdive-panel h5 { font-size:var(--text-sm); font-weight:var(--weight-semibold); margin:var(--space-3) 0 var(--space-1); color:var(--ink); }
    .deepdive-panel h5:first-child { margin-top:0; }
    .insight .val.missing { color:var(--ink-dim); font-style:italic; }
    .insight-btn { display:none; }
    body.served .insight-btn { display:inline-block; }
    .dd-retry { margin-left:var(--space-2); padding:2px var(--space-2); border-radius:var(--radius-sm); border:1px solid var(--border-strong); background:transparent; color:var(--primary); font:inherit; font-size:var(--text-xs); font-weight:var(--weight-semibold); cursor:pointer; }
    .dd-retry:hover { border-color:var(--primary); }
    .deepdive-panel .dd-overview { font-size:var(--text-sm); line-height:1.6; color:var(--text-2); margin:0 0 var(--space-2); }
    .deepdive-panel ul { margin:0; padding-left:18px; }
    .deepdive-panel li { font-size:var(--text-sm); line-height:1.55; color:var(--text-2); margin-bottom:var(--space-1); }
    .deepdive-panel li b { color:var(--ink); font-weight:var(--weight-semibold); }
    .dd-note { font-size:var(--text-xs); color:var(--ink-dim); font-style:italic; margin:0 0 var(--space-2); }
    .dd-loading { font-size:var(--text-sm); color:var(--ink-dim); }
    .dd-transcript-toggle { display:inline-flex; align-items:center; gap:5px; padding:var(--space-1) var(--space-3); margin-top:var(--space-2); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:transparent; color:var(--ink-dim); font:inherit; font-size:var(--text-xs); font-weight:var(--weight-semibold); cursor:pointer; }
    .dd-transcript-toggle:hover { border-color:var(--primary); color:var(--primary); }
    .dd-transcript { max-height:280px; overflow-y:auto; font-size:var(--text-sm); line-height:1.55; color:var(--text-2); background:var(--surface-2); border:1px solid var(--border); border-radius:var(--radius-md); padding:var(--space-3); margin-top:var(--space-2); white-space:pre-wrap; }

    /* --- Insight sempre visibile --- */
    .insight { display:grid; gap:var(--space-2); margin-top:var(--space-3); padding-top:var(--space-3); border-top:1px solid var(--border); }
    .insight .blk { display:flex; gap:9px; }
    .insight .blk .ic { color:var(--ink-dim); flex-shrink:0; margin-top:1px; }
    .insight .blk .lab { font-size:var(--text-xs); color:var(--ink-dim); margin:0 0 2px; }
    .insight .blk .val { font-size:var(--text-sm); line-height:1.55; color:var(--text-2); margin:0; }
    .insight a.src { display:inline-flex; align-items:center; gap:6px; padding:var(--space-1) 0; font-size:var(--text-sm); color:var(--primary); text-decoration:none; }

    /* --- Strumenti di Esplora (chip categoria + filtri) --- */
    .explore-tools { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-lg); padding:var(--space-4); margin-top:var(--space-4); }
    .chips { display:flex; flex-wrap:wrap; gap:var(--space-2); }
    .chip { font:inherit; font-size:var(--text-sm); padding:6px var(--space-3); border-radius:var(--radius-pill); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink-dim); cursor:pointer; }
    .chip:hover { border-color:var(--border-strong); color:var(--ink); }
    .chip[aria-pressed="true"] { background:var(--primary-soft); border-color:var(--primary); color:var(--primary); }
    .filters { display:flex; flex-wrap:wrap; gap:var(--space-3); margin-top:var(--space-3); }
    .filters label { display:flex; flex-direction:column; gap:var(--space-1); font-size:var(--text-xs); color:var(--ink-dim); }
    .filters input, .filters select { font:inherit; font-size:var(--text-sm); height:38px; padding:0 var(--space-3); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink); min-width:150px; }
    .filters input:focus, .filters select:focus { outline:none; border-color:var(--primary); box-shadow:0 0 0 3px var(--primary-soft); }
    .filters input { min-width:220px; }
    .empty { border:1px dashed var(--ink-faint); border-radius:var(--radius-lg); padding:var(--space-5); font-size:var(--text-sm); color:var(--ink-dim); }

    /* --- Consumo AI / Fonti: metriche dense + righe in errore nella mm-table --- */
    .usage-summary { display:flex; flex-wrap:wrap; gap:var(--space-3); margin-bottom:var(--space-4); }
    .usage-metric { background:var(--surface-2); border-radius:var(--radius-md); padding:var(--space-3) var(--space-4); min-width:120px; }
    .usage-metric .lab { font-size:var(--text-xs); color:var(--ink-dim); margin:0 0 var(--space-1); }
    .usage-metric .val { font-size:var(--text-2xl); font-weight:var(--weight-semibold); margin:0; }
    .mm-table tr.row-err td { background:var(--error-soft); }

    /* --- Profilo: layout dedicato, indipendente dal form compatto Fonti --- */
    .profile-titles, .profile-form { display:none; }
    body.served .profile-form { display:block; }
    body.profile-active .profile-titles { display:block; }
    body.profile-active .brief-titles, body.profile-active .brief-summary,
    body.profile-active .brief-stats, body.profile-active #analyzeBtn,
    body.profile-active #profileBanner { display:none; }
    .profile-card { padding:var(--space-5); margin-bottom:var(--space-5); min-width:0; }
    .profile-label { display:block; font-size:var(--text-md); font-weight:var(--weight-semibold); color:var(--ink); margin:0 0 var(--space-2); }
    .profile-help { font-size:var(--text-sm); line-height:1.6; color:var(--ink-dim); margin:0 0 var(--space-3); }
    #profileView .mm-textarea, #profileView .mm-select { box-sizing:border-box; min-width:0; max-width:100%; }
    #profileView .mm-textarea { font-size:var(--text-md); line-height:1.6; }
    #profAboutMe { min-height:180px; }
    .profile-field { min-width:0; margin-bottom:var(--space-5); }
    .profile-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:var(--space-5); }
    .profile-ai-action { display:flex; flex-wrap:wrap; align-items:flex-start; gap:var(--space-3); margin-top:var(--space-3); }
    .profile-ai-action .profile-help { flex:1 1 300px; margin:0; }
    .profile-save-row { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-3); }
    .profile-save-row .src-msg { flex:1 1 240px; margin:0; }
    #profExpandMsg:not(:empty) { margin:var(--space-3) 0 0; }
    #profExpandMsg:empty { margin:0; }
    @media (max-width:767px) {
      .profile-grid { grid-template-columns:minmax(0, 1fr); gap:0; }
      .profile-card { padding:var(--space-4); }
      #profileView .mm-btn { max-width:100%; white-space:normal; height:auto; min-height:40px; }
    }

    /* --- Newsletter --- */
    .nl-controls { display:flex; flex-wrap:wrap; align-items:center; gap:var(--space-2); margin-bottom:var(--space-4); }
    .nl-controls label { display:inline-flex; align-items:center; gap:6px; font-size:var(--text-sm); color:var(--ink-dim); }
    .nl-controls input[type="date"] { font:inherit; font-size:var(--text-sm); height:36px; padding:0 var(--space-2); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink); }
    .nl-editions { display:flex; flex-wrap:wrap; gap:var(--space-2); margin-bottom:var(--space-4); }
    .nl-edition { font:inherit; font-size:var(--text-xs); padding:5px var(--space-3); border-radius:var(--radius-pill); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink-dim); cursor:pointer; }
    .nl-edition:hover { border-color:var(--primary); color:var(--primary); }
    .nl-paper { background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-lg); padding:var(--space-5); max-width:760px; }
    .nl-paper h2 { font-size:var(--text-2xl); font-weight:var(--weight-bold); margin:0 0 var(--space-1); }
    .nl-paper .nl-period { font-size:var(--text-sm); color:var(--ink-dim); margin:0 0 var(--space-4); }
    .nl-paper .nl-intro { font-size:var(--text-md); line-height:1.7; color:var(--text-2); margin:0 0 var(--space-2); padding-bottom:var(--space-4); border-bottom:1px solid var(--border); }
    .nl-section { margin-top:var(--space-5); }
    .nl-section h3 { font-size:var(--text-lg); font-weight:var(--weight-semibold); margin:0 0 var(--space-2); }
    .nl-section .nl-brief { font-size:var(--text-sm); line-height:1.65; color:var(--text-2); background:var(--primary-soft); border-radius:var(--radius-md); padding:var(--space-3) var(--space-4); margin:0 0 var(--space-3); }
    .nl-item { padding:var(--space-2) 0; border-top:1px solid var(--border); }
    .nl-item a { font-size:var(--text-md); font-weight:var(--weight-semibold); color:var(--ink); text-decoration:none; line-height:1.4; }
    .nl-item a:hover { color:var(--primary); }
    .nl-item .nl-meta { font-size:var(--text-xs); color:var(--ink-dim); margin:3px 0 0; }

    /* --- Fonti: form di aggiunta e stato per-fonte --- */
    .src-form { display:none; flex-wrap:wrap; gap:var(--space-2); align-items:flex-end; background:var(--surface); border:1px solid var(--border); border-radius:var(--radius-lg); padding:var(--space-4); margin-bottom:var(--space-4); }
    body.served .src-form.open { display:flex; }
    .src-actions { display:none; margin:var(--space-4) 0; }
    body.served .src-actions { display:flex; }
    .src-form-toggle { display:none; }
    body.served .src-form-toggle { display:inline-flex; }
    .src-form label { display:flex; flex-direction:column; gap:var(--space-1); font-size:var(--text-xs); color:var(--ink-dim); }
    .src-form input, .src-form select { font:inherit; font-size:var(--text-sm); height:36px; padding:0 var(--space-2); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink); min-width:150px; }
    .src-form .wide input { min-width:260px; }
    .src-enabled-label { flex-direction:row !important; align-items:center; gap:6px !important; }
    .src-enabled-label input { height:auto; min-width:0; }
    .src-msg { font-size:var(--text-sm); margin:0 0 var(--space-3); }
    .src-news { padding:var(--space-2) var(--space-3); background:var(--surface-2); border-radius:var(--radius-md); margin:6px 0 var(--space-3); }
    .src-news .nl-item:first-child { border-top:none; }
    .src-toggle { font:inherit; font-size:var(--text-xs); padding:3px 9px; border-radius:var(--radius-md); border:1px solid var(--border-strong); background:transparent; color:var(--ink-dim); cursor:pointer; }
    .src-toggle:hover { border-color:var(--primary); color:var(--primary); }
    .src-feed-cell { max-width:220px; }
    .src-feed-link { font-size:var(--text-xs); color:var(--ink-dim); text-decoration:none; word-break:break-all; }
    .src-feed-link:hover { color:var(--primary); text-decoration:underline; }
    .src-note { max-width:260px; color:var(--ink-dim); font-size:var(--text-xs); line-height:1.45; }
    .src-rerun-btn { display:none; align-items:center; gap:4px; font:inherit; font-size:var(--text-xs); padding:3px 9px; border-radius:var(--radius-md); border:1px solid var(--border-strong); background:transparent; color:var(--ink-dim); cursor:pointer; }
    body.served .src-rerun-btn { display:inline-flex; }
    .src-rerun-btn:hover { border-color:var(--primary); color:var(--primary); }
    .src-rerun-btn:disabled { opacity:0.6; cursor:default; }
    .src-date-label { display:inline-flex; align-items:center; gap:var(--space-2); font-size:var(--text-sm); color:var(--ink-dim); margin-bottom:var(--space-4); }
    .src-date-label select { font:inherit; font-size:var(--text-sm); height:36px; padding:0 var(--space-2); border-radius:var(--radius-md); border:1px solid var(--border-strong); background:var(--surface); color:var(--ink); }
    .src-ok { color:var(--ok); display:inline-flex; align-items:center; gap:var(--space-1); }
    .src-idle { color:var(--ink-faint); }
    .src-on { color:var(--ok); font-size:var(--text-xs); }
    .src-off { color:var(--ink-dim); font-size:var(--text-xs); }
    .src-err { color:var(--error); display:inline-flex; align-items:center; gap:var(--space-1); cursor:help; }

    .hidden { display:none; }
    .sr-only { position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); border:0; }
  </style>
</head>
<body class="mm-app">
"""


def _render_body(
    *,
    summary: dict[str, Any],
    total: int,
    last_run_label: str,
    sources_total: int,
    sources_error_count: int,
    big_cards: str,
    per_te_cards: str,
    opinion_cards: str,
    tools_cards: str,
    stream_cards: str,
    topic_chips: str,
    source_category_chips: str,
    section_briefs: dict[str, str],
    brief_references: dict[str, str],
) -> str:
    headline = escape(str(summary.get("headline", "")))
    main_shift = escape(str(summary.get("main_shift", "")))
    err_dot = "mm-dot-error" if sources_error_count else "mm-dot-ok"
    sources_note = (
        f"su {sources_total} fonti nell'ultimo run" if sources_total else "nessun run registrato"
    )

    def brief(key: str, default: str) -> str:
        text = str(section_briefs.get(key, "")).strip() or default
        return escape(text)

    big_brief = brief("big", "Le novità dalle aziende AI più note.")
    perte_brief = brief("perte", "Per il tuo mestiere: product, data analysis e costruire prodotti con l'AI.")
    opinion_brief = brief("opinion", "Punti di vista, editoriali e video da approfondire.")
    tools_brief = brief("tools", "Strumenti, API e progetti OSS da provare nei tuoi prodotti.")
    def references(key: str) -> str:
        return brief_references.get(key, "")
    return f"""  <div class="mm-shell">
    <aside class="mm-rail">
      <div class="mm-rail-brand">
        <img src="../../design/mm/app-logo.svg" alt=""><span>Signal Desk</span>
      </div>
      <p class="rail-tag">AI Intelligence — il tuo brief quotidiano sull'AI, con una modalità esplora quando vuoi scavare.</p>
      <nav class="rail-nav" aria-label="Sezioni">
        {_nav("brief", "Brief", "Le cose che contano oggi", "◎", active=True)}
        {_nav("esplora", "Esplora", "Tutti i segnali, con filtri", "⌕")}
        {_nav("saved", "Salvati", "I tuoi articoli salvati", "★")}
        {_nav("newsletter", "Newsletter", "Genera un'edizione su un periodo", "✉")}
        {_nav("sources", "Fonti", "Stato e resa delle fonti", "⚑")}
        {_nav("profile", "Profilo", "Chi sei e cosa ti interessa", "◐")}
        {_nav("usage", "Consumo AI", "Storico del consumo AI", "◷")}
      </nav>
    </aside>

    <main class="mm-content">
     <div class="mm-wrap">
      <div id="llmBanner" class="mm-card llm-banner hidden" role="status"></div>
      <div id="profileBanner" class="mm-card llm-banner hidden" role="status"></div>
      <div id="transcriptBanner" class="mm-card llm-banner hidden" role="status"></div>

      <header class="mm-page-head">
        <div class="titles brief-titles">
          <p class="eyebrow">Brief del giorno</p>
          <h1>{headline}</h1>
          <p class="sub">{main_shift}</p>
        </div>
        <div class="titles profile-titles">
          <p class="eyebrow">Personalizzazione</p>
          <h1>Il tuo profilo</h1>
          <p class="sub">Aiutaci a trovare le notizie più rilevanti per il tuo lavoro e i tuoi interessi.</p>
        </div>
        <div class="actions">
          <button type="button" id="analyzeBtn" class="mm-btn mm-btn-primary hidden">{_icon("bolt", 15)}<span id="analyzeLabel">Analizza nuovi contenuti</span></button>
          <button type="button" id="themeToggle" class="mm-btn mm-btn-ghost" aria-label="Cambia tema">
            <span id="themeIcon">{_icon("moon", 15)}</span><span id="themeLabel">Tema scuro</span>
          </button>
        </div>
      </header>

      <div class="mm-stat-row brief-stats">
        <div class="mm-stat">
          <div class="mm-stat-label">Segnali ultimi 7 giorni</div>
          <div class="mm-stat-value"><span id="visibleCount">{total}</span><span class="mm-dot mm-dot-ok"></span></div>
          <div class="mm-stat-note">Ultimo run: {escape(last_run_label)}</div>
        </div>
        <div class="mm-stat">
          <div class="mm-stat-label">Salvati</div>
          <div class="mm-stat-value"><span id="savedCount">0</span></div>
          <div class="mm-stat-note">in questo browser</div>
        </div>
        <div class="mm-stat">
          <div class="mm-stat-label">Fonti in errore</div>
          <div class="mm-stat-value"><span id="sourcesErrorCount">{sources_error_count}</span><span class="mm-dot {err_dot}"></span></div>
          <div class="mm-stat-note">{sources_note}</div>
        </div>
      </div>

      <div id="briefView">
        <div class="mm-tabs" role="tablist">
          <button type="button" class="mm-tab on" data-tab="big">Le big dell'AI</button>
          <button type="button" class="mm-tab" data-tab="perte" aria-label="Per te — notizie più affini al tuo profilo">Per te<span class="tab-info" aria-hidden="true" data-tooltip="Questa selezione dà più peso alle notizie affini al tuo profilo, ai tuoi interessi e ai feedback che hai dato.">i</span></button>
          <button type="button" class="mm-tab" data-tab="opinion">Opinioni e video</button>
          <button type="button" class="mm-tab" data-tab="tools">Strumenti &amp; API</button>
        </div>

        <div class="brief-group" data-group="big">
          <div class="section-brief">{big_brief}</div>
          {references("big")}
          <div id="bigList">{big_cards}</div>
        </div>
        <div class="brief-group hidden" data-group="perte">
          <div class="section-brief">{perte_brief}</div>
          {references("perte")}
          <div id="perTeList">{per_te_cards}</div>
        </div>
        <div class="brief-group hidden" data-group="opinion">
          <div class="section-brief">{opinion_brief}</div>
          {references("opinion")}
          <div id="opinionList">{opinion_cards}</div>
        </div>
        <div class="brief-group hidden" data-group="tools">
          <div class="section-brief">{tools_brief}</div>
          {references("tools")}
          <div id="toolsList">{tools_cards}</div>
        </div>
      </div>

      <div id="exploreView" class="hidden">
        <div class="explore-tools">
          <div class="chips" id="topicChips">
            <button type="button" data-chip="" aria-pressed="true" class="chip">Tutte</button>
            {topic_chips}
          </div>
          <div class="filters">
            <label>Cerca
              <input id="searchInput" type="search" placeholder="Titoli, fonti, riassunti" aria-label="Cerca segnali">
            </label>
            <label>Importanza minima
              <select id="signalFilter" aria-label="Importanza minima">
                <option value="0">Tutte</option>
                <option value="40">Media (40+)</option>
                <option value="60">Alta (60+)</option>
                <option value="80">Massima (80+)</option>
              </select>
            </label>
            <label>Periodo
              <select id="dateRangeFilter" aria-label="Periodo">
                <option value="all">Tutto</option>
                <option value="today">Oggi</option>
                <option value="7d" selected>Ultimi 7 giorni</option>
              </select>
            </label>
          </div>
        </div>

        <div class="sec-head"><h3 id="streamTitle">Tutti i segnali</h3><span class="count" id="resultCount">0 segnali</span></div>
        <div id="streamList">{stream_cards}</div>
      </div>

      <div id="newsletterView" class="hidden">
        <div class="sec-head"><h3>Newsletter</h3><span class="count">genera su un periodo</span></div>
        <p class="group-desc">Scegli il periodo da coprire e genera l'edizione: intro e una sintesi per ogni sezione, con le notizie in evidenza.</p>
        <div class="nl-controls">
          <label>Dal <input type="date" id="nlFrom"></label>
          <label>Al <input type="date" id="nlTo"></label>
          <span class="nl-presets">
            <button type="button" class="chip" data-preset="7">Ultimi 7 giorni</button>
            <button type="button" class="chip" data-preset="14">14 giorni</button>
            <button type="button" class="chip" data-preset="30">30 giorni</button>
          </span>
          <button type="button" id="nlGenerate" class="mm-btn mm-btn-primary">{_icon("bolt", 15)}<span id="nlGenerateLabel">Genera newsletter</span></button>
          <button type="button" id="nlCopy" class="mm-btn mm-btn-ghost mm-btn-mini hidden">Copia HTML</button>
        </div>
        <div id="nlEditions" class="nl-editions"></div>
        <div id="nlOutput"></div>
      </div>

      <div id="sourcesView" class="hidden">
        <div class="sec-head"><h3>Fonti</h3><span class="count" id="srcCount"></span></div>
        <p class="group-desc">Tutte le fonti disponibili, attive o no. Cerca quella che ti serve e attivala. Scegli un run per vedere soltanto le fonti raccolte quel giorno.</p>
        <div class="explore-tools">
          <div class="chips" id="srcCategoryChips">
            <button type="button" data-src-chip="" aria-pressed="true" class="chip">Tutte</button>
            {source_category_chips}
          </div>
          <div class="filters">
            <label>Cerca
              <input id="srcSearch" type="search" placeholder="Nome, azienda, argomento, URL" aria-label="Cerca fonti">
            </label>
            <label>Stato
              <select id="srcStatusFilter" aria-label="Stato fonte">
                <option value="">Tutte</option>
                <option value="on">Solo attive</option>
                <option value="off">Solo disattivate</option>
                <option value="err">In errore nell'ultimo run</option>
              </select>
            </label>
          </div>
        </div>
        <div class="src-actions">
          <button type="button" id="srcFormToggle" class="mm-btn mm-btn-ghost src-form-toggle"
                  aria-expanded="false" aria-controls="srcForm">{_icon("plus", 15)}<span id="srcFormToggleLabel">Aggiungi fonte</span></button>
        </div>
        <div class="src-form" id="srcForm">
          <label class="wide">URL del feed<input type="url" id="srcUrl" placeholder="https://esempio.com/feed oppure https://youtube.com/@canale"></label>
          <label>Nome<input type="text" id="srcName" placeholder="Nome fonte"></label>
          <label>ID<input type="text" id="srcId" placeholder="id_univoco"></label>
          <label>Tipo
            <select id="srcType"><option value="rss">RSS</option><option value="html">HTML</option></select>
          </label>
          <label>Categoria
            <select id="srcCategory">
              <option value="general_ai">General AI</option>
              <option value="coding_ai">Coding AI</option>
              <option value="image_generation">Immagini</option>
              <option value="music_generation">Musica &amp; Voce</option>
              <option value="research">Research</option>
              <option value="business_product">Enterprise AI</option>
            </select>
          </label>
          <label>Sezione
            <select id="srcGroup">
              <option value="product_data">Product &amp; Data</option>
              <option value="opinion_video">Opinioni e video</option>
              <option value="foundation_models">Le big dell'AI</option>
              <option value="builder_ecosystem">Strumenti &amp; API</option>
            </select>
          </label>
          <label>Segnale
            <select id="srcSignal">
              <option value="official_news">Notizia</option>
              <option value="opinion">Opinione</option>
              <option value="video">Video</option>
            </select>
          </label>
          <label class="wide">Note<input type="text" id="srcNotes" placeholder="Note (opzionale)"></label>
          <label class="src-enabled-label"><input type="checkbox" id="srcEnabled" checked>Attiva</label>
          <button type="button" id="srcAdd" class="mm-btn mm-btn-primary">{_icon("check", 15)}Aggiungi fonte</button>
          <button type="button" id="srcCancelEdit" class="mm-btn mm-btn-ghost mm-btn-mini hidden">Annulla modifica</button>
        </div>
        <p id="srcMsg" class="src-msg"></p>
        <label class="src-date-label">Run <select id="sourcesDate"></select></label>
        <div id="sourcesSummary" class="usage-summary"></div>
        <div id="sourcesTableWrap"></div>
      </div>

      <div id="usageView" class="hidden">
        <div class="sec-head"><h3>Consumo AI</h3><span class="count">storico per run</span></div>
        <p class="group-desc">Token registrati e modalità di accesso per ogni elaborazione AI. Il costo compare solo quando restituito dal provider; le celle vuote indicano un costo non disponibile.</p>
        <div id="usageSummary" class="usage-summary"></div>
        <div id="usageTableWrap"></div>
      </div>

      <div id="profileView" class="hidden">
        <div class="profile-form">
          <section class="mm-card profile-card" aria-labelledby="profAboutLabel">
            <label id="profAboutLabel" for="profAboutMe" class="profile-label">Raccontaci di te</label>
            <p id="profAboutHelp" class="profile-help">Descrivi il tuo lavoro, cosa ti interessa e cosa vorresti imparare.</p>
            <textarea id="profAboutMe" class="mm-textarea" rows="7" maxlength="2000" aria-describedby="profAboutHelp" placeholder="Per esempio: lavoro nell’analisi dati, mi interessa capire come usare l’AI nei prodotti e vorrei migliorare nella sperimentazione."></textarea>
            <div class="profile-ai-action">
              <button type="button" id="profExpand" class="mm-btn mm-btn-ghost" aria-describedby="profAiHelp">{_icon("bolt", 15)}<span id="profExpandLabel">Compila i campi con l’AI</span></button>
              <p id="profAiHelp" class="profile-help">L’AI propone i valori nei campi qui sotto. Puoi correggerli: nulla viene salvato finché non premi «Salva profilo».</p>
            </div>
            <p id="profExpandMsg" class="src-msg" role="status" aria-live="polite"></p>
          </section>
          <section class="mm-card profile-card" aria-label="Obiettivo e interessi">
            <div class="profile-field">
              <label for="profGoal" class="profile-label">Cosa vuoi ottenere</label>
              <p id="profGoalHelp" class="profile-help">Qual è il tuo obiettivo nel restare aggiornato?</p>
              <textarea id="profGoal" class="mm-textarea" rows="3" maxlength="500" aria-describedby="profGoalHelp" placeholder="Per esempio: scegliere strumenti utili al mio lavoro e imparare a costruire prodotti con l’AI."></textarea>
            </div>
            <div class="profile-grid">
              <div class="profile-field">
                <label for="profInterests" class="profile-label">Interessi</label>
                <p id="profInterestsHelp" class="profile-help">Separa le voci con una virgola o vai a capo.</p>
                <textarea id="profInterests" class="mm-textarea" rows="5" aria-describedby="profInterestsHelp" placeholder="Per esempio: product management, analisi dati, strumenti AI"></textarea>
              </div>
              <div class="profile-field">
                <label for="profSkills" class="profile-label">Competenze da sviluppare</label>
                <p id="profSkillsHelp" class="profile-help">Separa le voci con una virgola o vai a capo.</p>
                <textarea id="profSkills" class="mm-textarea" rows="5" aria-describedby="profSkillsHelp" placeholder="Per esempio: SQL, visualizzazione dati, valutazione dei modelli"></textarea>
              </div>
            </div>
            <div class="profile-field">
              <label for="profKeywords" class="profile-label">Argomenti prioritari</label>
              <p id="profKeywordsHelp" class="profile-help">Questi termini aiutano a dare maggiore rilevanza alle notizie che ti interessano. Separa le voci con una virgola o vai a capo.</p>
              <textarea id="profKeywords" class="mm-textarea" rows="4" aria-describedby="profKeywordsHelp" placeholder="Per esempio: agenti AI, API, automazione, sperimentazione"></textarea>
            </div>
            <div class="profile-save-row">
              <button type="button" id="profSave" class="mm-btn mm-btn-primary">{_icon("check", 15)}Salva profilo</button>
              <p id="profMsg" class="src-msg" role="status" aria-live="polite"></p>
            </div>
          </section>
          <section class="mm-card profile-card profile-settings" aria-labelledby="profileSettingsTitle">
            <h2 id="profileSettingsTitle" class="profile-label">Impostazioni AI</h2>
            <p class="profile-help">Scegli il provider e il modello da usare per insight, brief, newsletter e compilazione del profilo.</p>
            <div class="profile-grid">
              <div class="profile-field">
                <label for="llmProviderSelect" class="profile-label">Provider</label>
                <select id="llmProviderSelect" class="mm-select"></select>
              </div>
              <div class="profile-field">
                <label for="llmModelInput" class="profile-label">Modello</label>
                <select id="llmModelInput" class="mm-select"></select>
                <p class="profile-help">Il catalogo è salvato localmente. Aggiornalo dalle fonti ufficiali prima di scegliere una versione nuova.</p>
              </div>
            </div>
            <div class="profile-save-row">
              <button type="button" id="llmSettingsSave" class="mm-btn mm-btn-ghost">{_icon("check", 15)}Salva impostazioni AI</button>
              <button type="button" id="llmModelRefresh" class="mm-btn mm-btn-ghost">Aggiorna catalogo modelli</button>
              <button type="button" id="llmProfileConnect" class="mm-btn mm-btn-ghost">Connetti</button>
              <span id="llmSettingsMsg" class="src-msg" role="status" aria-live="polite"></span>
            </div>
            <p id="llmConnectionMsg" class="profile-help" role="status" aria-live="polite"></p>
          </section>
          <section class="mm-card profile-card profile-settings" aria-labelledby="languageSettingsTitle">
            <h2 id="languageSettingsTitle" class="profile-label">Lingua</h2>
            <p class="profile-help">Scegli separatamente la lingua dell'interfaccia e quella usata per i contenuti prodotti.</p>
            <div class="profile-grid">
              <div class="profile-field">
                <label for="uiLanguageSelect" class="profile-label">Lingua dell'app</label>
                <select id="uiLanguageSelect" class="mm-select">
                  <option value="it">Italiano</option>
                  <option value="en">English</option>
                </select>
                <p class="profile-help">Cambia menu, etichette e messaggi dell'interfaccia.</p>
              </div>
              <div class="profile-field">
                <label for="llmLanguageSelect" class="profile-label">Lingua dei contenuti</label>
                <select id="llmLanguageSelect" class="mm-select">
                  <option value="it">Italiano</option>
                  <option value="en">English</option>
                </select>
                <p class="profile-help">Si applica a insight, brief, newsletter e compilazione del profilo.</p>
              </div>
            </div>
            <div class="profile-save-row">
              <button type="button" id="languageSettingsSave" class="mm-btn mm-btn-primary">{_icon("check", 15)}Salva lingue</button>
              <span id="languageSettingsMsg" class="src-msg" role="status" aria-live="polite"></span>
            </div>
          </section>
        </div>
      </div>
     </div>
    </main>
  </div>

"""


def _nav(view: str, label: str, description: str, icon: str, active: bool = False) -> str:
    cls = "mm-rail-item on" if active else "mm-rail-item"
    v = escape(view, quote=True)
    return (
        f'<button type="button" class="{cls}" data-view="{v}" '
        f'title="{escape(description, quote=True)}" '
        f'onclick="window.dashboardSetView ? window.dashboardSetView(\'{v}\') : null">'
        f'<span class="ic" aria-hidden="true">{escape(icon)}</span>'
        f'<span class="lbl">{escape(label)}</span></button>'
    )


def _render_cards(items: list[dict[str, Any]], empty_message: str) -> str:
    if not items:
        return f'<div class="empty">{escape(empty_message)}</div>'
    return "\n".join(_render_static_card(item) for item in items)


def _render_brief_references(items: list[dict[str, Any]], limit: int = 4) -> str:
    """Render compact source links for the articles selected in a brief tab."""
    links: list[str] = []
    seen: set[str] = set()
    for item in items:
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        href = _safe_href(url)
        if href == "#":
            continue
        seen.add(url)
        title = str(item.get("clean_title") or item.get("title") or "Fonte").strip()
        links.append(
            f'<a href="{href}" target="_blank" rel="noopener noreferrer">{escape(title)}</a>'
        )
        if len(links) == limit:
            break
    if not links:
        return ""
    return '<div class="brief-references"><span class="lab">Articoli di riferimento:</span>' + " · ".join(links) + "</div>"


def _safe_href(url: str) -> str:
    """Escape a URL for use in an <a href>, or return '#' if unsafe.

    Collected item URLs come from external feeds; without a scheme check, a
    hostile feed could publish a `javascript:` URL that runs when the
    rendered link is clicked.
    """
    try:
        scheme = urlsplit(str(url).strip()).scheme.lower()
    except ValueError:
        return "#"
    if scheme not in ("http", "https"):
        return "#"
    return escape(str(url), quote=True)


def insight_placeholder(item: dict[str, Any]) -> str:
    """Say why a per-item insight is missing, and what fills it in.

    Two different gaps, and they need different words: an item the analysis
    never reached can be fixed by running it, while an item the AI answered
    without producing the field cannot — telling the reader to re-run would
    waste their time and their budget.
    """
    if str(item.get("insight_source", "")).strip():
        return "L'AI ha analizzato questo articolo ma non ha prodotto questo campo."
    return "Analisi AI non ancora eseguita su questo articolo."


def _render_static_card(item: dict[str, Any]) -> str:
    title = escape(str(item.get("title", "")))
    url = escape(str(item.get("url", "")), quote=True)
    href_url = _safe_href(item.get("url", ""))
    company = escape(str(item.get("company", "")))
    source = escape(str(item.get("source_name") or item.get("company") or "Source"))
    topic = escape(_category_label(str(item.get("category", "")) or item.get("primary_topic", "")))
    summary = escape(str(item.get("summary", "")))
    content_quality = escape(str(item.get("content_quality") or "title_only"))
    signal_score = int(item.get("display_signal_score") or _signal_score(item))
    placeholder = escape(insight_placeholder(item))
    if not str(item.get("insight_source", "")).strip():
        placeholder += (
            ' <button type="button" class="dd-retry insight-btn" '
            f'data-insight-url="{escape(str(item.get("url", "")), quote=True)}">'
            "Analizza questo articolo</button>"
        )
    why = escape(str(item.get("why_it_matters") or "")) or placeholder
    action = escape(str(item.get("possible_action") or "")) or placeholder
    why_missing = " missing" if not str(item.get("why_it_matters") or "").strip() else ""
    action_missing = " missing" if not str(item.get("possible_action") or "").strip() else ""
    anchor = escape(_source_initial(item))

    personal_value = int(item.get("personal_relevance_score", 0) or 0)
    me_class = " me" if personal_value > 0 else ""
    pte = ""
    if personal_value > 0:
        matched = escape(", ".join(str(t) for t in (item.get("personal_match") or [])[:4]), quote=True)
        title_attr = f' title="{matched}"' if matched else ""
        pte = f'<span class="pte"{title_attr}>Per te {personal_value}</span>'
    trd = ""
    if item.get("signal_type") == "open_source_trend":
        trd = f'<span class="trd">Trend {int(item.get("trend_score", 0) or 0)}</span>'
    date_label = escape(
        _format_date(str(item.get("published_at") or item.get("collected_at") or "")) or "data n/d"
    )

    partial = (
        '<span class="partial">Contenuto parziale</span>'
        if str(item.get("content_quality") or "") != "full"
        else ""
    )

    return (
        f'<article class="mm-card signal{me_class}" data-quality="{content_quality}">'
        f'<div class="anchor" aria-hidden="true">{anchor}</div>'
        f'<div class="body">'
        f'<div class="toprow"><span class="eyebrow-topic">{topic}</span>'
        f'<span class="actions">'
        f'<button type="button" class="vote-btn" data-vote="1" data-vote-url="{url}" title="Interessante">{_icon("up", 14)}</button>'
        f'<button type="button" class="vote-btn" data-vote="-1" data-vote-url="{url}" title="Non rilevante">{_icon("down", 14)}</button>'
        f'<button type="button" class="deepdive-btn" data-deepdive-url="{url}">{_icon("search", 14)}Approfondisci</button>'
        f'<button type="button" class="save" data-save-url="{url}">{_icon("bookmark", 14)}Salva</button>'
        f'</span></div>'
        f'<h4>{title}</h4>'
        f'<p class="metarow"><span class="src-name">{source}</span><span aria-hidden="true">·</span>'
        f'<span class="pub-date">{date_label}</span><span aria-hidden="true">·</span>'
        f'<span class="imp" title="Importanza {signal_score}">{_icon("bolt", 13)}{signal_score}</span>'
        f'{pte}{trd}{partial}</p>'
        f'<p class="sum">{summary}</p>'
        f'<div class="insight">'
        f'<div class="blk"><span class="ic">{_icon("bolt", 16)}</span><div><p class="lab">Perché conta</p><p class="val{why_missing}">{why}</p></div></div>'
        f'<div class="blk"><span class="ic">{_icon("arrow", 16)}</span><div><p class="lab">Azione possibile</p><p class="val{action_missing}">{action}</p></div></div>'
        f'<a class="src" href="{href_url}" target="_blank" rel="noopener noreferrer">Apri la fonte{_icon("arrow", 14)}</a>'
        f'</div>'
        f'<div class="deepdive-panel hidden" data-deepdive-panel></div>'
        f'</div>'
        f'</article>'
    )


_MONTHS_IT = ("", "gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")


def _format_date(value: str) -> str:
    """Format a date string as '10 giu 2026', or '' if unparseable."""
    parsed = _parse_date(str(value or ""))
    if parsed is None:
        return ""
    return f"{parsed.day} {_MONTHS_IT[parsed.month]} {parsed.year}"


def _source_initial(item: dict[str, Any]) -> str:
    name = str(item.get("company") or item.get("source_name") or "").strip()
    return name[0].upper() if name else "•"


def _safe_json(items: list[dict[str, Any]]) -> str:
    return _escape_json_for_script(json.dumps(items, ensure_ascii=False))


def _source_catalog() -> list[dict[str, Any]]:
    """Return the configured sources, for the catalogue-driven Fonti page.

    Read at generation time so the page lists every source even before the
    first collection run (and in a static export, where there is no server
    to call ``/api/sources``). Degrades to an empty list rather than failing
    the whole dashboard if the catalogue is unreadable.
    """
    try:
        return list(load_sources_config().get("sources", []))
    except Exception:  # noqa: BLE001 - a broken catalogue must not break rendering
        logger.exception("Could not load the source catalogue for the dashboard.")
        return []


def _safe_json_object(payload: dict[str, Any]) -> str:
    return _escape_json_for_script(json.dumps(payload, ensure_ascii=False))


def _escape_json_for_script(raw: str) -> str:
    """Escape JSON for safe embedding inside a <script type="application/json"> tag.

    Escaping just "</" isn't enough: a value containing "<!--" can flip the
    HTML parser into script-data-double-escaped state, so a later real
    "</script>" in the document no longer closes the tag it belongs to.
    Escaping every "<" (as its JSON/JS unicode escape, so the JSON stays
    valid) closes every variant of this at once.
    """
    return raw.replace("<", "\\u003c")


def _with_current_score(item: dict[str, Any], today: date_type | None = None) -> dict[str, Any]:
    """Return a copy carrying ``display_signal_score`` for ``today``.

    Written as a separate field on purpose: ``serve`` rebuilds the page from
    the items embedded in the previous one, so overwriting ``signal_score``
    would age them again on every restart and sink them without limit.
    """
    annotated = dict(item)
    annotated["display_signal_score"] = current_signal_score(item, today=today)
    return annotated


def current_signal_score(item: dict[str, Any], today: date_type | None = None) -> int:
    """Return the item's signal score with recency re-applied for ``today``.

    The stored ``signal_score`` freezes the recency bonus at processing time
    (+18 within a day, +8 within three, +2 within a week). The dashboard
    deliberately merges several days of items, so without re-applying it an
    item keeps forever the freshness it had the morning it was collected, and
    competes with that day's news on equal terms.

    Only the recency component is replaced; relevance, source authority,
    content quality and keyword weight are whatever the pipeline computed. An
    item with no usable date keeps its stored score -- there is nothing to age
    it by, and zeroing it would bury it for no reason.
    """
    stored = int(item.get("signal_score") or 0) or _signal_score(item)
    published = str(item.get("published_at", ""))
    if _parse_date(published) is None:
        return stored
    current = today or date_type.today()
    was = _novelty_points(published, today=_processing_date(item, current))
    now = _novelty_points(published, today=current)
    return max(0, min(100, stored - was + now))


def _processing_date(item: dict[str, Any], fallback: date_type) -> date_type:
    """The day the stored score was computed, i.e. when the item was collected."""
    collected = _parse_date(str(item.get("collected_at", "")))
    return collected or _parse_date(str(item.get("published_at", ""))) or fallback


def _sort_dashboard_items(
    items: list[dict[str, Any]], today: date_type | None = None
) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            current_signal_score(item, today=today),
            _quality_rank(str(item.get("content_quality", ""))),
            str(item.get("published_at", "")),
        ),
        reverse=True,
    )


def _sort_recent_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort non-editorial article lists newest-first, keeping unknown dates last."""
    return sorted(
        items,
        key=lambda item: _parse_date(str(item.get("published_at", ""))) or date_type.min,
        reverse=True,
    )


def _signal_score(item: dict[str, Any]) -> int:
    return int(item.get("signal_score") or (int(item.get("relevance_score", 1) or 1) * 12))


def _quality_rank(value: str) -> int:
    return {"full": 3, "excerpt": 2, "title_only": 1}.get(value, 0)


def _valid_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item.get("content_quality") != "invalid" and int(item.get("relevance_score", 3) or 3) > 1
    ]


def _category_label(value: str) -> str:
    return {
        "general_ai": "General AI",
        "coding_ai": "Coding AI",
        "image_generation": "Immagini",
        "music_generation": "Musica & Voce",
        "research": "Research",
        "business_product": "Enterprise AI",
    }.get(value, value or "General AI")


# JavaScript kept as a plain string (no f-string) so its braces need no
# escaping. Data reaches it via the embedded news-data JSON.
_DASHBOARD_SCRIPT = """<script>
  const newsItems = JSON.parse(document.getElementById('news-data').textContent);
  const usageData = JSON.parse(document.getElementById('usage-data').textContent);
  const sourcesData = JSON.parse(document.getElementById('sources-data').textContent);
  // Baseline injected at generation time so the Fonti page works with zero
  // runs and as a static export; refreshed from /api/sources when served.
  let sourcesCatalog = JSON.parse(document.getElementById('sources-catalog').textContent);
  let sourcesCatalogRefreshed = false;
  const sourceLatest = JSON.parse(document.getElementById('source-latest-data').textContent);
  const savedKey = 'ai-news-dashboard-saved-urls';
  const themeKey = 'ai-news-theme';
  const SERVED = location.protocol.indexOf('http') === 0;  // local server vs file://
  const state = { view: window.dashboardPendingView || 'brief', savedUrls: loadSavedUrls(), savedItems: {}, votes: {}, category: '', tab: 'big', srcCategory: '' };
  window.dashboardState = state;

  const ICON = {
    bookmark: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 3h12v18l-6-4-6 4z"/></svg>',
    bolt: '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 3 4 14h7l-1 7 9-11h-7z"/></svg>',
    bolt16: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M13 3 4 14h7l-1 7 9-11h-7z"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>',
    arrow: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    arrow16: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
    sun: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    moon: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/></svg>',
    search: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/></svg>',
    up: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 22V11l5-9a2.5 2.5 0 0 1 2.5 3L13 10h5.5a2 2 0 0 1 2 2.4l-1.6 7A2 2 0 0 1 17 21H7z"/><path d="M3 11h4v11H3z"/></svg>',
    down: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 2v11l-5 9a2.5 2.5 0 0 1-2.5-3L11 14H5.5a2 2 0 0 1-2-2.4l1.6-7A2 2 0 0 1 7 3h10z"/><path d="M21 13h-4V2h4z"/></svg>',
    check: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l4 4 11-11"/></svg>',
    alert: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M15 9l-6 6M9 9l6 6"/></svg>'
  };

  const CATEGORY = { general_ai:'General AI', coding_ai:'Coding AI', image_generation:'Immagini', music_generation:'Musica & Voce', research:'Research', business_product:'Enterprise AI' };
  function categoryLabel(v) { return CATEGORY[v] || v || 'General AI'; }

  const MAINSTREAM = ['openai','anthropic','google','deepmind','microsoft','meta','apple','amazon','nvidia','mistral','xai'];
  function isMainstream(i) { const n = String(i.company || i.source_name || '').toLowerCase(); return MAINSTREAM.some(b => n.includes(b)); }
  const TOOL_GROUPS = ['builder_ecosystem','agentic_coding','google_ecosystem'];
  function isOpinion(i) { return i.signal_type === 'opinion' || i.signal_type === 'video'; }
  function isTool(i) { return i.signal_type === 'open_source_trend' || TOOL_GROUPS.includes(i.source_group); }

  const briefView = document.getElementById('briefView');
  const exploreView = document.getElementById('exploreView');
  const usageView = document.getElementById('usageView');
  const profileView = document.getElementById('profileView');
  const usageSummary = document.getElementById('usageSummary');
  const usageTableWrap = document.getElementById('usageTableWrap');
  const newsletterView = document.getElementById('newsletterView');
  const nlFrom = document.getElementById('nlFrom');
  const nlTo = document.getElementById('nlTo');
  const nlGenerate = document.getElementById('nlGenerate');
  const nlGenerateLabel = document.getElementById('nlGenerateLabel');
  const nlCopy = document.getElementById('nlCopy');
  const nlOutput = document.getElementById('nlOutput');
  const nlEditions = document.getElementById('nlEditions');
  const sourcesView = document.getElementById('sourcesView');
  const sourcesDate = document.getElementById('sourcesDate');
  const sourcesSummary = document.getElementById('sourcesSummary');
  const sourcesTableWrap = document.getElementById('sourcesTableWrap');
  const srcSearch = document.getElementById('srcSearch');
  const srcForm = document.getElementById('srcForm');
  const srcFormToggle = document.getElementById('srcFormToggle');
  const srcFormToggleLabel = document.getElementById('srcFormToggleLabel');

  function setSourceFormOpen(open) {
    if (!srcForm) return;
    srcForm.classList.toggle('open', !!open);
    if (srcFormToggle) srcFormToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (srcFormToggleLabel) srcFormToggleLabel.textContent = open ? 'Chiudi' : 'Aggiungi fonte';
  }
  const srcStatusFilter = document.getElementById('srcStatusFilter');
  const srcCategoryChips = document.getElementById('srcCategoryChips');
  const srcCount = document.getElementById('srcCount');
  const bigList = document.getElementById('bigList');
  const perTeList = document.getElementById('perTeList');
  const opinionList = document.getElementById('opinionList');
  const toolsList = document.getElementById('toolsList');
  const streamList = document.getElementById('streamList');
  const streamTitle = document.getElementById('streamTitle');
  const searchInput = document.getElementById('searchInput');
  const signalFilter = document.getElementById('signalFilter');
  const dateRangeFilter = document.getElementById('dateRangeFilter');
  const resultCount = document.getElementById('resultCount');
  const savedCount = document.getElementById('savedCount');
  const navButtons = Array.from(document.querySelectorAll('[data-view]'));
  const chipButtons = Array.from(document.querySelectorAll('[data-chip]'));
  const srcChipButtons = Array.from(document.querySelectorAll('[data-src-chip]'));
  const tabButtons = Array.from(document.querySelectorAll('[data-tab]'));
  const briefGroups = Array.from(document.querySelectorAll('[data-group]'));
  const themeToggle = document.getElementById('themeToggle');

  function loadSavedUrls() { try { return new Set(JSON.parse(localStorage.getItem(savedKey) || '[]')); } catch (e) { return new Set(); } }
  function persistSavedUrls() { localStorage.setItem(savedKey, JSON.stringify(Array.from(state.savedUrls))); }

  function esc(v) { return String(v == null ? '' : v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;'); }
  // Feed-supplied URLs must never be rendered as a clickable href unless
  // they're http(s): otherwise a hostile feed could publish a javascript:
  // URL that runs when the link is clicked.
  function safeHref(v) {
    try {
      const scheme = new URL(String(v == null ? '' : v), 'http://placeholder-base.invalid').protocol.replace(':', '');
      if (scheme !== 'http' && scheme !== 'https') return '#';
    } catch (e) { return '#'; }
    return esc(v);
  }
  function parseDate(v) { const t = Date.parse(v || ''); return Number.isNaN(t) ? 0 : t; }
  function qualityRank(v) { return { full:3, excerpt:2, title_only:1 }[v] || 0; }
  function signalScore(i) { return Number(i.display_signal_score ?? i.signal_score ?? (Number(i.relevance_score || 1) * 12)); }
  function personalScore(i) { return Number(i.personal_relevance_score || 0); }
  function compareItems(a, b) { return signalScore(b)-signalScore(a) || qualityRank(b.content_quality)-qualityRank(a.content_quality) || parseDate(b.published_at)-parseDate(a.published_at); }
  function compareByDate(a, b) { return parseDate(b.published_at)-parseDate(a.published_at); }
  function textForSearch(i) { return [i.title,i.company,i.source_name,i.category,i.primary_topic,i.summary,i.why_it_matters,i.possible_action].filter(Boolean).join(' ').toLowerCase(); }
  function sourceInitial(i) { const n = String(i.company || i.source_name || '').trim(); return n ? n[0].toUpperCase() : '\\u2022'; }
  function _shortUrl(url) {
    try {
      const u = new URL(url);
      const path = u.pathname && u.pathname !== '/' ? u.pathname : '';
      const short = u.hostname.replace(/^www\\./, '') + path;
      return short.length > 40 ? short.slice(0, 37) + '…' : short;
    } catch (e) { return url.length > 40 ? url.slice(0, 37) + '…' : url; }
  }
  const MONTHS_IT = ['','gen','feb','mar','apr','mag','giu','lug','ago','set','ott','nov','dic'];
  function formatDate(v) { const t = parseDate(v); if (!t) return ''; const d = new Date(t); return d.getUTCDate() + ' ' + MONTHS_IT[d.getUTCMonth() + 1] + ' ' + d.getUTCFullYear(); }

  function renderCard(item) {
    const source = item.source_name || item.company || 'Source';
    const isSaved = state.savedUrls.has(item.url);
    const topic = categoryLabel(item.category || '');
    const personal = personalScore(item);
    const me = personal > 0 ? ' me' : '';
    let badges = '';
    if (personal > 0) {
      const matched = Array.isArray(item.personal_match) ? item.personal_match.slice(0,4).join(', ') : '';
      const t = matched ? ' title="' + esc(matched) + '"' : '';
      badges += '<span class="pte"' + t + '>Per te ' + esc(personal) + '</span>';
    }
    if (item.signal_type === 'open_source_trend') badges += '<span class="trd">Trend ' + esc(item.trend_score || 0) + '</span>';
    if ((item.content_quality || '') !== 'full') badges += '<span class="partial">Contenuto parziale</span>';
    const vote = Number(state.votes[item.url] || 0);
    const dateLabel = formatDate(item.published_at || item.collected_at) || 'data n/d';
    // missingNote is already HTML (it carries the button): escape the
    // insight text, never the note, or the button shows up as markup.
    const missingNote = item.insight_source
      ? esc("L'AI ha analizzato questo articolo ma non ha prodotto questo campo.")
      : esc("Analisi AI non ancora eseguita su questo articolo.")
        + ' <button type="button" class="dd-retry insight-btn" data-insight-url="' + esc(item.url) + '">Analizza questo articolo</button>';
    const whyMissing = !(item.why_it_matters || '').trim();
    const actionMissing = !(item.possible_action || '').trim();
    const why = whyMissing ? missingNote : esc(item.why_it_matters);
    const action = actionMissing ? missingNote : esc(item.possible_action);
    return '<article class="mm-card signal' + me + '" data-quality="' + esc(item.content_quality || 'title_only') + '">'
      + '<div class="anchor" aria-hidden="true">' + esc(sourceInitial(item)) + '</div>'
      + '<div class="body">'
      + '<div class="toprow"><span class="eyebrow-topic">' + esc(topic) + '</span>'
      + '<span class="actions">'
      + '<button type="button" class="vote-btn' + (vote === 1 ? ' on-up' : '') + '" data-vote="1" data-vote-url="' + esc(item.url) + '" title="Interessante">' + ICON.up + '</button>'
      + '<button type="button" class="vote-btn' + (vote === -1 ? ' on-down' : '') + '" data-vote="-1" data-vote-url="' + esc(item.url) + '" title="Non rilevante">' + ICON.down + '</button>'
      + '<button type="button" class="deepdive-btn" data-deepdive-url="' + esc(item.url) + '">' + ICON.search + 'Approfondisci</button>'
      + '<button type="button" class="save' + (isSaved ? ' on' : '') + '" data-save-url="' + esc(item.url) + '">' + ICON.bookmark + (isSaved ? 'Salvato' : 'Salva') + '</button>'
      + '</span></div>'
      + '<h4>' + esc(item.title) + '</h4>'
      + '<p class="metarow"><span class="src-name">' + esc(source) + '</span><span aria-hidden="true">·</span>'
      + '<span class="pub-date">' + esc(dateLabel) + '</span><span aria-hidden="true">·</span>'
      + '<span class="imp" title="Importanza ' + esc(signalScore(item)) + '">' + ICON.bolt + esc(signalScore(item)) + '</span>'
      + badges + '</p>'
      + '<p class="sum">' + esc(item.summary) + '</p>'
      + '<div class="insight">'
      + '<div class="blk"><span class="ic">' + ICON.bolt16 + '</span><div><p class="lab">Perché conta</p><p class="val' + (whyMissing ? ' missing' : '') + '">' + why + '</p></div></div>'
      + '<div class="blk"><span class="ic">' + ICON.arrow16 + '</span><div><p class="lab">Azione possibile</p><p class="val' + (actionMissing ? ' missing' : '') + '">' + action + '</p></div></div>'
      + '<a class="src" href="' + safeHref(item.url) + '" target="_blank" rel="noopener noreferrer">Apri la fonte' + ICON.arrow + '</a>'
      + '</div>'
      + '<div class="deepdive-panel hidden" data-deepdive-panel></div>'
      + '</div></article>';
  }

  function renderSection(target, items, message) {
    if (!target) return;
    target.innerHTML = items.length ? items.map(renderCard).join('') : '<div class="empty">' + esc(message) + '</div>';
  }

  function matchesDateRange(item) {
    const range = dateRangeFilter ? dateRangeFilter.value : 'all';
    if (range === 'all') return true;
    const t = parseDate(item.published_at);
    if (!t) return true;
    const d = new Date(t), now = new Date();
    if (range === 'today') return d.toDateString() === now.toDateString();
    const wk = new Date(now); wk.setDate(now.getDate() - 7);
    return d >= wk;
  }

  function exploreItems() {
    const q = (searchInput ? searchInput.value : '').trim().toLowerCase();
    const minSignal = Number(signalFilter ? signalFilter.value : 0);
    return newsItems
      .filter(i => state.view !== 'saved' || state.savedUrls.has(i.url))
      .filter(i => !state.category || i.category === state.category)
      .filter(i => signalScore(i) >= minSignal)
      .filter(matchesDateRange)
      .filter(i => !q || textForSearch(i).includes(q))
      .sort(compareByDate);
  }

  function updateNav() {
    navButtons.forEach(b => b.classList.toggle('on', b.dataset.view === state.view));
  }
  function updateChips() {
    chipButtons.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.chip || '') === state.category)));
    srcChipButtons.forEach(b => b.setAttribute('aria-pressed', String((b.dataset.srcChip || '') === state.srcCategory)));
  }

  function metric(lab, val) {
    return '<div class="usage-metric"><p class="lab">' + esc(lab) + '</p><p class="val">' + esc(val) + '</p></div>';
  }
  const KIND_LABELS = { insights: 'Insight notizie', section_briefs: 'Brief sezioni', deep_dive: 'Approfondimento', newsletter: 'Newsletter' };
  function kindLabel(k) { return KIND_LABELS[k || 'insights'] || k; }
  function availableCost(r) {
    return r.cost_estimated !== true && typeof r.total_cost_usd === 'number'
      && Number.isFinite(r.total_cost_usd) && r.total_cost_usd >= 0 ? r.total_cost_usd : null;
  }
  function authLabel(r) {
    if (r.auth_mode === 'api_key') return 'API key';
    if (r.auth_mode === 'account') return 'Account Claude/ChatGPT';
    return 'Non disponibile';
  }
  function providerUsageLabel(r) {
    if (r.provider === 'codex') return 'ChatGPT';
    if (r.provider === 'claude' || !r.provider) return 'Claude';
    return r.provider;
  }
  function modelUsageLabel(r) {
    return r.model || 'Predefinito CLI';
  }
  function renderUsage() {
    const runs = Array.isArray(usageData) ? usageData : [];
    const knownCosts = runs.map(availableCost).filter(cost => cost !== null);
    const totCost = knownCosts.reduce((s, cost) => s + cost, 0);
    const totInput = runs.reduce((s, r) => s + Number(r.input_tokens || 0), 0);
    const totOutput = runs.reduce((s, r) => s + Number(r.output_tokens || 0), 0);
    const totCalls = runs.reduce((s, r) => s + Number(r.calls || 0), 0);
    // Cost split by kind (records predating the field count as "insights").
    const byKind = {};
    runs.forEach(r => {
      const k = r.kind || 'insights';
      const cost = availableCost(r);
      if (cost !== null) byKind[k] = (byKind[k] || 0) + cost;
    });
    const kindMetrics = Object.keys(byKind).sort((a, b) => byKind[b] - byKind[a])
      .map(k => metric(kindLabel(k), '$' + byKind[k].toFixed(2))).join('');
    usageSummary.innerHTML = metric('Run totali', runs.length) + metric('Costi disponibili', knownCosts.length ? '$' + totCost.toFixed(2) : '')
      + metric('Chiamate', totCalls) + metric('Token input', totInput) + metric('Token output', totOutput) + kindMetrics;
    if (!runs.length) {
      usageTableWrap.innerHTML = '<div class="empty">Nessun run registrato. Lancia run-daily con gli insight AI attivi.</div>';
      return;
    }
    const rows = [...runs].reverse().map(r =>
      '<tr><td>' + esc(r.date || '') + '</td>'
      + '<td>' + esc(kindLabel(r.kind)) + '</td>'
      + '<td>' + esc(providerUsageLabel(r)) + '</td>'
      + '<td>' + esc(modelUsageLabel(r)) + '</td>'
      + '<td>' + esc(authLabel(r)) + '</td>'
      + '<td class="mm-num">' + esc(r.items_analyzed || 0) + '</td>'
      + '<td class="mm-num">' + esc(r.calls || 0) + '</td>'
      + '<td class="mm-num">' + (availableCost(r) === null ? '' : '$' + availableCost(r).toFixed(4)) + '</td>'
      + '<td class="mm-num">' + esc(r.input_tokens || 0) + ' / ' + esc(r.output_tokens || 0) + '</td></tr>'
    ).join('');
    usageTableWrap.innerHTML = '<div class="mm-table-wrap"><table class="mm-table"><thead><tr><th>Data</th><th>Tipo</th><th>Provider</th><th>Modello</th><th>Accesso</th><th class="mm-num">Item</th>'
      + '<th class="mm-num">Chiamate</th><th class="mm-num">Costo</th><th class="mm-num">Token in/out</th></tr></thead><tbody>'
      + rows + '</tbody></table></div>';
  }

  function isoDay(d) { return d.toISOString().slice(0, 10); }
  function setPreset(days) {
    const to = new Date(), from = new Date();
    from.setDate(to.getDate() - (Number(days) - 1));
    if (nlFrom) nlFrom.value = isoDay(from);
    if (nlTo) nlTo.value = isoDay(to);
  }
  function renderNewsletter(nl) {
    if (!nlOutput) return;
    if (!nl) { nlOutput.innerHTML = ''; return; }
    let html = '<div class="nl-paper"><h2>' + esc(nl.title || 'Newsletter') + '</h2>'
      + '<p class="nl-period">' + esc(nl.period || '') + ' · ' + esc(nl.item_count || 0) + ' notizie</p>'
      + '<p class="nl-intro">' + esc(nl.intro || '') + '</p>';
    (nl.sections || []).forEach(sec => {
      html += '<div class="nl-section"><h3>' + esc(sec.label || '') + '</h3>';
      if (sec.brief) html += '<p class="nl-brief">' + esc(sec.brief) + '</p>';
      (sec.items || []).forEach(it => {
        html += '<div class="nl-item"><a href="' + safeHref(it.url) + '" target="_blank" rel="noopener noreferrer">' + esc(it.title) + '</a>'
          + '<p class="nl-meta">' + esc(it.source || '') + (it.published_at ? ' · ' + esc(formatDate(it.published_at) || it.published_at) : '') + '</p></div>';
      });
      html += '</div>';
    });
    nlOutput.innerHTML = html + '</div>';
    if (nlCopy) nlCopy.classList.remove('hidden');
  }
  function loadEditions() {
    if (!SERVED || !nlEditions) return;
    fetch('/api/newsletter').then(r => r.json()).then(data => {
      const eds = Array.isArray(data.editions) ? data.editions : [];
      nlEditions.innerHTML = eds.length
        ? '<span style="font-size:12px;color:var(--text-3);align-self:center;">Edizioni:</span>' + eds.map(e =>
            '<button type="button" class="nl-edition" data-edition-from="' + esc(e.date_from) + '" data-edition-to="' + esc(e.date_to) + '">' + esc(e.period || '') + '</button>').join('')
        : '';
    }).catch(() => {});
  }
  function pollNewsletter(from, to) {
    fetch('/api/newsletter?from=' + encodeURIComponent(from) + '&to=' + encodeURIComponent(to))
      .then(r => r.json()).then(s => {
        if (s.status === 'done' && s.result) {
          renderNewsletter(s.result);
          if (nlGenerateLabel) nlGenerateLabel.textContent = 'Genera newsletter';
          if (nlGenerate) nlGenerate.disabled = false;
          loadEditions();
          return;
        }
        if (s.status === 'error') {
          if (nlOutput) nlOutput.innerHTML = '<div class="empty">Generazione non riuscita.</div>';
          if (nlGenerateLabel) nlGenerateLabel.textContent = 'Genera newsletter';
          if (nlGenerate) nlGenerate.disabled = false;
          return;
        }
        setTimeout(() => pollNewsletter(from, to), 2500);
      }).catch(() => {
        if (nlGenerate) nlGenerate.disabled = false;
        if (nlGenerateLabel) nlGenerateLabel.textContent = 'Genera newsletter';
      });
  }
  function generateNewsletter(force) {
    if (!nlFrom || !nlTo) return;
    const from = nlFrom.value, to = nlTo.value;
    if (!from || !to) { if (nlOutput) nlOutput.innerHTML = '<div class="empty">Scegli un periodo.</div>'; return; }
    if (nlGenerateLabel) nlGenerateLabel.textContent = 'Generazione in corso…';
    if (nlGenerate) nlGenerate.disabled = true;
    if (nlOutput) nlOutput.innerHTML = '<p class="dd-loading">Sto preparando l\\u2019edizione…</p>';
    fetch('/api/newsletter', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date_from: from, date_to: to, force: !!force }) })
      .then(() => pollNewsletter(from, to))
      .catch(() => { if (nlGenerate) nlGenerate.disabled = false; if (nlGenerateLabel) nlGenerateLabel.textContent = 'Genera newsletter'; });
  }

  function sourcesDays() { return Object.keys(sourcesData).sort().reverse(); }
  function populateSourcesDates() {
    if (!sourcesDate) return;
    const label = sourcesDate.closest('.src-date-label');
    if (label) label.classList.toggle('hidden', !sourcesDays().length);
    sourcesDate.innerHTML = '<option value="">Tutte le fonti</option>'
      + sourcesDays().map(d => '<option value="' + esc(d) + '">' + esc(formatDate(d) || d) + '</option>').join('');
  }
  function sourceCatalog() { return Array.isArray(sourcesCatalog) ? sourcesCatalog : []; }

  function sourceSearchText(src, stat) {
    return [src.id, src.name, src.company, src.category_hint, src.source_group,
            src.type, src.notes, src.url, stat && stat.error]
      .filter(Boolean).join(' ').toLowerCase();
  }

  function visibleSources(day) {
    const rep = sourcesData[day] || { per_source: [], failed: [] };
    const stats = {};
    (Array.isArray(rep.per_source) ? rep.per_source : []).forEach(r => { stats[r.source_id] = r; });

    const q = (srcSearch ? srcSearch.value : '').trim().toLowerCase();
    const status = srcStatusFilter ? srcStatusFilter.value : '';
    return sourceCatalog()
      .map(src => ({ src: src, stat: stats[src.id] || null }))
      .filter(row => !day || row.stat)
      .filter(row => !state.srcCategory || row.src.category_hint === state.srcCategory)
      .filter(row => !status
        || (status === 'on' && row.src.enabled)
        || (status === 'off' && !row.src.enabled)
        || (status === 'err' && row.src.enabled && row.stat && row.stat.status !== 'ok'))
      .filter(row => !q || sourceSearchText(row.src, row.stat).includes(q))
      .sort((a, b) => (a.src.enabled === b.src.enabled ? 0 : a.src.enabled ? -1 : 1)
        || String(a.src.name || a.src.id).localeCompare(String(b.src.name || b.src.id)));
  }

  function renderSources(day) {
    if (!sourcesTableWrap) return;
    const errOpt = srcStatusFilter ? srcStatusFilter.querySelector('option[value="err"]') : null;
    if (errOpt) errOpt.disabled = !day;
    if (!day && srcStatusFilter && srcStatusFilter.value === 'err') srcStatusFilter.value = '';
    const catalog = sourceCatalog();
    const rep = sourcesData[day] || { per_source: [], failed: [] };
    const runRows = Array.isArray(rep.per_source) ? rep.per_source : [];
    const errCount = runRows.filter(r => {
      const source = catalog.find(s => s.id === r.source_id);
      return r.status !== 'ok' && (!source || source.enabled);
    }).length;
    const enabledCount = catalog.filter(s => s.enabled).length;

    sourcesSummary.innerHTML = day
      ? metric('Raccolte', runRows.length) + metric('In errore', errCount)
      : metric('Nel catalogo', catalog.length) + metric('Attive', enabledCount);

    const rows = visibleSources(day);
    if (srcCount) srcCount.textContent = rows.length + (rows.length === 1 ? ' fonte' : ' fonti');

    if (day ? !runRows.length : !catalog.length) {
      sourcesTableWrap.innerHTML = '<div class="empty">'
        + (day ? 'Nessuna fonte raccolta in questo run.' : 'Nessuna fonte configurata.') + '</div>';
      return;
    }
    if (!rows.length) {
      sourcesTableWrap.innerHTML = '<div class="empty">Nessuna fonte corrisponde ai filtri.</div>';
      return;
    }

    const columnCount = day ? 10 : 7;
    const body = rows.map(row => {
      const src = row.src;
      const r = row.stat;
      const id = src.id || '';
      const status = !r
        ? '<span class="src-idle">—</span>'
        : (!src.enabled
            ? '<span class="src-off">disabilitata</span>'
            : r.status === 'ok'
            ? '<span class="src-ok">' + ICON.check + 'ok</span>'
            : '<span class="src-err" title="' + esc(r.error || '') + '">' + ICON.alert + 'errore</span>');
      const last = sourceLatest[id];
      const lastLabel = last ? esc(formatDate(last) || last) : '—';
      const feedUrl = src.url || '';
      const feedCell = feedUrl
        ? '<a class="src-feed-link" href="' + safeHref(feedUrl) + '" target="_blank" rel="noopener noreferrer" title="' + esc(feedUrl) + '">' + esc(_shortUrl(feedUrl)) + '</a>'
        : '—';
      const stateCell = src.enabled
        ? '<span class="src-on">attiva</span>'
        : '<span class="src-off">disattivata</span>';
      return '<tr class="' + (r && r.status !== 'ok' ? 'row-err' : '') + '"><td>' + esc(src.name || id) + '</td>'
        + '<td>' + esc(categoryLabel(src.category_hint)) + '</td>'
        + '<td>' + stateCell + '</td>'
        + '<td class="src-note">' + esc(src.notes || '—') + '</td>'
        + '<td class="src-feed-cell">' + feedCell + '</td>'
        + (day ? '<td class="mm-num">' + (r ? esc(r.found || 0) : '—') + '</td>'
          + '<td class="mm-num">' + (r ? esc(r.accepted || 0) : '—') + '</td>' : '')
        + '<td>' + lastLabel + '</td>'
        + (day ? '<td>' + status + '</td>' : '')
        + '<td><button type="button" class="src-toggle" data-source-news="' + esc(id) + '">Storico</button>'
        + ' <button type="button" class="src-rerun-btn" data-rerun-source="' + esc(id) + '">' + ICON.bolt + 'Rilancia</button>'
        + ' <button type="button" class="src-rerun-btn" data-edit-source="' + esc(id) + '">Modifica</button>'
        + ' <button type="button" class="src-rerun-btn" data-remove-source="' + esc(id) + '">Rimuovi</button></td></tr>'
        + '<tr class="hidden" data-news-row="' + esc(id) + '"><td colspan="' + columnCount + '"><div class="src-news">Caricamento…</div></td></tr>';
    }).join('');
    const headers = day
      ? '<th>Fonte</th><th>Categoria</th><th>Stato</th><th>Nota</th><th>Feed</th><th class="mm-num">Trovate</th><th class="mm-num">Nuove</th><th>Ultima notizia</th><th>Esito run</th><th></th>'
      : '<th>Fonte</th><th>Categoria</th><th>Stato</th><th>Nota</th><th>Feed</th><th>Ultima notizia</th><th></th>';
    sourcesTableWrap.innerHTML = '<div class="mm-table-wrap"><table class="mm-table"><thead><tr>' + headers + '</tr></thead><tbody>' + body + '</tbody></table></div>';
  }

  function toggleSourceNews(sourceId) {
    const row = document.querySelector('[data-news-row="' + sourceId + '"]');
    if (!row) return;
    const wasHidden = row.classList.contains('hidden');
    row.classList.toggle('hidden');
    if (!wasHidden || row.dataset.loaded === '1') return;
    const box = row.querySelector('.src-news');
    fetch('/api/source-news?source_id=' + encodeURIComponent(sourceId)).then(r => r.json()).then(data => {
      const items = Array.isArray(data.items) ? data.items : [];
      row.dataset.loaded = '1';
      box.innerHTML = items.length
        ? '<p class="nl-meta" style="margin:0 0 6px;">' + items.length + ' notizie raccolte da questa fonte</p>'
          + items.map(i => '<div class="nl-item"><a href="' + safeHref(i.url) + '" target="_blank" rel="noopener noreferrer">' + esc(i.title) + '</a>'
            + '<p class="nl-meta">' + esc(formatDate(i.published_at || i.collected_at) || '') + '</p></div>').join('')
        : '<p class="nl-meta" style="margin:0;">Nessuna notizia registrata per questa fonte.</p>';
    }).catch(() => { box.innerHTML = '<p class="nl-meta" style="margin:0;">Errore nel caricamento.</p>'; });
  }

  let editingSourceId = null;
  let sourcesListCache = null;
  function loadSourcesList() {
    if (sourcesListCache) return Promise.resolve(sourcesListCache);
    return fetch('/api/sources').then(r => r.json()).then(data => {
      sourcesListCache = Array.isArray(data.sources) ? data.sources : [];
      return sourcesListCache;
    }).catch(() => []);
  }

  function refreshSourceCatalog() {
    // Served: pick up sources added or toggled after this page was
    // generated. Static export: the injected baseline is all there is.
    if (!SERVED) return;
    sourcesListCache = null;
    loadSourcesList().then(list => {
      if (!list.length) return;
      sourcesCatalog = list;
      if (state.view === 'sources') render();
    });
  }
  function setSourceFormMode(editing) {
    const addBtn = document.getElementById('srcAdd');
    const cancelBtn = document.getElementById('srcCancelEdit');
    const idInput = document.getElementById('srcId');
    if (addBtn) addBtn.textContent = editing ? 'Salva modifiche' : 'Aggiungi fonte';
    if (cancelBtn) cancelBtn.classList.toggle('hidden', !editing);
    if (idInput) idInput.disabled = editing;
  }
  function clearSourceForm() {
    ['srcUrl', 'srcName', 'srcId', 'srcNotes'].forEach(k => { const el = document.getElementById(k); if (el) el.value = ''; });
    const enabledEl = document.getElementById('srcEnabled');
    if (enabledEl) enabledEl.checked = true;
    editingSourceId = null;
    setSourceFormMode(false);
    setSourceFormOpen(false);
  }
  function startEditSource(sourceId) {
    loadSourcesList().then(sources => {
      const source = sources.find(s => s.id === sourceId);
      if (!source) return;
      const set = (id, value) => { const el = document.getElementById(id); if (el) el.value = value || ''; };
      set('srcUrl', source.url);
      set('srcName', source.name);
      set('srcId', source.id);
      set('srcType', source.type || 'rss');
      set('srcCategory', source.category_hint || 'general_ai');
      set('srcGroup', source.source_group || 'product_data');
      set('srcSignal', source.signal_type || 'official_news');
      set('srcNotes', source.notes);
      const enabledEl = document.getElementById('srcEnabled');
      if (enabledEl) enabledEl.checked = source.enabled !== false;
      editingSourceId = sourceId;
      setSourceFormMode(true);
      setSourceFormOpen(true);
      const form = document.querySelector('.src-form');
      if (form) form.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
  }
  function removeSource(sourceId) {
    if (!window.confirm('Rimuovere questa fonte? L\\u2019azione non si pu\\u00f2 annullare.')) return;
    fetch('/api/sources/remove', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_id: sourceId }) })
      .then(r => r.json()).then(res => {
        if (res.ok) { sourcesListCache = null; location.reload(); }
        else window.alert('Errore: ' + (res.error || 'rimozione non riuscita'));
      }).catch(() => window.alert('Errore di connessione.'));
  }
  function addSource() {
    const msg = document.getElementById('srcMsg');
    const get = id => { const el = document.getElementById(id); return el ? el.value.trim() : ''; };
    const url = get('srcUrl');
    if (!url) { if (msg) { msg.textContent = 'Inserisci l\\u2019URL del feed.'; msg.style.color = 'var(--text-3)'; } return; }
    let host = '';
    try { host = new URL(url).hostname.replace('www.', ''); } catch (e) {}
    const name = get('srcName') || host;
    const enabled = document.getElementById('srcEnabled');
    const fields = {
      name: name, company: name, url: url,
      type: get('srcType') || 'rss', category_hint: get('srcCategory') || 'general_ai',
      source_group: get('srcGroup') || 'product_data', signal_type: get('srcSignal') || 'official_news',
      notes: get('srcNotes'), enabled: enabled ? enabled.checked : true,
    };
    if (msg) { msg.textContent = editingSourceId ? 'Salvataggio in corso…' : 'Aggiunta in corso…'; msg.style.color = 'var(--text-3)'; }
    const request = editingSourceId
      ? fetch('/api/sources/update', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source_id: editingSourceId, updates: fields }) })
      : fetch('/api/sources', { method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ source: { id: get('srcId') || (host.split('.')[0] || 'fonte') + '_' + Math.random().toString(36).slice(2, 6), ...fields } }) });
    request.then(r => r.json()).then(res => {
        if (!msg) return;
        if (res.ok) {
          msg.textContent = editingSourceId
            ? 'Fonte aggiornata.'
            : 'Fonte aggiunta. Verrà raccolta al prossimo "Analizza".';
          msg.style.color = 'var(--ok)';
          sourcesListCache = null;
          clearSourceForm();
        } else {
          msg.textContent = 'Errore: ' + (res.error || 'non riuscito');
          msg.style.color = 'var(--error)';
        }
      }).catch(() => { if (msg) { msg.textContent = 'Errore di connessione.'; msg.style.color = 'var(--error)'; } });
  }

  function render() {
    const isBrief = state.view === 'brief';
    const isUsage = state.view === 'usage';
    const isSources = state.view === 'sources';
    const isNewsletter = state.view === 'newsletter';
    const isProfile = state.view === 'profile';
    document.body.classList.toggle('profile-active', isProfile);
    briefView.classList.toggle('hidden', !isBrief);
    usageView.classList.toggle('hidden', !isUsage);
    sourcesView.classList.toggle('hidden', !isSources);
    newsletterView.classList.toggle('hidden', !isNewsletter);
    if (profileView) profileView.classList.toggle('hidden', !isProfile);
    exploreView.classList.toggle('hidden', isBrief || isUsage || isSources || isNewsletter || isProfile);
    if (isNewsletter) {
      if (nlFrom && !nlFrom.value) setPreset(7);
      loadEditions();
    } else if (isSources) {
      renderSources(sourcesDate ? sourcesDate.value : '');
      if (!sourcesCatalogRefreshed) { sourcesCatalogRefreshed = true; refreshSourceCatalog(); }
    } else if (isUsage) {
      renderUsage();
    } else if (isProfile) {
      loadProfile();
    } else if (isBrief) {
      const sorted = [...newsItems].sort(compareItems);
      const big = sorted.filter(isMainstream).slice(0, 6);
      const perTe = [...newsItems].filter(i => personalScore(i) > 0).sort((a,b) => personalScore(b)-personalScore(a) || compareItems(a,b)).slice(0, 6);
      const opinion = sorted.filter(isOpinion).slice(0, 6);
      const tools = newsItems.filter(isTool).sort((a,b) => Number(b.trend_score||0)-Number(a.trend_score||0) || compareItems(a,b)).slice(0, 6);
      renderSection(bigList, big, 'Nessuna novità dalle big oggi.');
      renderSection(perTeList, perTe, 'Nessun segnale rilevante per product & data oggi.');
      renderSection(opinionList, opinion, 'Nessuna opinione o video oggi. Aggiungi fonti o canali YouTube nel config.');
      renderSection(toolsList, tools, 'Nessuno strumento o API emergente oggi.');
      briefGroups.forEach(g => g.classList.toggle('hidden', g.dataset.group !== state.tab));
      tabButtons.forEach(t => t.classList.toggle('on', t.dataset.tab === state.tab));
    } else {
      const items = (state.view === 'saved' && SERVED)
        ? Object.values(state.savedItems).sort(compareByDate)
        : exploreItems();
      renderSection(streamList, items, state.view === 'saved' ? 'Nessun articolo salvato.' : 'Nessun segnale corrisponde ai filtri.');
      if (streamTitle) streamTitle.textContent = state.view === 'saved' ? 'I tuoi salvati' : 'Tutti i segnali';
      if (resultCount) resultCount.textContent = items.length + ' segnali';
    }
    if (savedCount) savedCount.textContent = String(state.savedUrls.size);
    updateNav();
    updateChips();
  }

  function setView(view) { state.view = view || 'brief'; render(); }
  window.dashboardRenderView = setView;
  window.dashboardSetView = setView;

  function findItem(url) {
    return newsItems.find(i => i.url === url) || state.savedItems[url] || { url: url };
  }
  function loadVotes() {
    if (!SERVED) return;
    fetch('/api/feedback').then(r => r.json()).then(data => {
      const fb = data.feedback || {};
      state.votes = {};
      Object.keys(fb).forEach(url => { state.votes[url] = Number(fb[url].vote || 0); });
      render();
    }).catch(() => {});
  }
  function sendVote(url, vote) {
    if (!SERVED || !url) return;
    const current = Number(state.votes[url] || 0);
    const next = current === vote ? 0 : vote;   // clicking the same vote clears it
    state.votes[url] = next;
    render();
    fetch('/api/feedback', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item: findItem(url), vote: next }) }).catch(() => {});
  }
  function loadServerSaved() {
    fetch('/api/saved').then(r => r.json()).then(data => {
      const items = Array.isArray(data.items) ? data.items : [];
      state.savedItems = {};
      state.savedUrls = new Set();
      items.forEach(it => { if (it.url) { state.savedItems[it.url] = it; state.savedUrls.add(it.url); } });
      render();
    }).catch(() => {});
  }
  function toggleSaved(url) {
    if (!url) return;
    const has = state.savedUrls.has(url);
    if (SERVED) {
      const item = findItem(url);
      if (has) { state.savedUrls.delete(url); delete state.savedItems[url]; }
      else { state.savedUrls.add(url); state.savedItems[url] = item; }
      const endpoint = has ? '/api/unsave' : '/api/save';
      const body = has ? { url: url } : { item: item };
      fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).catch(() => {});
      render();
      return;
    }
    if (has) state.savedUrls.delete(url); else state.savedUrls.add(url);
    persistSavedUrls();
    render();
  }

  function applyTheme(dark) {
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
    const icon = document.getElementById('themeIcon'), label = document.getElementById('themeLabel');
    if (icon) icon.innerHTML = dark ? ICON.sun : ICON.moon;
    if (label) label.textContent = dark ? 'Tema chiaro' : 'Tema scuro';
  }
  function initTheme() { applyTheme(document.documentElement.getAttribute('data-theme') === 'dark'); }

  function renderDeepDive(panel, r, url) {
    let html = '';
    if (r.content_complete === false) html += '<p class="dd-note">' + esc(r.partial_note || 'Fonte completa non accessibile: approfondimento basato sul contenuto disponibile.') + '</p>';
    if (r.overview) html += '<h5>Panoramica</h5><p class="dd-overview">' + esc(r.overview) + '</p>';
    if (Array.isArray(r.points) && r.points.length) {
      html += '<h5>Punti chiave</h5><ul>' + r.points.map(p => '<li><b>' + esc(p.title) + '</b> — ' + esc(p.detail) + '</li>').join('') + '</ul>';
    }
    if (Array.isArray(r.insights) && r.insights.length) {
      html += '<h5>Insight</h5><ul>' + r.insights.map(i => '<li>' + esc(i) + '</li>').join('') + '</ul>';
    }
    if (r.source_text) {
      const isVideo = /youtube\\.com|youtu\\.be/.test(url || '');
      const label = isVideo ? 'Vedi trascrizione' : 'Vedi testo completo della fonte';
      html += '<button type="button" class="dd-transcript-toggle" data-dd-transcript-toggle data-label-show="' + esc(label) + '" data-label-hide="Nascondi">' + esc(label) + '</button>';
      html += '<div class="dd-transcript hidden" data-dd-transcript>' + esc(r.source_text) + '</div>';
    }
    panel.innerHTML = html || '<p class="dd-loading">Nessun approfondimento disponibile.</p>';
  }
  // A deep dive is a real subprocess: it can fail, be queued out, or hit the
  // daily budget. Every one of those has to end in something the reader can
  // act on, never in a spinner that turns forever.
  const DD_MAX_POLLS = 90;  // ~3 minutes at 2s
  function deepDiveFailed(panel, btn, message) {
    panel.innerHTML = '<p class="dd-loading">' + esc(message)
      + ' <button type="button" class="dd-retry" data-dd-retry>Riprova</button></p>';
    panel.dataset.loaded = '';
    btn.disabled = false;
  }
  function insightBlocks(card) {
    return [...card.querySelectorAll('.insight .val.missing')];
  }
  function runSingleInsight(btn) {
    const url = btn.getAttribute('data-insight-url');
    const card = btn.closest('.signal');
    if (!card) return;
    const blocks = insightBlocks(card);
    blocks.forEach(b => { b.textContent = 'Analisi in corso…'; });
    const item = findItem(url);
    if (!item) { blocks.forEach(b => { b.textContent = 'Articolo non più disponibile in questa pagina.'; }); return; }
    fetch('/api/insight', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item: item }) })
      .then(r => r.ok ? pollInsight(url, card, 0) : r.json().catch(() => ({})).then(e => {
        insightFailed(card, e.error || 'Analisi non avviata.');
      }))
      .catch(() => insightFailed(card, 'Errore di connessione.'));
  }
  function insightFailed(card, message) {
    insightBlocks(card).forEach(b => { b.textContent = message; });
  }
  function pollInsight(url, card, attempt) {
    if (attempt >= 90) { insightFailed(card, 'L’analisi sta impiegando troppo tempo.'); return; }
    fetch('/api/insight?url=' + encodeURIComponent(url)).then(r => r.json()).then(s => {
      if (s.status === 'done' && s.result) {
        const item = findItem(url);
        if (item) { Object.assign(item, s.result, { insight_source: 'claude_cli' }); }
        const blocks = insightBlocks(card);
        if (blocks[0]) { blocks[0].textContent = s.result.why_it_matters || ''; blocks[0].classList.remove('missing'); }
        if (blocks[1]) { blocks[1].textContent = s.result.possible_action || ''; blocks[1].classList.remove('missing'); }
        return;
      }
      if (s.status === 'error') { insightFailed(card, s.error || 'Analisi non riuscita.'); return; }
      setTimeout(() => pollInsight(url, card, attempt + 1), 2000);
    }).catch(() => insightFailed(card, 'Errore di connessione.'));
  }
  function pollDeepDive(url, panel, btn, attempt) {
    attempt = attempt || 0;
    if (attempt >= DD_MAX_POLLS) {
      deepDiveFailed(panel, btn, 'L’approfondimento sta impiegando troppo tempo.');
      return;
    }
    fetch('/api/deep-dive?url=' + encodeURIComponent(url)).then(r => r.json()).then(s => {
      if (s.status === 'done' && s.result) { renderDeepDive(panel, s.result, url); panel.dataset.loaded = '1'; btn.disabled = false; return; }
      if (s.status === 'error') {
        deepDiveFailed(panel, btn, s.error || 'Approfondimento non riuscito.');
        return;
      }
      setTimeout(() => pollDeepDive(url, panel, btn, attempt + 1), 2000);
    }).catch(() => deepDiveFailed(panel, btn, 'Errore di connessione.'));
  }
  function openDeepDive(btn) {
    const url = btn.getAttribute('data-deepdive-url');
    const card = btn.closest('.signal');
    const panel = card ? card.querySelector('[data-deepdive-panel]') : null;
    if (!panel) return;
    if (panel.dataset.loaded === '1') { panel.classList.toggle('hidden'); return; }
    panel.classList.remove('hidden');
    panel.innerHTML = '<p class="dd-loading">Approfondimento in corso…</p>';
    btn.disabled = true;
    const item = findItem(url);
    if (!item) { deepDiveFailed(panel, btn, 'Articolo non più disponibile in questa pagina.'); return; }
    fetch('/api/deep-dive', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ item: item }) })
      .then(r => r.ok ? pollDeepDive(url, panel, btn) : r.json().catch(() => ({})).then(e => {
        // 429 means nothing was started: polling would spin forever.
        deepDiveFailed(panel, btn, e.error || 'Approfondimento non avviato.');
      }))
      .catch(() => deepDiveFailed(panel, btn, 'Errore di connessione.'));
  }

  document.addEventListener('click', event => {
    const nav = event.target.closest('[data-view]');
    if (nav) { event.preventDefault(); setView(nav.dataset.view || 'brief'); return; }
    const chip = event.target.closest('[data-chip]');
    if (chip) { event.preventDefault(); state.category = chip.dataset.chip || ''; if (state.view === 'brief') state.view = 'esplora'; render(); return; }
    const srcChip = event.target.closest('[data-src-chip]');
    if (srcChip) { event.preventDefault(); state.srcCategory = srcChip.dataset.srcChip || ''; render(); return; }
    const tab = event.target.closest('[data-tab]');
    if (tab) { event.preventDefault(); state.tab = tab.dataset.tab || 'big'; if (state.view !== 'brief') state.view = 'brief'; render(); return; }
    const save = event.target.closest('[data-save-url]');
    if (save) { event.preventDefault(); toggleSaved(save.getAttribute('data-save-url')); return; }
    const voteBtn = event.target.closest('[data-vote-url]');
    if (voteBtn) { event.preventDefault(); sendVote(voteBtn.getAttribute('data-vote-url'), Number(voteBtn.dataset.vote)); return; }
    const insightBtn = event.target.closest('[data-insight-url]');
    if (insightBtn) { event.preventDefault(); runSingleInsight(insightBtn); return; }
    const retry = event.target.closest('[data-dd-retry]');
    if (retry) {
      event.preventDefault();
      const card = retry.closest('.signal');
      const again = card ? card.querySelector('[data-deepdive-url]') : null;
      if (again) openDeepDive(again);
      return;
    }
    const dd = event.target.closest('[data-deepdive-url]');
    if (dd) { event.preventDefault(); openDeepDive(dd); return; }
    const ddTranscript = event.target.closest('[data-dd-transcript-toggle]');
    if (ddTranscript) {
      event.preventDefault();
      const box = ddTranscript.nextElementSibling;
      if (box) {
        const nowHidden = box.classList.toggle('hidden');
        ddTranscript.textContent = nowHidden ? ddTranscript.dataset.labelShow : ddTranscript.dataset.labelHide;
      }
      return;
    }
    const preset = event.target.closest('[data-preset]');
    if (preset) { event.preventDefault(); setPreset(preset.dataset.preset); return; }
    const edition = event.target.closest('[data-edition-from]');
    if (edition) {
      event.preventDefault();
      if (nlFrom) nlFrom.value = edition.dataset.editionFrom;
      if (nlTo) nlTo.value = edition.dataset.editionTo;
      pollNewsletter(edition.dataset.editionFrom, edition.dataset.editionTo);
      return;
    }
    const srcNews = event.target.closest('[data-source-news]');
    if (srcNews) { event.preventDefault(); toggleSourceNews(srcNews.getAttribute('data-source-news')); return; }
    const srcRerun = event.target.closest('[data-rerun-source]');
    if (srcRerun) { event.preventDefault(); startSourceAnalysis(srcRerun); return; }
    const srcEdit = event.target.closest('[data-edit-source]');
    if (srcEdit) { event.preventDefault(); startEditSource(srcEdit.getAttribute('data-edit-source')); return; }
    const srcRemove = event.target.closest('[data-remove-source]');
    if (srcRemove) { event.preventDefault(); removeSource(srcRemove.getAttribute('data-remove-source')); return; }
    if (event.target.closest('#srcCancelEdit')) { event.preventDefault(); clearSourceForm(); return; }
    if (event.target.closest('#srcFormToggle')) {
      event.preventDefault();
      if (srcForm && srcForm.classList.contains('open')) clearSourceForm();
      else setSourceFormOpen(true);
      return;
    }
    if (event.target.closest('#srcAdd')) { event.preventDefault(); addSource(); return; }
    if (event.target.closest('#nlGenerate')) { event.preventDefault(); generateNewsletter(false); return; }
    if (event.target.closest('#profExpand')) { event.preventDefault(); expandProfile(); return; }
    if (event.target.closest('#profSave')) { event.preventDefault(); saveProfile(); return; }
    if (event.target.closest('#llmSettingsSave')) { event.preventDefault(); saveLlmSettings(); return; }
    if (event.target.closest('#languageSettingsSave')) { event.preventDefault(); saveLanguageSettings(); return; }
    if (event.target.closest('#llmModelRefresh')) { event.preventDefault(); refreshModelCatalog(); return; }
    if (event.target.closest('#llmProfileConnect')) { event.preventDefault(); if (window.connectConfiguredLlm) window.connectConfiguredLlm(); return; }
    if (event.target.closest('#nlCopy')) {
      event.preventDefault();
      const paper = nlOutput ? nlOutput.querySelector('.nl-paper') : null;
      if (paper && navigator.clipboard) {
        navigator.clipboard.writeText(paper.innerHTML).then(() => {
          nlCopy.textContent = 'Copiato';
          setTimeout(() => { nlCopy.textContent = 'Copia HTML'; }, 1500);
        }).catch(() => {});
      }
      return;
    }
    if (event.target.closest('#themeToggle')) {
      const dark = document.documentElement.getAttribute('data-theme') !== 'dark';
      applyTheme(dark);
      try { localStorage.setItem(themeKey, dark ? 'dark' : 'light'); } catch (e) {}
    }
  }, true);

  [searchInput, signalFilter, dateRangeFilter].forEach(c => { if (!c) return; c.addEventListener('input', render); c.addEventListener('change', render); });
  [srcSearch, srcStatusFilter].forEach(c => { if (!c) return; c.addEventListener('input', render); c.addEventListener('change', render); });

  // In-app analysis button: only when served by the local server (http), not file://.
  const analyzeBtn = document.getElementById('analyzeBtn');
  const analyzeLabel = document.getElementById('analyzeLabel');
  let analysisActive = false;  // true only for a run started in this page session
  function setAnalyze(text, disabled) { if (analyzeLabel) analyzeLabel.textContent = text; if (analyzeBtn) analyzeBtn.disabled = disabled; }
  async function pollAnalysis() {
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      if (s.status === 'running') { setTimeout(pollAnalysis, 2500); return; }
      if (!analysisActive) { setAnalyze('Analizza nuovi contenuti', false); return; }
      analysisActive = false;
      if (s.status === 'done') { setAnalyze('Completato, ricarico…', true); setTimeout(() => location.reload(), 800); return; }
      if (s.status === 'error') { setAnalyze('Errore: ' + String(s.error || '').slice(0, 50), false); return; }
      setAnalyze('Analizza nuovi contenuti', false);
    } catch (e) { setAnalyze('Errore di connessione', false); }
  }
  async function startAnalysis() {
    analysisActive = true;
    setAnalyze('Analisi in corso…', true);
    try { await fetch('/api/run', { method: 'POST' }); pollAnalysis(); }
    catch (e) { analysisActive = false; setAnalyze('Errore di connessione', false); }
  }
  async function startSourceAnalysis(btn) {
    const sourceId = btn.getAttribute('data-rerun-source');
    if (!sourceId) return;
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'In corso…';
    try {
      const r = await fetch('/api/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source_id: sourceId }),
      });
      if (r.status === 409) {
        btn.textContent = 'Un’altra analisi è già in corso';
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
        return;
      }
      pollSourceAnalysis(btn, original);
    } catch (e) {
      btn.textContent = 'Errore di connessione';
      setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
    }
  }
  async function pollSourceAnalysis(btn, original) {
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      if (s.status === 'running') { setTimeout(() => pollSourceAnalysis(btn, original), 2500); return; }
      if (s.status === 'done') { btn.textContent = 'Completato, ricarico…'; setTimeout(() => location.reload(), 800); return; }
      if (s.status === 'error') {
        btn.textContent = 'Errore: ' + String(s.error || '').slice(0, 30);
        setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 4000);
        return;
      }
      btn.textContent = original;
      btn.disabled = false;
    } catch (e) {
      btn.textContent = 'Errore di connessione';
      setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 3000);
    }
  }
  async function resumeIfRunning() {
    // On page load, only re-attach to an analysis that is genuinely still running.
    try {
      const r = await fetch('/api/status');
      const s = await r.json();
      if (s.status === 'running') { analysisActive = true; setAnalyze('Analisi in corso…', true); pollAnalysis(); }
    } catch (e) {}
  }
  if (SERVED) {
    document.body.classList.add('served');  // enables deep-dive/vote buttons
    loadServerSaved();                       // persistent saved articles from disk
    loadVotes();                             // learned feedback state
    if (analyzeBtn) {
      analyzeBtn.classList.remove('hidden');
      analyzeBtn.addEventListener('click', startAnalysis);
      resumeIfRunning();
    }
  }

  let profilePollTimer = null;
  // The dashboard is static HTML, so translate its stable UI labels in the
  // browser after reading the saved application-language preference.
  const APP_EN = {
    'Esplora': 'Explore', 'Fonti': 'Sources', 'Consumo AI': 'AI usage', 'Profilo': 'Profile',
    'Per te': 'For you', 'Tutte': 'All', 'Cerca': 'Search', 'Periodo': 'Period',
    'Tutto': 'All time', 'Oggi': 'Today', 'Ultimi 7 giorni': 'Last 7 days',
    'Tutti i segnali': 'All signals', 'Genera newsletter': 'Generate newsletter',
    'Copia HTML': 'Copy HTML', 'Aggiungi fonte': 'Add source', 'Salva profilo': 'Save profile',
    'Impostazioni AI': 'AI settings', 'Modello': 'Model',
    'Lingua': 'Language',
    'Lingua dei contenuti': 'Content language', "Lingua dell'app": 'App language',
    'Salva impostazioni AI': 'Save AI settings', 'Aggiorna catalogo modelli': 'Refresh model catalog',
    'Salva lingue': 'Save languages',
    'Connetti': 'Connect', 'Italiano': 'Italian', 'Raccontaci di te': 'Tell us about yourself',
    'Cosa vuoi ottenere': 'What do you want to achieve', 'Interessi': 'Interests',
    'Competenze da sviluppare': 'Skills to develop', 'Argomenti prioritari': 'Priority topics'
  };
  const APP_IT = Object.fromEntries(Object.entries(APP_EN).map(([it, en]) => [en, it]));
  function applyAppLanguage(language) {
    const dictionary = language === 'en' ? APP_EN : APP_IT;
    document.documentElement.lang = language === 'en' ? 'en' : 'it';
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const value = node.nodeValue.trim();
      if (dictionary[value]) node.nodeValue = node.nodeValue.replace(value, dictionary[value]);
    });
    document.querySelectorAll('[placeholder], [aria-label], [title]').forEach(element => {
      ['placeholder', 'aria-label', 'title'].forEach(attribute => {
        const value = element.getAttribute(attribute);
        if (value && dictionary[value]) element.setAttribute(attribute, dictionary[value]);
      });
    });
  }
  function splitCsv(s) { return String(s || '').split(/[,\\r\\n]+/).map(x => x.trim()).filter(Boolean); }
  function joinCsv(list) { return (Array.isArray(list) ? list : []).join(', '); }

  function fillProfileForm(p) {
    const goal = document.getElementById('profGoal');
    const interests = document.getElementById('profInterests');
    const skills = document.getElementById('profSkills');
    const keywords = document.getElementById('profKeywords');
    if (goal) goal.value = p.goal || '';
    if (interests) interests.value = joinCsv(p.interests);
    if (skills) skills.value = joinCsv(p.skills_to_grow);
    if (keywords) keywords.value = joinCsv(p.keywords_boost);
  }

  async function loadProfile() {
    let data;
    try { data = await (await fetch('/api/profile')).json(); }
    catch (e) { return; }
    const p = data.profile || {};
    const about = document.getElementById('profAboutMe');
    if (about) about.value = p.about_me || '';
    fillProfileForm(p);
    loadLlmSettings();
  }

  let modelCatalog = null;

  async function loadModelCatalog() {
    if (modelCatalog) return modelCatalog;
    try { modelCatalog = await (await fetch('/api/model-catalog')).json(); }
    catch (e) { modelCatalog = { providers: {} }; }
    return modelCatalog;
  }

  async function loadLlmSettings() {
    let data;
    try { data = await (await fetch('/api/llm-settings')).json(); }
    catch (e) { return; }
    const select = document.getElementById('llmProviderSelect');
    if (select) {
      select.innerHTML = (data.providers || []).map(p =>
        '<option value="' + esc(p.id) + '">' + esc(p.label)
        + (p.installed ? '' : ' (non installato)') + '</option>'
      ).join('');
      select.value = data.provider || 'claude';
    }
    await loadModelCatalog();
    updateModelOptions(data.provider || 'claude', data.model || '');
    const selected = (data.providers || []).find(p => p.id === data.provider) || {};
    const connection = document.getElementById('llmConnectionMsg');
    const connect = document.getElementById('llmProfileConnect');
    if (connection) {
      connection.textContent = selected.authed ? (selected.label + ' è connesso.')
        : (selected.installed ? (selected.label + ' non è connesso.') : (selected.label + ' non è installato.'));
    }
    if (connect) {
      connect.textContent = selected.authed ? ('Riconnetti ' + (selected.label || 'AI')) : ('Connetti ' + (selected.label || 'AI'));
      connect.disabled = !selected.installed;
    }
    const langSelect = document.getElementById('llmLanguageSelect');
    if (langSelect) langSelect.value = data.language || 'it';
    const uiLangSelect = document.getElementById('uiLanguageSelect');
    if (uiLangSelect) uiLangSelect.value = data.ui_language || data.language || 'it';
    applyAppLanguage(data.ui_language || 'it');
  }

  async function saveLlmSettings() {
    const msg = document.getElementById('llmSettingsMsg');
    const provider = (document.getElementById('llmProviderSelect') || {}).value;
    const model = (document.getElementById('llmModelInput') || {}).value || '';
    if (msg) { msg.textContent = 'Salvataggio…'; msg.style.color = ''; }
    try {
      const res = await fetch('/api/llm-settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: provider, model: model }),
      });
      if (res.ok) {
        if (msg) { msg.textContent = 'Impostazioni salvate.'; msg.style.color = ''; }
        loadLlmSettings();
        refreshLlmBanner();
      } else if (msg) {
        msg.textContent = 'Salvataggio non riuscito.'; msg.style.color = 'var(--error)';
      }
    } catch (e) {
      if (msg) { msg.textContent = 'Errore di connessione.'; msg.style.color = 'var(--error)'; }
    }
  }

  async function saveLanguageSettings() {
    const msg = document.getElementById('languageSettingsMsg');
    const language = (document.getElementById('llmLanguageSelect') || {}).value;
    const uiLanguage = (document.getElementById('uiLanguageSelect') || {}).value;
    if (msg) { msg.textContent = 'Salvataggio…'; msg.style.color = ''; }
    try {
      const res = await fetch('/api/llm-settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ language: language, ui_language: uiLanguage }),
      });
      if (res.ok) {
        if (msg) { msg.textContent = 'Lingue salvate.'; msg.style.color = ''; }
        applyAppLanguage(uiLanguage || 'it');
        loadLlmSettings();
      } else if (msg) {
        msg.textContent = 'Salvataggio non riuscito.'; msg.style.color = 'var(--error)';
      }
    } catch (e) {
      if (msg) { msg.textContent = 'Errore di connessione.'; msg.style.color = 'var(--error)'; }
    }
  }

  function updateModelOptions(provider, activeModel) {
    const select = document.getElementById('llmModelInput');
    if (!select) return;
    const entry = ((modelCatalog || {}).providers || {})[provider] || {};
    const models = Array.isArray(entry.models) ? entry.models : [];
    const options = ['<option value="">Predefinito del provider</option>'].concat(models.map(model =>
      '<option value="' + esc(model.id) + '">' + esc(model.label || model.id) + '</option>'
    ));
    if (activeModel && !models.some(model => model.id === activeModel)) {
      options.push('<option value="' + esc(activeModel) + '">' + esc(activeModel) + ' (catalogo precedente)</option>');
    }
    select.innerHTML = options.join('');
    select.value = activeModel || '';
  }

  async function refreshModelCatalog() {
    const button = document.getElementById('llmModelRefresh');
    const msg = document.getElementById('llmSettingsMsg');
    if (button) button.disabled = true;
    if (msg) { msg.textContent = 'Aggiornamento catalogo…'; msg.style.color = ''; }
    try {
      const response = await fetch('/api/model-catalog/refresh', { method: 'POST' });
      const data = await response.json();
      if (!response.ok) throw new Error('refresh failed');
      modelCatalog = data;
      const provider = (document.getElementById('llmProviderSelect') || {}).value || 'claude';
      updateModelOptions(provider, (document.getElementById('llmModelInput') || {}).value || '');
      if (msg) msg.textContent = 'Catalogo aggiornato.';
    } catch (e) {
      if (msg) { msg.textContent = 'Aggiornamento non riuscito: resta disponibile l’ultimo catalogo valido.'; msg.style.color = 'var(--error)'; }
    } finally { if (button) button.disabled = false; }
  }

  const llmProviderSelect = document.getElementById('llmProviderSelect');
  if (llmProviderSelect) llmProviderSelect.addEventListener('change', () => {
    const modelInput = document.getElementById('llmModelInput');
    if (modelInput) modelInput.value = '';
    updateModelOptions(llmProviderSelect.value, '');
  });

  async function saveProfile() {
    const msg = document.getElementById('profMsg');
    const payload = { profile: {
      about_me: (document.getElementById('profAboutMe') || {}).value || '',
      goal: (document.getElementById('profGoal') || {}).value || '',
      interests: splitCsv((document.getElementById('profInterests') || {}).value),
      skills_to_grow: splitCsv((document.getElementById('profSkills') || {}).value),
      keywords_boost: splitCsv((document.getElementById('profKeywords') || {}).value),
    } };
    if (msg) { msg.textContent = 'Salvataggio…'; msg.style.color = ''; }
    try {
      const res = await fetch('/api/profile', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const body = await res.json();
      if (res.ok && body.ok) {
        if (msg) { msg.textContent = "Profilo salvato. Rilancia l'analisi per vederne l'effetto."; msg.style.color = ''; }
        fillProfileForm(body.profile || {});
        refreshProfileBanner();
      } else {
        if (msg) { msg.textContent = 'Salvataggio non riuscito.'; msg.style.color = 'var(--error)'; }
      }
    } catch (e) {
      if (msg) { msg.textContent = 'Errore di connessione.'; msg.style.color = 'var(--error)'; }
    }
  }

  function resetExpandButton() {
    const btn = document.getElementById('profExpand');
    const label = document.getElementById('profExpandLabel');
    if (btn) btn.disabled = false;
    if (label) label.textContent = "Compila i campi con l’AI";
  }

  async function pollExpand() {
    let s;
    try { s = await (await fetch('/api/profile/expand/status')).json(); }
    catch (e) { return; }
    if (s.state === 'running') return;
    clearInterval(profilePollTimer); profilePollTimer = null;
    resetExpandButton();
    const msg = document.getElementById('profExpandMsg');
    if (s.state === 'done' && s.proposal) {
      fillProfileForm(s.proposal);
      if (msg) { msg.textContent = 'Proposta generata: controlla i campi qui sotto e premi «Salva profilo».'; msg.style.color = ''; }
    } else if (msg) {
      msg.textContent = s.error || 'Espansione non riuscita.'; msg.style.color = 'var(--error)';
    }
  }

  async function expandProfile() {
    const about = (document.getElementById('profAboutMe') || {}).value || '';
    const msg = document.getElementById('profExpandMsg');
    const btn = document.getElementById('profExpand');
    const label = document.getElementById('profExpandLabel');
    if (!about.trim()) {
      if (msg) { msg.textContent = 'Scrivi prima qualche riga su di te.'; msg.style.color = 'var(--error)'; }
      return;
    }
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'Generazione in corso…';
    if (msg) { msg.textContent = ''; msg.style.color = ''; }
    try {
      const res = await fetch('/api/profile/expand', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ about_me: about }),
      });
      if (res.status === 429) {
        if (msg) { msg.textContent = 'Espansione già in corso, riprova tra poco.'; msg.style.color = 'var(--error)'; }
        resetExpandButton();
        return;
      }
      if (!res.ok) {
        if (msg) { msg.textContent = 'Espansione non riuscita.'; msg.style.color = 'var(--error)'; }
        resetExpandButton();
        return;
      }
    } catch (e) {
      if (msg) { msg.textContent = 'Errore di connessione.'; msg.style.color = 'var(--error)'; }
      resetExpandButton();
      return;
    }
    if (profilePollTimer) clearInterval(profilePollTimer);
    profilePollTimer = setInterval(pollExpand, 1500);
  }

  function refreshProfileBanner() {
    if (!SERVED) return;
    const banner = document.getElementById('profileBanner');
    if (!banner) return;
    fetch('/api/profile').then(r => r.json()).then(data => {
      if (data.has_profile) { banner.className = 'mm-card llm-banner hidden'; banner.innerHTML = ''; return; }
      banner.className = 'mm-card llm-banner';
      banner.innerHTML = '<span>Non hai ancora un profilo: la sezione «Per te» resta vuota. '
        + 'Scrivi due righe su di te per personalizzare i segnali.</span>'
        + '<div class="llm-actions"><button type="button" id="profileBannerLink" class="mm-btn mm-btn-ghost mm-btn-mini">Vai al profilo</button></div>';
      const link = document.getElementById('profileBannerLink');
      if (link) link.onclick = () => (window.dashboardSetView ? window.dashboardSetView('profile') : null);
    }).catch(() => {});
  }
  refreshProfileBanner();

  function refreshTranscriptBanner() {
    if (!SERVED) return;
    const banner = document.getElementById('transcriptBanner');
    if (!banner) return;
    fetch('/api/transcript-status').then(r => r.json()).then(data => {
      if (!data.blocked) { banner.className = 'mm-card llm-banner hidden'; banner.innerHTML = ''; return; }
      const when = new Date(data.resume_at).toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
      banner.className = 'mm-card llm-banner';
      banner.innerHTML = '<span>YouTube sta bloccando le trascrizioni: fino alle ' + esc(when)
        + ' i video usano il riassunto del feed invece del testo parlato.</span>';
    }).catch(() => {});
  }
  refreshTranscriptBanner();

  if (sourcesDate) sourcesDate.addEventListener('change', () => renderSources(sourcesDate.value));
  populateSourcesDates();

  (function llmBannerInit() {
    if (!SERVED) return;
    const banner = document.getElementById('llmBanner');
    if (!banner) return;
    let authTimer = null;

    function show(html, ok) { banner.className = 'mm-card llm-banner' + (ok ? ' ok' : ''); banner.innerHTML = html; }
    function hide() { banner.className = 'mm-card llm-banner hidden'; banner.innerHTML = ''; }
    function bind(id, fn) { const el = document.getElementById(id); if (el) el.onclick = fn; }

    function providerLabel(data) {
      const found = (data.providers || []).find(p => p.id === data.provider);
      return (found && found.label) || 'AI';
    }

    async function refresh() {
      let s, settings;
      try {
        [s, settings] = await Promise.all([
          fetch('/api/llm-status').then(r => r.json()),
          fetch('/api/llm-settings').then(r => r.json()),
        ]);
      }
      catch (e) { return; }
      const label = providerLabel(settings);
      let msg, connect = false;
      if (s.ok) { hide(); return; }
      else if (s.reason === 'logged_out') { msg = "L'analisi AI è disattiva: riconnetti " + esc(label) + " dal Profilo."; connect = true; }
      else if (s.reason === 'quota') { msg = 'La quota di ' + esc(label) + ' è esaurita: le analisi AI riprendono dopo il reset. <code>' + esc(s.detail || '') + '</code>'; }
      else if (s.reason === 'not_installed') { msg = 'CLI <code>' + esc(label) + '</code> non trovata: configura un altro provider dal Profilo.'; }
      else { msg = esc(label) + ' non raggiungibile: ' + esc(s.detail || 'errore sconosciuto') + '.'; }
      show('<span>' + msg + '</span><div class="llm-actions">'
        + (connect ? '<button type="button" id="llmConnect" class="mm-btn mm-btn-ghost mm-btn-mini">Riconnetti ' + esc(label) + '</button>' : '')
        + '<button type="button" id="llmProfile" class="mm-btn mm-btn-ghost mm-btn-mini">Apri Profilo</button>'
        + '<button type="button" id="llmRecheck" class="mm-btn mm-btn-ghost mm-btn-mini">Ricontrolla</button></div>', s.ok);
      bind('llmRecheck', refresh);
      bind('llmConnect', connect_);
      bind('llmProfile', () => (window.dashboardSetView ? window.dashboardSetView('profile') : null));
    }

    async function connect_() {
      let settings;
      try { settings = await (await fetch('/api/llm-settings')).json(); } catch (e) { settings = { providers: [] }; }
      const label = providerLabel(settings);
      show('<span>Avvio dell’accesso a ' + esc(label) + '…</span>', false);
      try { await fetch('/api/llm-auth/start', { method: 'POST' }); } catch (e) {}
      if (authTimer) clearInterval(authTimer);
      authTimer = setInterval(pollAuth, 2000);
      pollAuth();
    }

    async function pollAuth() {
      let s;
      try { s = await (await fetch('/api/llm-auth/status')).json(); }
      catch (e) { return; }
      let settings;
      try { settings = await (await fetch('/api/llm-settings')).json(); } catch (e) { settings = { providers: [] }; }
      const label = providerLabel(settings);
      if (s.state === 'pending') {
        const link = s.url ? ' <a href="' + safeHref(s.url) + '" target="_blank" rel="noopener">Non si è aperto? Apri qui</a>' : '';
        const deviceCode = s.device_code ? ' Inserisci il codice: <code>' + esc(s.device_code) + '</code>.' : '';
        show('<span>Ho aperto il browser per l’accesso a ' + esc(label) + '. Completa l’autorizzazione lì.' + deviceCode + link + '</span>', false);
        return;
      }
      clearInterval(authTimer); authTimer = null;
      if (s.state === 'success') {
        hide();
      } else {
        const why = s.state === 'timeout' ? 'Tempo scaduto.' : (esc(s.message) || 'Accesso non riuscito.');
        show('<span>' + why + '</span><div class="llm-actions"><button type="button" id="llmRetry" class="mm-btn mm-btn-ghost mm-btn-mini">Riprova</button></div>', false);
        bind('llmRetry', connect_);
      }
    }

    window.refreshLlmBanner = refresh;
    window.connectConfiguredLlm = connect_;
    refresh();
  })();

  if (SERVED) {
    fetch('/api/llm-settings').then(r => r.json())
      .then(settings => applyAppLanguage(settings.ui_language || 'it'))
      .catch(() => {});
  }
  initTheme();
  render();
</script>"""

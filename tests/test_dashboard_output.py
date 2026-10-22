"""Tests for local dashboard output (Brief / Esplora UX)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from datetime import date
from pathlib import Path

from src.outputs.dashboard_output import (
    DashboardOutput,
    _is_mainstream,
    _render_brief_references,
    _safe_href,
    _sort_recent_items,
)
from src.storage.json_storage import save_json


ITEMS = [
    {
        "title": "AI dashboard item",
        "url": "https://example.com/source",
        "company": "Example",
        "source_name": "Example Source",
        "category": "general_ai",
        "published_at": "2026-05-05",
        "summary": "A concise summary.",
        "key_points": ["First useful point.", "Second useful point."],
        "content_quality": "title_only",
        "relevance_score": 3,
        "signal_score": 58,
        "primary_topic": "Foundation Models",
        "secondary_topic": "",
        "why_it_matters": "This affects model selection and roadmap timing.",
        "possible_action": "Add this update to the next model evaluation backlog.",
        "summary_quality": "medium",
        "date_quality": "unknown",
        "personal_relevance_score": 30,
        "personal_match": ["agent"],
    }
]


class SafeHrefTest(unittest.TestCase):
    def test_allows_http_and_https(self) -> None:
        self.assertEqual(_safe_href("https://example.com/x"), "https://example.com/x")
        self.assertEqual(_safe_href("http://example.com/x"), "http://example.com/x")

    def test_rejects_javascript_scheme(self) -> None:
        self.assertEqual(_safe_href("javascript:alert(1)"), "#")

    def test_rejects_data_scheme(self) -> None:
        self.assertEqual(_safe_href("data:text/html,<script>alert(1)</script>"), "#")

    def test_rejects_empty_or_missing_url(self) -> None:
        self.assertEqual(_safe_href(""), "#")


class SafeJsonScriptEscapingTest(unittest.TestCase):
    def test_title_with_comment_open_does_not_break_script_tag_parsing(self) -> None:
        # A title containing "<!--" would otherwise flip the HTML parser into
        # script-data-double-escaped state, so the real closing </script> for
        # news-data no longer terminates the tag it belongs to.
        tricky_item = {**ITEMS[0], "title": "Breaking <!--<script>alert(1)</script> news"}
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([tricky_item])

        news_data_start = html.index('<script id="news-data"')
        news_data_end = html.index("</script>", news_data_start)
        news_data_block = html[news_data_start:news_data_end]

        self.assertNotIn("<!--", news_data_block)
        self.assertIn("\\u003c", news_data_block)
        # And the real closing tag must still be intact and immediately follow.
        self.assertTrue(html[news_data_end:].startswith("</script>"))

    def test_title_with_closing_script_tag_is_escaped(self) -> None:
        tricky_item = {**ITEMS[0], "title": "Ends with </script><script>alert(1)</script>"}
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([tricky_item])

        news_data_start = html.index('<script id="news-data"')
        news_data_end = html.index("</script>", news_data_start)
        news_data_block = html[news_data_start:news_data_end]

        self.assertNotIn("</script", news_data_block)
        self.assertNotIn("<script>alert", news_data_block)


class DashboardCardHrefTest(unittest.TestCase):
    def test_javascript_url_in_item_renders_as_hash_not_executable_href(self) -> None:
        malicious_item = {**ITEMS[0], "url": "javascript:alert(document.cookie)"}
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([malicious_item])
        self.assertNotIn('href="javascript:', html)

    def test_client_side_safe_href_helper_is_used_for_dynamic_links(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("function safeHref(", html)
        # Every JS-rendered <a href=...> for feed-supplied data must go
        # through safeHref(), not the plain esc() entity-encoder.
        self.assertIn("safeHref(item.url)", html)
        self.assertIn("safeHref(it.url)", html)
        self.assertIn("safeHref(feedUrl)", html)
        self.assertIn("safeHref(i.url)", html)


class DashboardOutputTest(unittest.TestCase):
    def test_generates_normal_and_preview_dashboard_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            output = DashboardOutput(output_dir=tmp_dir, date=date(2026, 5, 5))

            normal = output.generate(ITEMS, preview=False)
            preview = output.generate(ITEMS, preview=True)

            self.assertEqual(normal, Path(tmp_dir) / "index.html")
            self.assertEqual(preview, Path(tmp_dir) / "preview_index.html")
            self.assertTrue(normal.exists())
            self.assertTrue(preview.exists())

    def test_html_contains_core_content_and_only_local_brand_image(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn('href="https://example.com/source"', html)
        self.assertIn("AI dashboard item", html)
        self.assertIn("Example Source", html)
        self.assertIn("5 mag 2026", html)  # published_at formatted, always shown
        self.assertIn("A concise summary.", html)
        # The only <img> is the local, brand-neutral app mark; no remote or
        # tracking images, and no personal branding baked into the page.
        img_srcs = re.findall(r'<img[^>]*src="([^"]+)"', html)
        self.assertEqual(img_srcs, ["../../design/mm/app-logo.svg"])
        self.assertNotIn("Marco Manfrini", html)

    def test_html_uses_mm_design_tokens_no_cdn(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        # No utility-framework CDN, no Google Fonts, no remaining indigo brand color.
        self.assertNotIn("cdn.tailwindcss.com", html)
        self.assertNotIn("fonts.googleapis.com", html)
        self.assertNotIn("fonts.gstatic.com", html)
        self.assertNotIn("#4F46E5", html)
        self.assertNotIn("#4f46e5", html.lower())
        # tokens.css + ui-kit.css are both linked with a path that resolves from
        # file:// (outputs/dashboard/*.html -> repo root) and via the local
        # server (which serves /design/mm/* -- see src/server.py).
        self.assertIn('<link rel="stylesheet" href="../../design/mm/tokens.css">', html)
        self.assertIn('<link rel="stylesheet" href="../../design/mm/ui-kit.css">', html)
        self.assertIn('src="../../design/mm/app-logo.svg"', html)
        # Page-level resets (background, base font) now come from the kit's
        # .mm-app, and the dashboard shell/header/stats/tables use mm- components.
        self.assertIn('<body class="mm-app">', html)
        for component in ("mm-shell", "mm-rail", "mm-content", "mm-wrap",
                          "mm-page-head", "mm-stat-row", "mm-stat-value",
                          "mm-tabs", "mm-tab", "mm-card", "mm-table"):
            self.assertIn(component, html, f"missing kit component: {component}")
        # The remaining local product CSS references MM token names, never
        # reinvented local hex values.
        for token in (
            "var(--surface)", "var(--surface-2)",
            "var(--border)", "var(--border-strong)",
            "var(--ink)", "var(--ink-dim)", "var(--ink-faint)",
            "var(--primary)", "var(--primary-soft)",
            "var(--accent)", "var(--accent-soft)", "var(--accent-text)",
            "var(--ok)", "var(--ok-soft)", "var(--error)", "var(--error-soft)",
            "var(--space-", "var(--radius-",
        ):
            self.assertIn(token, html, f"missing token reference: {token}")

    def test_html_supports_light_dark_theme(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn('[data-theme="dark"]', html)
        self.assertIn("ai-news-theme", html)
        self.assertIn("themeToggle", html)
        self.assertIn("prefers-color-scheme", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is required to parse generated dashboard JavaScript")
    def test_generated_dashboard_javascript_parses(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        script = re.search(r"(const newsItems.*)\n</script>", html, re.S)
        self.assertIsNotNone(script)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
            handle.write(script.group(1))  # type: ignore[union-attr]
            script_path = Path(handle.name)
        try:
            result = subprocess.run(
                [shutil.which("node") or "node", "--check", str(script_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            script_path.unlink(missing_ok=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_html_has_llm_auth_banner_and_poller(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn('id="llmBanner"', html)
        self.assertIn("/api/llm-status", html)
        self.assertIn('id="llmProfileConnect"', html)
        self.assertIn("connectConfiguredLlm", html)
        self.assertIn('id="llmModelInput" class="mm-select"', html)
        self.assertIn('id="llmModelRefresh"', html)
        self.assertIn("updateModelOptions", html)
        self.assertIn("/api/llm-auth/start", html)
        self.assertIn("/api/llm-auth/status", html)

    def test_html_has_brief_explore_navigation(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        # Sidebar entries.
        self.assertIn(">Brief<", html)
        self.assertIn(">Esplora<", html)
        self.assertIn(">Salvati<", html)
        self.assertIn(">Consumo AI<", html)
        self.assertIn('id="usageView"', html)
        self.assertIn(">Fonti<", html)
        self.assertIn('id="sourcesView"', html)
        self.assertIn(">Newsletter<", html)
        self.assertIn('id="newsletterView"', html)
        self.assertIn("/api/newsletter", html)
        self.assertIn('data-preset="7"', html)

    def test_brief_omits_fixed_banners_and_labels_last_run(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertNotIn("Local-first. Nessun backend o login", html)
        self.assertNotIn('<div class="focus brief-summary">', html)
        self.assertIn("Segnali ultimi 7 giorni", html)
        self.assertIn("Nessun run registrato", html)

    def test_usage_table_has_provider_and_model_columns(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn("providerUsageLabel", html)
        self.assertIn("modelUsageLabel", html)
        self.assertIn("<th>Provider</th><th>Modello</th><th>Accesso</th>", html)

    def test_sources_page_embeds_reports_and_renders_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            (base / "2026-05-05").mkdir()
            save_json(
                base / "2026-05-05" / "collection_report.json",
                {"per_source": [
                    {"source_id": "openai_news", "source_name": "OpenAI News", "source_group": "foundation_models",
                     "found": 8, "accepted": 2, "status": "ok", "error": ""},
                    {"source_id": "amplitude_blog", "source_name": "Amplitude", "source_group": "product_data",
                     "found": 0, "accepted": 0, "status": "error", "error": "404"},
                ]},
            )
            html = DashboardOutput(data_dir=base, date=date(2026, 5, 5)).render_html(ITEMS)
            self.assertIn("function renderSources", html)
            self.assertIn("OpenAI News", html)  # embedded per-source data
            self.assertIn("Amplitude", html)
            self.assertIn('"status": "error"', html)
            # The run stats are still embedded, but the table is now driven by
            # the catalogue and joins these in (see SourcesCatalogTest below).
            self.assertIn('id="sources-catalog"', html)
        self.assertNotIn("Top Signals Today", html)
        self.assertNotIn("High Relevance", html)
        self.assertNotIn("Worth Watching", html)

        # View wiring.
        self.assertIn("window.dashboardPendingView = 'brief'", html)
        self.assertIn("window.dashboardSetView = function", html)
        self.assertIn("window.dashboardRenderView = setView", html)

        # Brief sections.
        self.assertIn("Le big dell'AI", html)
        self.assertIn('data-tab="perte" aria-label="Per te — notizie più affini al tuo profilo"', html)
        self.assertIn("tab-info", html)
        self.assertIn("Questa selezione dà più peso", html)
        self.assertIn("Opinioni e video", html)
        self.assertIn("Strumenti &amp; API", html)

    def test_sources_table_has_per_source_rerun_button(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("data-rerun-source", html)
        self.assertIn("startSourceAnalysis", html)
        self.assertIn("src-rerun-btn", html)

    def test_sources_table_has_edit_and_remove_buttons(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("data-edit-source", html)
        self.assertIn("data-remove-source", html)
        self.assertIn("startEditSource", html)
        self.assertIn("removeSource", html)

    def test_source_form_has_category_notes_and_enabled_fields(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn('id="srcCategory"', html)
        self.assertIn('id="srcNotes"', html)
        self.assertIn('id="srcEnabled"', html)
        self.assertIn('id="srcCancelEdit"', html)

    def test_sources_table_shows_operational_notes(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn("<th>Nota</th>", html)
        self.assertIn("src-note", html)

    def test_html_has_lean_cards_visible_insight_and_minimal_filters(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        # Lean cards: source anchor, importance, personal badge, always-on insight.
        self.assertIn('title="Importanza 58"', html)
        self.assertIn("Per te 30", html)
        self.assertIn("Perché conta", html)
        self.assertIn("Azione possibile", html)
        self.assertNotIn("data-expand", html)
        self.assertNotIn("Summary medium", html)
        self.assertNotIn("Signal 58", html)

        # Minimal filter set; removed controls are gone.
        self.assertIn("searchInput", html)
        self.assertIn("signalFilter", html)
        self.assertIn("dateRangeFilter", html)
        self.assertNotIn("qualityFilter", html)
        self.assertNotIn("topicFilter", html)
        self.assertNotIn("trendFilter", html)
        self.assertNotIn("compactToggle", html)

        # Behaviour wiring preserved.
        self.assertIn("localStorage", html)
        self.assertIn("savedKey", html)
        self.assertIn("compareItems", html)
        self.assertIn("event.target.closest('[data-view]')", html)
        self.assertIn("event.target.closest('[data-save-url]')", html)
        self.assertIn("event.target.closest('[data-chip]')", html)

    def test_section_briefs_are_rendered_per_tab(self) -> None:
        briefs = {
            "big": "Brief delle big.",
            "perte": "Brief product e data.",
            "opinion": "Brief opinioni.",
            "tools": "Brief strumenti.",
        }
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS, section_briefs=briefs)
        self.assertIn("section-brief", html)
        self.assertIn("Brief delle big.", html)
        self.assertIn("Brief product e data.", html)
        self.assertIn("Brief opinioni.", html)
        self.assertIn("Brief strumenti.", html)

    def test_brief_includes_compact_links_to_its_articles(self) -> None:
        references = _render_brief_references(ITEMS)

        self.assertIn("Articoli di riferimento:", references)
        self.assertIn('href="https://example.com/source"', references)
        self.assertIn("AI dashboard item", references)

    def test_brief_world_section_sorts_by_signal_then_quality_then_date(self) -> None:
        items = [
            {
                **ITEMS[0],
                "title": "Older full but less relevant",
                "url": "https://example.com/older",
                "published_at": "2026-05-01",
                "content_quality": "full",
                "relevance_score": 4,
                "signal_score": 70,
            },
            {
                **ITEMS[0],
                "title": "Top relevant excerpt",
                "url": "https://example.com/top",
                "published_at": "2026-05-02",
                "content_quality": "excerpt",
                "relevance_score": 5,
                "signal_score": 82,
            },
            {
                **ITEMS[0],
                "title": "Newest weak full",
                "url": "https://example.com/newest",
                "published_at": "2026-05-06",
                "content_quality": "full",
                "relevance_score": 3,
                "signal_score": 52,
            },
        ]

        html = DashboardOutput(date=date(2026, 5, 5)).render_html(items)

        self.assertLess(html.index("Top relevant excerpt"), html.index("Older full but less relevant"))
        self.assertLess(html.index("Older full but less relevant"), html.index("Newest weak full"))

    def test_non_editorial_lists_sort_newest_first(self) -> None:
        items = [
            {"title": "Senza data", "published_at": ""},
            {"title": "Meno recente", "published_at": "2026-05-01"},
            {"title": "Più recente", "published_at": "2026-05-06"},
        ]

        self.assertEqual(
            [item["title"] for item in _sort_recent_items(items)],
            ["Più recente", "Meno recente", "Senza data"],
        )

    def test_browser_uses_date_order_outside_brief(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn("function compareByDate", html)
        self.assertIn(".sort(compareByDate);", html)

    def test_missing_published_date_falls_back_to_collected_at(self) -> None:
        item = {
            **ITEMS[0],
            "title": "Video senza data di pubblicazione",
            "url": "https://www.youtube.com/watch?v=zzz",
            "published_at": "",
            "collected_at": "2026-06-09T10:00:00+00:00",
        }
        html = DashboardOutput(date=date(2026, 6, 15)).render_html([item])
        # Check the server-rendered cards, not the JS (which has 'data n/d' as a runtime fallback).
        body = html.split('<script id="news-data"')[0]
        self.assertIn("9 giu 2026", body)
        self.assertNotIn("data n/d", body)

    def test_deepdive_button_and_partial_badge(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("Approfondisci", html)
        self.assertIn("data-deepdive-url", html)
        self.assertIn("deepdive-panel", html)
        self.assertIn("/api/deep-dive", html)
        # ITEMS[0] is title_only → partial badge shown.
        self.assertIn("Contenuto parziale", html)

    def test_deepdive_panel_uses_specific_partial_reason_when_present(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        # renderDeepDive() must prefer r.partial_note (set by deep_dive.py when
        # a video has no captions or a fetch failed) over the generic message.
        self.assertIn("r.partial_note ||", html)

    def test_deepdive_panel_renders_transcript_toggle_when_source_text_present(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("data-dd-transcript-toggle", html)
        self.assertIn("data-dd-transcript", html)
        self.assertIn("Vedi trascrizione", html)

    def test_full_content_item_has_no_partial_badge_in_its_card(self) -> None:
        item = {**ITEMS[0], "content_quality": "full"}
        body = DashboardOutput(date=date(2026, 5, 5)).render_html([item]).split('<script id="news-data"')[0]
        self.assertNotIn("Contenuto parziale", body)

    def test_usage_table_shows_kind_column(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("<th>Tipo</th>", html)
        self.assertIn("Insight notizie", html)
        self.assertIn("Approfondimento", html)
        self.assertIn("kindLabel", html)

    def test_vote_buttons_and_source_form_present(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)
        self.assertIn("data-vote-url", html)
        self.assertIn("/api/feedback", html)
        self.assertIn("src-form", html)
        self.assertIn("/api/sources", html)
        self.assertIn("/api/source-news", html)
        self.assertIn("data-source-news", html)

    def test_mainstream_detection_by_company(self) -> None:
        self.assertTrue(_is_mainstream({"company": "OpenAI"}))
        self.assertTrue(_is_mainstream({"company": "Google DeepMind"}))
        self.assertFalse(_is_mainstream({"company": "Suno"}))
        self.assertFalse(_is_mainstream({"company": ""}))

    def test_generate_from_file_loads_news_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            news_path = tmp_path / "2026-05-05" / "news.json"
            save_json(news_path, ITEMS)
            output = DashboardOutput(
                data_dir=tmp_path,
                output_dir=tmp_path / "dashboard",
                date=date(2026, 5, 5),
            )

            generated_path = output.generate_from_file(preview=False)

            self.assertEqual(generated_path, tmp_path / "dashboard" / "index.html")
            self.assertIn("AI dashboard item", generated_path.read_text(encoding="utf-8"))


class ProfileViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

    def test_rail_has_a_profile_nav_entry(self) -> None:
        self.assertIn('data-view="profile"', self.html)
        self.assertIn(">Profilo<", self.html)

    def test_profile_view_has_the_editable_fields(self) -> None:
        for field_id in (
            "profAboutMe", "profGoal", "profInterests", "profSkills",
            "profKeywords", "profExpand", "profSave",
        ):
            self.assertIn(f'id="{field_id}"', self.html)

    def test_profile_form_is_gated_the_same_way_as_the_sources_form(self) -> None:
        # Profile keeps the static-export gate, with its own spacious layout.
        self.assertIn('id="profileView"', self.html)
        profile_section = self.html[self.html.index('id="profileView"'):]
        self.assertIn("profile-form", profile_section[:600])
        self.assertIn("body.served .profile-form { display:block; }", self.html)

    def test_profile_js_wires_up_expand_and_save_and_a_dedicated_banner(self) -> None:
        self.assertIn("function loadProfile()", self.html)
        self.assertIn("function saveProfile()", self.html)
        self.assertIn("function expandProfile()", self.html)
        self.assertIn("/api/profile", self.html)
        self.assertIn("/api/profile/expand", self.html)
        self.assertIn('id="profileBanner"', self.html)
        self.assertIn("function refreshProfileBanner()", self.html)


class SourcesCatalogTest(unittest.TestCase):
    """The Fonti page lists the catalogue, not just what a run produced.

    Regression: the table was built purely from
    `data/<date>/collection_report.json`, so a fresh install showed
    "Nessun dato per-fonte per questo run" and none of the configured
    sources — you could not discover, search or enable anything before
    running a collection first.
    """

    def test_catalog_is_embedded_even_with_no_runs_at_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            # No dated folders at all: the fresh-install case.
            html = DashboardOutput(data_dir=Path(tmp_dir), date=date(2026, 5, 5)).render_html([])

        blob = re.search(r'id="sources-catalog" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(blob)
        catalog = json.loads(blob.group(1).replace("\\u003c", "<"))
        self.assertGreater(len(catalog), 50, "the shipped catalogue should be embedded")
        self.assertTrue(all("id" in src and "url" in src for src in catalog))

    def test_render_joins_catalog_with_run_stats_not_the_other_way_round(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        # One row per catalogue source, run columns filled in when present.
        self.assertIn("function visibleSources", html)
        self.assertIn("sourceCatalog()", html)
        self.assertIn("stats[src.id] || null", html)

    def test_search_and_filters_are_present(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        for element_id in ("srcSearch", "srcStatusFilter", "srcCategoryChips", "srcCount"):
            self.assertIn(f'id="{element_id}"', html)
        self.assertIn("function sourceSearchText", html)
        self.assertIn("data-src-chip", html)

    def test_source_form_is_collapsed_and_gated_on_served(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        self.assertIn('<div class="src-form" id="srcForm">', html)
        self.assertNotRegex(html, r'class="src-form[^"]*\bopen\b')
        self.assertIn('body.served .src-form.open { display:flex; }', html)
        self.assertIn('id="srcFormToggle"', html)
        self.assertIn('aria-expanded="false" aria-controls="srcForm"', html)
        self.assertIn('.src-form-toggle { display:none; }', html)
        self.assertIn('body.served .src-form-toggle', html)

    def test_run_selector_defaults_to_catalogue(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        self.assertIn('<option value="">Tutte le fonti</option>', html)
        self.assertIn("renderSources(sourcesDate ? sourcesDate.value : '');", html)

    def test_run_mode_filters_to_collected_sources(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        self.assertIn('.filter(row => !day || row.stat)', html)
        self.assertIn('if (errOpt) errOpt.disabled = !day;', html)
        self.assertIn('Nessuna fonte raccolta in questo run.', html)

    def test_catalogue_and_run_headers_match_history_colspan(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        self.assertIn(
            '<th>Fonte</th><th>Categoria</th><th>Stato</th><th>Nota</th><th>Feed</th>'
            '<th>Ultima notizia</th><th></th>', html)
        self.assertIn(
            '<th>Fonte</th><th>Categoria</th><th>Stato</th><th>Nota</th><th>Feed</th>'
            '<th class="mm-num">Trovate</th><th class="mm-num">Nuove</th>'
            '<th>Ultima notizia</th><th>Esito run</th><th></th>', html)
        self.assertIn('const columnCount = day ? 10 : 7;', html)
        self.assertIn("""colspan="' + columnCount + '" """.strip(), html)

    def test_disabled_sources_are_listed_too(self) -> None:
        # The whole point: you must be able to find and enable a source that
        # has never been collected.
        html = DashboardOutput(date=date(2026, 5, 5)).render_html([])
        blob = re.search(r'id="sources-catalog" type="application/json">(.*?)</script>', html, re.S)
        catalog = json.loads(blob.group(1).replace("\\u003c", "<"))
        self.assertTrue(any(not src.get("enabled") for src in catalog))
        self.assertTrue(any(src.get("enabled") for src in catalog))


if __name__ == "__main__":
    unittest.main()


class SingleEscaperTest(unittest.TestCase):
    """One escaper, one guarantee.

    The page used to define `esc` twice: the top-level one entity-encodes
    quotes, while a second, local one round-tripped through
    `textContent`/`innerHTML`, which leaves `"` and `'` intact. The local one
    was used in attribute position for the login URL, which is scraped from
    the CLI's PTY output by a regex whose `\\S+` matches a quote.
    """

    def test_page_defines_exactly_one_esc_helper(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertEqual(html.count("function esc("), 1)

    def test_login_fallback_link_goes_through_safe_href(self) -> None:
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertIn("safeHref(s.url)", html)
        self.assertNotIn("' + esc(s.url) + '", html)

    def test_no_raw_interpolation_into_an_html_attribute(self) -> None:
        # The sources date dropdown escaped its option label but not its
        # value="..."; it was the last unescaped attribute in the page.
        html = DashboardOutput(date=date(2026, 5, 5)).render_html(ITEMS)

        self.assertNotIn("""'<option value="' + d + '">""", html)

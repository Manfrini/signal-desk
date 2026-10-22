"""The AI-written brief must survive a restart, not just a same-day one.

The brief is stored per run under `data/<date>/`, and nothing deletes it. But
the page is rebuilt every time `serve` starts, and that rebuild used to drop
it: the stored texts were never passed through, and the loader only ever
looked at *today's* folder. Collection runs once a day, so from the day after
a run the page silently fell back to the deterministic summary and to generic
section descriptions -- a poorer page, with no visible gap to notice.
"""

from __future__ import annotations

import json
from datetime import date
from html import escape

import pytest

from src.outputs.dashboard_output import DashboardOutput
from src.server import refresh_dashboard_shell

RUN_DAY = "2026-09-18"
SECTION_BRIEF = "Le big dell'AI stanno spostando l'attenzione sui processi operativi."
HEADLINE = "Una headline scritta dall'AI, non ricalcolabile dagli articoli"

ITEM = {
    "title": "Un articolo",
    "url": "https://example.com/a",
    "source_name": "Example",
    "company": "Example",
    "category": "general_ai",
    "summary": "Riassunto.",
    "relevance_score": 4,
    "signal_score": 90,
    "content_quality": "full",
    "published_at": f"{RUN_DAY}T10:00:00+00:00",
    "collected_at": f"{RUN_DAY}T10:00:00+00:00",
    "is_valid": True,
}


@pytest.fixture()
def stored_run(tmp_path):
    """A completed run from a previous day, with its brief on disk."""
    run_dir = tmp_path / "data" / RUN_DAY
    run_dir.mkdir(parents=True)
    (run_dir / "news.json").write_text(json.dumps([ITEM]), encoding="utf-8")
    (run_dir / "daily_intelligence_summary.json").write_text(
        json.dumps({"headline": HEADLINE, "main_shift": "Uno spostamento."}), encoding="utf-8"
    )
    (run_dir / "section_briefs.json").write_text(
        json.dumps({"big": SECTION_BRIEF}), encoding="utf-8"
    )
    return tmp_path / "data"


def test_loads_the_brief_from_the_most_recent_run_not_only_today(stored_run):
    # "Today" is four days after the run, as on any day you do not collect.
    output = DashboardOutput(data_dir=stored_run, date=date(2026, 9, 22))

    html = output.render_html([ITEM])

    # Escaped, because that is how it reaches the page.
    assert escape(SECTION_BRIEF) in html
    assert escape(HEADLINE) in html


def test_restarting_the_server_keeps_the_brief_on_the_page(stored_run, tmp_path):
    dashboard = tmp_path / "outputs" / "index.html"
    dashboard.parent.mkdir(parents=True)
    # The page as a completed run left it: items embedded, brief rendered.
    first = DashboardOutput(
        data_dir=stored_run, output_dir=dashboard.parent, date=date(2026, 9, 18)
    )
    first.generate([ITEM], daily_summary={"headline": HEADLINE}, section_briefs={"big": SECTION_BRIEF})
    assert escape(SECTION_BRIEF) in dashboard.read_text(encoding="utf-8")

    # Now the server restarts on a later day and rebuilds the shell.
    assert refresh_dashboard_shell(dashboard, data_dir=stored_run, date=date(2026, 9, 22))

    assert escape(SECTION_BRIEF) in dashboard.read_text(encoding="utf-8")

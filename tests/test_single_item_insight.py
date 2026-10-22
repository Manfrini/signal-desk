"""Analysing one article on demand.

The batch analysis only covers items from the last 24 hours, so an article
that ages out without an insight can never get one: the placeholder told the
reader to re-run the analysis, and re-running skipped exactly those items.

Rather than widen the batch — which would spend on everything, unasked — the
card offers to analyse that one article. The reader decides whether it is
worth it, on the article in front of them.
"""

from __future__ import annotations

import time
import uuid

import pytest

from src.server import InsightManager


@pytest.fixture()
def url() -> str:
    return f"https://example.invalid/{uuid.uuid4().hex}"


def _wait(manager: InsightManager, url: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = manager.get(url)["status"]
        if status != "running":
            return status
        time.sleep(0.01)
    return manager.get(url)["status"]


def test_a_successful_analysis_returns_the_two_fields(url, monkeypatch):
    monkeypatch.setattr("src.storage.json_storage.merge_llm_insights", lambda new, *a, **k: new)
    manager = InsightManager(
        generator=lambda item: {
            "why_it_matters": "Conta perché cambia il costo per token.",
            "possible_action": "Ricalcola il budget di inferenza.",
        }
    )

    manager.start(url, {"url": url, "title": "Un articolo"})

    assert _wait(manager, url) == "done"
    result = manager.get(url)["result"]
    assert result["why_it_matters"] == "Conta perché cambia il costo per token."
    assert result["possible_action"] == "Ricalcola il budget di inferenza."


def test_the_insight_is_persisted_so_the_next_render_keeps_it(url, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "src.storage.json_storage.merge_llm_insights",
        lambda new, *a, **k: saved.update(new) or saved,
    )
    manager = InsightManager(generator=lambda item: {"why_it_matters": "Conta.", "possible_action": "Agisci."})

    manager.start(url, {"url": url, "title": "Un articolo"})
    _wait(manager, url)

    assert url in saved


def test_an_analysis_that_produces_nothing_is_not_cached(url, monkeypatch):
    # Same rule as the deep dives: a failure must never be served as a result,
    # or the article is stuck with it for good.
    saved = {}
    monkeypatch.setattr(
        "src.storage.json_storage.merge_llm_insights",
        lambda new, *a, **k: saved.update(new) or saved,
    )
    manager = InsightManager(generator=lambda item: {})

    manager.start(url, {"url": url, "title": "Un articolo"})

    assert _wait(manager, url) == "error"
    assert saved == {}


def test_a_second_request_while_running_does_not_start_another(url):
    import threading

    gate = threading.Event()
    manager = InsightManager(generator=lambda item: gate.wait(2) or {"why_it_matters": "x"})

    assert manager.start(url, {"url": url}) == "running"
    assert manager.start(url, {"url": url}) == "running"
    gate.set()

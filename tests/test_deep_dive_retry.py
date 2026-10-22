"""A failed deep dive must not be cached as if it had succeeded.

`_error_dive` returns an ordinary dict -- same shape as a real analysis, with
the failure written into `overview`. The manager could not tell the two apart,
so it persisted the failure and answered "done" forever after: clicking
Approfondisci again returned the stored error message and the article could
never be analysed again. Found in the wild on an OpenAI article whose cached
dive read "Approfondimento non riuscito. Riprova.".

Exceptions were never the problem -- those leave status "error" and do retry.
This is about the soft failure that looks like a result.
"""

from __future__ import annotations

import time
import uuid

import pytest

from src.processors.deep_dive import _error_dive
from src.server import DeepDiveManager


@pytest.fixture()
def url() -> str:
    return f"https://example.invalid/{uuid.uuid4().hex}"


def _wait(manager: DeepDiveManager, url: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = manager.get(url)["status"]
        if status not in ("running",):
            return status
        time.sleep(0.01)
    return manager.get(url)["status"]


def test_a_failed_dive_is_marked_as_failed():
    dive = _error_dive("L'AI non ha restituito un risultato utilizzabile.")

    assert dive["failed"] is True


def test_a_successful_dive_is_not_marked_as_failed(url, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "src.storage.json_storage.save_deep_dive", lambda u, d: saved.update({u: d})
    )
    manager = DeepDiveManager(generator=lambda item: {"overview": "Analisi vera", "points": []})

    manager.start(url, {"url": url})

    assert _wait(manager, url) == "done"
    assert url in saved


def test_a_failed_dive_is_not_persisted(url, monkeypatch):
    saved = {}
    monkeypatch.setattr(
        "src.storage.json_storage.save_deep_dive", lambda u, d: saved.update({u: d})
    )
    manager = DeepDiveManager(generator=lambda item: _error_dive("Fonte non raggiungibile."))

    manager.start(url, {"url": url})
    _wait(manager, url)

    assert saved == {}, "a failure must never reach the cache on disk"


def test_the_article_can_be_analysed_again_after_a_failure(url, monkeypatch):
    monkeypatch.setattr("src.storage.json_storage.save_deep_dive", lambda u, d: None)
    attempts = {"n": 0}

    def flaky(item):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _error_dive("Fonte non raggiungibile.")
        return {"overview": "Riuscito al secondo tentativo", "points": []}

    manager = DeepDiveManager(generator=flaky)

    manager.start(url, {"url": url})
    _wait(manager, url)
    manager.start(url, {"url": url})
    _wait(manager, url)

    result = manager.get(url)
    assert result["status"] == "done"
    assert result["result"]["overview"] == "Riuscito al secondo tentativo"


def test_a_failure_reports_error_rather_than_done(url, monkeypatch):
    # The UI keys off this: "done" renders the panel, "error" offers a retry.
    monkeypatch.setattr("src.storage.json_storage.save_deep_dive", lambda u, d: None)
    manager = DeepDiveManager(generator=lambda item: _error_dive("Fonte non raggiungibile."))

    manager.start(url, {"url": url})

    assert _wait(manager, url) == "error"

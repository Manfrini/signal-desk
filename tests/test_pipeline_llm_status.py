"""The daily pipeline records Claude CLI health so a silent LLM fallback shows."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.outputs.output_service import OutputService
from src.pipeline import daily_pipeline
from src.pipeline.daily_pipeline import DailyPipeline
from src.processors.processor_service import ProcessorService
from tests.test_daily_pipeline import FakeCollectorService


def _build_pipeline(tmp_path: Path) -> DailyPipeline:
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("output_dashboard_enabled: true\n", encoding="utf-8")
    return DailyPipeline(
        collector_service=FakeCollectorService(),
        processor_service=ProcessorService(
            base_dir=tmp_path, date=date(2026, 5, 5), llm_enabled=True
        ),
        output_service=OutputService(
            settings_path=settings_path,
            dashboard_dir=tmp_path / "dashboard",
            email_dir=tmp_path / "email",
            date=date(2026, 5, 5),
        ),
        base_dir=tmp_path,
        date=date(2026, 5, 5),
    )


@pytest.fixture(autouse=True)
def _no_real_cli(monkeypatch):
    # Keep section-brief generation deterministic (no real `claude` subprocess).
    monkeypatch.setattr(daily_pipeline, "is_available", lambda *a, **k: False)


def test_pipeline_writes_llm_status_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(
        daily_pipeline,
        "claude_cli_health",
        lambda **_: {
            "ok": True,
            "reason": "ok",
            "detail": "",
            "checked_at": "2026-08-28T10:00:00+00:00",
        },
    )
    _build_pipeline(tmp_path).run(dry_run=False)
    status = json.loads((tmp_path / "llm_status.json").read_text())
    assert status["ok"] is True
    assert status["last_ok_at"] == "2026-08-28T10:00:00+00:00"


def test_pipeline_writes_llm_status_logged_out(tmp_path, monkeypatch):
    monkeypatch.setattr(
        daily_pipeline,
        "claude_cli_health",
        lambda **_: {
            "ok": False,
            "reason": "logged_out",
            "detail": "d",
            "checked_at": "2026-08-28T10:00:00+00:00",
        },
    )
    _build_pipeline(tmp_path).run(dry_run=False)
    status = json.loads((tmp_path / "llm_status.json").read_text())
    assert status["ok"] is False
    assert status["reason"] == "logged_out"


def test_dry_run_does_not_write_llm_status(tmp_path, monkeypatch):
    monkeypatch.setattr(
        daily_pipeline,
        "claude_cli_health",
        lambda **_: {"ok": True, "reason": "ok", "detail": "", "checked_at": "x"},
    )
    _build_pipeline(tmp_path).run(dry_run=True)
    assert not (tmp_path / "llm_status.json").exists()

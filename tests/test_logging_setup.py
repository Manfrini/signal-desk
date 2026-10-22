"""File logging setup (src/logging_setup.py)."""

from __future__ import annotations

import logging

from src import logging_setup


def test_configure_logging_creates_the_log_dir_and_file(tmp_path):
    log_dir = tmp_path / "logs"

    logging_setup.configure_logging(log_dir=log_dir)
    logging.getLogger("test").info("hello")

    assert (log_dir / "app.log").exists()
    assert "hello" in (log_dir / "app.log").read_text(encoding="utf-8")


def test_configure_logging_is_idempotent_does_not_duplicate_handlers(tmp_path):
    log_dir = tmp_path / "logs"

    logging_setup.configure_logging(log_dir=log_dir)
    logging_setup.configure_logging(log_dir=log_dir)

    assert len(logging.getLogger().handlers) == 2  # stderr + file, not 4


def test_configure_logging_falls_back_to_stderr_when_dir_cannot_be_created(monkeypatch):
    def _boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.mkdir", _boom)

    logging_setup.configure_logging(log_dir="/should/not/be/writable")  # must not raise

    assert len(logging.getLogger().handlers) == 1  # stderr only


def test_resolve_log_level_defaults_to_info_without_a_settings_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert logging_setup.resolve_log_level() == logging.INFO


def test_resolve_log_level_reads_settings_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("log_level: DEBUG\n", encoding="utf-8")

    assert logging_setup.resolve_log_level() == logging.DEBUG


def test_resolve_log_level_ignores_an_invalid_value(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.yaml").write_text("log_level: NOT_A_LEVEL\n", encoding="utf-8")

    assert logging_setup.resolve_log_level() == logging.INFO

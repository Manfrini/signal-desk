"""Output generation service."""

from __future__ import annotations

import logging
from datetime import date as date_type
from pathlib import Path
from typing import Any

import yaml

from src.outputs.dashboard_output import DashboardOutput
from src.outputs.email_output import EmailOutput
from src.storage.json_storage import today_utc

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS_PATH = Path("config/settings.yaml")
DEFAULT_DASHBOARD_DIR = Path("outputs/dashboard")
DEFAULT_EMAIL_DIR = Path("outputs/email")


def save_html(path: str | Path, content: str) -> Path:
    """Write UTF-8 HTML to disk."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists():
        logger.warning("Overwriting existing HTML file: %s", target_path)
    target_path.write_text(content, encoding="utf-8")
    logger.info("Saved HTML file: %s", target_path)
    return target_path


class OutputService:
    """Generate configured local outputs."""

    def __init__(
        self,
        settings_path: str | Path = DEFAULT_SETTINGS_PATH,
        dashboard_dir: str | Path = DEFAULT_DASHBOARD_DIR,
        email_dir: str | Path = DEFAULT_EMAIL_DIR,
        date: date_type | None = None,
    ) -> None:
        self.settings_path = Path(settings_path)
        self.dashboard_dir = Path(dashboard_dir)
        self.email_dir = Path(email_dir)
        self.date = date or today_utc()

    def generate(
        self,
        items: list[dict[str, Any]],
        dry_run: bool,
        daily_summary: dict[str, Any] | None = None,
        email_items: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Generate dashboard and email HTML outputs."""
        settings = self._load_settings()
        dashboard_path: Path | None = None
        newsletter_items = items if email_items is None else email_items

        if settings.get("output_dashboard_enabled", True):
            dashboard_path = DashboardOutput(
                output_dir=self.dashboard_dir,
                date=self.date,
            ).generate(items, preview=dry_run, daily_summary=daily_summary)
        else:
            logger.info("Dashboard output disabled in settings")

        email_paths = EmailOutput(
            output_dir=self.email_dir,
            date=self.date,
        ).generate(
            newsletter_items,
            preview=dry_run,
            daily_summary=daily_summary,
        )

        return {
            "dashboard_path": dashboard_path,
            "email_html_path": email_paths.html_path,
            "email_text_path": email_paths.text_path,
            "email_sending": self._email_sending_status(settings=settings, dry_run=dry_run),
        }

    def _load_settings(self) -> dict[str, Any]:
        with self.settings_path.open("r", encoding="utf-8") as file_handle:
            return yaml.safe_load(file_handle) or {}

    @staticmethod
    def _email_sending_status(settings: dict[str, Any], dry_run: bool) -> str:
        if dry_run:
            return "skipped dry-run"
        if not settings.get("output_email_enabled", False):
            return "disabled"
        return "not implemented"

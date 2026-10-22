"""Logging configuration for the CLI and the local server.

Before this, every log line (including `serve`'s, which runs for as long as
the dashboard is open) only ever reached stderr -- lost the moment the
terminal that started it closes. This adds a rotating file under
``data/logs/app.log`` alongside the existing stderr output, so a crash or a
silent LLM fallback during a long `serve` session can still be diagnosed
after the fact.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

DEFAULT_LOG_DIR = Path("data/logs")
DEFAULT_LOG_FILENAME = "app.log"
MAX_LOG_BYTES = 5_000_000
BACKUP_COUNT = 3

_LOG_FORMAT = "%(asctime)s %(levelname)s:%(name)s:%(message)s"


def resolve_log_level() -> int:
    """Return the configured log level (``config/settings.yaml``'s
    ``log_level``, default INFO). Never raises -- an invalid/missing value
    just falls back to INFO."""
    try:
        import yaml

        with Path("config/settings.yaml").open("r", encoding="utf-8") as handle:
            raw = str((yaml.safe_load(handle) or {}).get("log_level", "INFO")).strip().upper()
    except Exception:  # noqa: BLE001 - logging setup must never fail app startup
        raw = "INFO"
    return getattr(logging, raw, logging.INFO) if isinstance(getattr(logging, raw, None), int) else logging.INFO


def configure_logging(*, log_dir: str | Path = DEFAULT_LOG_DIR) -> None:
    """Set up root logging: stderr (as before) plus a rotating file.

    Safe to call more than once (idempotent: clears prior handlers first) and
    never raises -- if the log directory can't be created/written (e.g. a
    read-only filesystem), logging silently falls back to stderr only rather
    than crashing the command it's supposed to support.
    """
    level = resolve_log_level()
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    try:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path / DEFAULT_LOG_FILENAME,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        logging.getLogger(__name__).warning(
            "Could not set up file logging under %s; stderr-only.", log_dir
        )

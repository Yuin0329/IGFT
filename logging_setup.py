"""Logging configuration that avoids recording browser credentials or state."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import config


def configure_logging() -> None:
    """Configure the rotating application file log once."""

    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    if any(getattr(handler, "_ig_tracker_handler", False) for handler in root_logger.handlers):
        return

    root_logger.setLevel(config.LOG_LEVEL)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        config.LOG_FILE,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(config.LOG_LEVEL)
    file_handler.setFormatter(formatter)
    file_handler._ig_tracker_handler = True  # type: ignore[attr-defined]

    root_logger.addHandler(file_handler)

"""Application-wide logging: console output plus a rotating log file.

Modules simply use ``logger = logging.getLogger(__name__)``; because the package
is named ``adobe_reviewer`` those loggers all inherit the handlers set up here.
The API key is never logged by any module.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from adobe_reviewer.config.models import LoggingSettings

PACKAGE_LOGGER_NAME = "adobe_reviewer"
_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(settings: LoggingSettings, log_dir: Path) -> None:
    """Configure console and rotating-file logging. Safe to call more than once.

    Args:
        settings: The ``logging`` section of the application settings.
        log_dir: Folder for the log file (created if missing).
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, settings.level)
    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    # Remove handlers from any previous call so messages are not duplicated.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / settings.file_name,
        maxBytes=settings.max_bytes,
        backupCount=settings.backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # httpx logs every request URL at INFO; keep the noise down.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

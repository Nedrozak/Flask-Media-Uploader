from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from media_uploader.config import Config


def setup_logging() -> None:
    level_name = str(Config.LOG_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    # Audit events are JSON Lines so log collectors and simple file tooling can
    # parse them independently from human-readable operational logs.
    audit_logger = logging.getLogger("media_uploader.audit")
    audit_logger.handlers.clear()
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False

    audit_formatter = logging.Formatter("%(message)s")
    if Config.AUDIT_LOG_CONSOLE:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(audit_formatter)
        audit_logger.addHandler(stream_handler)

    if Config.AUDIT_ENABLED and Config.AUDIT_LOG_PATH:
        audit_path = Path(Config.AUDIT_LOG_PATH)
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            audit_path,
            when="midnight",
            backupCount=int(Config.AUDIT_RETENTION_DAYS),
            encoding="utf-8",
            utc=True,
        )
        file_handler.setFormatter(audit_formatter)
        audit_logger.addHandler(file_handler)

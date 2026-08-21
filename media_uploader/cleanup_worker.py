from __future__ import annotations

import logging
import signal
from threading import Event

import redis
from flask import Flask

from media_uploader import create_app
from media_uploader.logging_config import setup_logging
from media_uploader.services.admin import prune_share_history
from media_uploader.services.storage import UploadStorage

logger = logging.getLogger(__name__)
stop_event = Event()


def _stop(*_: object) -> None:
    stop_event.set()


def cleanup_once(app: Flask) -> tuple[dict[str, int], int]:
    storage: UploadStorage = app.extensions["upload_storage"]
    result = {"sessions": 0, "shares": 0, "errors": 0}
    try:
        result = storage.cleanup_expired()
        if result["sessions"] or result["shares"]:
            logger.info("Expired upload cleanup: %s", result)
        if result["errors"]:
            logger.warning("Expired upload cleanup had %d filesystem errors", result["errors"])
    except OSError:
        logger.exception("Expired upload cleanup failed")

    pruned = 0
    try:
        pruned = prune_share_history(app.extensions["redis"])
        if pruned:
            logger.info("Pruned %d expired admin share-history records", pruned)
    except redis.RedisError:
        logger.exception("Admin share-history cleanup failed")
    return result, pruned


def main() -> None:
    setup_logging()
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    app = create_app()
    interval = int(app.config["CLEANUP_INTERVAL_SECONDS"])

    while not stop_event.is_set():
        cleanup_once(app)
        stop_event.wait(interval)


if __name__ == "__main__":
    main()

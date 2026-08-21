from __future__ import annotations

import logging
from urllib.parse import urlsplit

import redis
from flask import Flask
from waitress import serve

from media_uploader import create_app
from media_uploader.logging_config import setup_logging

logger = logging.getLogger("media_uploader")


def _redis_endpoint(redis_url: str) -> str:
    """Return a credential-free Redis endpoint for diagnostics."""
    parsed = urlsplit(redis_url)
    host = parsed.hostname or "unknown-host"
    port = parsed.port or 6379
    database = parsed.path.lstrip("/") or "0"
    return f"{host}:{port} (db {database})"


def ensure_redis_available(app: Flask) -> None:
    """Fail fast when the required Redis dependency cannot be reached."""
    try:
        app.extensions["redis"].ping()
    except redis.RedisError as exc:
        endpoint = _redis_endpoint(str(app.config["REDIS_URL"]))
        raise RuntimeError(
            f"Redis is unavailable at {endpoint}. Start Redis or set REDIS_URL to a reachable instance. "
            "For local Docker development, run: docker compose -f docker-compose.dev.yml up -d"
        ) from exc


def main() -> None:
    setup_logging()
    app = create_app()
    try:
        ensure_redis_available(app)
    except RuntimeError as exc:
        logger.critical("%s", exc)
        raise SystemExit(1) from exc
    serve(app, host="0.0.0.0", port=5000, threads=6)


if __name__ == "__main__":
    main()

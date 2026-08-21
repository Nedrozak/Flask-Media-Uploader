from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from urllib.parse import urlsplit
from urllib.request import urlopen

import pytest
import redis
from werkzeug.serving import BaseWSGIServer, make_server

from media_uploader import create_app


def _isolated_redis_database(redis_url: str) -> tuple[str, int, int]:
    parsed = urlsplit(redis_url)
    if parsed.scheme not in {"redis", "rediss"}:
        raise ValueError("E2E_REDIS_URL must use the redis:// or rediss:// scheme.")

    database_text = parsed.path.lstrip("/") or "0"
    try:
        database = int(database_text)
    except ValueError as exc:
        raise ValueError("E2E_REDIS_URL must contain a numeric Redis database number.") from exc

    if database <= 0:
        raise ValueError(
            "E2E_REDIS_URL must use an isolated non-zero Redis database because the E2E suite clears it. "
            "Use redis://127.0.0.1:6379/15 for local development."
        )

    return parsed.hostname or "127.0.0.1", parsed.port or 6379, database


def _wait_for_server(base_url: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urlopen(f"{base_url}/healthz", timeout=1.0) as response:
                if response.status == 200:
                    return
        except OSError as exc:
            last_error = exc
        time.sleep(0.05)

    raise RuntimeError(f"E2E application did not become healthy at {base_url}") from last_error


@pytest.fixture(scope="session")
def app_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    redis_url = os.environ.get("E2E_REDIS_URL", "redis://127.0.0.1:6379/15")

    try:
        redis_host, redis_port, redis_database = _isolated_redis_database(redis_url)
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)

    redis_client = redis.Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2.0,
        socket_timeout=2.0,
    )

    try:
        redis_client.ping()
    except redis.RedisError:
        pytest.fail(
            f"E2E Redis is unavailable at {redis_host}:{redis_port} (db {redis_database}). "
            "Start it with: docker compose -f docker-compose.dev.yml up -d",
            pytrace=False,
        )

    redis_client.flushdb()
    test_root = tmp_path_factory.mktemp("media-uploader-e2e")

    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "e2e-only-secret-key-with-more-than-32-characters",
            "REDIS_URL": redis_url,
            "UPLOAD_FOLDER": str(test_root / "uploads"),
            "WTF_CSRF_ENABLED": True,
            "WTF_CSRF_TIME_LIMIT": 3600,
            "SESSION_COOKIE_SECURE": False,
            "TRUSTED_HOSTS": ["127.0.0.1"],
            "PROXY_FIX_X_FOR": 0,
            "PROXY_FIX_X_PROTO": 0,
            "PROXY_FIX_X_HOST": 0,
            "HSTS_MAX_AGE": 0,
            "AUDIT_ENABLED": False,
            "MIN_FREE_DISK_BYTES": 0,
            "CHUNK_SIZE": 1024 * 1024,
            "MAX_FILE_SIZE": 10 * 1024 * 1024,
            "MAX_REQUEST_SIZE": 2 * 1024 * 1024,
            "MAX_FILES_PER_SESSION": 5,
            "MAX_SESSION_BYTES": 20 * 1024 * 1024,
            "MAX_CHUNKS_PER_FILE": 10,
            "SESSION_EXPIRY_SECONDS": 600,
            "TOKEN_EXPIRY_SECONDS": 600,
            "RATE_LIMIT_SESSION_CREATES": 0,
            "RATE_LIMIT_UPLOAD_CHUNKS": 0,
            "RATE_LIMIT_FINALIZE": 0,
            "UPLOAD_CONCURRENCY": 1,
            "CHUNK_UPLOAD_RETRIES": 0,
            "UPLOAD_REQUEST_TIMEOUT_MS": 30_000,
            "TRANSFER_FINALIZE_TIMEOUT_SECONDS": 60,
        },
        redis_client=redis_client,
    )

    server: BaseWSGIServer = make_server("127.0.0.1", 0, app, threaded=True)
    base_url = f"http://127.0.0.1:{server.server_port}"
    server_thread = threading.Thread(
        target=server.serve_forever,
        name="media-uploader-e2e-server",
        daemon=True,
    )
    server_thread.start()

    try:
        _wait_for_server(base_url)
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5.0)
        redis_client.flushdb()
        redis_client.close()

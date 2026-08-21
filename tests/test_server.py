from __future__ import annotations

import fakeredis
import pytest
import redis
from flask import Flask

from media_uploader.server import _redis_endpoint, ensure_redis_available


def test_redis_endpoint_redacts_credentials() -> None:
    endpoint = _redis_endpoint("redis://user:super-secret@redis.example:6380/7")
    assert endpoint == "redis.example:6380 (db 7)"
    assert "user" not in endpoint
    assert "super-secret" not in endpoint


def test_redis_preflight_accepts_available_backend() -> None:
    app = Flask(__name__)
    app.config["REDIS_URL"] = "redis://localhost:6379/0"
    app.extensions["redis"] = fakeredis.FakeRedis(decode_responses=True)

    ensure_redis_available(app)


def test_redis_preflight_fails_with_actionable_message() -> None:
    class UnavailableRedis:
        def ping(self) -> None:
            raise redis.ConnectionError("redis down")

    app = Flask(__name__)
    app.config["REDIS_URL"] = "redis://user:super-secret@redis.example:6380/7"
    app.extensions["redis"] = UnavailableRedis()

    with pytest.raises(RuntimeError) as exc_info:
        ensure_redis_available(app)

    message = str(exc_info.value)
    assert "redis.example:6380 (db 7)" in message
    assert "docker compose -f docker-compose.dev.yml up -d" in message
    assert "super-secret" not in message

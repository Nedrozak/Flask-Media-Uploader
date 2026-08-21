from __future__ import annotations

from typing import Any

import redis
from flask import Flask, current_app
from flask_wtf.csrf import CSRFProtect

csrf = CSRFProtect()


def init_redis(app: Flask, client: Any | None = None) -> None:
    if client is None:
        client = redis.Redis.from_url(app.config["REDIS_URL"], decode_responses=True)
    app.extensions["redis"] = client


def get_redis() -> Any:
    return current_app.extensions["redis"]

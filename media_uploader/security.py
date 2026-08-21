from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import Any, TypeVar, cast

import redis
from flask import Response, current_app, request
from werkzeug.security import check_password_hash

from media_uploader.audit import audit_event
from media_uploader.extensions import get_redis
from media_uploader.responses import json_error

F = TypeVar("F", bound=Callable[..., Any])


def upload_auth_required(view: F) -> F:
    """Protect uploader-facing routes with HTTP Basic Auth when enabled."""

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not current_app.config.get("UPLOAD_AUTH_ENABLED", False):
            return view(*args, **kwargs)

        expected_username = str(current_app.config.get("UPLOAD_AUTH_USERNAME") or "")
        password_hash = str(current_app.config.get("UPLOAD_AUTH_PASSWORD_HASH") or "")
        auth = request.authorization
        valid = bool(
            auth
            and (auth.type or "").lower() == "basic"
            and auth.username == expected_username
            and auth.password is not None
            and check_password_hash(password_hash, auth.password)
        )
        if valid:
            return view(*args, **kwargs)

        audit_event("upload_auth_rejected")
        response = Response("Authentication required", 401, mimetype="text/plain")
        response.headers["WWW-Authenticate"] = 'Basic realm="Media Uploader", charset="UTF-8"'
        response.headers["Cache-Control"] = "private, no-store, max-age=0"
        return response

    return cast(F, wrapped)


def rate_limit(scope: str, config_key: str) -> Callable[[F], F]:
    """Apply a small Redis-backed fixed-window limit to an endpoint."""

    def decorator(view: F) -> F:
        @functools.wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            limit = int(current_app.config[config_key])
            if limit <= 0:
                return view(*args, **kwargs)

            window = int(current_app.config["RATE_LIMIT_WINDOW_SECONDS"])
            bucket = int(time.time()) // window
            client_ip = request.remote_addr or "unknown"
            key = f"rate:{scope}:{client_ip}:{bucket}"

            try:
                pipe = get_redis().pipeline(transaction=True)
                pipe.incr(key)
                pipe.expire(key, window + 1)
                count, _ = pipe.execute()
            except redis.RedisError:
                current_app.logger.exception("Rate limit backend unavailable")
                return json_error("service_unavailable", "The service is temporarily unavailable.", 503)

            if int(count) > limit:
                audit_event("rate_limited", scope=scope, limit=limit, window_seconds=window)
                return json_error("rate_limited", "Too many requests. Please retry shortly.", 429)
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return decorator

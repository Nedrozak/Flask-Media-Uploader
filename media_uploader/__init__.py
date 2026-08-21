from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import click
import redis
from flask import Flask, Response, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from media_uploader.audit import audit_event, begin_request_audit, complete_request_audit
from media_uploader.blueprints.admin import bp as admin_bp
from media_uploader.blueprints.main import bp as main_bp
from media_uploader.blueprints.shares import bp as shares_bp
from media_uploader.blueprints.uploads import bp as uploads_bp
from media_uploader.config import Config
from media_uploader.extensions import csrf, init_redis
from media_uploader.responses import json_error
from media_uploader.services.admin import prune_share_history
from media_uploader.services.storage import UploadStorage


def create_app(
    config_overrides: Mapping[str, Any] | None = None,
    redis_client: Any | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config_overrides:
        app.config.update(config_overrides)
    if app.config.get("SHARE_REGISTRY_ENABLED") is None:
        app.config["SHARE_REGISTRY_ENABLED"] = bool(app.config.get("ADMIN_ENABLED"))
    secret = str(app.config.get("SECRET_KEY") or "")
    if not secret:
        raise RuntimeError("FLASK_SECRET_KEY environment variable must be set")
    if not app.config.get("TESTING") and len(secret) < 32:
        raise RuntimeError("FLASK_SECRET_KEY must be at least 32 characters")
    validate_config(app)
    app.permanent_session_lifetime = timedelta(days=int(app.config["VISITOR_RETENTION_DAYS"]))

    x_for = int(app.config["PROXY_FIX_X_FOR"])
    x_proto = int(app.config["PROXY_FIX_X_PROTO"])
    x_host = int(app.config["PROXY_FIX_X_HOST"])
    if any((x_for, x_proto, x_host)):
        app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
            app.wsgi_app,
            x_for=x_for,
            x_proto=x_proto,
            x_host=x_host,
        )

    init_redis(app, redis_client)
    app.extensions["upload_storage"] = UploadStorage(Path(app.config["UPLOAD_FOLDER"]))
    register_audit_hooks(app)
    csrf.init_app(app)

    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(uploads_bp)
    app.register_blueprint(shares_bp)

    register_error_handlers(app)
    register_security_headers(app)
    register_cli(app)
    return app


def validate_config(app: Flask) -> None:
    positive = (
        "CHUNK_SIZE",
        "MAX_FILE_SIZE",
        "MAX_REQUEST_SIZE",
        "MAX_FORM_MEMORY_SIZE",
        "MAX_FORM_PARTS",
        "MAX_FILENAME_LENGTH",
        "MAX_FILES_PER_SESSION",
        "MAX_SESSION_BYTES",
        "MAX_CHUNKS_PER_FILE",
        "SESSION_EXPIRY_SECONDS",
        "TOKEN_EXPIRY_SECONDS",
        "CLEANUP_INTERVAL_SECONDS",
        "RATE_LIMIT_WINDOW_SECONDS",
        "UPLOAD_CONCURRENCY",
        "UPLOAD_REQUEST_TIMEOUT_MS",
        "TRANSFER_FINALIZE_TIMEOUT_SECONDS",
        "AUDIT_RETENTION_DAYS",
        "VISITOR_RETENTION_DAYS",
        "ADMIN_SESSION_TTL_SECONDS",
        "ADMIN_SHARE_HISTORY_DAYS",
    )
    for name in positive:
        if int(app.config[name]) <= 0:
            raise RuntimeError(f"{name} must be greater than zero")

    non_negative = (
        "CLEANUP_EXPIRY_GRACE_SECONDS",
        "RATE_LIMIT_SESSION_CREATES",
        "RATE_LIMIT_UPLOAD_CHUNKS",
        "RATE_LIMIT_FINALIZE",
        "RATE_LIMIT_ADMIN_LOGIN",
        "CHUNK_UPLOAD_RETRIES",
        "HSTS_MAX_AGE",
        "PROXY_FIX_X_FOR",
        "PROXY_FIX_X_PROTO",
        "PROXY_FIX_X_HOST",
        "MIN_FREE_DISK_BYTES",
    )
    for name in non_negative:
        if int(app.config[name]) < 0:
            raise RuntimeError(f"{name} must not be negative")

    if int(app.config["MAX_REQUEST_SIZE"]) <= int(app.config["CHUNK_SIZE"]):
        raise RuntimeError("MAX_REQUEST_SIZE must be larger than CHUNK_SIZE to allow multipart overhead")
    if int(app.config["MAX_SESSION_BYTES"]) < int(app.config["MAX_FILE_SIZE"]):
        raise RuntimeError("MAX_SESSION_BYTES must be at least MAX_FILE_SIZE")

    required_chunks = (int(app.config["MAX_FILE_SIZE"]) + int(app.config["CHUNK_SIZE"]) - 1) // int(
        app.config["CHUNK_SIZE"]
    )
    if int(app.config["MAX_CHUNKS_PER_FILE"]) < required_chunks:
        raise RuntimeError("MAX_CHUNKS_PER_FILE is too small for MAX_FILE_SIZE and CHUNK_SIZE")

    if app.config.get("ADMIN_ENABLED"):
        if not app.config.get("SHARE_REGISTRY_ENABLED"):
            raise RuntimeError("SHARE_REGISTRY_ENABLED must be true when ADMIN_ENABLED is true")
        if not str(app.config.get("ADMIN_USERNAME") or "").strip():
            raise RuntimeError("ADMIN_USERNAME must be set when ADMIN_ENABLED is true")
        if not str(app.config.get("ADMIN_PASSWORD_HASH") or "").strip():
            raise RuntimeError("ADMIN_PASSWORD_HASH must be set when ADMIN_ENABLED is true")


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def handle_csrf_error(_: CSRFError) -> tuple[Response, int]:
        audit_event("csrf_rejected")
        return json_error("invalid_csrf", "CSRF token is missing or invalid.", 400)

    @app.errorhandler(RequestEntityTooLarge)
    def handle_request_too_large(_: RequestEntityTooLarge) -> tuple[Response, int]:
        audit_event("request_rejected", reason="request_too_large")
        return json_error("request_too_large", "The upload request is too large.", 413)


def register_audit_hooks(app: Flask) -> None:
    app.before_request(begin_request_audit)
    app.after_request(complete_request_audit)


def register_security_headers(app: Flask) -> None:
    @app.after_request
    def add_security_headers(response: Response) -> Response:
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if request.is_secure and int(app.config["HSTS_MAX_AGE"]) > 0:
            response.headers.setdefault(
                "Strict-Transport-Security",
                f"max-age={int(app.config['HSTS_MAX_AGE'])}",
            )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        return response


def register_cli(app: Flask) -> None:
    @app.cli.command("cleanup-uploads")
    def cleanup_uploads() -> None:
        """Remove expired upload sessions and shares from disk."""
        storage: UploadStorage = app.extensions["upload_storage"]
        result = storage.cleanup_expired()
        try:
            pruned = prune_share_history(app.extensions["redis"])
            registry_status = f" Pruned {pruned} expired share-history records."
        except redis.RedisError:
            registry_status = " Share-history indexes were not pruned because Redis is unavailable."
        click.echo(
            f"Removed {result['sessions']} expired sessions and {result['shares']} expired shares; "
            f"{result['errors']} cleanup errors.{registry_status}"
        )

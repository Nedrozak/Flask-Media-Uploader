from __future__ import annotations

import time
import uuid

import redis
from flask import Blueprint, Response, current_app, jsonify, render_template, request
from flask_wtf.csrf import generate_csrf

from media_uploader.audit import audit_event, fingerprint
from media_uploader.extensions import get_redis
from media_uploader.responses import json_error
from media_uploader.security import rate_limit, upload_auth_required
from media_uploader.services.r2_storage import R2Storage, R2StorageError
from media_uploader.services.storage import StorageCapacityError, UploadStorage
from media_uploader.services.uploads import create_session

bp = Blueprint("main", __name__)


def human_readable_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


@bp.get("/")
@upload_auth_required
@rate_limit("session-create", "RATE_LIMIT_SESSION_CREATES")
def index() -> Response | tuple[Response, int]:
    session_id = uuid.uuid4().hex
    ttl = int(current_app.config["SESSION_EXPIRY_SECONDS"])
    try:
        if current_app.config["STORAGE_BACKEND"] == "local":
            storage: UploadStorage = current_app.extensions["upload_storage"]
            storage.ensure_capacity(0, int(current_app.config["MIN_FREE_DISK_BYTES"]))
        expires_at = create_session(get_redis(), session_id, ttl)
        if current_app.config["STORAGE_BACKEND"] == "local":
            storage.ensure_session(
                session_id,
                expires_at + int(current_app.config["CLEANUP_EXPIRY_GRACE_SECONDS"]),
            )
    except redis.RedisError:
        current_app.logger.exception("Could not create upload session")
        return json_error("service_unavailable", "The upload service is temporarily unavailable.", 503)
    except StorageCapacityError as exc:
        current_app.logger.warning("Upload session rejected by disk safety margin: %s", exc)
        audit_event("upload_session_rejected", reason="insufficient_storage")
        return json_error("insufficient_storage", "The server does not have enough free storage for uploads.", 507)
    except OSError:
        current_app.logger.exception("Could not initialize upload storage")
        return json_error("storage_unavailable", "Upload storage is temporarily unavailable.", 503)

    session_fingerprint = fingerprint(session_id)
    current_app.logger.debug("Created upload session %s for %s", session_fingerprint, request.remote_addr)
    audit_event("upload_session_created", session=session_fingerprint)
    allowed_extensions = sorted(current_app.config["ALLOWED_MIME_BY_EXTENSION"].keys())
    response = current_app.make_response(
        render_template(
            "index.jinja",
            session_id=session_id,
            page_csrf_token=generate_csrf(),
            max_file_size=human_readable_size(int(current_app.config["MAX_FILE_SIZE"])),
            max_session_size=human_readable_size(int(current_app.config["MAX_SESSION_BYTES"])),
            allowed_extensions=allowed_extensions,
            accept_filter=",".join(f".{extension}" for extension in allowed_extensions),
        )
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/healthz")
def healthz() -> tuple[Response, int] | Response:
    now = time.monotonic()
    interval = int(current_app.config["HEALTH_DEPENDENCY_CHECK_INTERVAL_SECONDS"])
    cache = current_app.extensions.setdefault(
        "health_dependency_cache",
        {"checked_at": 0.0, "healthy": False},
    )
    should_probe = now - float(cache["checked_at"]) >= interval

    if should_probe:
        healthy = False
        try:
            get_redis().ping()
            if current_app.config["STORAGE_BACKEND"] == "r2":
                r2: R2Storage = current_app.extensions["r2_storage"]
                r2.probe()
            else:
                storage: UploadStorage = current_app.extensions["upload_storage"]
                if not storage.root.is_dir():
                    raise OSError("upload root is missing")
                storage.probe_writable()
                storage.ensure_capacity(0, int(current_app.config["MIN_FREE_DISK_BYTES"]))
            healthy = True
        except (redis.RedisError, OSError, StorageCapacityError, R2StorageError):
            current_app.logger.warning("Health dependency probe failed", exc_info=True)
        cache["checked_at"] = now
        cache["healthy"] = healthy

    if not cache["healthy"]:
        return jsonify({"status": "unhealthy"}), 503
    return jsonify({"status": "ok", "time": int(time.time())})

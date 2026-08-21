from __future__ import annotations

import time
import uuid

import redis
from flask import Blueprint, Response, current_app, jsonify, render_template, request
from flask_wtf.csrf import generate_csrf

from media_uploader.audit import audit_event, fingerprint
from media_uploader.extensions import get_redis
from media_uploader.responses import json_error
from media_uploader.security import rate_limit
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
@rate_limit("session-create", "RATE_LIMIT_SESSION_CREATES")
def index() -> Response | tuple[Response, int]:
    session_id = uuid.uuid4().hex
    ttl = int(current_app.config["SESSION_EXPIRY_SECONDS"])
    try:
        storage: UploadStorage = current_app.extensions["upload_storage"]
        storage.ensure_capacity(0, int(current_app.config["MIN_FREE_DISK_BYTES"]))
        expires_at = create_session(get_redis(), session_id, ttl)
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
    try:
        get_redis().ping()
        storage: UploadStorage = current_app.extensions["upload_storage"]
        if not storage.root.is_dir():
            raise OSError("upload root is missing")
        storage.probe_writable()
        storage.ensure_capacity(0, int(current_app.config["MIN_FREE_DISK_BYTES"]))
    except (redis.RedisError, OSError, StorageCapacityError):
        current_app.logger.warning("Health check failed", exc_info=True)
        return jsonify({"status": "unhealthy"}), 503
    return jsonify({"status": "ok", "time": int(time.time())})

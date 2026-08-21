from __future__ import annotations

from typing import Any

import redis
from flask import Blueprint, Response, abort, current_app, g, render_template, request, send_file, url_for

from media_uploader.audit import audit_event, fingerprint
from media_uploader.extensions import get_redis
from media_uploader.responses import json_error
from media_uploader.services.admin import ensure_share_record, record_share_activity
from media_uploader.services.storage import UploadStorage
from media_uploader.services.uploads import InvalidUpload, load_share, validate_hex_id

bp = Blueprint("shares", __name__, url_prefix="/s")


def _share_fingerprint(token: str | None) -> str | None:
    return fingerprint(token)


def _record_activity(token: str, activity: str, payload: dict[str, Any]) -> None:
    if not current_app.config.get("SHARE_REGISTRY_ENABLED", False):
        return
    try:
        client = get_redis()
        ensure_share_record(
            client,
            token,
            payload,
            int(current_app.config["ADMIN_SHARE_HISTORY_DAYS"]) * 24 * 60 * 60,
        )
        record_share_activity(
            client,
            token,
            activity=activity,
            visitor=getattr(g, "visitor_fingerprint", None),
            client_ip=request.remote_addr,
        )
    except redis.RedisError:
        current_app.logger.debug("Could not update share activity counters", exc_info=True)


@bp.get("/<token>")
def summary(token: str) -> Response | tuple[Response, int]:
    presented_token = token
    try:
        token = validate_hex_id(token, "token")
    except InvalidUpload:
        audit_event("share_open_denied", share=_share_fingerprint(presented_token), reason="invalid_token")
        abort(404)
    try:
        loaded = load_share(get_redis(), token)
    except redis.RedisError:
        current_app.logger.exception("Redis unavailable while loading share")
        return json_error("service_unavailable", "The share service is temporarily unavailable.", 503)
    if loaded is None:
        audit_event("share_open_denied", share=_share_fingerprint(token), reason="unknown_or_expired")
        abort(404)

    payload, ttl = loaded
    storage: UploadStorage = current_app.extensions["upload_storage"]
    image_exts = current_app.config["PREVIEW_IMAGE_EXTENSIONS"]
    video_exts = current_app.config["PREVIEW_VIDEO_EXTENSIONS"]
    files: list[dict[str, Any]] = []
    for item in payload["files"]:
        if not isinstance(item, dict):
            continue
        file_id = item.get("file_id")
        if not isinstance(file_id, str):
            continue
        try:
            path = storage.shared_file(token, file_id)
        except ValueError:
            continue
        if not path.is_file():
            current_app.logger.warning("Share %s references missing file %s", _share_fingerprint(token), file_id)
            continue

        extension = str(item.get("extension", "")).lower()
        preview_kind = "image" if extension in image_exts else "video" if extension in video_exts else None
        files.append(
            {
                "file_id": file_id,
                "name": str(item.get("name", "file")),
                "size": int(item.get("size", path.stat().st_size)),
                "mime": str(item.get("mime", "application/octet-stream")),
                "sha256": str(item.get("sha256", "")),
                "preview_kind": preview_kind,
                "view_url": url_for("shares.shared_file", token=token, file_id=file_id),
                "download_url": url_for("shares.shared_file", token=token, file_id=file_id, download="1"),
            }
        )

    if not files:
        audit_event("share_open_denied", share=_share_fingerprint(token), reason="no_available_files")
        abort(404)

    audit_event("share_opened", share=_share_fingerprint(token), files=len(files), ttl_seconds=ttl)
    _record_activity(token, "open", payload)
    response = current_app.make_response(render_template("summary.jinja", files=files, token=token, ttl=ttl))
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/<token>/files/<file_id>")
def shared_file(token: str, file_id: str) -> Response | tuple[Response, int]:
    presented_token = token
    presented_file_id = file_id
    try:
        token = validate_hex_id(token, "token")
        file_id = validate_hex_id(file_id, "file_id")
    except InvalidUpload:
        audit_event(
            "shared_file_denied",
            share=_share_fingerprint(presented_token),
            file_id=fingerprint(presented_file_id),
            reason="invalid_identifier",
        )
        abort(404)

    try:
        loaded = load_share(get_redis(), token)
    except redis.RedisError:
        current_app.logger.exception("Redis unavailable while authorizing shared file")
        return json_error("service_unavailable", "The share service is temporarily unavailable.", 503)
    if loaded is None:
        audit_event("shared_file_denied", share=_share_fingerprint(token), file_id=file_id, reason="expired_share")
        abort(404)

    payload, _ = loaded
    metadata = next(
        (item for item in payload["files"] if isinstance(item, dict) and item.get("file_id") == file_id),
        None,
    )
    if metadata is None:
        audit_event("shared_file_denied", share=_share_fingerprint(token), file_id=file_id, reason="file_not_in_share")
        abort(404)

    storage: UploadStorage = current_app.extensions["upload_storage"]
    path = storage.shared_file(token, file_id)
    if not path.is_file():
        audit_event("shared_file_denied", share=_share_fingerprint(token), file_id=file_id, reason="missing_storage")
        abort(404)

    is_download = request.args.get("download") == "1"
    audit_event(
        "shared_file_downloaded" if is_download else "shared_file_viewed",
        share=_share_fingerprint(token),
        file_id=file_id,
        filename=str(metadata.get("name", "download")),
        size=int(metadata.get("size", path.stat().st_size)),
        mime=str(metadata.get("mime", "application/octet-stream")),
        sha256=str(metadata.get("sha256", "")),
    )
    _record_activity(token, "download" if is_download else "preview", payload)
    response = send_file(
        path,
        mimetype=str(metadata.get("mime", "application/octet-stream")),
        as_attachment=is_download,
        download_name=str(metadata.get("name", "download")),
        conditional=True,
        max_age=0,
    )
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    sha256 = str(metadata.get("sha256", ""))
    if sha256:
        response.headers["X-Content-SHA256"] = sha256
    return response

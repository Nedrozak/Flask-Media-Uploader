from __future__ import annotations

import hmac
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from typing import Any, TypeVar, cast

import redis
from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_wtf.csrf import generate_csrf
from werkzeug.security import check_password_hash

from media_uploader.audit import audit_event, fingerprint
from media_uploader.extensions import get_redis
from media_uploader.security import rate_limit
from media_uploader.services.admin import get_share_record, list_share_records, revoke_share
from media_uploader.services.r2_storage import R2Storage, R2StorageError
from media_uploader.services.storage import UploadStorage
from media_uploader.services.uploads import InvalidUpload, validate_hex_id

bp = Blueprint("admin", __name__, url_prefix="/admin")
F = TypeVar("F", bound=Callable[..., Any])
_PAGE_SIZE = 50


def _admin_enabled() -> bool:
    return bool(current_app.config.get("ADMIN_ENABLED", False))


def _clear_admin_session() -> None:
    session.pop("admin_authenticated", None)
    session.pop("admin_authenticated_at", None)


def _admin_authenticated() -> bool:
    if not session.get("admin_authenticated"):
        return False
    authenticated_at = session.get("admin_authenticated_at")
    if not isinstance(authenticated_at, int | float):
        _clear_admin_session()
        return False
    ttl = int(current_app.config["ADMIN_SESSION_TTL_SECONDS"])
    if time.time() - float(authenticated_at) > ttl:
        _clear_admin_session()
        return False
    return True


def admin_required(view: F) -> F:
    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        if not _admin_enabled():
            abort(404)
        if not _admin_authenticated():
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return cast(F, wrapped)


def _format_timestamp(timestamp: int) -> str:
    if timestamp <= 0:
        return "—"
    return datetime.fromtimestamp(timestamp, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PiB"


def _decorate_record(record: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(record)
    decorated["created_at_display"] = _format_timestamp(int(record["created_at"]))
    decorated["expires_at_display"] = _format_timestamp(int(record["expires_at"]))
    decorated["last_access_at_display"] = _format_timestamp(int(record["last_access_at"]))
    decorated["revoked_at_display"] = _format_timestamp(int(record["revoked_at"]))
    decorated["total_bytes_display"] = _human_size(int(record["total_bytes"]))
    decorated["share_url"] = (
        url_for("shares.summary", token=record["token"], _external=True) if record["status"] == "active" else None
    )
    for file_record in decorated.get("files", []):
        file_record["size_display"] = _human_size(int(file_record.get("size", 0)))
    return decorated


@bp.before_request
def _hide_when_disabled() -> None:
    if not _admin_enabled():
        abort(404)


@bp.after_request
def _admin_no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


@bp.get("/login")
def login() -> Response:
    if _admin_authenticated():
        return redirect(url_for("admin.index"))
    return current_app.make_response(
        render_template(
            "admin_login.jinja",
            error=None,
            page_csrf_token=generate_csrf(),
        )
    )


@bp.post("/login")
@rate_limit("admin-login", "RATE_LIMIT_ADMIN_LOGIN")
def login_submit() -> Response | tuple[str, int]:
    if _admin_authenticated():
        return redirect(url_for("admin.index"))

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    configured_username = str(current_app.config["ADMIN_USERNAME"])
    password_hash = str(current_app.config["ADMIN_PASSWORD_HASH"])

    username_matches = hmac.compare_digest(username, configured_username)
    password_matches = bool(password_hash) and check_password_hash(password_hash, password)
    if username_matches and password_matches:
        session["admin_authenticated"] = True
        session["admin_authenticated_at"] = int(time.time())
        session.modified = True
        audit_event("admin_login_succeeded")
        return redirect(url_for("admin.index"))

    audit_event("admin_login_failed")
    return (
        render_template(
            "admin_login.jinja",
            error="Invalid username or password.",
            page_csrf_token=generate_csrf(),
        ),
        401,
    )


@bp.post("/logout")
@admin_required
def logout() -> Response:
    audit_event("admin_logout")
    _clear_admin_session()
    return redirect(url_for("admin.login"))


@bp.get("/")
@admin_required
def index() -> Response | tuple[str, int]:
    try:
        page = max(int(request.args.get("page", "1")), 1)
    except ValueError:
        page = 1

    history_seconds = int(current_app.config["ADMIN_SHARE_HISTORY_DAYS"]) * 24 * 60 * 60
    try:
        records, total = list_share_records(
            get_redis(),
            page=page,
            page_size=_PAGE_SIZE,
            history_seconds=history_seconds,
        )
    except redis.RedisError:
        current_app.logger.exception("Could not load admin share registry")
        return (
            render_template(
                "admin.jinja",
                records=[],
                total=0,
                page=1,
                pages=1,
                page_csrf_token=generate_csrf(),
                error="Share registry is unavailable.",
            ),
            503,
        )

    decorated = [_decorate_record(record) for record in records]
    pages = max(1, math.ceil(total / _PAGE_SIZE))
    audit_event("admin_panel_opened", page=page, total_shares=total)
    return current_app.make_response(
        render_template(
            "admin.jinja",
            records=decorated,
            total=total,
            page=page,
            pages=pages,
            page_csrf_token=generate_csrf(),
            error=None,
        )
    )


@bp.get("/shares/<token>")
@admin_required
def share_detail(token: str) -> Response:
    try:
        token = validate_hex_id(token, "token")
    except InvalidUpload:
        abort(404)

    try:
        record = get_share_record(get_redis(), token)
    except redis.RedisError:
        current_app.logger.exception("Could not load admin share record")
        abort(503)
    if record is None:
        abort(404)

    audit_event("admin_share_opened", share=fingerprint(token))
    return current_app.make_response(
        render_template(
            "admin_share.jinja",
            record=_decorate_record(record),
            page_csrf_token=generate_csrf(),
        )
    )


@bp.post("/shares/<token>/revoke")
@admin_required
def revoke(token: str) -> Response:
    try:
        token = validate_hex_id(token, "token")
    except InvalidUpload:
        abort(404)

    visitor = getattr(g, "visitor_fingerprint", None)
    record: dict[str, Any] | None = None
    try:
        if current_app.config["STORAGE_BACKEND"] == "r2":
            record = get_share_record(get_redis(), token)
        if not revoke_share(get_redis(), token, visitor):
            abort(404)
    except redis.RedisError:
        current_app.logger.exception("Could not revoke share")
        abort(503)

    if current_app.config["STORAGE_BACKEND"] == "r2":
        keys = []
        if record is not None:
            for item in record.get("files", []):
                if isinstance(item, dict) and isinstance(item.get("object_key"), str):
                    keys.append(item["object_key"])
        try:
            r2: R2Storage = current_app.extensions["r2_storage"]
            r2.delete_objects(keys)
        except R2StorageError:
            current_app.logger.warning(
                "Share was revoked but its R2 files could not be removed immediately",
                exc_info=True,
            )
    else:
        storage: UploadStorage = current_app.extensions["upload_storage"]
        try:
            storage.delete_share(token)
        except OSError:
            current_app.logger.warning(
                "Share was revoked but its files could not be removed immediately",
                exc_info=True,
            )

    audit_event("admin_share_revoked", share=fingerprint(token))
    return redirect(url_for("admin.share_detail", token=token))

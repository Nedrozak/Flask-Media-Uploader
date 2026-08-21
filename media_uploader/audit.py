from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from flask import Response, current_app, g, request, session

logger = logging.getLogger("media_uploader.audit")


def _request_excluded_from_tracking() -> bool:
    if request.endpoint == "static" and not current_app.config.get("AUDIT_LOG_STATIC", False):
        return True
    if request.endpoint == "main.healthz" and not current_app.config.get("AUDIT_LOG_HEALTH", False):
        return True
    return False


def should_audit_request() -> bool:
    return bool(current_app.config.get("AUDIT_ENABLED", True)) and not _request_excluded_from_tracking()


def fingerprint(value: str | None) -> str | None:
    """Return a stable, non-secret identifier suitable for audit correlation."""
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()[:20]


def _bounded_header(name: str, limit: int) -> str | None:
    value = request.headers.get(name, "").strip()
    return value[:limit] if value else None


def _referrer_origin() -> str | None:
    referrer = request.referrer
    if not referrer:
        return None
    parsed = urlsplit(referrer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def audit_event(event: str, **fields: Any) -> None:
    """Emit one structured application audit event without bearer secrets."""
    if not current_app.config.get("AUDIT_ENABLED", True):
        return

    payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "event": event,
        "request_id": getattr(g, "request_id", None),
        "visitor": getattr(g, "visitor_fingerprint", None),
        "client_ip": request.remote_addr,
        "client_port": request.environ.get("REMOTE_PORT"),
        "method": request.method,
        "endpoint": request.endpoint,
        "route": str(request.url_rule) if request.url_rule is not None else None,
        "host": request.host,
        "user_agent": _bounded_header("User-Agent", 512),
        "accept_language": _bounded_header("Accept-Language", 256),
        "referrer_origin": _referrer_origin(),
    }
    payload.update(fields)
    logger.info(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def begin_request_audit() -> None:
    g.request_id = uuid.uuid4().hex
    g.request_started_at = time.perf_counter()

    if _request_excluded_from_tracking():
        return

    visitor_id = session.get("audit_visitor_id")
    is_new_visitor = not isinstance(visitor_id, str) or len(visitor_id) != 32
    if is_new_visitor:
        visitor_id = uuid.uuid4().hex
        session["audit_visitor_id"] = visitor_id
    session.permanent = True
    g.visitor_fingerprint = fingerprint(visitor_id)
    if is_new_visitor:
        audit_event("visitor_first_seen")


def complete_request_audit(response: Response) -> Response:
    request_id = getattr(g, "request_id", None)
    if request_id:
        response.headers.setdefault("X-Request-ID", request_id)

    if not should_audit_request():
        return response

    started = getattr(g, "request_started_at", None)
    duration_ms = round((time.perf_counter() - started) * 1000, 2) if started is not None else None
    audit_event(
        "http_request",
        status=response.status_code,
        duration_ms=duration_ms,
        content_length=response.calculate_content_length(),
    )
    return response

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value is not None else default


def _env_hosts(name: str) -> list[str] | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return [host.strip() for host in value.split(",") if host.strip()]


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local").strip().lower()
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", str(PROJECT_ROOT / "uploads"))

    R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET = os.environ.get("R2_BUCKET", "")
    R2_DELIVERY_BASE_URL = os.environ.get("R2_DELIVERY_BASE_URL", "")
    R2_DELIVERY_SECRET = os.environ.get("R2_DELIVERY_SECRET", "")
    R2_DELIVERY_URL_TTL_SECONDS = _env_int("R2_DELIVERY_URL_TTL_SECONDS", 300)
    R2_UPLOAD_URL_TTL_SECONDS = _env_int("R2_UPLOAD_URL_TTL_SECONDS", 600)

    CHUNK_SIZE = _env_int("CHUNK_SIZE", 5 * 1024 * 1024)
    MAX_FILE_SIZE = _env_int("MAX_FILE_SIZE", 1024 * 1024 * 1024)
    MAX_REQUEST_SIZE = _env_int("MAX_REQUEST_SIZE", CHUNK_SIZE + 2 * 1024 * 1024)
    MAX_CONTENT_LENGTH = MAX_REQUEST_SIZE
    MAX_FORM_MEMORY_SIZE = _env_int("MAX_FORM_MEMORY_SIZE", 512 * 1024)
    MAX_FORM_PARTS = _env_int("MAX_FORM_PARTS", 32)
    MAX_FILENAME_LENGTH = _env_int("MAX_FILENAME_LENGTH", 255)
    MAX_FILES_PER_SESSION = _env_int("MAX_FILES_PER_SESSION", 20)
    MAX_SESSION_BYTES = _env_int("MAX_SESSION_BYTES", 2 * 1024 * 1024 * 1024)
    MAX_CHUNKS_PER_FILE = _env_int(
        "MAX_CHUNKS_PER_FILE",
        math.ceil(MAX_FILE_SIZE / CHUNK_SIZE) if CHUNK_SIZE > 0 else 0,
    )

    SESSION_EXPIRY_SECONDS = _env_int("SESSION_EXPIRY_SECONDS", 30 * 60)
    TOKEN_EXPIRY_SECONDS = _env_int("TOKEN_EXPIRY_SECONDS", 6 * 60 * 60)
    CLEANUP_INTERVAL_SECONDS = _env_int("CLEANUP_INTERVAL_SECONDS", 5 * 60)
    CLEANUP_EXPIRY_GRACE_SECONDS = _env_int("CLEANUP_EXPIRY_GRACE_SECONDS", 60)
    MIN_FREE_DISK_BYTES = _env_int("MIN_FREE_DISK_BYTES", 512 * 1024 * 1024)

    RATE_LIMIT_WINDOW_SECONDS = _env_int("RATE_LIMIT_WINDOW_SECONDS", 60)
    RATE_LIMIT_SESSION_CREATES = _env_int("RATE_LIMIT_SESSION_CREATES", 30)
    RATE_LIMIT_UPLOAD_CHUNKS = _env_int("RATE_LIMIT_UPLOAD_CHUNKS", 600)
    RATE_LIMIT_FINALIZE = _env_int("RATE_LIMIT_FINALIZE", 60)
    RATE_LIMIT_ADMIN_LOGIN = _env_int("RATE_LIMIT_ADMIN_LOGIN", 10)

    UPLOAD_CONCURRENCY = _env_int("UPLOAD_CONCURRENCY", 3)
    UPLOAD_REQUEST_TIMEOUT_MS = _env_int("UPLOAD_REQUEST_TIMEOUT_MS", 180_000)
    CHUNK_UPLOAD_RETRIES = _env_int("CHUNK_UPLOAD_RETRIES", 2)
    TRANSFER_FINALIZE_TIMEOUT_SECONDS = _env_int("TRANSFER_FINALIZE_TIMEOUT_SECONDS", 900)

    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_TIME_LIMIT = _env_int("WTF_CSRF_TIME_LIMIT_SECONDS", 12 * 60 * 60)
    HSTS_MAX_AGE = _env_int("HSTS_MAX_AGE", 0)
    HEALTH_DEPENDENCY_CHECK_INTERVAL_SECONDS = _env_int("HEALTH_DEPENDENCY_CHECK_INTERVAL_SECONDS", 60)

    TRUSTED_HOSTS = _env_hosts("TRUSTED_HOSTS")
    PROXY_FIX_X_FOR = _env_int("PROXY_FIX_X_FOR", 0)
    PROXY_FIX_X_PROTO = _env_int("PROXY_FIX_X_PROTO", 0)
    PROXY_FIX_X_HOST = _env_int("PROXY_FIX_X_HOST", 0)

    AUDIT_ENABLED = _env_bool("AUDIT_ENABLED", True)
    AUDIT_LOG_STATIC = _env_bool("AUDIT_LOG_STATIC", False)
    AUDIT_LOG_HEALTH = _env_bool("AUDIT_LOG_HEALTH", False)
    AUDIT_LOG_CONSOLE = _env_bool("AUDIT_LOG_CONSOLE", False)
    AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", str(PROJECT_ROOT / "logs" / "audit.jsonl"))
    AUDIT_RETENTION_DAYS = _env_int("AUDIT_RETENTION_DAYS", 30)
    VISITOR_RETENTION_DAYS = _env_int("VISITOR_RETENTION_DAYS", 365)

    ADMIN_ENABLED = _env_bool("ADMIN_ENABLED", False)
    SHARE_REGISTRY_ENABLED = (
        None if os.environ.get("SHARE_REGISTRY_ENABLED") is None else _env_bool("SHARE_REGISTRY_ENABLED")
    )
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
    ADMIN_SESSION_TTL_SECONDS = _env_int("ADMIN_SESSION_TTL_SECONDS", 60 * 60)
    ADMIN_SHARE_HISTORY_DAYS = _env_int("ADMIN_SHARE_HISTORY_DAYS", 365)

    UPLOAD_AUTH_ENABLED = _env_bool("UPLOAD_AUTH_ENABLED", False)
    UPLOAD_AUTH_USERNAME = os.environ.get("UPLOAD_AUTH_USERNAME", "uploader")
    UPLOAD_AUTH_PASSWORD_HASH = os.environ.get("UPLOAD_AUTH_PASSWORD_HASH", "")

    SEND_FILE_MAX_AGE_DEFAULT = 0

    ALLOWED_MIME_BY_EXTENSION = {
        "png": {"image/png"},
        "jpg": {"image/jpeg"},
        "jpeg": {"image/jpeg"},
        "gif": {"image/gif"},
        "heic": {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"},
        "heif": {"image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence"},
        "mp4": {"video/mp4"},
        "mov": {"video/quicktime"},
        "avi": {"video/x-msvideo", "video/avi", "video/msvideo"},
        "webm": {"video/webm"},
    }
    PREVIEW_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}
    PREVIEW_VIDEO_EXTENSIONS = {"mp4", "webm", "mov"}

from __future__ import annotations

import io
import json
import math
import uuid
from pathlib import Path
from typing import Any, cast

from flask.testing import FlaskClient
from werkzeug.test import TestResponse


def build_test_config(tmp_path: Path, **overrides: Any) -> dict[str, Any]:
    """Return a complete, environment-independent Flask test configuration."""
    config: dict[str, Any] = {
        "TESTING": True,
        "SECRET_KEY": "test-secret",
        "WTF_CSRF_ENABLED": False,
        "WTF_CSRF_TIME_LIMIT": 12 * 60 * 60,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "CHUNK_SIZE": 8,
        "MAX_FILE_SIZE": 64,
        "MAX_REQUEST_SIZE": 1024 * 1024,
        "MAX_CONTENT_LENGTH": 1024 * 1024,
        "MAX_FORM_MEMORY_SIZE": 512 * 1024,
        "MAX_FORM_PARTS": 32,
        "MAX_FILENAME_LENGTH": 255,
        "MAX_FILES_PER_SESSION": 4,
        "MAX_SESSION_BYTES": 128,
        "MAX_CHUNKS_PER_FILE": 8,
        "SESSION_EXPIRY_SECONDS": 1800,
        "TOKEN_EXPIRY_SECONDS": 3600,
        "CLEANUP_INTERVAL_SECONDS": 300,
        "CLEANUP_EXPIRY_GRACE_SECONDS": 0,
        "MIN_FREE_DISK_BYTES": 0,
        "RATE_LIMIT_WINDOW_SECONDS": 60,
        "RATE_LIMIT_SESSION_CREATES": 1000,
        "RATE_LIMIT_UPLOAD_CHUNKS": 1000,
        "RATE_LIMIT_FINALIZE": 1000,
        "RATE_LIMIT_ADMIN_LOGIN": 1000,
        "UPLOAD_CONCURRENCY": 2,
        "UPLOAD_REQUEST_TIMEOUT_MS": 1000,
        "CHUNK_UPLOAD_RETRIES": 0,
        "TRANSFER_FINALIZE_TIMEOUT_SECONDS": 30,
        "HSTS_MAX_AGE": 0,
        "TRUSTED_HOSTS": None,
        "PROXY_FIX_X_FOR": 0,
        "PROXY_FIX_X_PROTO": 0,
        "PROXY_FIX_X_HOST": 0,
        "AUDIT_ENABLED": False,
        "AUDIT_LOG_STATIC": False,
        "AUDIT_LOG_HEALTH": False,
        "AUDIT_LOG_CONSOLE": False,
        "AUDIT_LOG_PATH": "",
        "AUDIT_RETENTION_DAYS": 30,
        "VISITOR_RETENTION_DAYS": 365,
        "ADMIN_ENABLED": False,
        "SHARE_REGISTRY_ENABLED": False,
        "ADMIN_USERNAME": "admin",
        "ADMIN_PASSWORD_HASH": "",
        "ADMIN_SESSION_TTL_SECONDS": 3600,
        "ADMIN_SHARE_HISTORY_DAYS": 365,
    }
    config.update(overrides)
    return config


def post_chunk(
    client: FlaskClient,
    session_id: str,
    content: bytes,
    *,
    filename: str = "photo.jpg",
    transfer_id: str | None = None,
    file_size: int | None = None,
    chunk_size: int = 8,
    chunk_index: int = 0,
    total_chunks: int | None = None,
) -> tuple[TestResponse, str]:
    transfer_id = transfer_id or uuid.uuid4().hex
    file_size = len(content) if file_size is None else file_size
    total_chunks = math.ceil(file_size / chunk_size) if total_chunks is None else total_chunks
    response = client.post(
        "/api/uploads/chunks",
        data={
            "session_id": session_id,
            "original_filename": filename,
            "fileUuid": transfer_id,
            "file_size": str(file_size),
            "chunk_index": str(chunk_index),
            "total_chunks": str(total_chunks),
            "chunk": (io.BytesIO(content), "chunk.bin"),
        },
        content_type="multipart/form-data",
    )
    return response, transfer_id


def upload_bytes(
    client: FlaskClient,
    session_id: str,
    content: bytes,
    *,
    filename: str = "photo.jpg",
    transfer_id: str | None = None,
    chunk_size: int = 8,
) -> tuple[str, list[TestResponse]]:
    transfer_id = transfer_id or uuid.uuid4().hex
    total_chunks = math.ceil(len(content) / chunk_size)
    responses = []
    for index in range(total_chunks):
        start = index * chunk_size
        end = min(start + chunk_size, len(content))
        response, _ = post_chunk(
            client,
            session_id,
            content[start:end],
            filename=filename,
            transfer_id=transfer_id,
            file_size=len(content),
            chunk_size=chunk_size,
            chunk_index=index,
            total_chunks=total_chunks,
        )
        responses.append(response)
    return transfer_id, responses


def redis_json_object(client: Any, key: str) -> dict[str, Any]:
    """Read a JSON object from Redis with explicit test-side type narrowing."""
    raw = client.get(key)
    assert raw is not None
    value = json.loads(raw)
    assert isinstance(value, dict)
    return cast(dict[str, Any], value)


def redis_text_hash(client: Any, key: str) -> dict[str, str]:
    """Read a decode-responses Redis hash as text for typed tests."""
    return cast(dict[str, str], client.hgetall(key))

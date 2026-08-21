from __future__ import annotations

import re
from pathlib import Path

import fakeredis
import pytest
import redis
from flask import Flask
from flask.testing import FlaskClient

from media_uploader import create_app
from tests.helpers import build_test_config

def test_security_headers_are_added(client: FlaskClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

def test_upload_page_uses_custom_file_queue(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)

    assert 'id="chooseFilesBtn"' in html
    assert 'id="fileSelectionSummary"' in html
    file_input = re.search(r'<input[^>]+id="fileInput"[^>]*>', html, flags=re.DOTALL)
    assert file_input is not None
    assert "required" not in file_input.group(0)

def test_rate_limit_is_enforced(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        build_test_config(
            tmp_path,
            RATE_LIMIT_SESSION_CREATES=1,
            RATE_LIMIT_WINDOW_SECONDS=60,
        ),
        redis_client=redis_client,
    )
    client = app.test_client()
    assert client.get("/").status_code == 200
    limited = client.get("/")
    assert limited.status_code == 429
    assert limited.get_json()["error"]["code"] == "rate_limited"

def test_trusted_hosts_reject_unexpected_host(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        build_test_config(tmp_path, TRUSTED_HOSTS=["uploads.example.test"]),
        redis_client=redis_client,
    )
    client = app.test_client()
    assert client.get("/healthz", headers={"Host": "attacker.example"}).status_code == 400
    assert client.get("/healthz", headers={"Host": "uploads.example.test"}).status_code == 200

def test_production_secret_must_be_strong(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(RuntimeError, match="at least 32 characters"):
        create_app(
            build_test_config(tmp_path, TESTING=False, SECRET_KEY="short-secret"),
            redis_client=redis_client,
        )

def test_health_fails_when_redis_is_unavailable(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    client = app.test_client()

    def fail_ping() -> None:
        raise redis.ConnectionError("redis down")

    monkeypatch.setattr(app.extensions["redis"], "ping", fail_ping)
    response = client.get("/healthz")
    assert response.status_code == 503


def test_health_fails_when_upload_storage_is_not_writable(
    app: Flask,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = app.extensions["upload_storage"]

    def fail_probe() -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(storage, "probe_writable", fail_probe)
    response = app.test_client().get("/healthz")
    assert response.status_code == 503

def test_csrf_protects_upload_mutations(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        build_test_config(tmp_path, WTF_CSRF_ENABLED=True),
        redis_client=redis_client,
    )
    response = app.test_client().post("/api/uploads/finalize", json={"session_id": "0" * 32})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_csrf"

def test_valid_csrf_token_reaches_upload_route(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    app = create_app(
        build_test_config(tmp_path, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=60),
        redis_client=redis_client,
    )
    client = app.test_client()

    page = client.get("/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)

    csrf_match = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    session_match = re.search(r'data-session-id="([0-9a-f]{32})"', html)
    assert csrf_match is not None
    assert session_match is not None

    response = client.post(
        "/api/uploads/finalize",
        json={"session_id": session_match.group(1)},
        headers={"X-CSRFToken": csrf_match.group(1)},
    )

    # A valid token must reach the upload route. This empty session is incomplete,
    # so the route returns an application-level error rather than a CSRF error/500.
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "incomplete_session"

def test_invalid_resource_limit_configuration_is_rejected(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(RuntimeError, match="MAX_REQUEST_SIZE must be larger than CHUNK_SIZE"):
        create_app(
            build_test_config(
                tmp_path,
                CHUNK_SIZE=1024,
                MAX_REQUEST_SIZE=1024,
            ),
            redis_client=redis_client,
        )

def test_upload_page_is_not_cacheable(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store, max-age=0"

from __future__ import annotations

import re
from pathlib import Path

import fakeredis
import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

from media_uploader import create_app
from media_uploader.cleanup_worker import cleanup_once
from media_uploader.services.admin import SHARE_HISTORY_EXPIRY_KEY, SHARE_INDEX_KEY, get_share_record
from media_uploader.services.uploads import token_key
from tests.helpers import build_test_config, redis_json_object, upload_bytes


def _enable_admin(app: Flask) -> None:
    app.config.update(
        ADMIN_ENABLED=True,
        SHARE_REGISTRY_ENABLED=True,
        ADMIN_USERNAME="operator",
        ADMIN_PASSWORD_HASH=generate_password_hash("correct horse battery staple"),
        ADMIN_SESSION_TTL_SECONDS=3600,
        ADMIN_SHARE_HISTORY_DAYS=365,
    )


def _login(client: FlaskClient, password: str = "correct horse battery staple") -> None:
    response = client.post(
        "/admin/login",
        data={"username": "operator", "password": password},
    )
    assert response.status_code == 302


def _new_session(client: FlaskClient) -> str:
    page = client.get("/")
    assert page.status_code == 200
    match = re.search(rb'data-session-id="([0-9a-f]{32})"', page.data)
    assert match is not None
    return match.group(1).decode("ascii")


def test_share_registry_is_not_collected_when_disabled(
    app: Flask,
    redis_client: fakeredis.FakeRedis,
) -> None:
    uploader = app.test_client()
    session_id = _new_session(uploader)
    upload_bytes(uploader, session_id, b"abcdefgh")
    finalized = uploader.post("/api/uploads/finalize", json={"session_id": session_id})
    assert finalized.status_code == 200
    token = finalized.get_json()["token"]

    assert get_share_record(redis_client, token) is None
    assert redis_client.zcard(SHARE_INDEX_KEY) == 0
    assert redis_client.zcard(SHARE_HISTORY_EXPIRY_KEY) == 0

    payload = redis_json_object(redis_client, token_key(token))
    file_id = payload["files"][0]["file_id"]
    assert uploader.get(f"/s/{token}").status_code == 200
    assert uploader.get(f"/s/{token}/files/{file_id}?download=1").status_code == 200
    assert get_share_record(redis_client, token) is None

def test_cleanup_worker_prunes_expired_share_history_indexes(
    app: Flask,
    redis_client: fakeredis.FakeRedis,
) -> None:
    token = "a" * 32
    redis_client.zadd(SHARE_INDEX_KEY, {token: 1})
    redis_client.zadd(SHARE_HISTORY_EXPIRY_KEY, {token: 1})

    _, pruned = cleanup_once(app)

    assert pruned == 1
    assert redis_client.zcard(SHARE_INDEX_KEY) == 0
    assert redis_client.zcard(SHARE_HISTORY_EXPIRY_KEY) == 0

def test_admin_is_hidden_when_disabled(client: FlaskClient) -> None:
    assert client.get("/admin/").status_code == 404
    assert client.get("/admin/login").status_code == 404

def test_admin_requires_login_and_rejects_bad_password(app: Flask) -> None:
    _enable_admin(app)
    client = app.test_client()

    protected = client.get("/admin/")
    assert protected.status_code == 302
    assert protected.headers["Location"].endswith("/admin/login")

    bad_login = client.post(
        "/admin/login",
        data={"username": "operator", "password": "incorrect-password"},
    )
    assert bad_login.status_code == 401
    assert b"Invalid username or password" in bad_login.data

def test_admin_tracks_creator_activity_and_revocation(
    app: Flask,
    redis_client: fakeredis.FakeRedis,
) -> None:
    _enable_admin(app)
    uploader = app.test_client()
    session_id = _new_session(uploader)
    _, responses = upload_bytes(uploader, session_id, b"abcdefgh", filename="NОТО.jpg")
    assert responses[-1].status_code == 200

    finalized = uploader.post(
        "/api/uploads/finalize",
        json={"session_id": session_id},
        environ_base={"REMOTE_ADDR": "203.0.113.50"},
        headers={"User-Agent": "UploaderBrowser/1.0"},
    )
    assert finalized.status_code == 200
    token = finalized.get_json()["token"]

    record = get_share_record(redis_client, token)
    assert record is not None
    assert record["status"] == "active"
    assert record["creator_visitor"]
    assert record["creator_ip"] == "203.0.113.50"
    assert record["creator_user_agent"] == "UploaderBrowser/1.0"
    assert record["file_count"] == 1
    assert record["total_bytes"] == 8

    summary = uploader.get(
        f"/s/{token}",
        environ_base={"REMOTE_ADDR": "198.51.100.20"},
    )
    assert summary.status_code == 200

    file_id = record["files"][0]["file_id"]
    download = uploader.get(
        f"/s/{token}/files/{file_id}?download=1",
        environ_base={"REMOTE_ADDR": "198.51.100.20"},
    )
    assert download.status_code == 200

    admin = app.test_client()
    _login(admin)
    panel = admin.get("/admin/")
    assert panel.status_code == 200
    assert b"Share administration" in panel.data
    assert b"203.0.113.50" in panel.data

    detail = admin.get(f"/admin/shares/{token}")
    assert detail.status_code == 200
    detail_text = detail.get_data(as_text=True)
    assert "UploaderBrowser/1.0" in detail_text
    assert "Opens" in detail_text
    assert "Downloads" in detail_text
    assert "NОТО.jpg" in detail_text

    storage = app.extensions["upload_storage"]
    assert storage.share_dir(token).is_dir()
    revoked = admin.post(f"/admin/shares/{token}/revoke")
    assert revoked.status_code == 302
    assert uploader.get(f"/s/{token}").status_code == 404
    assert not storage.share_dir(token).exists()

    revoked_record = get_share_record(redis_client, token)
    assert revoked_record is not None
    assert revoked_record["status"] == "revoked"
    assert revoked_record["revoked_at"] > 0

def test_admin_configuration_requires_password_hash(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(RuntimeError, match="ADMIN_PASSWORD_HASH"):
        create_app(
            build_test_config(
                tmp_path,
                ADMIN_ENABLED=True,
                SHARE_REGISTRY_ENABLED=True,
                ADMIN_USERNAME="admin",
                ADMIN_PASSWORD_HASH="",
            ),
            redis_client=redis_client,
        )

def test_admin_configuration_requires_share_registry(tmp_path: Path) -> None:
    redis_client = fakeredis.FakeRedis(decode_responses=True)
    with pytest.raises(RuntimeError, match="SHARE_REGISTRY_ENABLED"):
        create_app(
            build_test_config(
                tmp_path,
                ADMIN_ENABLED=True,
                SHARE_REGISTRY_ENABLED=False,
                ADMIN_USERNAME="admin",
                ADMIN_PASSWORD_HASH=generate_password_hash("password"),
            ),
            redis_client=redis_client,
        )

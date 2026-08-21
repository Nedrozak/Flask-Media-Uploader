from __future__ import annotations

import uuid

import fakeredis
import pytest
from flask import Flask
from flask.testing import FlaskClient

from media_uploader.services.uploads import session_key
from tests.helpers import post_chunk, upload_bytes


def test_rejects_wrong_chunk_size_and_releases_reservation(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    response, _ = post_chunk(client, session_id, b"1234567", file_size=8, total_chunks=1)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_chunk_size"
    session = redis_client.hgetall(session_key(session_id))
    assert int(session["reserved_files"]) == 0
    assert int(session["reserved_bytes"]) == 0


@pytest.mark.parametrize(
    ("file_size", "chunk_index", "total_chunks"),
    [
        (16, -1, 2),
        (16, 2, 2),
        (16, 0, 1),
        (65, 0, 9),
        (0, 0, 1),
    ],
)
def test_rejects_invalid_chunk_metadata(
    client: FlaskClient,
    session_id: str,
    file_size: int,
    chunk_index: int,
    total_chunks: int,
) -> None:
    response, _ = post_chunk(
        client,
        session_id,
        b"abcdefgh",
        file_size=file_size,
        chunk_index=chunk_index,
        total_chunks=total_chunks,
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_upload"


def test_rejects_mime_extension_mismatch(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("media_uploader.blueprints.uploads.detect_mime", lambda _: "video/mp4")
    response, _ = post_chunk(client, session_id, b"abcdefgh", filename="image.jpg")
    assert response.status_code == 400
    assert "does not match" in response.get_json()["error"]["message"]
    session = redis_client.hgetall(session_key(session_id))
    assert int(session["reserved_files"]) == 0


def test_file_count_quota_is_enforced(app: Flask, client: FlaskClient, session_id: str) -> None:
    app.config["MAX_FILES_PER_SESSION"] = 1
    _, responses = upload_bytes(client, session_id, b"abcdefgh", filename="one.jpg")
    assert responses[-1].status_code == 200

    response, _ = post_chunk(client, session_id, b"ijklmnop", filename="two.jpg")
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "session_quota_exceeded"


def test_session_byte_quota_is_enforced(app: Flask, client: FlaskClient, session_id: str) -> None:
    app.config["MAX_SESSION_BYTES"] = 12
    _, responses = upload_bytes(client, session_id, b"abcdefgh", filename="one.jpg")
    assert responses[-1].status_code == 200

    response, _ = post_chunk(client, session_id, b"12345678", filename="two.jpg")
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "session_quota_exceeded"


def test_transfer_metadata_cannot_change_mid_upload(client: FlaskClient, session_id: str) -> None:
    transfer_id = uuid.uuid4().hex
    first, _ = post_chunk(
        client,
        session_id,
        b"abcdefgh",
        filename="one.jpg",
        transfer_id=transfer_id,
        file_size=12,
        chunk_index=0,
        total_chunks=2,
    )
    assert first.status_code == 200

    changed, _ = post_chunk(
        client,
        session_id,
        b"ijkl",
        filename="other.jpg",
        transfer_id=transfer_id,
        file_size=12,
        chunk_index=1,
        total_chunks=2,
    )
    assert changed.status_code == 400
    assert changed.get_json()["error"]["code"] == "invalid_upload"


def test_path_like_filename_is_rejected(client: FlaskClient, session_id: str) -> None:
    response, _ = post_chunk(client, session_id, b"abcdefgh", filename="../../photo.jpg")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_upload"


def test_request_body_limit_returns_json_error(app: Flask, client: FlaskClient, session_id: str) -> None:
    app.config["MAX_CONTENT_LENGTH"] = 128
    response, _ = post_chunk(client, session_id, b"x" * 256, file_size=8, total_chunks=1)
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "request_too_large"

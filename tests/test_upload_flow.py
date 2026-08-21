from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote

import fakeredis
import pytest
import redis
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from media_uploader.services.storage import UploadStorage
from media_uploader.services.uploads import (
    TransferSpec,
    abort_transfer,
    claim_transfer_completion,
    complete_transfer as complete_transfer_state,
    completed_key,
    reserve_transfer,
    transfer_key,
)
from media_uploader.services.uploads import (
    publish_share as publish_share_state,
)
from media_uploader.services.uploads import (
    session_key,
    token_key,
)
from tests.helpers import post_chunk, redis_json_object, redis_text_hash, upload_bytes


def finalize(client: FlaskClient, session_id: str) -> TestResponse:
    return client.post("/api/uploads/finalize", json={"session_id": session_id})

def test_complete_upload_share_and_download(
    app: Flask,
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    content = b"abcdefghijklmnopqrst"
    _, responses = upload_bytes(client, session_id, content)
    assert [response.status_code for response in responses] == [200, 200, 200]
    assert responses[-1].get_json()["status"] == "success"

    finalized = finalize(client, session_id)
    assert finalized.status_code == 200
    body = finalized.get_json()
    token = body["token"]
    assert body["shareable_url"].endswith(f"/s/{token}")

    summary = client.get(f"/s/{token}")
    assert summary.status_code == 200
    assert b"photo.jpg" in summary.data

    payload = redis_json_object(redis_client, token_key(token))
    file_id = payload["files"][0]["file_id"]
    assert payload["files"][0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert hashlib.sha256(content).hexdigest().encode() in summary.data
    view = client.get(f"/s/{token}/files/{file_id}")
    assert view.status_code == 200
    assert view.data == content
    assert view.headers["Cache-Control"].startswith("private, no-store")
    assert view.headers["X-Content-Type-Options"] == "nosniff"
    assert view.headers["X-Content-SHA256"] == hashlib.sha256(content).hexdigest()

    download = client.get(f"/s/{token}/files/{file_id}?download=1")
    assert download.status_code == 200
    assert "attachment" in download.headers["Content-Disposition"]
    assert "photo.jpg" in download.headers["Content-Disposition"]

    assert client.get("/uploads/anything").status_code == 404

def test_revoked_or_expired_token_blocks_file_even_if_bytes_remain(
    app: Flask,
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    content = b"abcdefgh"
    upload_bytes(client, session_id, content)
    finalized = finalize(client, session_id).get_json()
    token = finalized["token"]
    payload = redis_json_object(redis_client, token_key(token))
    file_id = payload["files"][0]["file_id"]

    storage: UploadStorage = app.extensions["upload_storage"]
    stored = storage.shared_file(token, file_id)
    assert stored.is_file()

    redis_client.delete(token_key(token))
    assert client.get(f"/s/{token}/files/{file_id}").status_code == 404
    assert stored.is_file()

def test_lost_redis_completion_response_keeps_committed_file_consistent(
    client: FlaskClient,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def commit_then_disconnect(
        redis_backend: Any,
        current_session_id: str,
        transfer_id: str,
        claim_id: str,
        metadata: dict[str, Any],
        ttl: int,
    ) -> NoReturn:
        complete_transfer_state(redis_backend, current_session_id, transfer_id, claim_id, metadata, ttl)
        raise redis.ConnectionError("response lost after EXEC")

    monkeypatch.setattr("media_uploader.blueprints.uploads.complete_transfer", commit_then_disconnect)
    response, _ = post_chunk(client, session_id, b"abcdefgh")

    assert response.status_code == 200
    assert response.get_json()["status"] == "success"
    assert response.get_json()["recovered"] is True


def test_stale_finalizer_cannot_commit_or_abort_after_lease_is_reclaimed(
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    transfer_id = uuid.uuid4().hex
    spec = TransferSpec(
        transfer_id=transfer_id,
        filename="photo.jpg",
        extension="jpg",
        file_size=8,
        total_chunks=1,
    )
    ttl = 1800
    assert reserve_transfer(redis_client, session_id, spec, ttl, 4, 128, 30) == "reserved"

    first_state, first_claim = claim_transfer_completion(redis_client, session_id, transfer_id, ttl)
    assert first_state == "claimed"
    assert first_claim is not None

    redis_client.hset(transfer_key(session_id, transfer_id), "finalizing_at", int(time.time()) - 31)
    assert reserve_transfer(redis_client, session_id, spec, ttl, 4, 128, 30) == "reserved"

    second_state, second_claim = claim_transfer_completion(redis_client, session_id, transfer_id, ttl)
    assert second_state == "claimed"
    assert second_claim is not None
    assert second_claim != first_claim

    metadata = {
        "transfer_id": transfer_id,
        "file_id": uuid.uuid4().hex,
        "name": "photo.jpg",
        "extension": "jpg",
        "mime": "image/jpeg",
        "size": 8,
        "sha256": "0" * 64,
    }
    assert complete_transfer_state(redis_client, session_id, transfer_id, first_claim, metadata, ttl) is False
    assert abort_transfer(redis_client, session_id, transfer_id, 8, first_claim) is False
    assert redis_client.llen(completed_key(session_id)) == 0

    assert complete_transfer_state(redis_client, session_id, transfer_id, second_claim, metadata, ttl) is True
    assert redis_client.llen(completed_key(session_id)) == 1

def test_finalize_recovers_when_publish_did_not_commit(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_bytes(client, session_id, b"abcdefgh")

    def disconnect_before_publish(
        _redis_backend: Any,
        _current_session_id: str,
        _token: str,
        _files: list[dict[str, Any]],
        _token_ttl: int,
        **_kwargs: Any,
    ) -> NoReturn:
        raise redis.ConnectionError("redis unavailable")

    monkeypatch.setattr("media_uploader.blueprints.uploads.publish_share", disconnect_before_publish)
    failed = finalize(client, session_id)
    assert failed.status_code == 503

    pending = redis_text_hash(redis_client, session_key(session_id))
    token = pending["finalize_token"]
    assert pending["status"] == "finalizing"

    monkeypatch.setattr("media_uploader.blueprints.uploads.publish_share", publish_share_state)
    retried = finalize(client, session_id)
    assert retried.status_code == 200
    assert retried.get_json()["token"] == token
    assert client.get(f"/s/{token}").status_code == 200

def test_finalize_recovers_when_publish_committed_but_response_was_lost(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upload_bytes(client, session_id, b"abcdefgh")

    def publish_then_disconnect(
        redis_backend: Any,
        current_session_id: str,
        token: str,
        files: list[dict[str, Any]],
        token_ttl: int,
        **kwargs: Any,
    ) -> NoReturn:
        publish_share_state(redis_backend, current_session_id, token, files, token_ttl, **kwargs)
        raise redis.ConnectionError("response lost after EXEC")

    monkeypatch.setattr("media_uploader.blueprints.uploads.publish_share", publish_then_disconnect)
    recovered = finalize(client, session_id)

    assert recovered.status_code == 200
    token = recovered.get_json()["token"]
    assert redis_client.get(token_key(token)) is not None
    assert client.get(f"/s/{token}").status_code == 200

def test_finalize_is_idempotent_after_success(
    client: FlaskClient,
    session_id: str,
) -> None:
    upload_bytes(client, session_id, b"abcdefgh")
    first = finalize(client, session_id)
    second = finalize(client, session_id)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.get_json()["token"] == first.get_json()["token"]
    assert second.get_json()["shareable_url"] == first.get_json()["shareable_url"]

def test_same_filename_uses_distinct_server_storage_ids(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    upload_bytes(client, session_id, b"abcdefgh", filename="same.jpg")
    upload_bytes(client, session_id, b"ijklmnop", filename="same.jpg")
    token = finalize(client, session_id).get_json()["token"]
    payload = redis_json_object(redis_client, token_key(token))

    assert [item["name"] for item in payload["files"]] == ["same.jpg", "same.jpg"]
    file_ids = [item["file_id"] for item in payload["files"]]
    assert len(set(file_ids)) == 2

def test_out_of_order_chunks_are_assembled_when_complete(client: FlaskClient, session_id: str) -> None:
    transfer_id = uuid.uuid4().hex
    content = b"abcdefghijkl"
    last, _ = post_chunk(
        client,
        session_id,
        content[8:],
        transfer_id=transfer_id,
        file_size=len(content),
        chunk_index=1,
        total_chunks=2,
    )
    assert last.status_code == 200
    assert last.get_json()["status"] == "chunk_received"

    first, _ = post_chunk(
        client,
        session_id,
        content[:8],
        transfer_id=transfer_id,
        file_size=len(content),
        chunk_index=0,
        total_chunks=2,
    )
    assert first.status_code == 200
    assert first.get_json()["status"] == "success"

def test_finalize_rejects_incomplete_transfer(client: FlaskClient, session_id: str) -> None:
    response, _ = post_chunk(
        client,
        session_id,
        b"abcdefgh",
        file_size=12,
        chunk_index=0,
        total_chunks=2,
    )
    assert response.status_code == 200

    finalized = finalize(client, session_id)
    assert finalized.status_code == 409
    assert finalized.get_json()["error"]["code"] == "incomplete_session"

def test_cleanup_removes_expired_share_directory(
    app: Flask,
    client: FlaskClient,
    session_id: str,
) -> None:
    upload_bytes(client, session_id, b"abcdefgh")
    token = finalize(client, session_id).get_json()["token"]
    storage: UploadStorage = app.extensions["upload_storage"]
    share_dir = storage.share_dir(token)
    assert share_dir.is_dir()

    (share_dir / ".expires_at").write_text("0", encoding="ascii")
    result = storage.cleanup_expired(now=1)
    assert result["shares"] == 1
    assert not share_dir.exists()

def test_cleanup_removes_abandoned_session_directory(app: Flask, session_id: str) -> None:
    storage: UploadStorage = app.extensions["upload_storage"]
    session_dir = storage.session_dir(session_id)
    assert session_dir.is_dir()
    (session_dir / ".expires_at").write_text("0", encoding="ascii")

    result = storage.cleanup_expired(now=1)
    assert result["sessions"] == 1
    assert not session_dir.exists()

def test_unicode_filename_is_preserved_verbatim(
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    session_id: str,
) -> None:
    filename = "NОТО_Club_20260809_204554_767249429_1699591685501155_3227625225195879308_n.jpg"
    _, responses = upload_bytes(client, session_id, b"abcdefgh", filename=filename)
    assert responses[-1].status_code == 200

    finalized = finalize(client, session_id)
    assert finalized.status_code == 200
    token = finalized.get_json()["token"]
    payload = redis_json_object(redis_client, token_key(token))
    assert payload["files"][0]["name"] == filename

    summary = client.get(f"/s/{token}")
    assert summary.status_code == 200
    match = re.search(
        r'<script id="uploadedFilesData" type="application/json">(.*?)</script>',
        summary.get_data(as_text=True),
        re.DOTALL,
    )
    assert match is not None
    rendered_files = json.loads(match.group(1))
    assert rendered_files[0]["name"] == filename

    file_id = payload["files"][0]["file_id"]
    download = client.get(f"/s/{token}/files/{file_id}?download=1")
    assert download.status_code == 200
    assert quote(filename) in download.headers["Content-Disposition"]

def test_cleanup_reports_missing_or_invalid_expiry_markers(app: Flask, tmp_path: Path) -> None:
    storage = app.extensions["upload_storage"]

    missing_marker = storage.sessions_root / ("a" * 32)
    missing_marker.mkdir(parents=True)

    invalid_marker = storage.shares_root / ("b" * 32)
    invalid_marker.mkdir(parents=True)
    (invalid_marker / ".expires_at").write_text("not-a-timestamp", encoding="ascii")

    result = storage.cleanup_expired(now=10_000)

    assert result["errors"] == 2
    assert missing_marker.is_dir()
    assert invalid_marker.is_dir()

def test_disk_safety_margin_rejects_new_uploads(
    app: Flask, client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage: UploadStorage = app.extensions["upload_storage"]
    app.config["MIN_FREE_DISK_BYTES"] = 100
    monkeypatch.setattr(storage, "free_bytes", lambda: 99)

    response = client.get("/")

    assert response.status_code == 507
    assert response.get_json()["error"]["code"] == "insufficient_storage"

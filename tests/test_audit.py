from __future__ import annotations

import json
import logging
import re

import fakeredis
import pytest
from flask import Flask
from flask.testing import FlaskClient

from media_uploader.services.uploads import token_key
from tests.helpers import redis_json_object, upload_bytes


def _audit_payloads(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == "media_uploader.audit"]


def test_request_audit_tracks_share_open_and_download_without_logging_bearer_token(
    app: Flask,
    client: FlaskClient,
    redis_client: fakeredis.FakeRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.config["AUDIT_ENABLED"] = True
    caplog.set_level(logging.INFO, logger="media_uploader.audit")

    page = client.get("/")
    match = re.search(rb'data-session-id="([0-9a-f]{32})"', page.data)
    assert match is not None
    session_id = match.group(1).decode("ascii")
    upload_bytes(client, session_id, b"abcdefgh", filename="NОТО.jpg")
    finalized = client.post("/api/uploads/finalize", json={"session_id": session_id})
    assert finalized.status_code == 200
    token = finalized.get_json()["token"]
    payload = redis_json_object(redis_client, token_key(token))
    file_id = payload["files"][0]["file_id"]

    caplog.clear()
    headers = {"User-Agent": "AuditBrowser/1.0", "Accept-Language": "en-US,en;q=0.9"}
    summary = client.get(f"/s/{token}", headers=headers, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    assert summary.status_code == 200
    download = client.get(
        f"/s/{token}/files/{file_id}?download=1",
        headers=headers,
        environ_base={"REMOTE_ADDR": "203.0.113.9"},
    )
    assert download.status_code == 200

    events = _audit_payloads(caplog)
    event_names = {str(event["event"]) for event in events}
    assert "share_opened" in event_names
    assert "shared_file_downloaded" in event_names
    assert "http_request" in event_names
    assert any(event.get("client_ip") == "203.0.113.9" for event in events)
    assert any(event.get("user_agent") == "AuditBrowser/1.0" for event in events)
    assert all(token not in json.dumps(event) for event in events)


def test_audit_adds_request_id_header(app: Flask, client: FlaskClient) -> None:
    app.config["AUDIT_ENABLED"] = True
    response = client.get("/healthz")
    assert response.status_code == 200
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


def test_audit_visitor_identifier_is_stable_for_same_browser_and_differs_between_browsers(
    app: Flask, caplog: pytest.LogCaptureFixture
) -> None:
    app.config["AUDIT_ENABLED"] = True
    app.config["AUDIT_LOG_HEALTH"] = True
    caplog.set_level(logging.INFO, logger="media_uploader.audit")

    first = app.test_client()
    second = app.test_client()
    assert first.get("/healthz").status_code == 200
    assert first.get("/healthz").status_code == 200
    assert second.get("/healthz").status_code == 200

    events = [event for event in _audit_payloads(caplog) if event.get("event") == "http_request"]
    first_visitor = events[0]["visitor"]
    assert first_visitor is not None
    assert events[1]["visitor"] == first_visitor
    assert events[2]["visitor"] != first_visitor

def test_excluded_static_request_does_not_create_a_visitor(
    app: Flask,
    caplog: pytest.LogCaptureFixture,
) -> None:
    app.config["AUDIT_ENABLED"] = True
    app.config["AUDIT_LOG_STATIC"] = False
    caplog.set_level(logging.INFO, logger="media_uploader.audit")

    response = app.test_client().get("/static/css/styles.css")
    assert response.status_code == 200

    events = _audit_payloads(caplog)
    assert not any(event.get("event") == "visitor_first_seen" for event in events)
    assert not any(event.get("event") == "http_request" for event in events)

from __future__ import annotations

import re
from pathlib import Path

import fakeredis
import pytest
from flask import Flask
from flask.testing import FlaskClient

from media_uploader import create_app
from tests.helpers import build_test_config


@pytest.fixture
def redis_client() -> fakeredis.FakeRedis:
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def app(tmp_path: Path, redis_client: fakeredis.FakeRedis, monkeypatch: pytest.MonkeyPatch) -> Flask:
    application = create_app(
        build_test_config(tmp_path),
        redis_client=redis_client,
    )
    monkeypatch.setattr("media_uploader.blueprints.uploads.detect_mime", lambda _: "image/jpeg")
    return application


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def session_id(client: FlaskClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    match = re.search(rb'data-session-id="([0-9a-f]{32})"', response.data)
    assert match is not None
    return match.group(1).decode("ascii")

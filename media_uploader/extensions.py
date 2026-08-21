from __future__ import annotations

from typing import Any

import boto3
import redis
from botocore.client import Config as BotoConfig
from flask import Flask, current_app
from flask_wtf.csrf import CSRFProtect

from media_uploader.services.r2_storage import R2Storage

csrf = CSRFProtect()


def init_redis(app: Flask, client: Any | None = None) -> None:
    if client is None:
        client = redis.Redis.from_url(app.config["REDIS_URL"], decode_responses=True)
    app.extensions["redis"] = client


def get_redis() -> Any:
    return current_app.extensions["redis"]


def init_r2(app: Flask, client: Any | None = None) -> None:
    if app.config["STORAGE_BACKEND"] != "r2":
        return
    if client is None:
        account_id = str(app.config["R2_ACCOUNT_ID"])
        client = boto3.client(
            service_name="s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=app.config["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=app.config["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=BotoConfig(signature_version="s3v4"),
        )
    app.extensions["r2_storage"] = R2Storage(
        client,
        bucket=str(app.config["R2_BUCKET"]),
        delivery_base_url=str(app.config["R2_DELIVERY_BASE_URL"]),
        delivery_secret=str(app.config["R2_DELIVERY_SECRET"]),
        delivery_url_ttl_seconds=int(app.config["R2_DELIVERY_URL_TTL_SECONDS"]),
    )

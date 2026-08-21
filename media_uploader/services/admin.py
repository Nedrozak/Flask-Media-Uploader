from __future__ import annotations

import json
import time
from typing import Any, cast

import redis

SHARE_INDEX_KEY = "admin:shares:index"
SHARE_HISTORY_EXPIRY_KEY = "admin:shares:history-expiry"


def share_record_key(token: str) -> str:
    return f"admin:share:{token}"


def _share_token_key(token: str) -> str:
    return f"upload:share:{token}"


def queue_share_record(
    pipe: Any,
    *,
    token: str,
    payload: dict[str, Any],
    creator_visitor: str | None,
    creator_ip: str | None,
    creator_user_agent: str | None,
    history_seconds: int,
    source: str = "generated",
) -> None:
    created_at = int(payload["created_at"])
    expires_at = int(payload["expires_at"])
    files = [item for item in payload.get("files", []) if isinstance(item, dict)]
    total_bytes = sum(int(item.get("size", 0)) for item in files)
    history_expires_at = expires_at + history_seconds

    key = share_record_key(token)
    pipe.hset(
        key,
        mapping={
            "created_at": created_at,
            "expires_at": expires_at,
            "creator_visitor": creator_visitor or "",
            "creator_ip": creator_ip or "",
            "creator_user_agent": creator_user_agent or "",
            "file_count": len(files),
            "total_bytes": total_bytes,
            "files_json": json.dumps(files, ensure_ascii=False, separators=(",", ":")),
            "opens": 0,
            "previews": 0,
            "downloads": 0,
            "last_access_at": 0,
            "last_access_visitor": "",
            "last_access_ip": "",
            "revoked_at": 0,
            "revoked_by": "",
            "source": source,
        },
    )
    pipe.expireat(key, history_expires_at)
    pipe.zadd(SHARE_INDEX_KEY, {token: created_at})
    pipe.zadd(SHARE_HISTORY_EXPIRY_KEY, {token: history_expires_at})


def _decode_share_payload(raw: Any) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        return None
    return cast(dict[str, Any], payload)



def ensure_share_record(
    client: Any,
    token: str,
    payload: dict[str, Any],
    history_seconds: int,
) -> None:
    key = share_record_key(token)
    for _ in range(3):
        pipe = client.pipeline()
        try:
            pipe.watch(key)
            if pipe.exists(key):
                pipe.unwatch()
                return
            pipe.multi()
            queue_share_record(
                pipe,
                token=token,
                payload=payload,
                creator_visitor=None,
                creator_ip=None,
                creator_user_agent=None,
                history_seconds=history_seconds,
                source="discovered",
            )
            pipe.execute()
            return
        except redis.WatchError:
            continue
        finally:
            pipe.reset()

def discover_active_shares(client: Any, history_seconds: int) -> int:
    """Index active shares that predate the admin registry.

    Creator metadata cannot be reconstructed for those shares, but indexing them
    keeps the admin view authoritative for every currently live bearer token.
    """

    discovered = 0
    for raw_key in client.scan_iter(match="upload:share:*", count=200):
        key = str(raw_key)
        token = key.removeprefix("upload:share:")
        if len(token) != 32 or client.zscore(SHARE_INDEX_KEY, token) is not None:
            continue

        payload = _decode_share_payload(client.get(key))
        if payload is None:
            continue

        ensure_share_record(client, token, payload, history_seconds)
        discovered += 1
    return discovered


def prune_share_history(client: Any, now: int | None = None) -> int:
    timestamp = int(time.time()) if now is None else now
    expired_tokens = [str(token) for token in client.zrangebyscore(SHARE_HISTORY_EXPIRY_KEY, "-inf", timestamp)]
    if not expired_tokens:
        return 0

    pipe = client.pipeline(transaction=True)
    pipe.zrem(SHARE_INDEX_KEY, *expired_tokens)
    pipe.zrem(SHARE_HISTORY_EXPIRY_KEY, *expired_tokens)
    for token in expired_tokens:
        pipe.delete(share_record_key(token))
    pipe.execute()
    return len(expired_tokens)


def _record_from_hash(client: Any, token: str, raw: dict[str, str], now: int) -> dict[str, Any]:
    expires_at = int(raw.get("expires_at", 0))
    revoked_at = int(raw.get("revoked_at", 0))
    live = bool(client.exists(_share_token_key(token)))
    status = "revoked" if revoked_at else "active" if live and expires_at > now else "expired"

    files_payload: list[dict[str, Any]] = []
    try:
        decoded_files = json.loads(raw.get("files_json", "[]"))
        if isinstance(decoded_files, list):
            files_payload = [cast(dict[str, Any], item) for item in decoded_files if isinstance(item, dict)]
    except json.JSONDecodeError:
        files_payload = []

    return {
        "token": token,
        "created_at": int(raw.get("created_at", 0)),
        "expires_at": expires_at,
        "creator_visitor": raw.get("creator_visitor") or None,
        "creator_ip": raw.get("creator_ip") or None,
        "creator_user_agent": raw.get("creator_user_agent") or None,
        "file_count": int(raw.get("file_count", 0)),
        "total_bytes": int(raw.get("total_bytes", 0)),
        "files": files_payload,
        "opens": int(raw.get("opens", 0)),
        "previews": int(raw.get("previews", 0)),
        "downloads": int(raw.get("downloads", 0)),
        "last_access_at": int(raw.get("last_access_at", 0)),
        "last_access_visitor": raw.get("last_access_visitor") or None,
        "last_access_ip": raw.get("last_access_ip") or None,
        "revoked_at": revoked_at,
        "revoked_by": raw.get("revoked_by") or None,
        "source": raw.get("source", "generated"),
        "status": status,
    }


def list_share_records(
    client: Any,
    *,
    page: int,
    page_size: int,
    history_seconds: int,
) -> tuple[list[dict[str, Any]], int]:
    discover_active_shares(client, history_seconds)
    prune_share_history(client)

    page = max(page, 1)
    start = (page - 1) * page_size
    stop = start + page_size - 1
    tokens = [str(token) for token in client.zrevrange(SHARE_INDEX_KEY, start, stop)]
    total = int(client.zcard(SHARE_INDEX_KEY))
    if not tokens:
        return [], total

    pipe = client.pipeline(transaction=False)
    for token in tokens:
        pipe.hgetall(share_record_key(token))
    raw_records = pipe.execute()

    now = int(time.time())
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    for token, raw in zip(tokens, raw_records, strict=True):
        record_hash = cast(dict[str, str], raw)
        if not record_hash:
            missing.append(token)
            continue
        records.append(_record_from_hash(client, token, record_hash, now))

    if missing:
        pipe = client.pipeline(transaction=True)
        pipe.zrem(SHARE_INDEX_KEY, *missing)
        pipe.zrem(SHARE_HISTORY_EXPIRY_KEY, *missing)
        pipe.execute()
        total = max(0, total - len(missing))

    return records, total


def get_share_record(client: Any, token: str) -> dict[str, Any] | None:
    raw = cast(dict[str, str], client.hgetall(share_record_key(token)))
    if not raw:
        return None
    return _record_from_hash(client, token, raw, int(time.time()))


def record_share_activity(
    client: Any,
    token: str,
    *,
    activity: str,
    visitor: str | None,
    client_ip: str | None,
) -> None:
    field = {
        "open": "opens",
        "preview": "previews",
        "download": "downloads",
    }.get(activity)
    if field is None:
        raise ValueError("Unknown share activity")

    key = share_record_key(token)
    now = int(time.time())
    for _ in range(3):
        pipe = client.pipeline()
        try:
            pipe.watch(key)
            if not pipe.exists(key):
                pipe.unwatch()
                return
            pipe.multi()
            pipe.hincrby(key, field, 1)
            pipe.hset(
                key,
                mapping={
                    "last_access_at": now,
                    "last_access_visitor": visitor or "",
                    "last_access_ip": client_ip or "",
                },
            )
            pipe.execute()
            return
        except redis.WatchError:
            continue
        finally:
            pipe.reset()


def revoke_share(client: Any, token: str, revoked_by: str | None) -> bool:
    key = share_record_key(token)
    now = int(time.time())

    for _ in range(3):
        pipe = client.pipeline()
        try:
            pipe.watch(key)
            if not pipe.exists(key):
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.delete(_share_token_key(token))
            pipe.hset(
                key,
                mapping={
                    "revoked_at": now,
                    "revoked_by": revoked_by or "",
                    "last_access_at": now,
                },
            )
            pipe.execute()
            return True
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    return False

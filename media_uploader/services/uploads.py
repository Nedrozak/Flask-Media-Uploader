from __future__ import annotations

import json
import time
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import redis
from magic import from_buffer, from_file

from media_uploader.services.admin import queue_share_record


class UploadError(RuntimeError):
    code = "upload_error"
    status = 400


class InvalidSession(UploadError):
    code = "invalid_session"


class SessionBusy(UploadError):
    code = "session_busy"
    status = 409


class QuotaExceeded(UploadError):
    code = "session_quota_exceeded"
    status = 413


class InvalidUpload(UploadError):
    code = "invalid_upload"


class IncompleteSession(UploadError):
    code = "incomplete_session"
    status = 409


@dataclass(frozen=True)
class TransferSpec:
    transfer_id: str
    filename: str
    extension: str
    file_size: int
    total_chunks: int


def session_key(session_id: str) -> str:
    return f"upload:session:{session_id}"


def completed_key(session_id: str) -> str:
    return f"upload:session:{session_id}:completed"


def transfer_key(session_id: str, transfer_id: str) -> str:
    return f"upload:session:{session_id}:transfer:{transfer_id}"


def token_key(token: str) -> str:
    return f"upload:share:{token}"


def finalized_key(session_id: str) -> str:
    return f"upload:finalized:{session_id}"


def validate_hex_id(value: str | None, label: str) -> str:
    if not value:
        raise InvalidUpload(f"Missing {label}")
    try:
        return uuid.UUID(value).hex
    except (ValueError, AttributeError, TypeError) as exc:
        raise InvalidUpload(f"Invalid {label}") from exc


_BIDI_CONTROL_CHARACTERS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def validate_filename(original_name: str, max_length: int, allowed_mime: dict[str, set[str]]) -> tuple[str, str]:
    """Validate an upload filename while preserving its Unicode spelling verbatim."""
    if not original_name or original_name in {".", ".."}:
        raise InvalidUpload("Invalid filename")
    if len(original_name) > max_length:
        raise InvalidUpload(f"Filename exceeds {max_length} characters")
    if "/" in original_name or "\\" in original_name:
        raise InvalidUpload("Filename must not contain path separators")
    if any(unicodedata.category(character) == "Cc" for character in original_name):
        raise InvalidUpload("Filename must not contain control characters")
    if any(character in _BIDI_CONTROL_CHARACTERS for character in original_name):
        raise InvalidUpload("Filename must not contain bidirectional control characters")

    normalized_for_validation = unicodedata.normalize("NFKC", original_name)
    if "." not in normalized_for_validation:
        raise InvalidUpload("Invalid filename")
    extension = normalized_for_validation.rsplit(".", 1)[1].casefold()
    if extension not in allowed_mime:
        raise InvalidUpload("File extension is not allowed")
    return original_name, extension


def detect_mime(file_path: Path) -> str | None:
    try:
        detected = from_file(str(file_path), mime=True)
        return str(detected) if detected else None
    except Exception:
        return None


def detect_mime_bytes(content: bytes) -> str | None:
    try:
        detected = from_buffer(content, mime=True)
        return str(detected) if detected else None
    except Exception:
        return None


def create_session(client: Any, session_id: str, ttl: int) -> int:
    now = int(time.time())
    expires_at = now + ttl
    pipe = client.pipeline(transaction=True)
    pipe.hset(
        session_key(session_id),
        mapping={
            "status": "open",
            "created_at": now,
            "reserved_bytes": 0,
            "reserved_files": 0,
        },
    )
    pipe.expire(session_key(session_id), ttl)
    pipe.execute()
    return expires_at


def reserve_transfer(
    client: Any,
    session_id: str,
    spec: TransferSpec,
    ttl: int,
    max_files: int,
    max_bytes: int,
    finalize_timeout: int,
) -> str:
    s_key = session_key(session_id)
    t_key = transfer_key(session_id, spec.transfer_id)

    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(s_key, t_key)
            session = cast(dict[str, str], pipe.hgetall(s_key))
            if not session or session.get("status") != "open" or int(pipe.ttl(s_key)) <= 0:
                raise InvalidSession("Unknown or expired upload session")

            existing = cast(dict[str, str], pipe.hgetall(t_key))
            if existing:
                if (
                    existing.get("filename") != spec.filename
                    or int(existing.get("file_size", -1)) != spec.file_size
                    or int(existing.get("total_chunks", -1)) != spec.total_chunks
                ):
                    raise InvalidUpload("Upload metadata changed for an existing transfer")
                state = existing.get("state", "reserved")
                if state == "finalizing":
                    finalizing_at = int(existing.get("finalizing_at", 0))
                    if finalizing_at and int(time.time()) - finalizing_at >= finalize_timeout:
                        pipe.multi()
                        pipe.hset(t_key, "state", "reserved")
                        pipe.hdel(t_key, "finalizing_at", "finalizing_claim")
                        pipe.expire(t_key, ttl)
                        pipe.execute()
                        return "reserved"
                pipe.unwatch()
                return state

            reserved_files = int(session.get("reserved_files", 0))
            reserved_bytes = int(session.get("reserved_bytes", 0))
            if reserved_files + 1 > max_files:
                raise QuotaExceeded("Too many files in this upload session")
            if reserved_bytes + spec.file_size > max_bytes:
                raise QuotaExceeded("Upload session byte quota exceeded")

            pipe.multi()
            pipe.hincrby(s_key, "reserved_files", 1)
            pipe.hincrby(s_key, "reserved_bytes", spec.file_size)
            pipe.expire(s_key, ttl)
            pipe.hset(
                t_key,
                mapping={
                    "state": "reserved",
                    "filename": spec.filename,
                    "extension": spec.extension,
                    "file_size": spec.file_size,
                    "total_chunks": spec.total_chunks,
                },
            )
            pipe.expire(t_key, ttl)
            pipe.execute()
            return "reserved"
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("Upload session is busy; retry the request")


def claim_transfer_completion(
    client: Any,
    session_id: str,
    transfer_id: str,
    ttl: int,
) -> tuple[str, str | None]:
    t_key = transfer_key(session_id, transfer_id)
    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(t_key)
            transfer = cast(dict[str, str], pipe.hgetall(t_key))
            if not transfer:
                raise InvalidUpload("Unknown file transfer")
            state = transfer.get("state", "reserved")
            if state != "reserved":
                pipe.unwatch()
                return state, None
            claim_id = uuid.uuid4().hex
            pipe.multi()
            pipe.hset(
                t_key,
                mapping={
                    "state": "finalizing",
                    "finalizing_at": int(time.time()),
                    "finalizing_claim": claim_id,
                },
            )
            pipe.expire(t_key, ttl)
            pipe.execute()
            return "claimed", claim_id
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("File transfer is busy; retry the request")


def reset_transfer(
    client: Any,
    session_id: str,
    transfer_id: str,
    claim_id: str,
    ttl: int,
) -> bool:
    t_key = transfer_key(session_id, transfer_id)
    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(t_key)
            transfer = cast(dict[str, str], pipe.hgetall(t_key))
            if (
                not transfer
                or transfer.get("state") != "finalizing"
                or transfer.get("finalizing_claim") != claim_id
            ):
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.hset(t_key, "state", "reserved")
            pipe.hdel(t_key, "finalizing_at", "finalizing_claim")
            pipe.expire(t_key, ttl)
            pipe.execute()
            return True
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("File transfer is busy; retry the request")


def refresh_transfer(client: Any, session_id: str, transfer_id: str, ttl: int) -> None:
    pipe = client.pipeline(transaction=True)
    pipe.expire(session_key(session_id), ttl)
    pipe.expire(transfer_key(session_id, transfer_id), ttl)
    pipe.expire(completed_key(session_id), ttl)
    pipe.execute()


def abort_transfer(
    client: Any,
    session_id: str,
    transfer_id: str,
    file_size: int,
    claim_id: str | None = None,
) -> bool:
    s_key = session_key(session_id)
    t_key = transfer_key(session_id, transfer_id)
    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(s_key, t_key)
            transfer = cast(dict[str, str], pipe.hgetall(t_key))
            session = cast(dict[str, str], pipe.hgetall(s_key))
            if not transfer or not session:
                pipe.unwatch()
                return False
            state = transfer.get("state", "reserved")
            owns_transfer = state == "reserved" if claim_id is None else (
                state == "finalizing" and transfer.get("finalizing_claim") == claim_id
            )
            if not owns_transfer:
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.hincrby(s_key, "reserved_files", -1)
            pipe.hincrby(s_key, "reserved_bytes", -file_size)
            pipe.delete(t_key)
            pipe.execute()
            return True
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("File transfer is busy; retry the request")


def complete_transfer(
    client: Any,
    session_id: str,
    transfer_id: str,
    claim_id: str,
    metadata: dict[str, Any],
    ttl: int,
) -> bool:
    t_key = transfer_key(session_id, transfer_id)
    c_key = completed_key(session_id)
    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(t_key)
            transfer = cast(dict[str, str], pipe.hgetall(t_key))
            if (
                not transfer
                or transfer.get("state") != "finalizing"
                or transfer.get("finalizing_claim") != claim_id
            ):
                pipe.unwatch()
                return False
            pipe.multi()
            pipe.hset(t_key, mapping={"state": "completed", "file_id": metadata["file_id"]})
            pipe.hdel(t_key, "finalizing_at", "finalizing_claim")
            pipe.expire(t_key, ttl)
            pipe.rpush(c_key, json.dumps(metadata, separators=(",", ":")))
            pipe.expire(c_key, ttl)
            pipe.expire(session_key(session_id), ttl)
            pipe.execute()
            return True
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("File transfer is busy; retry the request")


def read_transfer(client: Any, session_id: str, transfer_id: str) -> dict[str, str]:
    return cast(dict[str, str], client.hgetall(transfer_key(session_id, transfer_id)))


def attach_remote_upload(
    client: Any,
    session_id: str,
    transfer_id: str,
    *,
    upload_id: str,
    object_key: str,
    detected_mime: str,
    ttl: int,
) -> tuple[bool, dict[str, str]]:
    """Attach an R2 multipart upload to a transfer.

    The browser sends chunks for each file sequentially, but retries can still
    race. HSETNX ensures only one multipart upload wins; callers abort a losing
    upload and reuse the already-attached one.
    """
    key = transfer_key(session_id, transfer_id)
    attached = bool(client.hsetnx(key, "remote_upload_id", upload_id))
    if attached:
        pipe = client.pipeline(transaction=True)
        pipe.hset(
            key,
            mapping={
                "remote_object_key": object_key,
                "detected_mime": detected_mime,
            },
        )
        pipe.expire(key, ttl)
        pipe.execute()
    transfer = cast(dict[str, str], client.hgetall(key))
    return attached, transfer


def record_remote_part(
    client: Any,
    session_id: str,
    transfer_id: str,
    part_number: int,
    etag: str,
    ttl: int,
) -> None:
    key = transfer_key(session_id, transfer_id)
    pipe = client.pipeline(transaction=True)
    pipe.hset(key, f"remote_part:{part_number}", etag)
    pipe.expire(key, ttl)
    pipe.execute()


def remote_parts(
    client: Any,
    session_id: str,
    transfer_id: str,
    total_chunks: int,
) -> list[dict[str, int | str]] | None:
    key = transfer_key(session_id, transfer_id)
    fields = [f"remote_part:{index}" for index in range(1, total_chunks + 1)]
    values = client.hmget(key, fields)
    if len(values) != total_chunks or any(not value for value in values):
        return None
    parts: list[dict[str, int | str]] = []
    for index, value in enumerate(values, start=1):
        normalized_etag = str(value).strip().strip('"')
        parts.append({"PartNumber": index, "ETag": f'"{normalized_etag}"'})
    return parts


def prepare_finalize(
    client: Any,
    session_id: str,
    proposed_token: str,
    ttl: int,
) -> tuple[list[dict[str, Any]], str]:
    s_key = session_key(session_id)
    c_key = completed_key(session_id)
    for _ in range(5):
        pipe = client.pipeline()
        try:
            pipe.watch(s_key, c_key)
            session = cast(dict[str, str], pipe.hgetall(s_key))
            if not session or int(pipe.ttl(s_key)) <= 0:
                raise InvalidSession("Unknown or expired upload session")

            status = session.get("status", "open")
            if status not in {"open", "finalizing"}:
                raise SessionBusy("Upload session cannot be finalized")

            raw_files = pipe.lrange(c_key, 0, -1)
            reserved_files = int(session.get("reserved_files", 0))
            if reserved_files == 0:
                raise IncompleteSession("No files were uploaded")
            if len(raw_files) != reserved_files:
                raise IncompleteSession("One or more files have not finished uploading")
            files = [json.loads(item) for item in raw_files]

            existing_token = session.get("finalize_token")
            if status == "finalizing" and existing_token:
                token = validate_hex_id(existing_token, "finalize_token")
            else:
                token = proposed_token

            pipe.multi()
            pipe.hset(s_key, mapping={"status": "finalizing", "finalize_token": token})
            pipe.expire(s_key, ttl)
            pipe.expire(c_key, ttl)
            pipe.execute()
            return files, token
        except redis.WatchError:
            continue
        finally:
            pipe.reset()
    raise SessionBusy("Upload session is busy; retry the request")


def publish_share(
    client: Any,
    session_id: str,
    token: str,
    files: list[dict[str, Any]],
    token_ttl: int,
    *,
    creator_visitor: str | None = None,
    creator_ip: str | None = None,
    creator_user_agent: str | None = None,
    admin_history_seconds: int = 0,
) -> dict[str, Any]:
    now = int(time.time())
    payload = {"created_at": now, "expires_at": now + token_ttl, "files": files}
    transfer_keys = [transfer_key(session_id, item["transfer_id"]) for item in files]
    pipe = client.pipeline(transaction=True)
    pipe.set(token_key(token), json.dumps(payload, separators=(",", ":")), ex=token_ttl)
    pipe.set(finalized_key(session_id), token, ex=token_ttl)
    pipe.delete(session_key(session_id), completed_key(session_id), *transfer_keys)
    if admin_history_seconds > 0:
        queue_share_record(
            pipe,
            token=token,
            payload=payload,
            creator_visitor=creator_visitor,
            creator_ip=creator_ip,
            creator_user_agent=creator_user_agent,
            history_seconds=admin_history_seconds,
        )
    pipe.execute()
    return payload


def load_finalized_token(client: Any, session_id: str) -> str | None:
    token = client.get(finalized_key(session_id))
    if not token:
        return None
    try:
        return validate_hex_id(str(token), "token")
    except InvalidUpload:
        return None


def load_share(client: Any, token: str) -> tuple[dict[str, Any], int] | None:
    key = token_key(token)
    pipe = client.pipeline(transaction=False)
    pipe.get(key)
    pipe.ttl(key)
    raw, ttl = pipe.execute()
    if not raw or int(ttl) <= 0:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        return None
    return payload, int(ttl)

from __future__ import annotations

import math
import time
import uuid
from pathlib import Path
from typing import Any

import redis
from flask import Blueprint, Response, current_app, g, jsonify, request, url_for

from media_uploader.audit import audit_event, fingerprint
from media_uploader.extensions import get_redis
from media_uploader.responses import json_error
from media_uploader.security import rate_limit
from media_uploader.services.storage import (
    ChunkSizeError,
    MissingChunksError,
    StorageCapacityError,
    StorageError,
    UploadStorage,
)
from media_uploader.services.uploads import (
    InvalidUpload,
    TransferSpec,
    UploadError,
    abort_transfer,
    claim_transfer_completion,
    complete_transfer,
    detect_mime,
    load_finalized_token,
    load_share,
    prepare_finalize,
    publish_share,
    read_transfer,
    refresh_transfer,
    reserve_transfer,
    reset_transfer,
    validate_filename,
    validate_hex_id,
)

bp = Blueprint("uploads", __name__, url_prefix="/api/uploads")


def _error_from_upload(exc: UploadError) -> tuple[Response, int]:
    return json_error(exc.code, str(exc), exc.status)


def _parse_int_field(name: str) -> int:
    raw = request.form.get(name)
    if raw is None:
        raise InvalidUpload(f"Missing {name}")
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidUpload(f"Invalid {name}") from exc


@bp.post("/chunks")
@rate_limit("upload-chunk", "RATE_LIMIT_UPLOAD_CHUNKS")
def upload_chunk() -> tuple[Response, int] | Response:
    try:
        session_id = validate_hex_id(request.form.get("session_id"), "session_id")
        transfer_id = validate_hex_id(request.form.get("fileUuid"), "fileUuid")
        original_name = request.form.get("original_filename", "")
        if not original_name:
            raise InvalidUpload("Missing original_filename")

        allowed_mime: dict[str, set[str]] = current_app.config["ALLOWED_MIME_BY_EXTENSION"]
        filename, extension = validate_filename(
            original_name,
            int(current_app.config["MAX_FILENAME_LENGTH"]),
            allowed_mime,
        )
        file_size = _parse_int_field("file_size")
        chunk_index = _parse_int_field("chunk_index")
        total_chunks = _parse_int_field("total_chunks")

        chunk_size = int(current_app.config["CHUNK_SIZE"])
        max_file_size = int(current_app.config["MAX_FILE_SIZE"])
        if file_size <= 0 or file_size > max_file_size:
            raise InvalidUpload("File size is outside the allowed range")
        expected_total = math.ceil(file_size / chunk_size)
        if total_chunks != expected_total or total_chunks < 1:
            raise InvalidUpload("total_chunks does not match the declared file size")
        if total_chunks > int(current_app.config["MAX_CHUNKS_PER_FILE"]):
            raise InvalidUpload("Too many chunks for one file")
        if chunk_index < 0 or chunk_index >= total_chunks:
            raise InvalidUpload("chunk_index is outside the valid range")

        expected_chunk_size = min(chunk_size, file_size - (chunk_index * chunk_size))
        if expected_chunk_size <= 0:
            raise InvalidUpload("Invalid chunk size")
        chunk = request.files.get("chunk")
        if chunk is None:
            raise InvalidUpload("Missing chunk data")

        storage: UploadStorage = current_app.extensions["upload_storage"]
        min_free_disk = int(current_app.config["MIN_FREE_DISK_BYTES"])
        storage.ensure_capacity(expected_chunk_size, min_free_disk)

        spec = TransferSpec(
            transfer_id=transfer_id,
            filename=filename,
            extension=extension,
            file_size=file_size,
            total_chunks=total_chunks,
        )
        client = get_redis()
        ttl = int(current_app.config["SESSION_EXPIRY_SECONDS"])
        state = reserve_transfer(
            client,
            session_id,
            spec,
            ttl,
            int(current_app.config["MAX_FILES_PER_SESSION"]),
            int(current_app.config["MAX_SESSION_BYTES"]),
            int(current_app.config["TRANSFER_FINALIZE_TIMEOUT_SECONDS"]),
        )
        if state == "completed":
            return jsonify({"status": "success", "already_completed": True})
        if state != "reserved":
            return json_error("transfer_busy", "This file is currently being finalized.", 409)

        cleanup_grace = int(current_app.config["CLEANUP_EXPIRY_GRACE_SECONDS"])
        expires_at = int(time.time()) + ttl + cleanup_grace
        storage.ensure_session(session_id, expires_at)
        storage.save_chunk(
            session_id,
            transfer_id,
            chunk_index,
            chunk,
            expected_chunk_size,
            chunk_size,
        )
        refresh_transfer(client, session_id, transfer_id, ttl)
        storage.refresh_session(session_id, expires_at)

        if not storage.all_chunks_present(session_id, transfer_id, total_chunks):
            return jsonify({"status": "chunk_received"})

        claim_state, claim_id = claim_transfer_completion(client, session_id, transfer_id, ttl)
        if claim_state == "completed":
            return jsonify({"status": "success", "already_completed": True})
        if claim_state != "claimed" or claim_id is None:
            return json_error("transfer_busy", "This file is currently being finalized.", 409)

        assembled_path: Path | None = None
        committed_path: Path | None = None
        try:
            assembled_path, sha256 = storage.assemble(session_id, transfer_id, total_chunks, file_size, min_free_disk)
            detected_mime = detect_mime(assembled_path)
            if detected_mime not in allowed_mime[extension]:
                raise InvalidUpload("Detected file type does not match the filename extension")

            file_id = uuid.uuid4().hex
            committed_path = storage.commit_file(session_id, assembled_path, file_id)
            assembled_path = None
            metadata: dict[str, Any] = {
                "transfer_id": transfer_id,
                "file_id": file_id,
                "name": filename,
                "extension": extension,
                "mime": detected_mime,
                "size": file_size,
                "sha256": sha256,
            }
            if not complete_transfer(client, session_id, transfer_id, claim_id, metadata, ttl):
                committed_path.unlink(missing_ok=True)
                return json_error(
                    "transfer_lease_lost",
                    "This file finalization was superseded by a newer retry. Retry the upload.",
                    409,
                )
            storage.cleanup_transfer(session_id, transfer_id)
            current_app.logger.debug(
                "Completed file transfer session=%s transfer=%s file=%s size=%d",
                fingerprint(session_id),
                fingerprint(transfer_id),
                file_id,
                file_size,
            )
            audit_event(
                "upload_file_completed",
                session=fingerprint(session_id),
                transfer=fingerprint(transfer_id),
                file_id=file_id,
                filename=filename,
                size=file_size,
                mime=detected_mime,
                sha256=sha256,
            )
            return jsonify({"status": "success", "file_id": file_id})
        except InvalidUpload:
            if assembled_path is not None:
                assembled_path.unlink(missing_ok=True)
            if committed_path is not None:
                committed_path.unlink(missing_ok=True)
            if abort_transfer(client, session_id, transfer_id, file_size, claim_id):
                storage.cleanup_transfer(session_id, transfer_id)
            raise
        except StorageCapacityError as exc:
            if assembled_path is not None:
                assembled_path.unlink(missing_ok=True)
            current_app.logger.warning("File assembly rejected by disk safety margin: %s", exc)
            try:
                reset_transfer(client, session_id, transfer_id, claim_id, ttl)
            except redis.RedisError:
                current_app.logger.warning(
                    "Could not reset transfer %s after capacity rejection",
                    fingerprint(transfer_id),
                    exc_info=True,
                )
            audit_event(
                "upload_rejected",
                reason="insufficient_storage",
                session=fingerprint(session_id),
                transfer=fingerprint(transfer_id),
                free_bytes=storage.free_bytes(),
                min_free_bytes=min_free_disk,
            )
            return json_error(
                "insufficient_storage",
                "The server does not have enough free storage to assemble this file.",
                507,
            )
        except (StorageError, MissingChunksError, OSError):
            if assembled_path is not None:
                assembled_path.unlink(missing_ok=True)
            if committed_path is not None:
                committed_path.unlink(missing_ok=True)
            current_app.logger.exception("Could not finalize file transfer %s", fingerprint(transfer_id))
            try:
                reset_transfer(client, session_id, transfer_id, claim_id, ttl)
            except redis.RedisError:
                current_app.logger.warning(
                    "Could not reset transfer %s after storage error",
                    fingerprint(transfer_id),
                    exc_info=True,
                )
            return json_error("storage_error", "The uploaded file could not be stored.", 500)
        except redis.RedisError:
            if assembled_path is not None:
                assembled_path.unlink(missing_ok=True)
            current_app.logger.exception("Redis failed while completing transfer %s", fingerprint(transfer_id))

            # EXEC may have committed even when the client lost the response. Re-read
            # the transfer before deleting bytes so an ambiguous network failure cannot
            # turn a committed Redis record into a missing file.
            if committed_path is not None:
                try:
                    transfer = read_transfer(client, session_id, transfer_id)
                    if transfer.get("state") == "completed" and transfer.get("file_id") == committed_path.name:
                        storage.cleanup_transfer(session_id, transfer_id)
                        return jsonify(
                            {
                                "status": "success",
                                "file_id": committed_path.name,
                                "recovered": True,
                            }
                        )
                    if transfer and transfer.get("state") != "completed":
                        reset_transfer(client, session_id, transfer_id, claim_id, ttl)
                    committed_path.unlink(missing_ok=True)
                except (redis.RedisError, UploadError):
                    current_app.logger.warning(
                        "Could not resolve ambiguous Redis completion for transfer %s",
                        fingerprint(transfer_id),
                        exc_info=True,
                    )
            return json_error("service_unavailable", "The upload service is temporarily unavailable.", 503)

    except StorageCapacityError as exc:
        current_app.logger.warning("Upload rejected by disk safety margin: %s", exc)
        audit_event(
            "upload_rejected",
            reason="insufficient_storage",
            free_bytes=current_app.extensions["upload_storage"].free_bytes(),
            min_free_bytes=int(current_app.config["MIN_FREE_DISK_BYTES"]),
        )
        return json_error("insufficient_storage", "The server does not have enough free storage for this upload.", 507)
    except ChunkSizeError as exc:
        current_app.logger.debug("Rejected invalid chunk: %s", exc)
        try:
            if "session_id" in locals() and "transfer_id" in locals() and "file_size" in locals():
                storage = current_app.extensions["upload_storage"]
                if abort_transfer(get_redis(), session_id, transfer_id, file_size):
                    storage.cleanup_transfer(session_id, transfer_id)
        except Exception:
            current_app.logger.warning("Failed to clean rejected chunk", exc_info=True)
        return json_error("invalid_chunk_size", "Chunk size does not match the upload metadata.", 400)
    except UploadError as exc:
        return _error_from_upload(exc)
    except redis.RedisError:
        current_app.logger.exception("Redis unavailable during upload")
        return json_error("service_unavailable", "The upload service is temporarily unavailable.", 503)
    except OSError:
        current_app.logger.exception("Storage unavailable during upload")
        return json_error("storage_unavailable", "Upload storage is temporarily unavailable.", 503)


def _finalize_response(token: str, payload: dict[str, Any]) -> Response:
    return jsonify(
        {
            "token": token,
            "shareable_url": url_for("shares.summary", token=token, _external=True),
            "expires_at": payload["expires_at"],
        }
    )


@bp.post("/finalize")
@rate_limit("upload-finalize", "RATE_LIMIT_FINALIZE")
def finalize_upload() -> tuple[Response, int] | Response:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return json_error("invalid_json", "A JSON object is required.", 400)

    token_ttl = int(current_app.config["TOKEN_EXPIRY_SECONDS"])
    cleanup_grace = int(current_app.config["CLEANUP_EXPIRY_GRACE_SECONDS"])
    try:
        session_id = validate_hex_id(data.get("session_id"), "session_id")
        client = get_redis()
        existing_token = load_finalized_token(client, session_id)
        if existing_token is not None:
            loaded = load_share(client, existing_token)
            if loaded is not None:
                payload, _ = loaded
                return _finalize_response(existing_token, payload)

        files, token = prepare_finalize(
            client,
            session_id,
            uuid.uuid4().hex,
            token_ttl,
        )
    except UploadError as exc:
        return _error_from_upload(exc)
    except redis.RedisError:
        current_app.logger.exception("Redis unavailable while preparing finalization")
        return json_error("service_unavailable", "The upload service is temporarily unavailable.", 503)

    storage: UploadStorage = current_app.extensions["upload_storage"]
    expires_at = int(time.time()) + token_ttl + cleanup_grace
    try:
        storage.refresh_session(session_id, expires_at)
        storage.finalize_session(session_id, token, expires_at)
    except (StorageError, OSError):
        current_app.logger.exception("Could not move session %s into share storage", fingerprint(session_id))
        return json_error("storage_error", "The upload session could not be finalized.", 500)

    try:
        payload = publish_share(
            client,
            session_id,
            token,
            files,
            token_ttl,
            creator_visitor=getattr(g, "visitor_fingerprint", None),
            creator_ip=request.remote_addr,
            creator_user_agent=request.headers.get("User-Agent", "")[:512] or None,
            admin_history_seconds=(
                int(current_app.config["ADMIN_SHARE_HISTORY_DAYS"]) * 24 * 60 * 60
                if current_app.config.get("SHARE_REGISTRY_ENABLED", False)
                else 0
            ),
        )
    except redis.RedisError:
        current_app.logger.exception("Could not publish share token for session %s", fingerprint(session_id))

        # A lost EXEC response is ambiguous. Never move the bytes back here: if the
        # transaction committed, doing so would break a live share. The stable
        # finalize_token stored on the session lets a later retry finish safely when
        # the transaction did not commit.
        try:
            loaded = load_share(client, token)
        except redis.RedisError:
            loaded = None
        if loaded is not None:
            payload, _ = loaded
            audit_event(
                "share_created",
                share=fingerprint(token),
                session=fingerprint(session_id),
                files=len(payload.get("files", [])),
                expires_at=payload.get("expires_at"),
                recovered=True,
            )
            return _finalize_response(token, payload)
        return json_error("service_unavailable", "The share could not be published yet. Retry finalization.", 503)

    audit_event(
        "share_created",
        share=fingerprint(token),
        session=fingerprint(session_id),
        files=len(payload.get("files", [])),
        expires_at=payload.get("expires_at"),
        recovered=False,
    )
    return _finalize_response(token, payload)

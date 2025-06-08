import json
import re
import tempfile
import unicodedata
import uuid
from functools import wraps
from pathlib import Path
from typing import Any, Callable, List, Optional, cast

import redis
from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask.typing import ResponseReturnValue
from flask_wtf.csrf import CSRFError, generate_csrf, validate_csrf
from magic import from_file
from werkzeug.utils import secure_filename
from wtforms import ValidationError

from config import Config

app = Flask(__name__)
app.config.from_object(Config)
app.config["PREFERRED_URL_SCHEME"] = "http"
app.config.update(
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

redis_client = redis.from_url(app.config["REDIS_URL"])


@app.route("/api/csrf_token", methods=["GET"])
def csrf_token() -> Response:
    token = generate_csrf()
    return jsonify({"csrf_token": token})


def require_csrf(view_func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to enforce CSRF token on state-changing endpoints."""

    @wraps(view_func)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        try:
            validate_csrf(token)
        except (CSRFError, ValidationError) as e:
            app.logger.error(f"CSRF validation failed: {e}")
            return jsonify({"error": "Invalid or missing CSRF token"}), 400
        return view_func(*args, **kwargs)

    return wrapped


def allowed_extension(filename: str) -> bool:
    """Return True if filename extension is in the allowed list."""
    ext = filename.rsplit(".", 1)[-1].lower()
    return ext in app.config["ALLOWED_EXTENSIONS"]


def detect_mime(file_path: str) -> Optional[str]:
    """
    Use libmagic to detect a file's MIME type.
    Returns None if detection fails.
    """
    try:
        return from_file(file_path, mime=True)
    except Exception:
        return None


def human_readable_size(num_bytes: int) -> str:
    """Convert a byte count into a human-friendly string (e.g. '2.34MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024.0:
            return f"{size:.2f}{unit}"
        size /= 1024.0
    return f"{size:.2f}PB"


@app.errorhandler(CSRFError)
def handle_csrf_error(e: Any) -> ResponseReturnValue:
    """Return JSON on CSRF errors instead of HTML error page."""
    return jsonify({"error": "CSRF token missing or invalid"}), 400


@app.route("/", methods=["GET"])
def index() -> ResponseReturnValue:
    """
    Start a new upload session:
      1. Generate session ID.
      2. Create a Redis list 'session:<id>' with a placeholder.
      3. Set key expiry to SESSION_EXPIRY_SECONDS.
      4. Render upload page with session ID and CSRF token.
    """
    session_id = uuid.uuid4().hex
    session_key = f"session:{session_id}"
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    app.logger.info("New upload session created: session_id=%s, client_ip=%s", session_id, client_ip)

    # Push a placeholder so the list exists
    redis_client.rpush(session_key, "__placeholder__")
    redis_client.expire(session_key, app.config["SESSION_EXPIRY_SECONDS"])

    size_str = human_readable_size(app.config["MAX_CONTENT_LENGTH"])

    return render_template("index.jinja", session_id=session_id, max_file_size=size_str, csrf_token=generate_csrf())


@app.route("/upload-ajax", methods=["POST"])
@require_csrf
def upload_ajax() -> ResponseReturnValue:
    """
    Handle one upload chunk or a small full file:
      - Validate session_id in Redis.
      - Read chunk metadata (index, total_chunks, fileUuid).
      - Validate MIME type (only image/ or video/ allowed).
      - Save chunk to a temp directory.
      - If it's the last chunk, assemble all parts into one file.
      - Enforce size & extension whitelist.
      - Move assembled file into UPLOAD_FOLDER/<session_id>.
      - Record saved path in Redis list.
    Returns JSON with status or error.
    """
    session_id = request.form.get("session_id", None)
    if not session_id:
        app.logger.error("upload_ajax: missing session_id header")
        return jsonify({"error": "Missing session_id"}), 400

    session_key = f"session:{session_id}"
    pipe = redis_client.pipeline()
    pipe.type(session_key)
    pipe.ttl(session_key)
    key_type, ttl = pipe.execute()
    if key_type != b"list" or ttl <= 0:
        app.logger.warning("upload_ajax: invalid/expired session_id %s (type=%s ttl=%s)", session_id, key_type, ttl)
        return jsonify({"error": "Invalid or expired session_id"}), 400

    original_name = request.form.get("original_filename", "")
    if not original_name:
        app.logger.error("upload_ajax: missing original_filename for session %s", session_id)
        return jsonify({"error": "Missing original_filename"}), 400

    # Sanitize the filename first
    original_name = unicodedata.normalize("NFKC", original_name)
    filename_secure = secure_filename(original_name)
    if not filename_secure:
        return jsonify({"error": "Invalid filename"}), 400

    # Enforce a max‐length (protect against nasty OS or DB limits)
    max_len = app.config.get("MAX_FILENAME_LENGTH", 255)
    if len(filename_secure) > max_len:
        return jsonify({"error": f"Filename too long (>{max_len} chars)"}), 400

    try:
        chunk_index = int(request.form.get("chunk_index", -1))
        total_chunks = int(request.form.get("total_chunks", -1))
    except ValueError:
        app.logger.error(
            "upload_ajax: invalid chunk parameters (index=%s, total=%s)",
            request.form.get("chunk_index"),
            request.form.get("total_chunks"),
        )
        return jsonify({"error": "Invalid chunk parameters"}), 400

    file_uuid = request.form.get("fileUuid")
    if not file_uuid:
        return jsonify({"error": "Missing fileUuid"}), 400

    try:
        # normalize & reject anything that isn’t 32-hex chars
        file_uuid = uuid.UUID(file_uuid).hex
    except (ValueError, AttributeError):
        return jsonify({"error": "Invalid fileUuid"}), 400

    chunk = request.files.get("chunk", None)
    if chunk is None:
        app.logger.error("upload_ajax: missing chunk data for fileUuid %s", file_uuid)
        return jsonify({"error": "Missing chunk data"}), 400

    # Create a temp directory for all chunks if it doesn't exist
    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    tmp_dir = Path(app.config["UPLOAD_FOLDER"]) / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Save each part as tmp/<uuid>.<index>
    chunk_path = tmp_dir / f"{file_uuid}.{chunk_index}"
    try:
        chunk.save(chunk_path)
    except Exception as e:
        app.logger.exception("upload_ajax: failed to save chunk %s", chunk_path)
        return jsonify({"error": f"Failed to save chunk: {str(e)}"}), 500

    app.logger.debug("Chunk saved: %s (fileUuid=%s, index=%d/%d)", chunk_path, file_uuid, chunk_index, total_chunks)

    # If not the final chunk, just refresh TTL and return
    if chunk_index < total_chunks - 1:
        redis_client.expire(session_key, app.config["SESSION_EXPIRY_SECONDS"])
        app.logger.debug("Chunk %d/%d received, waiting for more", chunk_index + 1, total_chunks)
        return jsonify({"status": "chunk received"}), 200

    # Assemble all chunks into one temp file
    assembled_path = Path()
    try:
        with tempfile.NamedTemporaryFile(dir=str(tmp_dir), delete=False, suffix=f"_{file_uuid}_assembled") as dest_f:
            for idx in range(total_chunks):
                part_path = tmp_dir / f"{file_uuid}.{idx}"
                if not part_path.exists():
                    raise IOError(f"Missing chunk {idx}")
                with part_path.open("rb") as part_f:
                    while True:
                        buf = part_f.read(8192)
                        if not buf:
                            break
                        dest_f.write(buf)
        assembled_path = Path(dest_f.name)
    except Exception as e:
        app.logger.exception("upload_ajax: error assembling chunks for %s", file_uuid)
        if assembled_path != "." and assembled_path.exists():
            assembled_path.unlink()
        return jsonify({"error": str(e)}), 500
    finally:
        # Clean up chunk files
        for idx in range(total_chunks):
            p = tmp_dir / f"{file_uuid}.{idx}"
            if p.exists():
                p.unlink()

    # Verify size limit
    size_str = human_readable_size(app.config["MAX_CONTENT_LENGTH"])
    size = assembled_path.stat().st_size
    if size > app.config["MAX_CONTENT_LENGTH"]:
        assembled_path.unlink()
        app.logger.warning("upload_ajax: file too large (%d bytes) for %s", size, original_name)
        return jsonify({"error": f"File too large (>{size_str}): {original_name}"}), 400

    # Enforce file extension whitelist
    if not allowed_extension(original_name):
        assembled_path.unlink()
        return jsonify({"error": f"File type not allowed: {original_name}"}), 400

    # Full-file MIME sniff
    full_mime = detect_mime(str(assembled_path))
    if not full_mime or not (full_mime.startswith("image/") or full_mime.startswith("video/")):
        assembled_path.unlink()
        app.logger.warning("upload_ajax: MIME check failed for %s: %s", assembled_path, full_mime)
        return jsonify({"error": f"MIME check failed: {full_mime}"}), 400

    # Ensure destination session folder exists
    session_folder = upload_folder / session_id
    try:
        session_folder.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        assembled_path.unlink()
        app.logger.exception("upload_ajax: cannot create session folder %s", session_folder)
        return jsonify({"error": f"Failed to create session folder: {str(e)}"}), 500

    # Prevent name collisions by prefixing a UUID if needed
    final_name = filename_secure
    target = session_folder / final_name
    if target.exists():
        unique_prefix = uuid.uuid4().hex
        final_name = f"{unique_prefix}_{filename_secure}"
        target = session_folder / final_name

    try:
        assembled_path.replace(target)
    except Exception as e:
        if assembled_path.exists():
            assembled_path.unlink()
        app.logger.exception("upload_ajax: cannot move assembled file to %s", target)
        return jsonify({"error": f"Failed to move assembled file: {str(e)}"}), 500

    app.logger.info("File assembled and moved to %s", target)

    # Record the stored file path in Redis session list
    relative_path = f"{session_id}/{final_name}"
    try:
        pipe = redis_client.pipeline(transaction=True)
        pipe.rpush(session_key, relative_path)
        pipe.expire(session_key, app.config["SESSION_EXPIRY_SECONDS"])
        pipe.execute()
    except redis.RedisError as e:
        app.logger.error("Redis error storing %s: %s", relative_path, e, exc_info=True)
        # Safe‐delete the file, but don’t blow up if unlink itself fails
        try:
            # confirm we’re only nuking files under our upload dir
            real = target.resolve()
            if upload_folder.resolve() in real.parents:
                real.unlink()
            else:
                app.logger.warning("Refusing to unlink outside upload dir: %s", real)
        except Exception as cleanup_err:
            app.logger.error("Failed to cleanup %s after Redis error: %s", target, cleanup_err)
        return jsonify({"error": f"Redis error when adding filename: {e}"}), 500

    return jsonify({"status": "success", "saved_filename": relative_path}), 200


@app.route("/finalize-upload-session", methods=["POST"])
@require_csrf
def finalize_upload_session() -> ResponseReturnValue:
    """
    Client calls after all chunks:
      - Fetch list from Redis, drop placeholder.
      - Delete Redis session key.
      - If no real files, return error.
      - Generate one-time token, store file list under 'token:<token>'.
      - Return shareable URL.
    """
    data = request.get_json(silent=True)
    app.logger.info("Finalize session called with payload: %s", data)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    session_id = data.get("session_id")
    if not session_id:
        app.logger.error("finalize-upload-session: missing session_id")
        return jsonify({"error": "Missing session_id"}), 400

    if not re.match(r"^[0-9a-f]{32}$", session_id):
        return jsonify({"error": "Invalid session_id format"}), 400

    session_key = f"session:{session_id}"
    pipe = redis_client.pipeline()
    pipe.type(session_key)
    pipe.ttl(session_key)
    key_type, ttl = pipe.execute()
    if key_type != b"list" or ttl <= 0:
        return jsonify({"error": "Unknown or expired session_id"}), 400

    try:
        raw_items = cast(List[bytes], redis_client.lrange(session_key, 0, -1))
        redis_client.delete(session_key)
    except Exception as e:
        app.logger.exception("Redis error on finalizing session %s", session_id)
        return jsonify({"error": f"Redis error on finalize: {str(e)}"}), 500

    # Filter out "__placeholder__"
    file_list: List[str] = [item.decode("utf-8") for item in raw_items if item.decode("utf-8") != "__placeholder__"]

    # If no real files were uploaded, abort here
    if not file_list:
        return jsonify({"error": "No files were uploaded"}), 400

    # Generate token and store JSON(file_list) under "token:<token>" with TTL
    token = uuid.uuid4().hex
    token_key = f"token:{token}"
    try:
        redis_client.set(token_key, json.dumps(file_list))
        redis_client.expire(token_key, app.config["TOKEN_EXPIRY_SECONDS"])
    except Exception as e:
        return jsonify({"error": f"Redis error on token save: {str(e)}"}), 500

    share_url = url_for("short_upload_summary", token=token, _external=True)

    return jsonify({"token": token, "shareable_url": share_url}), 200


@app.route("/upload-summary/<token>", methods=["GET"])
def upload_summary(token: str) -> ResponseReturnValue:
    """
    Display uploaded file list for a given token:
      - Load file list from Redis.
      - Redirect to index if token expired or invalid.
      - Gather file sizes; skip missing files.
      - Render summary template.
    """
    token_key = f"token:{token}"
    ttl = redis_client.ttl(token_key)
    raw_data = redis_client.get(token_key)
    raw_data = cast(bytes, redis_client.get(token_key))

    if not raw_data:
        app.logger.warning("Invalid or expired token, redirecting: %s", token)
        return redirect(url_for("index"))
    app.logger.info("Rendering summary for token: %s", token)
    raw_str = raw_data.decode("utf-8")
    try:
        relative_paths = json.loads(raw_str)
    except Exception:
        return redirect(url_for("index"))

    if not relative_paths:
        return redirect(url_for("index"))

    upload_folder = Path(app.config["UPLOAD_FOLDER"])
    file_info_list = []
    for rel_path in relative_paths:
        abs_path: Path = upload_folder / rel_path
        try:
            size_bytes = abs_path.stat().st_size
        except OSError:
            continue
        file_info_list.append({"path": rel_path, "name": abs_path.name, "size": size_bytes})

    if not file_info_list:
        return jsonify({"error": "No files found"}), 400

    return render_template("summary.jinja", files=file_info_list, token=token, csrf_token=generate_csrf(), ttl=ttl)


@app.route("/<token>", methods=["GET"])
def short_upload_summary(token: str) -> ResponseReturnValue:
    """Alias for /upload-summary/<token>."""
    return upload_summary(token)


@app.route("/uploads/<path:filename>", methods=["GET"])
def uploaded_file(filename: str) -> Response:
    """
    Serve stored files under UPLOAD_FOLDER:
      - Block path traversal by resolving and checking parent.
      - Send with caching headers.
    """
    upload_folder = Path(app.config["UPLOAD_FOLDER"]).resolve()
    requested_path = (upload_folder / filename).resolve()
    cache_timeout = app.config.get("SEND_FILE_MAX_AGE_DEFAULT", 3600)

    # Reject any path not under UPLOAD_FOLDER
    if not requested_path.is_relative_to(upload_folder):
        app.logger.warning("Path traversal attempt: %s", filename)
        abort(404)

    return send_from_directory(upload_folder, filename, max_age=cache_timeout)


if __name__ == "__main__":
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=False)

application = app

import os

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
    if not SECRET_KEY:
        raise RuntimeError("FLASK_SECRET_KEY environment variable must be set")

    # Redis URL
    REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

    # Upload folder can also be customized via env var; default → ./uploads
    UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", os.path.join(basedir, "uploads"))

    # Max total request size (in bytes). Individual files will be checked separately.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 1024 * 1024 * 1024))

    # Chunk size for file uploads (in bytes).
    CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 5 * 1024 * 1024))

    # Allowed extensions
    ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "heic", "gif", "mp4", "mov", "avi", "webm"}

    # How long (in seconds) to keep a “session” before it expires (minutes).
    SESSION_EXPIRY_SECONDS = 30 * 60

    # How long (in seconds) to keep a “token” before it expires (minutes).
    TOKEN_EXPIRY_SECONDS = 360 * 60

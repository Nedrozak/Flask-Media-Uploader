from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import IO

from werkzeug.datastructures import FileStorage


class StorageError(RuntimeError):
    pass


class ChunkSizeError(StorageError):
    pass


class MissingChunksError(StorageError):
    pass


class StorageCapacityError(StorageError):
    pass


class UploadStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.sessions_root = self.root / "sessions"
        self.shares_root = self.root / "shares"
        self._assembly_lock = threading.Lock()
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        self.shares_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_hex_id(value: str) -> str:
        if len(value) != 32 or any(ch not in "0123456789abcdef" for ch in value):
            raise ValueError("invalid identifier")
        return value

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def ensure_capacity(self, required_bytes: int, min_free_bytes: int) -> None:
        if required_bytes < 0 or min_free_bytes < 0:
            raise ValueError("capacity limits must not be negative")
        free = self.free_bytes()
        if free - required_bytes < min_free_bytes:
            raise StorageCapacityError(
                f"upload storage has {free} free bytes; {required_bytes + min_free_bytes} are required"
            )

    def probe_writable(self) -> None:
        probe_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root,
                prefix=".health-",
                suffix=".tmp",
                delete=False,
            ) as probe:
                probe.write(b"ok")
                probe.flush()
                probe_path = Path(probe.name)
        finally:
            if probe_path is not None:
                probe_path.unlink(missing_ok=True)

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_root / self._validate_hex_id(session_id)

    def share_dir(self, token: str) -> Path:
        return self.shares_root / self._validate_hex_id(token)

    def ensure_session(self, session_id: str, expires_at: int) -> None:
        directory = self.session_dir(session_id)
        (directory / ".tmp").mkdir(parents=True, exist_ok=True)
        (directory / "files").mkdir(parents=True, exist_ok=True)
        self._write_expiry(directory, expires_at)

    def refresh_session(self, session_id: str, expires_at: int) -> None:
        directory = self.session_dir(session_id)
        if directory.exists():
            self._write_expiry(directory, expires_at)

    def _chunk_dir(self, session_id: str, transfer_id: str) -> Path:
        directory = self.session_dir(session_id) / ".tmp" / self._validate_hex_id(transfer_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def chunk_path(self, session_id: str, transfer_id: str, index: int) -> Path:
        return self._chunk_dir(session_id, transfer_id) / f"{index:08d}.part"

    def save_chunk(
        self,
        session_id: str,
        transfer_id: str,
        index: int,
        upload: FileStorage,
        expected_size: int,
        max_size: int,
    ) -> int:
        target = self.chunk_path(session_id, transfer_id, index)
        if target.exists() and target.stat().st_size == expected_size:
            return expected_size

        temp_path = target.with_name(f".{target.name}.{os.urandom(8).hex()}.incoming")
        written = 0
        try:
            with temp_path.open("xb") as output:
                written = self._copy_bounded(upload.stream, output, max_size)
            if written != expected_size:
                raise ChunkSizeError(f"expected {expected_size} bytes, received {written}")
            os.replace(temp_path, target)
            return written
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)

    @staticmethod
    def _copy_bounded(source: IO[bytes], destination: IO[bytes], max_size: int) -> int:
        written = 0
        while True:
            block = source.read(min(1024 * 1024, max_size + 1 - written))
            if not block:
                break
            destination.write(block)
            written += len(block)
            if written > max_size:
                raise ChunkSizeError(f"chunk exceeds {max_size} bytes")
        return written

    def all_chunks_present(self, session_id: str, transfer_id: str, total_chunks: int) -> bool:
        directory = self._chunk_dir(session_id, transfer_id)
        return all((directory / f"{index:08d}.part").is_file() for index in range(total_chunks))

    def assemble(
        self,
        session_id: str,
        transfer_id: str,
        total_chunks: int,
        expected_size: int,
        min_free_bytes: int,
    ) -> tuple[Path, str]:
        # Waitress serves this application with threads. Serializing the temporary
        # full-file copy makes the free-space check meaningful instead of allowing
        # several large assemblies to race through the same safety margin.
        with self._assembly_lock:
            self.ensure_capacity(expected_size, min_free_bytes)
            chunk_dir = self._chunk_dir(session_id, transfer_id)
            assembled_path: Path | None = None
            written = 0
            digest = hashlib.sha256()
            try:
                with tempfile.NamedTemporaryFile(
                    dir=chunk_dir,
                    prefix="assembled-",
                    suffix=".tmp",
                    delete=False,
                ) as output:
                    assembled_path = Path(output.name)
                    for index in range(total_chunks):
                        part = chunk_dir / f"{index:08d}.part"
                        if not part.is_file():
                            raise MissingChunksError(f"missing chunk {index}")
                        with part.open("rb") as source:
                            while block := source.read(1024 * 1024):
                                output.write(block)
                                digest.update(block)
                                written += len(block)
                                if written > expected_size:
                                    raise ChunkSizeError("assembled file is larger than declared size")
                if written != expected_size:
                    raise ChunkSizeError(f"expected {expected_size} bytes, assembled {written}")
                return assembled_path, digest.hexdigest()
            except Exception:
                if assembled_path is not None:
                    assembled_path.unlink(missing_ok=True)
                raise

    def commit_file(self, session_id: str, assembled_path: Path, file_id: str) -> Path:
        target = self.session_dir(session_id) / "files" / self._validate_hex_id(file_id)
        if target.exists():
            raise StorageError("generated storage identifier already exists")
        os.replace(assembled_path, target)
        return target

    def cleanup_transfer(self, session_id: str, transfer_id: str) -> None:
        shutil.rmtree(self.session_dir(session_id) / ".tmp" / self._validate_hex_id(transfer_id), ignore_errors=True)

    def finalize_session(self, session_id: str, token: str, expires_at: int) -> Path:
        source = self.session_dir(session_id)
        target = self.share_dir(token)
        if target.is_dir() and not source.exists():
            self._write_expiry(target, expires_at)
            return target
        if not source.is_dir():
            raise StorageError("upload session storage is missing")
        if target.exists():
            raise StorageError("share storage identifier already exists")
        try:
            os.replace(source, target)
        except FileNotFoundError:
            if not (target.is_dir() and not source.exists()):
                raise
        shutil.rmtree(target / ".tmp", ignore_errors=True)
        self._write_expiry(target, expires_at)
        return target

    def shared_file(self, token: str, file_id: str) -> Path:
        return self.share_dir(token) / "files" / self._validate_hex_id(file_id)

    def delete_share(self, token: str) -> None:
        shutil.rmtree(self.share_dir(token))

    @staticmethod
    def _write_expiry(directory: Path, expires_at: int) -> None:
        marker = directory / ".expires_at"
        temp = directory / f".expires_at.{os.urandom(6).hex()}.tmp"
        temp.write_text(str(int(expires_at)), encoding="ascii")
        os.replace(temp, marker)

    def cleanup_expired(self, now: int | None = None) -> dict[str, int]:
        now = int(time.time()) if now is None else now
        removed = {"sessions": 0, "shares": 0, "errors": 0}
        for label, root in (("sessions", self.sessions_root), ("shares", self.shares_root)):
            for directory in root.iterdir():
                if not directory.is_dir():
                    continue
                marker = directory / ".expires_at"
                try:
                    expires_at = int(marker.read_text(encoding="ascii").strip())
                except (OSError, ValueError):
                    removed["errors"] += 1
                    continue
                if expires_at > now:
                    continue
                try:
                    shutil.rmtree(directory)
                except FileNotFoundError:
                    continue
                except OSError:
                    removed["errors"] += 1
                    continue
                removed[label] += 1
        return removed

from __future__ import annotations

import hashlib
import hmac
import time
from pathlib import PurePosixPath
from urllib.parse import quote

from botocore.exceptions import BotoCoreError, ClientError


class R2StorageError(RuntimeError):
    pass


class R2Storage:
    """Small Cloudflare R2 adapter used by the Render deployment.

    Uploads use the S3-compatible multipart API. Reads are delivered through a
    Cloudflare Worker so large previews/downloads never pass through Render.
    """

    def __init__(
        self,
        client: object,
        *,
        bucket: str,
        delivery_base_url: str,
        delivery_secret: str,
        delivery_url_ttl_seconds: int,
    ) -> None:
        self.client = client
        self.bucket = bucket
        self.delivery_base_url = delivery_base_url.rstrip("/")
        self.delivery_secret = delivery_secret.encode("utf-8")
        self.delivery_url_ttl_seconds = delivery_url_ttl_seconds

    @staticmethod
    def object_key(session_id: str, transfer_id: str) -> str:
        # IDs are validated by the upload service before reaching this adapter.
        return str(PurePosixPath("media") / session_id / transfer_id)

    def probe(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)  # type: ignore[attr-defined]
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("R2 bucket is unavailable") from exc

    def create_multipart_upload(
        self,
        *,
        session_id: str,
        transfer_id: str,
        filename: str,
        mime: str,
    ) -> tuple[str, str]:
        key = self.object_key(session_id, transfer_id)
        try:
            result = self.client.create_multipart_upload(  # type: ignore[attr-defined]
                Bucket=self.bucket,
                Key=key,
                ContentType=mime,
                ContentDisposition=self._content_disposition("inline", filename),
                CacheControl="private, no-store, max-age=0",
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("Could not start R2 multipart upload") from exc
        upload_id = str(result.get("UploadId") or "")
        if not upload_id:
            raise R2StorageError("R2 did not return a multipart upload identifier")
        return key, upload_id

    def complete_multipart_upload(
        self,
        *,
        key: str,
        upload_id: str,
        parts: list[dict[str, int | str]],
    ) -> str:
        try:
            result = self.client.complete_multipart_upload(  # type: ignore[attr-defined]
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("Could not complete the R2 multipart upload") from exc
        return str(result.get("ETag") or "")

    def abort_multipart_upload(self, *, key: str, upload_id: str) -> None:
        try:
            self.client.abort_multipart_upload(  # type: ignore[attr-defined]
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
            )
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("Could not abort the R2 multipart upload") from exc

    def object_size(self, key: str) -> int:
        try:
            result = self.client.head_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("Could not read R2 object metadata") from exc
        return int(result.get("ContentLength", -1))

    def delete_object(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)  # type: ignore[attr-defined]
        except (BotoCoreError, ClientError) as exc:
            raise R2StorageError("Could not delete R2 object") from exc

    def delete_objects(self, keys: list[str]) -> None:
        unique = sorted({key for key in keys if key})
        if not unique:
            return
        # R2/S3 DeleteObjects accepts at most 1000 keys per request.
        for offset in range(0, len(unique), 1000):
            batch = unique[offset : offset + 1000]
            try:
                self.client.delete_objects(  # type: ignore[attr-defined]
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": key} for key in batch], "Quiet": True},
                )
            except (BotoCoreError, ClientError) as exc:
                raise R2StorageError("Could not delete R2 share objects") from exc

    def part_upload_url(
        self,
        *,
        key: str,
        upload_id: str,
        part_number: int,
        expected_size: int,
        ttl_seconds: int,
    ) -> str:
        expires = int(time.time()) + ttl_seconds
        message = f"upload\n{key}\n{upload_id}\n{part_number}\n{expected_size}\n{expires}".encode("utf-8")
        signature = hmac.new(self.delivery_secret, message, hashlib.sha256).hexdigest()
        return (
            f"{self.delivery_base_url}/{quote(key, safe='/')}"
            f"?action=upload&uploadId={quote(upload_id, safe='')}"
            f"&part={part_number}&size={expected_size}&exp={expires}&sig={signature}"
        )

    def delivery_url(
        self,
        *,
        key: str,
        filename: str,
        download: bool,
        share_expires_at: int,
    ) -> str:
        now = int(time.time())
        expires = min(now + self.delivery_url_ttl_seconds, share_expires_at)
        if expires <= now:
            raise R2StorageError("Share has already expired")
        disposition = "attachment" if download else "inline"
        encoded_name = quote(filename, safe="")
        message = f"{key}\n{expires}\n{disposition}\n{filename}".encode("utf-8")
        signature = hmac.new(self.delivery_secret, message, hashlib.sha256).hexdigest()
        return (
            f"{self.delivery_base_url}/{quote(key, safe='/')}"
            f"?exp={expires}&disposition={disposition}&name={encoded_name}&sig={signature}"
        )

    @staticmethod
    def _content_disposition(disposition: str, filename: str) -> str:
        # The delivery Worker overwrites this header for each request. Keeping a
        # safe UTF-8 filename on the object still makes direct inspection nicer.
        encoded = quote(filename, safe="")
        return f"{disposition}; filename*=UTF-8''{encoded}"

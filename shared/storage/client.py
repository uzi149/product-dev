"""MinIO wrapper. Nothing else in the code base talks to the `minio` package directly."""

import mimetypes
from datetime import timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from minio import Minio
from minio.deleteobjects import DeleteObject
from minio.error import MinioException, S3Error
from urllib3.exceptions import HTTPError

from shared.config import Settings, get_settings

# MinIO answers with MinioException; when it can't be reached at all, urllib3 raises HTTPError.
_ERRORS = (MinioException, HTTPError)

# Presigned URLs are signed locally. A fixed region avoids a lookup request to the server,
# which matters when the public endpoint isn't reachable from this process.
_REGION = "us-east-1"


class StorageError(Exception):
    """Any storage failure, so callers don't depend on the minio package's exceptions."""


class NotFound(StorageError):
    pass


def _wrap(error: Exception) -> StorageError:
    if isinstance(error, S3Error) and error.code in {"NoSuchKey", "NoSuchBucket", "NotFound"}:
        return NotFound(str(error))
    return StorageError(str(error))


class StorageClient:
    def __init__(self, settings: Settings):
        self.bucket = settings.minio_bucket
        credentials = {
            "access_key": settings.minio_access_key,
            "secret_key": settings.minio_secret_key,
            "secure": False,
            "region": _REGION,
        }
        self._client = Minio(settings.minio_endpoint, **credentials)
        # Same server, but the address the browser uses (see minio_public_endpoint).
        self._signer = Minio(settings.minio_signing_endpoint, **credentials)

    # --- URIs -----------------------------------------------------------------------------

    def to_uri(self, key: str) -> str:
        """The value stored in `storage_uri` columns."""
        return f"s3://{self.bucket}/{key}"

    def key_from_uri(self, uri: str) -> str:
        prefix = f"s3://{self.bucket}/"
        if not uri.startswith(prefix):
            raise ValueError(f"not a storage URI for bucket '{self.bucket}': {uri}")
        return uri[len(prefix) :]

    # --- Operations -----------------------------------------------------------------------

    def ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
        except _ERRORS as error:
            raise _wrap(error) from error

    def is_healthy(self) -> bool:
        """True when MinIO answers and the bucket exists."""
        try:
            return self._client.bucket_exists(self.bucket)
        except Exception:  # noqa: BLE001 - any failure means "unhealthy"
            return False

    def upload_file(self, key: str, path: str | Path, content_type: str | None = None) -> str:
        content_type = content_type or mimetypes.guess_type(str(path))[0]
        try:
            self._client.fput_object(
                self.bucket, key, str(path), content_type=content_type or "application/octet-stream"
            )
        except _ERRORS as error:
            raise _wrap(error) from error
        return self.to_uri(key)

    def upload_bytes(self, key: str, data: bytes, content_type: str) -> str:
        try:
            self._client.put_object(
                self.bucket, key, BytesIO(data), length=len(data), content_type=content_type
            )
        except _ERRORS as error:
            raise _wrap(error) from error
        return self.to_uri(key)

    def download_file(self, key: str, path: str | Path) -> None:
        try:
            self._client.fget_object(self.bucket, key, str(path))
        except _ERRORS as error:
            raise _wrap(error) from error

    def download_bytes(self, key: str) -> bytes:
        """A small object's content (keyframes); raises NotFound when it doesn't exist."""
        try:
            response = self._client.get_object(self.bucket, key)
            try:
                return response.read()
            finally:
                response.close()
                response.release_conn()
        except _ERRORS as error:
            raise _wrap(error) from error

    def exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, key)
        except S3Error as error:
            if error.code in {"NoSuchKey", "NoSuchBucket", "NotFound"}:
                return False
            raise _wrap(error) from error
        except _ERRORS as error:
            raise _wrap(error) from error
        return True

    def presigned_get_url(self, key: str, expires: timedelta = timedelta(hours=1)) -> str:
        try:
            return self._signer.presigned_get_object(self.bucket, key, expires=expires)
        except _ERRORS as error:
            raise _wrap(error) from error

    def delete(self, key: str) -> None:
        """Deleting a missing object is not an error."""
        try:
            self._client.remove_object(self.bucket, key)
        except _ERRORS as error:
            raise _wrap(error) from error

    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under a prefix; returns how many objects were removed."""
        try:
            keys = [
                DeleteObject(obj.object_name)
                for obj in self._client.list_objects(self.bucket, prefix=prefix, recursive=True)
            ]
            errors = list(self._client.remove_objects(self.bucket, keys))
        except _ERRORS as error:
            raise _wrap(error) from error
        if errors:
            raise StorageError(f"could not delete {len(errors)} objects: {errors[0]}")
        return len(keys)


@lru_cache
def get_storage() -> StorageClient:
    return StorageClient(get_settings())

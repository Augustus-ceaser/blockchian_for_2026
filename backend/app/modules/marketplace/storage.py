from __future__ import annotations

import io


class MinioReleaseObjectStore:
    """Small release-bucket adapter; it never creates presigned public URLs."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket_name: str,
    ) -> None:
        from minio import Minio

        self.bucket_name = bucket_name
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        if not self._client.bucket_exists(bucket_name):
            self._client.make_bucket(bucket_name)

    def put(self, object_key: str, payload: bytes, content_type: str) -> None:
        self._client.put_object(
            self.bucket_name,
            object_key,
            io.BytesIO(payload),
            len(payload),
            content_type=content_type,
        )

    def get(self, object_key: str) -> bytes:
        response = self._client.get_object(self.bucket_name, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

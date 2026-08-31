from __future__ import annotations

import io
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from app.execution.pathmnist import PATHMNIST_OUTPUT_FILES, sha256_file
from app.execution.workspace import ExecutionWorkspace, ExecutionWorkspaceManager


class QuarantineStorageError(ValueError):
    pass


def _content_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class MinioQuarantineArtifactWriter:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        secure: bool,
        bucket_name: str,
        workspace_root: Path,
    ) -> None:
        from minio import Minio

        self.bucket_name = bucket_name
        self._workspaces = ExecutionWorkspaceManager(workspace_root)
        self._client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        if not self._client.bucket_exists(bucket_name):
            self._client.make_bucket(bucket_name)

    def upload(
        self,
        *,
        run_id: UUID,
        workspace_reference: str,
        manifest: list[dict[str, Any]],
        manifest_digest: str,
    ) -> str:
        if workspace_reference != f"workspace-output:{run_id}":
            raise QuarantineStorageError("workspace output reference is invalid")
        root = self._workspaces._safe_child(str(run_id))
        workspace = ExecutionWorkspace(
            root,
            root / "input",
            root / "work",
            root / "output",
            root / "logs",
            root / "manifests",
        )
        if not workspace.output.is_dir() or workspace.output.is_symlink():
            raise QuarantineStorageError("execution output workspace is unavailable")
        names = [str(item.get("name")) for item in manifest]
        if set(names) != set(PATHMNIST_OUTPUT_FILES) or len(names) != len(
            PATHMNIST_OUTPUT_FILES
        ):
            raise QuarantineStorageError("output file allowlist mismatch")
        actual_names = {
            path.name for path in workspace.output.iterdir() if path.is_file()
        }
        if actual_names != set(PATHMNIST_OUTPUT_FILES):
            raise QuarantineStorageError("workspace contains unexpected output files")
        digest_segment = manifest_digest.removeprefix("sha256:")
        if len(digest_segment) != 64:
            raise QuarantineStorageError("output manifest digest is invalid")
        prefix = f"quarantine/{run_id}/{digest_segment}"
        for item in manifest:
            name = str(item["name"])
            path = self._workspaces.resolve_member(workspace, "output", name)
            payload = path.read_bytes()
            if path.is_symlink() or sha256_file(path) != item.get("digest"):
                raise QuarantineStorageError("output digest mismatch")
            if len(payload) != item.get("size_bytes"):
                raise QuarantineStorageError("output size mismatch")
            self._client.put_object(
                self.bucket_name,
                f"{prefix}/{name}",
                io.BytesIO(payload),
                len(payload),
                content_type=str(item.get("media_type") or "application/octet-stream"),
            )
        return f"minio-quarantine/{self.bucket_name}/{prefix}"


class MinioQuarantineArtifactReader:
    """Reads only a callback-attested three-file Artifact from quarantine."""

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

    def read(
        self,
        *,
        run_id: UUID,
        storage_reference: str,
        manifest: list[dict[str, Any]],
        manifest_digest: str,
    ) -> dict[str, bytes]:
        prefix = self._prefix(
            run_id=run_id,
            storage_reference=storage_reference,
            manifest_digest=manifest_digest,
        )
        expected = {
            str(item.get("name")): item
            for item in manifest
            if isinstance(item, dict)
        }
        if set(expected) != set(PATHMNIST_OUTPUT_FILES) or len(expected) != len(
            PATHMNIST_OUTPUT_FILES
        ):
            raise QuarantineStorageError("callback output manifest is not allowlisted")
        actual_names = {
            item.object_name.removeprefix(f"{prefix}/")
            for item in self._client.list_objects(
                self.bucket_name, prefix=f"{prefix}/", recursive=True
            )
        }
        if actual_names != set(PATHMNIST_OUTPUT_FILES):
            raise QuarantineStorageError("quarantine contains unexpected output files")
        files: dict[str, bytes] = {}
        for name in PATHMNIST_OUTPUT_FILES:
            response = self._client.get_object(self.bucket_name, f"{prefix}/{name}")
            try:
                payload = response.read()
            finally:
                response.close()
                response.release_conn()
            item = expected[name]
            if len(payload) != item.get("size_bytes"):
                raise QuarantineStorageError("quarantine output size mismatch")
            if _content_digest(payload) != item.get("digest"):
                raise QuarantineStorageError("quarantine output digest mismatch")
            files[name] = payload
        self._validate_content(files)
        return files

    def _prefix(
        self,
        *,
        run_id: UUID,
        storage_reference: str,
        manifest_digest: str,
    ) -> str:
        digest = manifest_digest.removeprefix("sha256:")
        expected = (
            f"minio-quarantine/{self.bucket_name}/quarantine/{run_id}/{digest}"
        )
        if len(digest) != 64 or storage_reference != expected:
            raise QuarantineStorageError("Artifact quarantine reference is invalid")
        return f"quarantine/{run_id}/{digest}"

    @staticmethod
    def _validate_content(files: dict[str, bytes]) -> None:
        try:
            metrics = json.loads(files["aggregate_metrics.json"])
            summary = json.loads(files["execution_summary.json"])
            matrix = files["confusion_matrix.csv"].decode("utf-8")
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise QuarantineStorageError(
                "quarantine output content is invalid"
            ) from exc
        if not isinstance(metrics, dict) or not isinstance(summary, dict):
            raise QuarantineStorageError("quarantine JSON output is invalid")
        rows = [row for row in matrix.splitlines() if row]
        if len(rows) < 2 or any(len(row.split(",")) != len(rows[0].split(",")) for row in rows):
            raise QuarantineStorageError("quarantine CSV output is invalid")

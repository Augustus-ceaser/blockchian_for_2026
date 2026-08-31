from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from app.modules.audit import canonical_json_digest_v1


class RegistryValidationError(ValueError):
    pass


BUILTIN_ENTRYPOINTS = {
    "builtin.synthetic_statistics.v1",
    "pathmnist_resnet18_v1",
}
FIXED_ENTRYPOINT_MODEL_DIGESTS = {
    "pathmnist_resnet18_v1": (
        "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
    ),
}
OUTPUT_TYPES = {
    "aggregate_statistics",
    "model_artifact",
    "feature_dataset",
    "risk_scoring_model",
}
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require_digest(value: object, name: str) -> str:
    text = str(value)
    if not DIGEST_PATTERN.fullmatch(text):
        raise RegistryValidationError(f"{name} must be sha256:<64 lowercase hex>")
    return text


@dataclass(frozen=True)
class ModelRegistration:
    model_name: str
    model_version: str
    model_digest: str
    entrypoint_id: str
    runtime: str
    dependency_lock_digest: str
    input_schema_version: str
    output_schema_version: str
    allowed_output_types: tuple[str, ...]
    allowed_output_files: tuple[str, ...]
    network_access: bool
    cpu_limit: int
    memory_limit: int
    timeout_seconds: int
    enabled: bool
    registration_digest: str


@dataclass(frozen=True)
class DatasetRegistration:
    dataset_name: str
    dataset_version: str
    manifest_digest: str
    data_type: str
    input_schema_version: str
    source_type: str
    public_or_authorized: str
    case_count: int
    allowed_model_types: tuple[str, ...]
    authorized_use: tuple[str, ...]
    enabled: bool
    registration_digest: str


def _digest(document: dict[str, Any]) -> str:
    return canonical_json_digest_v1(document)


class ModelRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, ModelRegistration] = {}

    def register(self, manifest: dict[str, Any]) -> ModelRegistration:
        document = dict(manifest)
        supplied = document.pop("registration_digest", None)
        digest = _digest(document)
        if supplied is not None and supplied != digest:
            raise RegistryValidationError("model registration_digest mismatch")
        entrypoint = document.get("entrypoint_id")
        if entrypoint not in BUILTIN_ENTRYPOINTS:
            raise RegistryValidationError("entrypoint_id is not built into the platform")
        if document.get("network_access") is not False:
            raise RegistryValidationError("local built-in execution requires network_access=false")
        expected_model_digest = FIXED_ENTRYPOINT_MODEL_DIGESTS.get(str(entrypoint))
        if (
            expected_model_digest is not None
            and document.get("model_digest") != expected_model_digest
        ):
            raise RegistryValidationError("fixed entrypoint model_digest mismatch")
        outputs = tuple(sorted(set(document.get("allowed_output_types", []))))
        if not outputs or not set(outputs).issubset(OUTPUT_TYPES):
            raise RegistryValidationError("allowed_output_types is invalid")
        output_files_raw = tuple(str(value) for value in document.get("allowed_output_files", []))
        if len(output_files_raw) != len(set(output_files_raw)):
            raise RegistryValidationError("allowed_output_files contains duplicates")
        if any(
            not name or "/" in name or "\\" in name or ".." in name
            for name in output_files_raw
        ):
            raise RegistryValidationError("allowed_output_files is invalid")
        output_files = tuple(sorted(output_files_raw))
        limits = (
            int(document.get("cpu_limit", 0)),
            int(document.get("memory_limit", 0)),
            int(document.get("timeout_seconds", 0)),
        )
        if any(value <= 0 for value in limits):
            raise RegistryValidationError("resource limits must be positive")
        entry = ModelRegistration(
            model_name=str(document["model_name"]),
            model_version=str(document["model_version"]),
            model_digest=_require_digest(document["model_digest"], "model_digest"),
            entrypoint_id=str(entrypoint),
            runtime=str(document["runtime"]),
            dependency_lock_digest=_require_digest(
                document["dependency_lock_digest"], "dependency_lock_digest"
            ),
            input_schema_version=str(document["input_schema_version"]),
            output_schema_version=str(document["output_schema_version"]),
            allowed_output_types=outputs,
            allowed_output_files=output_files,
            network_access=False,
            cpu_limit=limits[0],
            memory_limit=limits[1],
            timeout_seconds=limits[2],
            enabled=bool(document.get("enabled")),
            registration_digest=digest,
        )
        previous = self._entries.get(entry.model_digest)
        if previous is not None and previous != entry:
            raise RegistryValidationError("model digest maps to different registration")
        self._entries[entry.model_digest] = entry
        return entry

    def require_enabled(self, model_digest: str) -> ModelRegistration:
        entry = self._entries.get(model_digest)
        if entry is None or not entry.enabled:
            raise RegistryValidationError("model is not registered and enabled")
        return entry


class DatasetRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, DatasetRegistration] = {}

    def register(self, manifest: dict[str, Any]) -> DatasetRegistration:
        document = dict(manifest)
        supplied = document.pop("registration_digest", None)
        digest = _digest(document)
        if supplied is not None and supplied != digest:
            raise RegistryValidationError("dataset registration_digest mismatch")
        if document.get("source_type") not in {"synthetic_fixture", "public", "authorized"}:
            raise RegistryValidationError("dataset source_type is invalid")
        if document.get("public_or_authorized") not in {"synthetic", "public", "authorized"}:
            raise RegistryValidationError("dataset authorization declaration is missing")
        if int(document.get("case_count", 0)) <= 0:
            raise RegistryValidationError("case_count must be positive")
        entry = DatasetRegistration(
            dataset_name=str(document["dataset_name"]),
            dataset_version=str(document["dataset_version"]),
            manifest_digest=_require_digest(
                document["manifest_digest"], "manifest_digest"
            ),
            data_type=str(document["data_type"]),
            input_schema_version=str(document["input_schema_version"]),
            source_type=str(document["source_type"]),
            public_or_authorized=str(document["public_or_authorized"]),
            case_count=int(document["case_count"]),
            allowed_model_types=tuple(sorted(set(document.get("allowed_model_types", [])))),
            authorized_use=tuple(sorted(set(document.get("authorized_use", [])))),
            enabled=bool(document.get("enabled")),
            registration_digest=digest,
        )
        previous = self._entries.get(entry.manifest_digest)
        if previous is not None and previous != entry:
            raise RegistryValidationError("dataset digest maps to different registration")
        self._entries[entry.manifest_digest] = entry
        return entry

    def require_enabled(self, manifest_digest: str) -> DatasetRegistration:
        entry = self._entries.get(manifest_digest)
        if entry is None or not entry.enabled:
            raise RegistryValidationError("dataset is not registered and enabled")
        return entry

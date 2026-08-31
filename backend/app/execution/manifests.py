from __future__ import annotations

from typing import Any

from app.execution.registry import DatasetRegistration, ModelRegistration, RegistryValidationError
from app.modules.audit import canonical_json_digest_v1


class ManifestValidationError(ValueError):
    pass


class InputManifestValidator:
    def validate(
        self,
        *,
        model: ModelRegistration,
        dataset: DatasetRegistration,
        requested_use: str,
    ) -> str:
        if model.input_schema_version != dataset.input_schema_version:
            raise ManifestValidationError("model and dataset input schemas are incompatible")
        if requested_use not in dataset.authorized_use:
            raise ManifestValidationError("requested use is not authorized")
        if model.entrypoint_id not in dataset.allowed_model_types:
            raise ManifestValidationError("dataset does not allow this model type")
        return canonical_json_digest_v1(
            {
                "schema_version": "validated-input-manifest/v1",
                "model_registration_digest": model.registration_digest,
                "dataset_registration_digest": dataset.registration_digest,
                "requested_use": requested_use,
            }
        )


class OutputManifestValidator:
    _ALLOWED_KEYS = {"name", "media_type", "size_bytes", "digest"}

    def validate(
        self,
        *,
        model: ModelRegistration,
        artifact_type: str,
        manifest: list[dict[str, Any]],
    ) -> str:
        if artifact_type not in model.allowed_output_types:
            raise ManifestValidationError("output type is not allowlisted")
        if not manifest:
            raise ManifestValidationError("output manifest is empty")
        names: list[str] = []
        for item in manifest:
            if set(item) != self._ALLOWED_KEYS:
                raise ManifestValidationError("output manifest fields are invalid")
            name = str(item["name"])
            if not name or "/" in name or "\\" in name or ".." in name:
                raise ManifestValidationError("output name is unsafe")
            names.append(name)
            if not str(item["digest"]).startswith("sha256:"):
                raise ManifestValidationError("output digest is invalid")
            if not isinstance(item["size_bytes"], int) or item["size_bytes"] < 0:
                raise ManifestValidationError("output size is invalid")
        if len(names) != len(set(names)):
            raise ManifestValidationError("output manifest contains duplicate names")
        if model.allowed_output_files and not set(names).issubset(model.allowed_output_files):
            raise ManifestValidationError("output file is not allowlisted")
        return canonical_json_digest_v1(
            {"schema_version": "validated-output-manifest/v1", "outputs": manifest}
        )


def assert_registration_compatible(
    model: ModelRegistration, dataset: DatasetRegistration
) -> None:
    try:
        InputManifestValidator().validate(
            model=model, dataset=dataset, requested_use=dataset.authorized_use[0]
        )
    except (IndexError, RegistryValidationError) as exc:
        raise ManifestValidationError("registration compatibility cannot be established") from exc

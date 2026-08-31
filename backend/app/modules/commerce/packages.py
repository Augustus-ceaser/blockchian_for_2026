from __future__ import annotations

from io import BytesIO
import json
from typing import Any, Mapping
from uuid import NAMESPACE_URL, uuid5
from zipfile import ZIP_DEFLATED, ZipFile


class CommercialPackageError(ValueError):
    pass


# Reviewed, fixed fields from registered_assets/pathmnist_v1/dataset_manifest.json.
# The downloadable package exposes only this allow-listed public metadata; it
# never reads a caller path and never embeds the image archive.
PATHMNIST_PUBLIC_DELIVERY_MANIFEST = {
    "onboarding_profile": "pathmnist_public_v1",
    "dataset_name": "PathMNIST official public test split",
    "dataset_version": "v1",
    "manifest_digest": (
        "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"
    ),
    "source_type": "public",
    "source_reference": "https://zenodo.org/records/10519652",
    "license": "CC BY 4.0",
    "split": "test",
    "upstream_case_count": 7180,
    "authorized_use": ["model_validation"],
    "fixed_delivery_scope": "manifest_license_and_authorization_documents_only",
    "delivered_record_count": 0,
    "attribution_required": True,
    "attribution_instruction": (
        "Retain PathMNIST/MedMNIST source attribution and the CC BY 4.0 "
        "license notice in any permitted reuse."
    ),
    "patient_data_included": False,
}

PATHMNIST_DATA_DELIVERY_VERSION_ID = str(
    uuid5(NAMESPACE_URL, "medtrust:phase4:service-market:data-version")
)
PATHMNIST_MODEL_LICENSE_VERSION_ID = str(
    uuid5(NAMESPACE_URL, "medtrust:phase4:service-market:model-version")
)
AUTOMATED_DELIVERY_PROFILES = {
    (
        "data_document_package",
        PATHMNIST_DATA_DELIVERY_VERSION_ID,
    ): "pathmnist_public_documents_v1",
    (
        "model_license_package",
        PATHMNIST_MODEL_LICENSE_VERSION_ID,
    ): "pathmnist_model_license_documents_v1",
}


def automatic_delivery_profile(*, kind: str, version_id: str) -> str:
    try:
        return AUTOMATED_DELIVERY_PROFILES[(kind, version_id)]
    except KeyError as exc:
        raise CommercialPackageError(
            "automatic fulfillment is not registered for this fixed product version"
        ) from exc


def _entitled_version_id(
    *, kind: str, entitlement_snapshot: Mapping[str, Any]
) -> str:
    products = entitlement_snapshot.get("entitled_products")
    if not isinstance(products, list) or len(products) != 1:
        raise CommercialPackageError(
            "downloadable entitlement must bind exactly one fixed product version"
        )
    product = products[0]
    if not isinstance(product, Mapping):
        raise CommercialPackageError("downloadable entitlement product is invalid")
    expected_kind = "data" if kind == "data_document_package" else "model"
    if product.get("product_kind") != expected_kind:
        raise CommercialPackageError("downloadable entitlement product kind mismatch")
    version_id = product.get("version_id")
    if not isinstance(version_id, str):
        raise CommercialPackageError("downloadable entitlement version is missing")
    automatic_delivery_profile(kind=kind, version_id=version_id)
    return version_id


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8")


def build_delivery_zip(
    *, kind: str, entitlement_snapshot: Mapping[str, Any]
) -> tuple[str, bytes]:
    """Build a fixed allow-listed documentation ZIP; no caller path is accepted."""

    version_id = _entitled_version_id(
        kind=kind, entitlement_snapshot=entitlement_snapshot
    )
    order_number = str(entitlement_snapshot.get("order_number") or "ORDER")
    safe_order = "".join(
        char for char in order_number if char.isascii() and (char.isalnum() or char == "-")
    )[:48] or "ORDER"
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        if kind == "data_document_package":
            archive.writestr(
                "README.txt",
                "MedTrust Space public PathMNIST authorization package.\n"
                "This package contains metadata, a manifest and authorization terms only.\n"
                "It contains no patient-level records, hospital raw data or identifiers.\n",
            )
            archive.writestr(
                "pathmnist-public-manifest.json",
                _json_bytes(
                    {
                        "schema_version": "medtrust.pathmnist-public-manifest/v1",
                        **PATHMNIST_PUBLIC_DELIVERY_MANIFEST,
                        "licensed_product_version_id": version_id,
                        "record_delivery": False,
                        "patient_data_included": False,
                        "entitlement": dict(entitlement_snapshot),
                    }
                ),
            )
            archive.writestr(
                "AUTHORIZATION.txt",
                "Use is limited to the accepted purpose, duration and public benchmark terms.\n"
                "License: CC BY 4.0. Attribution to PathMNIST/MedMNIST and the upstream source is required.\n"
                "This demonstration does not grant access to hospital patient data.\n",
            )
            filename = f"{safe_order}-pathmnist-public-documents.zip"
        elif kind == "model_license_package":
            archive.writestr(
                "README.txt",
                "MedTrust Space model use-license documentation package.\n"
                "No model weights, executable binaries, registry credentials or source code are included.\n",
            )
            archive.writestr(
                "model-card.json",
                _json_bytes(
                    {
                        "schema_version": "medtrust.model-card/v1",
                        "licensed_product_version_id": version_id,
                        "intended_use": "accepted demonstration purpose only",
                        "weights_included": False,
                        "executable_included": False,
                        "entitlement": dict(entitlement_snapshot),
                    }
                ),
            )
            archive.writestr(
                "LICENSE.txt",
                "This is a limited, purpose-bound use-license record.\n"
                "It is not a transfer of model ownership and does not authorize redistribution.\n",
            )
            archive.writestr(
                "manifest.json",
                _json_bytes(
                    {
                        "schema_version": "medtrust.model-license-package/v1",
                        "allowlisted_files": [
                            "README.txt",
                            "model-card.json",
                            "LICENSE.txt",
                            "manifest.json",
                        ],
                        "weights_included": False,
                    }
                ),
            )
            filename = f"{safe_order}-model-license-documents.zip"
        else:
            raise CommercialPackageError("fulfillment is not downloadable")
    return filename, output.getvalue()

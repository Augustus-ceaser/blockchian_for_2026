from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.demo.phase4 import (
    PHASE4_SPACE_CODE,
    ensure_phase4_demo_initial,
)
from app.demo.service_market import ensure_phase4_service_market_products
from app.modules.identity.public_alpha import public_alpha_account_status
from app.modules.external_catalog.orthopedic_seed import (
    ensure_orthopedic_catalog_seed,
)
from app.modules.spaces.models import Space

RESOURCE_ROOT = Path(__file__).resolve().parents[1] / "resources" / "public_alpha"


def validate_safe_manifest(resource_root: Path = RESOURCE_ROOT) -> str:
    path = resource_root / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml"
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f"versioned demo model manifest is unavailable: {path}"
        ) from exc
    document = yaml.safe_load(content)
    if not isinstance(document, dict):
        raise RuntimeError("demo model manifest must be a YAML object")
    required_strings = (
        "model_name",
        "model_version",
        "model_digest",
        "entrypoint_id",
        "runtime",
        "input_schema_version",
        "output_schema_version",
        "asset_locator",
    )
    if any(not isinstance(document.get(key), str) for key in required_strings):
        raise RuntimeError("demo model manifest does not match the safe metadata schema")
    if document.get("non_clinical") is not True:
        raise RuntimeError("demo model manifest must be non-clinical")
    if document.get("synthetic_or_public") is not True:
        raise RuntimeError("demo model manifest must be synthetic or public")
    if document.get("contains_model_weights") is not False:
        raise RuntimeError("demo model manifest must not contain model weights")
    serialized = json.dumps(document, ensure_ascii=True, sort_keys=True)
    if "patient_id" in serialized.lower() or "medical_record" in serialized.lower():
        raise RuntimeError("demo model manifest contains a patient field")
    if any(token in serialized for token in ("C:\\\\", "D:\\\\", "/home/", "/Users/")):
        raise RuntimeError("demo model manifest contains an absolute local path")
    return hashlib.sha256(content).hexdigest()


async def _run() -> dict[str, object]:
    manifest_sha256 = validate_safe_manifest()
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                account_status = await public_alpha_account_status(session)
                if not account_status["foundation_complete"]:
                    raise RuntimeError(
                        "Public Alpha invitation accounts must be initialized first"
                    )
                existing = await session.scalar(
                    select(Space.id).where(Space.code == PHASE4_SPACE_CODE)
                )
                context = await ensure_phase4_demo_initial(session, workspace=RESOURCE_ROOT)
                service_market = await ensure_phase4_service_market_products(
                    session,
                    context,
                    workspace=RESOURCE_ROOT,
                )
                orthopedic = await ensure_orthopedic_catalog_seed(
                    session,
                    space_id=context.space_id,
                )
        return {
            "created": existing is None,
            "space_id": str(context.space_id),
            "demo": True,
            "synthetic_or_public": True,
            "non_clinical": True,
            "hard_isolation": False,
            "manifest_sha256": manifest_sha256,
            "service_market": {
                "data_product_id": str(service_market.data_product_id),
                "data_version_id": str(service_market.data_version_id),
                "model_product_id": str(service_market.model_product_id),
                "model_version_id": str(service_market.model_version_id),
                "data_created": service_market.data_created,
                "model_created": service_market.model_created,
            },
            "orthopedic_catalog": {
                "catalog_digest": orthopedic.catalog_digest,
                "dataset_source_id": str(orthopedic.data_source_id),
                "model_source_id": str(orthopedic.model_source_id),
                "datasets_inserted": orthopedic.datasets_inserted,
                "datasets_updated": orthopedic.datasets_updated,
                "datasets_unchanged": orthopedic.datasets_unchanged,
                "models_inserted": orthopedic.models_inserted,
                "models_updated": orthopedic.models_updated,
                "models_unchanged": orthopedic.models_unchanged,
                "fracatlas_materialization_status": (
                    orthopedic.fracatlas_materialization_status
                ),
                "execution_ready": False,
                "platform_validated": False,
            },
        }
    finally:
        await engine.dispose()


def main() -> None:
    print(json.dumps(asyncio.run(_run()), sort_keys=True))


if __name__ == "__main__":
    main()

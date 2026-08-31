from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from minio import Minio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.modules.roadshow_seal.services import read_business_state


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _stable_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")


def _minio_inventory() -> dict[str, Any]:
    settings = get_settings()
    client = Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
    buckets: dict[str, int] = {}
    for bucket in client.list_buckets():
        buckets[bucket.name] = sum(
            1 for _ in client.list_objects(bucket.name, recursive=True)
        )
    return {"object_count": sum(buckets.values()), "bucket_object_counts": buckets}


async def _read(database_url: str) -> dict[str, Any]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await read_business_state(session)
    finally:
        await engine.dispose()


def _manifest(
    state: dict[str, Any],
    storage: dict[str, Any],
    *,
    source_commit: str,
    source_dirty: bool,
) -> dict[str, Any]:
    reference = state["reference_relation"]
    assessment = reference["structured_assessment"]
    evidence_reference = reference["evidence_reference"]
    counts = state["counts"]
    status = state["status_counts"]
    document = {
        "manifest_version": "phase5.12.7/roadshow-state/v1",
        "project_name": "MedTrust Space",
        "generated_at": state["audit"]["head_occurred_at"],
        "source_commit": source_commit,
        "source_dirty": source_dirty,
        "alembic_head": state["alembic_head"],
        "deployment_mode": get_settings().deployment_mode,
        "canonical_storage_identity": {
            "postgres_volume": os.getenv(
                "MEDTRUST_POSTGRES_VOLUME_NAME", "medtrust-space_postgres_data"
            ),
            "minio_volume": os.getenv(
                "MEDTRUST_MINIO_VOLUME_NAME", "medtrust-space_minio_data"
            ),
        },
        "public_dataset_catalog_count": counts["external_dataset_records"],
        "public_model_catalog_count": counts["external_model_records"],
        "published_data_products": status["published_external_data_products"],
        "published_model_products": status["published_external_model_products"],
        "static_relation_count": (
            status["static_transformation_relations"]
            + status["static_incompatible_relations"]
        ),
        "verified_relation_count": 1 if reference["current_status"] == "verified" else 0,
        "reference_relation_id": str(reference["id"]),
        "reference_data_version_id": str(reference["data_product_version_id"]),
        "reference_model_version_id": str(reference["model_product_version_id"]),
        "reference_compute_run_id": evidence_reference["compute_run_id"],
        "reference_release_package_id": evidence_reference["result_package_id"],
        "reference_metrics": {
            "sample_count": assessment["sample_count"],
            "correct_count": assessment["correct_count"],
            "accuracy": assessment["aggregate_metrics"]["accuracy"],
        },
        "minio_object_count": storage["object_count"],
        "audit_head_digest": state["audit"]["head_digest"],
        "invalid_audit_chains": 0 if state["audit"]["chain_valid"] else 1,
        "hard_isolation": state["boundaries"]["hard_isolation"],
        "external_data_materialized": state["boundaries"]["external_data_materialized"],
        "external_model_materialized": state["boundaries"]["external_model_materialized"],
        "external_executor_registered": state["boundaries"]["external_executor_registered"],
        "clinical_use": state["boundaries"]["clinical_use"],
    }
    document["manifest_digest"] = "sha256:" + hashlib.sha256(
        _stable_bytes(document)
    ).hexdigest()
    return document


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate deterministic read-only Phase 5.12.7 state artifacts"
    )
    parser.add_argument("--kind", choices=("business-state", "manifest"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--source-commit", default="")
    parser.add_argument("--source-dirty", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    state = asyncio.run(_read(args.database_url or settings.database_url))
    storage = _minio_inventory()
    if args.kind == "business-state":
        document = state | {
            "storage": storage,
            "canonical_storage_identity": {
                "postgres_volume": os.getenv(
                    "MEDTRUST_POSTGRES_VOLUME_NAME", "medtrust-space_postgres_data"
                ),
                "minio_volume": os.getenv(
                    "MEDTRUST_MINIO_VOLUME_NAME", "medtrust-space_minio_data"
                ),
            },
        }
    else:
        if not args.source_commit:
            parser.error("--source-commit is required for manifest generation")
        document = _manifest(
            state,
            storage,
            source_commit=args.source_commit,
            source_dirty=args.source_dirty,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "kind": args.kind,
                "output": str(args.output),
                "manifest_digest": document.get("manifest_digest"),
                "minio_object_count": storage["object_count"],
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()

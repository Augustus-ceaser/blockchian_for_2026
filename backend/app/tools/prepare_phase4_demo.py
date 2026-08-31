from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.demo.phase4 import ensure_phase4_demo_initial
from app.demo.service_market import ensure_phase4_service_market_products
from app.core.config import get_settings
from app.modules.identity.local_auth import (
    USERNAME_BY_ROLE,
    ensure_local_demo_credentials,
)
from app.modules.external_catalog.orthopedic_materialization import (
    materialize_local_orthopedic_assets,
)
from app.modules.external_catalog.orthopedic_seed import (
    ensure_orthopedic_catalog_seed,
)


async def _run(database_url: str, workspace: Path) -> dict[str, object]:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                context = await ensure_phase4_demo_initial(session, workspace=workspace)
                service_market = await ensure_phase4_service_market_products(
                    session,
                    context,
                    workspace=workspace,
                )
                orthopedic_catalog = await ensure_orthopedic_catalog_seed(
                    session,
                    space_id=context.space_id,
                )
                orthopedic_asset_root = workspace / ".runtime" / "orthopedic-assets"
                orthopedic_materialization = None
                if orthopedic_asset_root.is_dir():
                    orthopedic_materialization = await materialize_local_orthopedic_assets(
                        session,
                        space_id=context.space_id,
                        asset_root=orthopedic_asset_root,
                    )
                passwords = get_settings().demo_passwords
                if not all(passwords.values()):
                    raise RuntimeError(
                        "Local demo passwords must be set in ignored local configuration"
                    )
                await ensure_local_demo_credentials(
                    session,
                    passwords=passwords,
                    min_password_length=get_settings().password_min_length,
                )
        return {
            "ready": True,
            "space_id": str(context.space_id),
            "data_product_id": str(context.data_product_id),
            "model_product_id": str(context.model_product_id),
            "service_market": {
                "data_product_id": str(service_market.data_product_id),
                "data_version_id": str(service_market.data_version_id),
                "model_product_id": str(service_market.model_product_id),
                "model_version_id": str(service_market.model_version_id),
                "data_created": service_market.data_created,
                "model_created": service_market.model_created,
            },
            "roles": sorted(context.actors),
            "usernames": [USERNAME_BY_ROLE[role] for role in sorted(context.actors)],
            "demo": True,
            "hard_isolation": False,
            "orthopedic_catalog": {
                "datasets_inserted": orthopedic_catalog.datasets_inserted,
                "datasets_updated": orthopedic_catalog.datasets_updated,
                "datasets_unchanged": orthopedic_catalog.datasets_unchanged,
                "models_inserted": orthopedic_catalog.models_inserted,
                "models_updated": orthopedic_catalog.models_updated,
                "models_unchanged": orthopedic_catalog.models_unchanged,
            },
            "orthopedic_materialization": (
                {
                    "status": "verified_static_candidate",
                    "dataset_outcome": orthopedic_materialization.dataset_outcome,
                    "model_outcome": orthopedic_materialization.model_outcome,
                    "application_eligible": False,
                    "executor_registered": False,
                    "can_execute": False,
                }
                if orthopedic_materialization is not None
                else {
                    "status": "assets_not_present_catalog_only",
                    "application_eligible": False,
                    "executor_registered": False,
                    "can_execute": False,
                }
            ),
        }
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create only the initial Phase 4 roadshow graph")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args.database_url, args.workspace.resolve())), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

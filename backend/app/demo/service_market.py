from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import (
    Phase4DemoContext,
    Phase4DemoError,
    command_for,
    load_pathmnist_model_registry,
)
from app.modules.audit import append_audit_event_with_outbox
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductSource,
    DataProductVersion,
    DataResource,
)
from app.modules.catalog.services import (
    add_product_source,
    approve_version,
    publish_version,
    submit_version_for_review,
)
from app.modules.connectors.models import Connector
from app.modules.contracts.services import canonical_document_digest
from app.modules.marketplace.models import (
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import (
    CONTROLLED_COMPUTE,
    DEIDENTIFIED_DATA_DELIVERY,
    MODEL_ARTIFACT_LICENSE,
    resolve_service_modes,
)
from app.modules.marketplace.services import (
    approve_model_version,
    publish_model_version,
    submit_model_version,
)


SERVICE_MARKET_DATA_PRODUCT_CODE = "PATHMNIST-SERVICE-MARKET-DATA-V1"
SERVICE_MARKET_MODEL_PRODUCT_CODE = "PATHMNIST-SERVICE-MARKET-MODEL-V1"
SERVICE_MARKET_MODEL_DESCRIPTION = (
    "固定白名单 CPU 推理模型；可申请模型使用许可或参与受控调用计算。"
)
LEGACY_SERVICE_MARKET_MODEL_DESCRIPTION = (
    "固定白名单 CPU 推理模型；可申请模型制品许可交付或参与受控调用计算。"
)


@dataclass(frozen=True)
class Phase4ServiceMarketSeedResult:
    data_product_id: UUID
    data_version_id: UUID
    model_product_id: UUID
    model_version_id: UUID
    data_created: bool
    model_created: bool


def _market_id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase4:service-market:{label}")


def _policy_with_modes(
    policy: dict,
    modes: list[str],
) -> dict:
    document = deepcopy(policy)
    document["service_modes"] = modes
    # Commercial pricing is an independently versioned plan.  Keeping it out
    # of this deterministic approved v1 policy prevents the same version id
    # from receiving different policy digests across old and fresh databases.
    document.pop("commercial_offer", None)
    return document


async def _existing_data_product(
    session: AsyncSession,
    *,
    space_id: UUID,
) -> DataProduct | None:
    product = await session.scalar(
        select(DataProduct).where(
            DataProduct.space_id == space_id,
            DataProduct.product_code == SERVICE_MARKET_DATA_PRODUCT_CODE,
        )
    )
    if product is not None and product.id != _market_id("data-product"):
        raise Phase4DemoError("service-market data product code is already in use")
    return product


async def _existing_model_product(
    session: AsyncSession,
    *,
    space_id: UUID,
) -> ModelProduct | None:
    product = await session.scalar(
        select(ModelProduct).where(
            ModelProduct.space_id == space_id,
            ModelProduct.product_code == SERVICE_MARKET_MODEL_PRODUCT_CODE,
        )
    )
    if product is not None and product.id != _market_id("model-product"):
        raise Phase4DemoError("service-market model product code is already in use")
    return product


async def _source_data_graph(
    session: AsyncSession,
    context: Phase4DemoContext,
) -> tuple[DataProductVersion, DataResource, DataProductSource, Connector]:
    version = await session.get(DataProductVersion, context.data_version_id)
    resource = await session.scalar(
        select(DataResource)
        .where(DataResource.data_product_version_id == context.data_version_id)
        .order_by(DataResource.position_no)
        .limit(1)
    )
    source = (
        None
        if resource is None
        else await session.scalar(
            select(DataProductSource)
            .where(DataProductSource.data_resource_id == resource.id)
            .order_by(DataProductSource.source_role)
            .limit(1)
        )
    )
    connector = None if source is None else await session.get(Connector, source.connector_id)
    if version is None or resource is None or source is None or connector is None:
        raise Phase4DemoError("source PathMNIST data graph is incomplete")
    return version, resource, source, connector


async def _ensure_data_market_product(
    session: AsyncSession,
    context: Phase4DemoContext,
) -> tuple[DataProduct, DataProductVersion, bool]:
    actor = context.actors["data_provider"]
    operator = context.actors["space_operator"]
    product = await _existing_data_product(session, space_id=context.space_id)
    created = product is None
    source_version, source_resource, source, connector = await _source_data_graph(
        session, context
    )
    policy = _policy_with_modes(
        source_version.default_policy_template,
        [CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY],
    )
    if product is None:
        product = DataProduct(
            id=_market_id("data-product"),
            space_id=context.space_id,
            provider_organization_id=actor.organization_id,
            product_code=SERVICE_MARKET_DATA_PRODUCT_CODE,
            name="PathMNIST 结直肠病理数据服务（公开演示）",
            description=(
                "公开、无直接标识符的固定 PathMNIST 范围；可申请公开数据授权交付"
                "或在医院节点受控调用计算。"
            ),
            product_type="controlled_compute",
            domain="digital_pathology",
            lifecycle_status="draft",
            is_demo=True,
            created_by=actor.user_id,
        )
        session.add(product)
        await session.flush()
    elif product.provider_organization_id != actor.organization_id:
        raise Phase4DemoError("service-market data product owner is invalid")

    version = await session.get(DataProductVersion, _market_id("data-version"))
    if version is None:
        if product.lifecycle_status != "draft":
            raise Phase4DemoError("service-market data product version is missing")
        policy_digest = canonical_document_digest(policy)
        snapshot = {
            "schema_version": "phase5.16/service-market-data-version/v1",
            "product_code": product.product_code,
            "version_label": "v1.0",
            "source_version_id": str(source_version.id),
            "resource_digest": source_resource.resource_digest,
            "policy_digest": policy_digest,
        }
        version = DataProductVersion(
            id=_market_id("data-version"),
            space_id=context.space_id,
            data_product_id=product.id,
            version_no=1,
            version_label="v1.0",
            status="draft",
            content_summary=source_version.content_summary,
            scope_metadata=deepcopy(source_version.scope_metadata),
            linkage_metadata=deepcopy(source_version.linkage_metadata),
            quality_report=deepcopy(source_version.quality_report),
            classification_level=source_version.classification_level,
            default_use_mode=CONTROLLED_COMPUTE,
            default_policy_template=policy,
            default_policy_digest=policy_digest,
            provenance_summary=deepcopy(source_version.provenance_summary),
            snapshot_digest=canonical_document_digest(snapshot),
            created_by=actor.user_id,
        )
        session.add(version)
        await session.flush()
    elif version.data_product_id != product.id:
        raise Phase4DemoError("service-market data version belongs to another product")

    expected_modes = (CONTROLLED_COMPUTE, DEIDENTIFIED_DATA_DELIVERY)
    if resolve_service_modes("data", version.default_policy_template) != expected_modes:
        raise Phase4DemoError("service-market data policy does not match expected modes")
    if canonical_document_digest(version.default_policy_template) != version.default_policy_digest:
        raise Phase4DemoError("service-market data policy digest is invalid")

    resource = await session.get(DataResource, _market_id("data-resource"))
    if resource is None:
        if version.status != "draft":
            raise Phase4DemoError("approved service-market data resource is missing")
        resource = DataResource(
            id=_market_id("data-resource"),
            space_id=context.space_id,
            data_product_version_id=version.id,
            resource_code=source_resource.resource_code,
            name=source_resource.name,
            resource_type=source_resource.resource_type,
            modality=source_resource.modality,
            format=source_resource.format,
            schema_metadata=deepcopy(source_resource.schema_metadata),
            scope_metadata=deepcopy(source_resource.scope_metadata),
            quality_report=deepcopy(source_resource.quality_report),
            classification_level=source_resource.classification_level,
            resource_digest=source_resource.resource_digest,
            position_no=1,
            created_by=actor.user_id,
        )
        session.add(resource)
        await session.flush()
    elif resource.data_product_version_id != version.id:
        raise Phase4DemoError("service-market data resource belongs to another version")

    bound_source = await session.scalar(
        select(DataProductSource).where(
            DataProductSource.data_resource_id == resource.id,
            DataProductSource.connector_id == connector.id,
            DataProductSource.local_resource_alias == source.local_resource_alias,
        )
    )
    if bound_source is None:
        if version.status != "draft":
            raise Phase4DemoError("approved service-market data source is missing")
        await add_product_source(
            session,
            resource,
            connector,
            local_resource_alias=source.local_resource_alias,
            source_digest=source.source_digest,
            source_role=source.source_role,
            source_snapshot_at=source.source_snapshot_at,
        )

    if version.status == "draft":
        await submit_version_for_review(session, version)
        command = command_for(actor, "service-market-data-submit", "seed-v1")
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type="data_product.version.submitted",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.16/service-market-data-submitted/v1",
                "version_id": str(version.id),
                "snapshot_digest": version.snapshot_digest,
                "policy_digest": version.default_policy_digest,
            },
            **command.append_kwargs(),
        )
    if version.status == "under_review":
        await approve_version(session, version, approved_by=operator.user_id)
        command = command_for(operator, "service-market-data-approve", "seed-v1")
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type="data_product.version.approved",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.16/service-market-data-approved/v1",
                "version_id": str(version.id),
                "snapshot_digest": version.snapshot_digest,
                "policy_digest": version.default_policy_digest,
            },
            **command.append_kwargs(),
        )
    publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == version.id,
            DataProductPublication.status == "active",
        )
    )
    if publication is None:
        if version.status != "approved":
            raise Phase4DemoError("service-market data version is not publishable")
        publication = await publish_version(
            session,
            product,
            version,
            published_by=operator.user_id,
            visibility="space",
        )
        command = command_for(operator, "service-market-data-publish", "seed-v1")
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type="data_product.version.published",
            subject_type="data_product_version",
            subject_id=version.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.16/service-market-data-published/v1",
                "version_id": str(version.id),
                "publication_id": str(publication.id),
                "snapshot_digest": version.snapshot_digest,
                "policy_digest": version.default_policy_digest,
            },
            **command.append_kwargs(),
        )
    return product, version, created


async def _ensure_model_market_product(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    workspace: Path,
) -> tuple[ModelProduct, ModelVersion, bool]:
    actor = context.actors["model_provider"]
    operator = context.actors["space_operator"]
    source_version = await session.get(ModelVersion, context.model_version_id)
    if source_version is None:
        raise Phase4DemoError("source PathMNIST model version is missing")
    product = await _existing_model_product(session, space_id=context.space_id)
    created = product is None
    policy = _policy_with_modes(
        source_version.default_policy_template,
        [CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE],
    )
    if product is None:
        product = ModelProduct(
            id=_market_id("model-product"),
            space_id=context.space_id,
            provider_organization_id=actor.organization_id,
            product_code=SERVICE_MARKET_MODEL_PRODUCT_CODE,
            name="PathMNIST ResNet-18 模型服务（非临床演示）",
            description=SERVICE_MARKET_MODEL_DESCRIPTION,
            domain="digital_pathology",
            lifecycle_status="draft",
            is_demo=True,
            created_by=actor.user_id,
        )
        session.add(product)
        await session.flush()
    elif product.provider_organization_id != actor.organization_id:
        raise Phase4DemoError("service-market model product owner is invalid")
    elif product.description == LEGACY_SERVICE_MARKET_MODEL_DESCRIPTION:
        product.description = SERVICE_MARKET_MODEL_DESCRIPTION
    elif product.description != SERVICE_MARKET_MODEL_DESCRIPTION:
        raise Phase4DemoError("service-market model product description is invalid")

    version = await session.get(ModelVersion, _market_id("model-version"))
    if version is None:
        if product.lifecycle_status != "draft":
            raise Phase4DemoError("service-market model version is missing")
        version = ModelVersion(
            id=_market_id("model-version"),
            space_id=context.space_id,
            model_product_id=product.id,
            version_no=1,
            version_label="v1.0",
            status="draft",
            entrypoint_id=source_version.entrypoint_id,
            model_digest=source_version.model_digest,
            manifest_digest=source_version.manifest_digest,
            registry_digest=source_version.registry_digest,
            runtime=source_version.runtime,
            input_schema_version=source_version.input_schema_version,
            output_schema_version=source_version.output_schema_version,
            compatibility_metadata=deepcopy(source_version.compatibility_metadata),
            license_metadata=deepcopy(source_version.license_metadata),
            default_policy_template=policy,
            default_policy_digest=canonical_document_digest(policy),
            created_by=actor.user_id,
        )
        session.add(version)
        await session.flush()
    elif version.model_product_id != product.id:
        raise Phase4DemoError("service-market model version belongs to another product")

    expected_modes = (CONTROLLED_COMPUTE, MODEL_ARTIFACT_LICENSE)
    if resolve_service_modes("model", version.default_policy_template) != expected_modes:
        raise Phase4DemoError("service-market model policy does not match expected modes")
    if canonical_document_digest(version.default_policy_template) != version.default_policy_digest:
        raise Phase4DemoError("service-market model policy digest is invalid")

    registry = load_pathmnist_model_registry(workspace)
    if version.status == "draft":
        await submit_model_version(
            session,
            version,
            registry=registry,
            provider_organization_id=actor.organization_id,
            provider_user_id=actor.user_id,
            command=command_for(actor, "service-market-model-submit", "seed-v1"),
            evidence_facts={"service_market_seed": True},
        )
    if version.status == "under_review":
        await approve_model_version(
            session,
            version,
            registry=registry,
            operator_organization_id=operator.organization_id,
            operator_user_id=operator.user_id,
            command=command_for(operator, "service-market-model-approve", "seed-v1"),
            evidence_facts={"service_market_seed": True},
        )
    publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == version.id,
            ModelPublication.status == "active",
        )
    )
    if publication is None:
        if version.status != "approved":
            raise Phase4DemoError("service-market model version is not publishable")
        await publish_model_version(
            session,
            product,
            version,
            operator_organization_id=operator.organization_id,
            operator_user_id=operator.user_id,
            command=command_for(operator, "service-market-model-publish", "seed-v1"),
            visibility="space",
            evidence_facts={"service_market_seed": True},
        )
    return product, version, created


async def ensure_phase4_service_market_products(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    workspace: Path,
) -> Phase4ServiceMarketSeedResult:
    data_product, data_version, data_created = await _ensure_data_market_product(
        session, context
    )
    model_product, model_version, model_created = await _ensure_model_market_product(
        session,
        context,
        workspace=workspace,
    )
    return Phase4ServiceMarketSeedResult(
        data_product_id=data_product.id,
        data_version_id=data_version.id,
        model_product_id=model_product.id,
        model_version_id=model_version.id,
        data_created=data_created,
        model_created=model_created,
    )

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.registry import ModelRegistry
from app.modules.applications.models import (
    Application,
    ApplicationAttachment,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
)
from app.modules.applications.services import submit_application
from app.modules.audit import (
    AuditCommandContext,
    AuditEvent,
    append_audit_event_with_outbox,
    digest_idempotency_key,
)
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
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.compute.models import Artifact, ComputeJob, ComputeRun
from app.modules.compute.services import (
    create_compute_job,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.contracts.models import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
)
from app.modules.contracts.services import (
    activate_contract_revision,
    build_contract_eligibility_evidence,
    canonical_document_digest,
    propose_contract_revision,
    sign_contract_revision,
)
from app.modules.identity.models import (
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ApprovedResultPackage,
    ArtifactReviewDecision,
    ArtifactReviewTask,
    ContractModelObject,
    ContractReadinessConfirmation,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.services import (
    approve_model_version,
    attach_model_to_demand,
    claim_artifact_review_task,
    confirm_contract_readiness,
    create_approved_result_package,
    create_artifact_review_plan,
    create_download_grant,
    decide_artifact_review_task,
    publish_model_version,
    require_all_readiness,
    submit_model_version,
)
from app.modules.reviews.models import ReviewDecision, ReviewTask
from app.modules.reviews.services import claim_review_task, submit_review_decision
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole


PHASE4_SPACE_CODE = "MEDTRUST-PHASE4-ROADSHOW"
PHASE4_DATA_PRODUCT_CODE = "PATHMNIST-COLORECTAL-PUBLIC-V1"
PHASE4_MODEL_PRODUCT_CODE = "PATHMNIST-RESNET18-V1"
PHASE4_APPLICATION_NUMBER = "APP-PHASE4-PATHMNIST-001"
PHASE4_CONTRACT_NUMBER = "CTR-PHASE4-PATHMNIST-001"
MODEL_DIGEST = "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
DATASET_DIGEST = "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"

ROLE_IDENTITIES = {
    "space_operator": (
        "MedTrust Space运营中心（演示）",
        "operator",
        "MedTrust 运营管理员（演示）",
    ),
    "data_provider": (
        "华南肿瘤医学中心（演示）",
        "hospital",
        "医院数据管理员（演示）",
    ),
    "model_provider": (
        "智衡医疗AI（演示）",
        "ai_company",
        "模型产品管理员（演示）",
    ),
    "data_requester": (
        "远景医药研发（演示）",
        "research_institute",
        "研发需求管理员（演示）",
    ),
}

CATALOG_CURATOR_IDENTITY = (
    "MedTrust Public Data Catalog Curator",
    "service_provider",
    "Public Data Catalog Curator",
)
ALL_ROLE_IDENTITIES = {
    **ROLE_IDENTITIES,
    "catalog_curator": CATALOG_CURATOR_IDENTITY,
}


class Phase4DemoError(ValueError):
    pass


@dataclass(frozen=True)
class DemoActor:
    role: str
    organization_id: UUID
    user_id: UUID
    organization_name: str
    user_name: str


@dataclass(frozen=True)
class Phase4DemoContext:
    space_id: UUID
    data_product_id: UUID
    data_version_id: UUID
    model_product_id: UUID
    model_version_id: UUID
    actors: dict[str, DemoActor]


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:phase4:{label}")


def command_for(actor: DemoActor, action: str, raw_key: str) -> AuditCommandContext:
    command_id = uuid5(NAMESPACE_URL, f"medtrust:phase4:{action}:{raw_key}")
    return AuditCommandContext(
        command_id=command_id,
        idempotency_key=digest_idempotency_key(f"phase4:{action}:{raw_key}"),
        correlation_id=_id("roadshow-correlation"),
        actor_type="user",
        actor_organization_id=actor.organization_id,
        actor_user_id=actor.user_id,
    )


def load_pathmnist_model_registry(workspace: Path) -> ModelRegistry:
    document = yaml.safe_load(
        (workspace / "registered_assets/pathmnist_resnet18_v1/model_manifest.yaml").read_text(
            encoding="utf-8"
        )
    )
    registry = ModelRegistry()
    registry.register(document)
    return registry


async def _actor_rows(session: AsyncSession) -> dict[str, DemoActor]:
    result: dict[str, DemoActor] = {}
    for role, (organization_name, _, user_name) in ALL_ROLE_IDENTITIES.items():
        organization = await session.scalar(
            select(Organization).where(
                Organization.external_identity_ref == f"phase4:{role}"
            )
        )
        user = await session.scalar(
            select(User).where(
                User.identity_issuer == "medtrust-demo",
                User.identity_subject == f"phase4:{role}",
            )
        )
        if organization is None or user is None:
            raise Phase4DemoError("Phase 4 demo identity is incomplete")
        result[role] = DemoActor(
            role=role,
            organization_id=organization.id,
            user_id=user.id,
            organization_name=organization_name,
            user_name=user_name,
        )
    return result


async def _ensure_catalog_curator(
    session: AsyncSession, *, space: Space
) -> None:
    role = "catalog_curator"
    organization_name, organization_type, user_name = CATALOG_CURATOR_IDENTITY
    operator = await session.scalar(
        select(User).where(
            User.identity_issuer == "medtrust-demo",
            User.identity_subject == "phase4:space_operator",
        )
    )
    if operator is None:
        raise Phase4DemoError("Phase 4 operator identity is incomplete")

    user = await session.scalar(
        select(User).where(
            User.identity_issuer == "medtrust-demo",
            User.identity_subject == f"phase4:{role}",
        )
    )
    if user is None:
        user = User(
            id=_id(f"user:{role}"),
            identity_issuer="medtrust-demo",
            identity_subject=f"phase4:{role}",
            display_name=user_name,
            email=f"{role}@demo.medtrust.invalid",
            status="active",
            mfa_status="enabled",
            is_demo=True,
        )
        session.add(user)
        await session.flush([user])

    organization = await session.scalar(
        select(Organization).where(
            Organization.external_identity_ref == f"phase4:{role}"
        )
    )
    if organization is None:
        organization = Organization(
            id=_id(f"organization:{role}"),
            legal_name=organization_name,
            display_name=organization_name,
            organization_type=organization_type,
            verification_status="verified",
            status="active",
            external_identity_ref=f"phase4:{role}",
            contact_metadata={
                "schema_version": "1.0",
                "demo": True,
                "role_boundary": "metadata_curator_not_upstream_rights_holder",
            },
            is_demo=True,
            created_by=user.id,
        )
        session.add(organization)
        await session.flush([organization])

    membership = await session.scalar(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == organization.id,
            OrganizationMember.user_id == user.id,
        )
    )
    if membership is None:
        membership = OrganizationMember(
            id=_id(f"membership:{role}"),
            organization_id=organization.id,
            user_id=user.id,
            status="active",
            valid_from=datetime.now(timezone.utc),
            created_by=user.id,
        )
        session.add(membership)
        await session.flush([membership])
        session.add(
            OrganizationMemberRole(
                organization_member_id=membership.id,
                role_code="auditor",
                granted_by=operator.id,
            )
        )

    participant = await session.scalar(
        select(SpaceParticipant).where(
            SpaceParticipant.space_id == space.id,
            SpaceParticipant.organization_id == organization.id,
        )
    )
    if participant is None:
        participant = SpaceParticipant(
            id=_id(f"participant:{role}"),
            space_id=space.id,
            organization_id=organization.id,
            admission_status="admitted",
            ruleset_accepted_version=space.ruleset_version,
            admitted_at=datetime.now(timezone.utc),
            created_by=operator.id,
        )
        session.add(participant)
        await session.flush([participant])
        session.add(
            SpaceParticipantRole(
                space_participant_id=participant.id,
                role_code=role,
                granted_by=operator.id,
            )
        )


async def get_phase4_context(session: AsyncSession) -> Phase4DemoContext:
    space = await session.scalar(select(Space).where(Space.code == PHASE4_SPACE_CODE))
    data_product = await session.scalar(
        select(DataProduct).where(DataProduct.product_code == PHASE4_DATA_PRODUCT_CODE)
    )
    model_product = await session.scalar(
        select(ModelProduct).where(ModelProduct.product_code == PHASE4_MODEL_PRODUCT_CODE)
    )
    if space is None or data_product is None or model_product is None:
        raise Phase4DemoError("Phase 4 roadshow baseline is not initialized")
    data_version = await session.scalar(
        select(DataProductVersion).where(
            DataProductVersion.data_product_id == data_product.id,
            DataProductVersion.version_no == 1,
        )
    )
    model_version = await session.scalar(
        select(ModelVersion).where(
            ModelVersion.model_product_id == model_product.id,
            ModelVersion.version_no == 1,
        )
    )
    if data_version is None or model_version is None:
        raise Phase4DemoError("Phase 4 catalog versions are incomplete")
    return Phase4DemoContext(
        space_id=space.id,
        data_product_id=data_product.id,
        data_version_id=data_version.id,
        model_product_id=model_product.id,
        model_version_id=model_version.id,
        actors=await _actor_rows(session),
    )


async def ensure_phase4_demo_initial(
    session: AsyncSession, *, workspace: Path
) -> Phase4DemoContext:
    existing = await session.scalar(select(Space).where(Space.code == PHASE4_SPACE_CODE))
    if existing is not None:
        await _ensure_catalog_curator(session, space=existing)
        return await get_phase4_context(session)

    now = datetime.now(timezone.utc)
    actors: dict[str, DemoActor] = {}
    for role, (organization_name, organization_type, user_name) in ROLE_IDENTITIES.items():
        user = User(
            id=_id(f"user:{role}"),
            identity_issuer="medtrust-demo",
            identity_subject=f"phase4:{role}",
            display_name=user_name,
            email=f"{role}@demo.medtrust.invalid",
            status="active",
            mfa_status="enabled",
            is_demo=True,
        )
        organization = Organization(
            id=_id(f"organization:{role}"),
            legal_name=organization_name,
            display_name=organization_name,
            organization_type=organization_type,
            verification_status="verified",
            status="active",
            external_identity_ref=f"phase4:{role}",
            contact_metadata={"schema_version": "1.0", "demo": True},
            is_demo=True,
        )
        session.add_all([user, organization])
        actors[role] = DemoActor(
            role=role,
            organization_id=organization.id,
            user_id=user.id,
            organization_name=organization_name,
            user_name=user_name,
        )
    await session.flush()
    for actor in actors.values():
        membership = OrganizationMember(
            id=_id(f"membership:{actor.role}"),
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            status="active",
            valid_from=now,
            created_by=actor.user_id,
        )
        session.add(membership)
        await session.flush()
        member_roles = {"contract_signer", "auditor"}
        if actor.role == "data_provider":
            member_roles |= {"provider_data_admin", "provider_output_reviewer", "connector_operator"}
        elif actor.role == "model_provider":
            member_roles |= {"consumer_ai_developer", "connector_operator"}
        elif actor.role == "data_requester":
            member_roles |= {"consumer_researcher"}
        else:
            member_roles |= {"connector_operator"}
        session.add_all(
            [
                OrganizationMemberRole(
                    organization_member_id=membership.id,
                    role_code=code,
                    granted_by=actor.user_id,
                )
                for code in sorted(member_roles)
            ]
        )

    operator = actors["space_operator"]
    space = Space(
        id=_id("space"),
        code=PHASE4_SPACE_CODE,
        name="MedTrust 数字病理多方协作空间（演示）",
        space_type="industry",
        operator_organization_id=operator.organization_id,
        status="active",
        ruleset_version="phase4-demo-v1",
        classification_scheme_version="medical-demo-v1",
        default_retention_policy={"schema_version": "1.0", "days": 30},
        is_demo=True,
        created_by=operator.user_id,
    )
    session.add(space)
    await session.flush()
    for actor in actors.values():
        participant = SpaceParticipant(
            id=_id(f"participant:{actor.role}"),
            space_id=space.id,
            organization_id=actor.organization_id,
            admission_status="admitted",
            ruleset_accepted_version=space.ruleset_version,
            admitted_at=now,
            created_by=operator.user_id,
        )
        session.add(participant)
        await session.flush()
        session.add(
            SpaceParticipantRole(
                space_participant_id=participant.id,
                role_code=actor.role,
                granted_by=operator.user_id,
            )
        )

    provider = actors["data_provider"]
    connector = Connector(
        id=_id("connector:data-provider"),
        space_id=space.id,
        owner_organization_id=provider.organization_id,
        external_connector_id="phase4-provider-node",
        name="医院数字病理受控节点（演示）",
        verification_status="verified",
        runtime_status="online",
        endpoint_metadata={"schema_version": "1.0", "mode": "local-demo"},
        certificate_fingerprint="sha256:phase4-demo-certificate",
        last_heartbeat_at=now,
        last_policy_ack_at=now,
        is_demo=True,
        created_by=provider.user_id,
    )
    session.add(connector)
    await session.flush()
    capability_parameters = {
        "product_publish": {"metadata_only": True},
        "controlled_compute_execution": {
            "environment_modes": ["controlled_compute"],
            "algorithm_digest_enforced": True,
            "run_count_enforced": True,
            "effective_window_enforced": True,
        },
        "egress_policy_enforcement": {
            "raw_export_denied": True,
            "artifact_review_gate": True,
            "output_type_filter": True,
        },
        "audit_evidence_emit": {
            "audit_levels": ["full"],
            "digest_algorithm": "sha256",
            "failure_mode": "fail_closed",
        },
    }
    session.add_all(
        [
            ConnectorCapability(
                connector_id=connector.id,
                capability_code=code,
                capability_version="1.0",
                status="verified",
                parameters=parameters,
                verified_at=now,
            )
            for code, parameters in capability_parameters.items()
        ]
    )

    data_policy = {
        "schema_version": "1.0",
        "service_modes": ["controlled_compute", "deidentified_data_delivery"],
        "allowed_actions": ["model_validation"],
        "allowed_outputs": ["model_artifact"],
        "environment_mode": "controlled_compute",
        "deny": ["raw_data", "patient_level_result", "features"],
    }
    data_product = DataProduct(
        id=_id("data-product"),
        space_id=space.id,
        provider_organization_id=provider.organization_id,
        product_code=PHASE4_DATA_PRODUCT_CODE,
        name="结直肠组织病理分类数据产品（公开验证）",
        description="PathMNIST公开测试集的固定20张图像受控验证范围；目录仅展示元数据。",
        product_type="controlled_compute",
        domain="digital_pathology",
        lifecycle_status="draft",
        is_demo=True,
        created_by=provider.user_id,
    )
    session.add(data_product)
    await session.flush()
    data_snapshot = {
        "schema_version": "1.0",
        "product_code": data_product.product_code,
        "version": "v1.0",
        "dataset_manifest_digest": DATASET_DIGEST,
        "fixed_scope": "pathmnist-test-fixed-20",
    }
    data_version = DataProductVersion(
        id=_id("data-version"),
        space_id=space.id,
        data_product_id=data_product.id,
        version_no=1,
        version_label="v1.0",
        status="draft",
        content_summary="PathMNIST 28×28 RGB，9类，官方test split固定20张演示图像。",
        scope_metadata={"schema_version": "1.0", "image_count": 20, "case_count": 20},
        linkage_metadata={"schema_version": "1.0", "direct_identifiers": False},
        quality_report={"schema_version": "1.0", "grade": "A", "manifest_validated": True},
        classification_level="public_demo",
        default_use_mode="controlled_compute",
        default_policy_template=data_policy,
        default_policy_digest=canonical_document_digest(data_policy),
        provenance_summary={
            "schema_version": "1.0",
            "source": "MedMNIST PathMNIST",
            "license": "CC BY 4.0",
            "public_validation": True,
        },
        snapshot_digest=canonical_document_digest(data_snapshot),
        created_by=provider.user_id,
    )
    session.add(data_version)
    await session.flush()
    resource = DataResource(
        id=_id("data-resource"),
        space_id=space.id,
        data_product_version_id=data_version.id,
        resource_code="PATHMNIST-TEST-20",
        name="PathMNIST固定测试子集（公开验证）",
        resource_type="image_collection",
        modality="digital_pathology_patch",
        format="npz",
        schema_metadata={"schema_version": "1.0", "shape": [20, 28, 28, 3], "dtype": "uint8"},
        scope_metadata={"schema_version": "1.0", "split": "test", "fixed_indices": True},
        quality_report={"schema_version": "1.0", "labels_available": True},
        classification_level="public_demo",
        resource_digest=DATASET_DIGEST,
        position_no=1,
        created_by=provider.user_id,
    )
    session.add(resource)
    await session.flush()
    await add_product_source(
        session,
        resource,
        connector,
        local_resource_alias="registered://datasets/pathmnist/v1/test-20",
        source_digest=DATASET_DIGEST,
        source_role="primary",
        source_snapshot_at=now,
    )

    model_provider = actors["model_provider"]
    registry = load_pathmnist_model_registry(workspace)
    registration = registry.require_enabled(MODEL_DIGEST)
    model_policy = {
        "schema_version": "1.0",
        "service_modes": ["controlled_compute", "model_artifact_license"],
        "allowed_actions": ["model_validation"],
        "allowed_outputs": ["model_artifact"],
        "deny": ["model_weights", "arbitrary_code", "raw_features"],
        "inference_only": True,
    }
    model_product = ModelProduct(
        id=_id("model-product"),
        space_id=space.id,
        provider_organization_id=model_provider.organization_id,
        product_code=PHASE4_MODEL_PRODUCT_CODE,
        name="PathMNIST ResNet-18病理分类模型",
        description="固定白名单、CPU推理、9分类的非临床工程演示模型。",
        domain="digital_pathology",
        lifecycle_status="draft",
        is_demo=True,
        created_by=model_provider.user_id,
    )
    session.add(model_product)
    await session.flush()
    model_version = ModelVersion(
        id=_id("model-version"),
        space_id=space.id,
        model_product_id=model_product.id,
        version_no=1,
        version_label="v1.0",
        status="draft",
        entrypoint_id=registration.entrypoint_id,
        model_digest=registration.model_digest,
        manifest_digest=canonical_document_digest(
            {"schema_version": "1.0", "entrypoint_id": registration.entrypoint_id, "model_digest": registration.model_digest}
        ),
        registry_digest=registration.registration_digest,
        runtime=registration.runtime,
        input_schema_version=registration.input_schema_version,
        output_schema_version=registration.output_schema_version,
        compatibility_metadata={"schema_version": "1.0", "modality": "digital_pathology_patch", "input_shape": [3, 28, 28], "classes": 9},
        license_metadata={"schema_version": "1.0", "use": "internal non-clinical reproducibility demo", "redistribution": False},
        default_policy_template=model_policy,
        default_policy_digest=canonical_document_digest(model_policy),
        created_by=model_provider.user_id,
    )
    session.add(model_version)
    await session.flush()
    await _ensure_catalog_curator(session, space=space)
    return await get_phase4_context(session)


async def submit_data_listing_command(
    session: AsyncSession, context: Phase4DemoContext, *, raw_key: str
) -> DataProductVersion:
    version = await session.get(DataProductVersion, context.data_version_id)
    actor = context.actors["data_provider"]
    if version is None:
        raise Phase4DemoError("data version is missing")
    if version.status != "draft":
        return version
    await submit_version_for_review(session, version)
    command = command_for(actor, "data-listing-submit", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="data_product.version.submitted",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={"schema_version": "phase4-data-listing-submitted/v1", "version_id": str(version.id), "snapshot_digest": version.snapshot_digest},
        **command.append_kwargs(),
    )
    return version


async def approve_data_listing_command(
    session: AsyncSession, context: Phase4DemoContext, *, raw_key: str
) -> DataProductPublication:
    version = await session.get(DataProductVersion, context.data_version_id)
    product = await session.get(DataProduct, context.data_product_id)
    actor = context.actors["space_operator"]
    if version is None or product is None:
        raise Phase4DemoError("data catalog graph is missing")
    existing = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == version.id,
            DataProductPublication.status == "active",
        )
    )
    if existing is not None:
        return existing
    if version.status != "under_review":
        raise Phase4DemoError("data version is not awaiting platform review")
    await approve_version(session, version, approved_by=actor.user_id)
    approved_command = command_for(actor, "data-listing-approve", f"{raw_key}:approve")
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="data_product.version.approved",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={"schema_version": "phase4-data-listing-approved/v1", "version_id": str(version.id), "snapshot_digest": version.snapshot_digest},
        **approved_command.append_kwargs(),
    )
    publication = await publish_version(
        session, product, version, published_by=actor.user_id, visibility="space"
    )
    published_command = command_for(actor, "data-listing-publish", f"{raw_key}:publish")
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="data_product.version.published",
        subject_type="data_product_version",
        subject_id=version.id,
        result="success",
        evidence_snapshot={"schema_version": "phase4-data-listing-published/v1", "version_id": str(version.id), "publication_id": str(publication.id), "visibility": "space"},
        **published_command.append_kwargs(),
    )
    return publication


async def submit_model_listing_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    workspace: Path,
    raw_key: str,
) -> ModelVersion:
    version = await session.get(ModelVersion, context.model_version_id)
    actor = context.actors["model_provider"]
    if version is None:
        raise Phase4DemoError("model version is missing")
    if version.status == "draft":
        await submit_model_version(
            session,
            version,
            registry=load_pathmnist_model_registry(workspace),
            provider_organization_id=actor.organization_id,
            provider_user_id=actor.user_id,
            command=command_for(actor, "model-listing-submit", raw_key),
        )
    return version


async def approve_model_listing_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    workspace: Path,
    raw_key: str,
) -> ModelPublication:
    version = await session.get(ModelVersion, context.model_version_id)
    product = await session.get(ModelProduct, context.model_product_id)
    actor = context.actors["space_operator"]
    if version is None or product is None:
        raise Phase4DemoError("model catalog graph is missing")
    existing = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == version.id,
            ModelPublication.status == "active",
        )
    )
    if existing is not None:
        return existing
    await approve_model_version(
        session,
        version,
        registry=load_pathmnist_model_registry(workspace),
        operator_organization_id=actor.organization_id,
        operator_user_id=actor.user_id,
        command=command_for(actor, "model-listing-approve", f"{raw_key}:approve"),
    )
    return await publish_model_version(
        session,
        product,
        version,
        operator_organization_id=actor.organization_id,
        operator_user_id=actor.user_id,
        command=command_for(actor, "model-listing-publish", f"{raw_key}:publish"),
    )


async def submit_compute_demand_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    raw_key: str,
) -> ApplicationSnapshot:
    """Create and freeze one multi-party compute demand from published metadata."""

    existing = await session.scalar(
        select(Application).where(
            Application.space_id == context.space_id,
            Application.application_number == PHASE4_APPLICATION_NUMBER,
        )
    )
    if existing is not None:
        snapshot = await session.scalar(
            select(ApplicationSnapshot).where(
                ApplicationSnapshot.application_id == existing.id
            )
        )
        if snapshot is None:
            raise Phase4DemoError("existing demand has no immutable snapshot")
        return snapshot

    data_publication = await session.scalar(
        select(DataProductPublication).where(
            DataProductPublication.data_product_version_id == context.data_version_id,
            DataProductPublication.status == "active",
        )
    )
    model_publication = await session.scalar(
        select(ModelPublication).where(
            ModelPublication.model_version_id == context.model_version_id,
            ModelPublication.status == "active",
        )
    )
    data_product = await session.get(DataProduct, context.data_product_id)
    data_version = await session.get(DataProductVersion, context.data_version_id)
    model_version = await session.get(ModelVersion, context.model_version_id)
    requester = context.actors["data_requester"]
    provider = context.actors["data_provider"]
    operator = context.actors["space_operator"]
    if (
        data_publication is None
        or model_publication is None
        or data_product is None
        or data_version is None
        or model_version is None
    ):
        raise Phase4DemoError("both data and model must be published before demand submission")

    application = Application(
        id=_id("application"),
        space_id=context.space_id,
        application_number=PHASE4_APPLICATION_NUMBER,
        applicant_organization_id=requester.organization_id,
        applicant_user_id=requester.user_id,
        provider_organization_id=provider.organization_id,
        purpose="固定公开病理数据与白名单模型的受控验证推理（演示）",
        legal_or_ethics_basis="公开数据工程验证；不用于临床诊断或医疗器械性能评价",
        algorithm_name="PathMNIST ResNet-18 28px",
        algorithm_version="v1.0",
        algorithm_digest=model_version.model_digest,
        requested_duration_seconds=3600,
        requested_run_limit=1,
        status="draft",
        is_demo=True,
        created_by=requester.user_id,
    )
    session.add(application)
    await session.flush()
    session.add_all(
        [
            ApplicationItem(
                id=_id("application-item"),
                application_id=application.id,
                space_id=context.space_id,
                provider_organization_id=provider.organization_id,
                data_product_id=data_product.id,
                data_product_version_id=data_version.id,
                position_no=1,
                requested_product_snapshot_digest=data_version.snapshot_digest,
                requested_policy_digest=data_version.default_policy_digest,
                requested_scope={
                    "schema_version": "1.0",
                    "resource_codes": ["PATHMNIST-TEST-20"],
                    "sample_count": 20,
                },
            ),
            ApplicationRequestedAction(
                application_id=application.id,
                action_code="model_validation",
                parameters={"schema_version": "1.0", "inference_only": True},
            ),
            ApplicationRequestedOutputType(
                application_id=application.id,
                output_type="model_artifact",
                requires_manual_review=True,
            ),
            ApplicationAttachment(
                id=_id("application-attachment"),
                application_id=application.id,
                attachment_type="research_protocol",
                display_name="公开病理数据受控验证方案（演示）.pdf",
                storage_ref="registered://demo-documents/pathmnist-validation-protocol/v1",
                content_digest=canonical_document_digest(
                    {"schema_version": "1.0", "document": "pathmnist-validation-protocol"}
                ),
                size_bytes=4096,
                scan_status="pending",
                created_by=requester.user_id,
            ),
        ]
    )
    await session.flush()
    attachment = await session.get(ApplicationAttachment, _id("application-attachment"))
    if attachment is None:
        raise Phase4DemoError("demand attachment was not created")
    attachment.scan_status = "clean"
    await session.flush()
    await attach_model_to_demand(
        session,
        application_id=application.id,
        model_version_id=model_version.id,
    )
    snapshot = await submit_application(
        session, application, submitted_by=requester.user_id
    )
    application.status = "prechecking"
    application.row_version += 1
    await session.flush()

    routing_specs = (
        ("application_precheck", 10, operator.organization_id),
        ("data_provider_review", 20, provider.organization_id),
        (
            "model_provider_review",
            20,
            context.actors["model_provider"].organization_id,
        ),
    )
    for review_type, sequence_no, organization_id in routing_specs:
        routing = {
            "schema_version": "phase4-review-route/v1",
            "application_snapshot_id": str(snapshot.id),
            "target_digest": snapshot.snapshot_digest,
            "review_type": review_type,
            "assignee_organization_id": str(organization_id),
            "sequence_no": sequence_no,
        }
        session.add(
            ReviewTask(
                id=_id(f"review-task:{review_type}"),
                space_id=context.space_id,
                review_type=review_type,
                application_id=application.id,
                application_snapshot_id=snapshot.id,
                target_digest=snapshot.snapshot_digest,
                assignee_organization_id=organization_id,
                task_status="pending",
                sequence_no=sequence_no,
                is_required=True,
                routing_rule_digest=canonical_document_digest(routing),
                created_by=operator.user_id,
            )
        )
    await session.flush()
    command = command_for(requester, "compute-demand-submit", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="application.submitted",
        subject_type="application",
        subject_id=application.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase4-compute-demand-submitted/v1",
            "application_snapshot_id": str(snapshot.id),
            "application_snapshot_digest": snapshot.snapshot_digest,
            "data_product_version_ids": [str(data_version.id)],
            "model_version_id": str(model_version.id),
        },
        **command.append_kwargs(),
    )
    return snapshot


async def decide_compute_demand_review_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    review_type: str,
    raw_key: str,
    decision: str = "approved",
) -> ReviewDecision:
    actor_role = {
        "application_precheck": "space_operator",
        "data_provider_review": "data_provider",
        "model_provider_review": "model_provider",
    }.get(review_type)
    if actor_role is None:
        raise Phase4DemoError("unsupported Phase 4 review type")
    actor = context.actors[actor_role]
    application = await session.scalar(
        select(Application).where(
            Application.application_number == PHASE4_APPLICATION_NUMBER,
            Application.space_id == context.space_id,
        )
    )
    if application is None:
        raise Phase4DemoError("compute demand is missing")
    task = await session.scalar(
        select(ReviewTask).where(
            ReviewTask.application_id == application.id,
            ReviewTask.review_type == review_type,
        )
    )
    if task is None or task.assignee_organization_id != actor.organization_id:
        raise Phase4DemoError("review task does not belong to this role")
    existing = await session.scalar(
        select(ReviewDecision).where(ReviewDecision.review_task_id == task.id)
    )
    if existing is not None:
        return existing
    if review_type != "application_precheck":
        precheck = await session.scalar(
            select(ReviewTask).where(
                ReviewTask.application_id == application.id,
                ReviewTask.review_type == "application_precheck",
                ReviewTask.task_status == "decided",
            )
        )
        if precheck is None:
            raise Phase4DemoError("platform precheck must complete first")
        precheck_decision = await session.scalar(
            select(ReviewDecision).where(
                ReviewDecision.review_task_id == precheck.id,
                ReviewDecision.decision == "approved",
            )
        )
        if precheck_decision is None:
            raise Phase4DemoError("platform precheck did not approve this demand")
    claim_review_task(task, user_id=actor.user_id)
    await session.flush()
    row = await submit_review_decision(
        session,
        task,
        decision=decision,
        decided_by_user_id=actor.user_id,
        decided_for_organization_id=actor.organization_id,
        reason_code=None if decision == "approved" else "policy_conflict",
        comment="演示审核：范围与公开验证用途一致" if decision == "approved" else "演示拒绝",
        remediation=None if decision == "approved" else "clone_and_resubmit",
        evidence={
            "schema_version": "phase4-review-evidence/v1",
            "review_type": review_type,
            "snapshot_digest": task.target_digest,
            "metadata_only": True,
        },
    )
    command = command_for(actor, f"compute-demand-review:{review_type}", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="application.review.decided",
        subject_type="review_decision",
        subject_id=row.id,
        result="success" if decision == "approved" else "denied",
        evidence_snapshot={
            "schema_version": "phase4-compute-demand-review/v1",
            "application_id": str(application.id),
            "review_type": review_type,
            "decision": decision,
            "decision_digest": row.decision_digest,
        },
        **command.append_kwargs(),
    )
    application_changed = False
    if decision == "rejected":
        application.status = "rejected"
        application.decided_at = datetime.now(timezone.utc)
        application.decision_summary = f"{review_type} rejected"
        application_changed = True
    elif review_type == "application_precheck":
        application.status = "provider_review"
        application_changed = True
    else:
        tasks = list(
            (
                await session.scalars(
                    select(ReviewTask).where(
                        ReviewTask.application_id == application.id,
                        ReviewTask.is_required.is_(True),
                    )
                )
            ).all()
        )
        if all(item.task_status == "decided" for item in tasks):
            decisions = list(
                (
                    await session.scalars(
                        select(ReviewDecision).where(
                            ReviewDecision.review_task_id.in_([item.id for item in tasks])
                        )
                    )
                ).all()
            )
            if len(decisions) == len(tasks) and all(
                item.decision == "approved" for item in decisions
            ):
                application.status = "approved"
                application.decided_at = datetime.now(timezone.utc)
                application.decision_summary = "所有必需审核均已通过，可进入合同阶段"
                application_changed = True
    if application_changed:
        application.row_version += 1
    await session.flush()
    return row


async def build_phase4_contract_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    raw_key: str,
) -> ContractRevision:
    existing = await session.scalar(
        select(Contract).where(
            Contract.space_id == context.space_id,
            Contract.contract_number == PHASE4_CONTRACT_NUMBER,
        )
    )
    if existing is not None:
        revision = await session.scalar(
            select(ContractRevision).where(
                ContractRevision.contract_id == existing.id,
                ContractRevision.revision_no == 1,
            )
        )
        if revision is None:
            raise Phase4DemoError("existing contract has no revision")
        return revision

    application = await session.scalar(
        select(Application).where(
            Application.space_id == context.space_id,
            Application.application_number == PHASE4_APPLICATION_NUMBER,
        )
    )
    snapshot = (
        None
        if application is None
        else await session.scalar(
            select(ApplicationSnapshot).where(
                ApplicationSnapshot.application_id == application.id
            )
        )
    )
    data_product = await session.get(DataProduct, context.data_product_id)
    data_version = await session.get(DataProductVersion, context.data_version_id)
    model_product = await session.get(ModelProduct, context.model_product_id)
    model_version = await session.get(ModelVersion, context.model_version_id)
    connector = await session.get(Connector, _id("connector:data-provider"))
    if (
        application is None
        or snapshot is None
        or application.status != "approved"
        or data_product is None
        or data_version is None
        or model_product is None
        or model_version is None
        or connector is None
    ):
        raise Phase4DemoError("approved demand and fixed catalog objects are required")

    eligibility = await build_contract_eligibility_evidence(
        session, application=application, snapshot=snapshot
    )
    operator = context.actors["space_operator"]
    contract = Contract(
        id=_id("contract"),
        space_id=context.space_id,
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        application_snapshot_digest=snapshot.snapshot_digest,
        eligibility_evidence=eligibility,
        eligibility_digest=canonical_document_digest(eligibility),
        contract_number=PHASE4_CONTRACT_NUMBER,
        created_by=operator.user_id,
        is_demo=True,
    )
    session.add(contract)
    await session.flush()
    terms = {
        "schema_version": "phase4-contract-terms/v1",
        "purpose": "public_pathology_model_validation",
        "hard_isolation": False,
        "clinical_use": False,
        "raw_data_export": False,
        "model_weight_export": False,
        "safe_result_package_only": True,
    }
    now = datetime.now(timezone.utc)
    revision = ContractRevision(
        id=_id("contract-revision"),
        contract_id=contract.id,
        revision_no=1,
        name="PathMNIST多方可信计算协议（演示）",
        summary="固定数据版本、固定模型版本、单次CPU推理和聚合结果审核。",
        terms_schema_version="phase4-contract-terms/v1",
        terms_document=terms,
        terms_digest=canonical_document_digest(terms),
        status="draft",
        signing_mode="multi_party",
        effective_from=now - timedelta(minutes=1),
        effective_until=now + timedelta(days=1),
        created_by=operator.user_id,
    )
    session.add(revision)
    await session.flush()

    party_specs = (
        ("data_provider", "data_provider", 1),
        ("model_provider", "model_provider", 2),
        ("data_requester", "data_requester", 3),
        ("space_operator", "operator_witness", 4),
    )
    parties: dict[str, ContractParty] = {}
    for actor_role, party_role, signing_order in party_specs:
        actor = context.actors[actor_role]
        party = ContractParty(
            id=_id(f"contract-party:{actor_role}"),
            contract_revision_id=revision.id,
            organization_id=actor.organization_id,
            party_role=party_role,
            signing_order=signing_order,
            is_required=True,
            party_name_snapshot=actor.organization_name,
            identity_snapshot={
                "schema_version": "phase4-contract-party/v1",
                "organization_id": str(actor.organization_id),
                "space_role": actor.role,
                "is_demo": True,
            },
            created_by=operator.user_id,
        )
        session.add(party)
        parties[party_role] = party
    await session.flush()

    data_scope = {
        "schema_version": "1.0",
        "resource_codes": ["PATHMNIST-TEST-20"],
        "sample_count": 20,
    }
    contract_object = ContractObject(
        id=_id("contract-data-object"),
        contract_revision_id=revision.id,
        data_product_version_id=data_version.id,
        product_snapshot_digest=data_version.snapshot_digest,
        product_name_snapshot=data_product.name,
        authorized_scope=data_scope,
        authorized_scope_digest=canonical_document_digest(data_scope),
        position_no=1,
        created_by=operator.user_id,
    )
    session.add(contract_object)
    model_scope = {
        "schema_version": "phase4-authorized-model-scope/v1",
        "entrypoint_id": model_version.entrypoint_id,
        "model_digest": model_version.model_digest,
        "inference_only": True,
        "weight_export": False,
    }
    session.add(
        ContractModelObject(
            id=_id("contract-model-object"),
            contract_revision_id=revision.id,
            model_version_id=model_version.id,
            model_snapshot_digest=model_version.snapshot_digest,
            model_name_snapshot=model_product.name,
            authorized_scope=model_scope,
            authorized_scope_digest=canonical_document_digest(model_scope),
            created_by=operator.user_id,
        )
    )
    await session.flush()

    requester_party = parties["data_requester"]
    policy_specs: tuple[tuple[str, str, str, str, int], ...] = (
        ("permit-compute", "permission", "permit", "execute_controlled_compute", 100),
        ("permit-safe-result", "permission", "permit", "export_artifact", 90),
        ("deny-raw-export", "prohibition", "deny", "export_raw_data", 1000),
        ("deny-reidentify", "prohibition", "deny", "reidentify_subject", 1000),
        ("deny-redistribute", "prohibition", "deny", "redistribute_data", 1000),
        ("require-audit", "obligation", "require", "write_audit_log", 900),
    )
    policies: dict[str, Policy] = {}
    for code, policy_type, effect, action_code, priority in policy_specs:
        policy = Policy(
            id=_id(f"policy:{code}"),
            contract_revision_id=revision.id,
            policy_code=code,
            policy_type=policy_type,
            effect=effect,
            subject_contract_party_id=requester_party.id,
            contract_object_id=contract_object.id,
            action_code=action_code,
            priority=priority,
            created_by=operator.user_id,
        )
        session.add(policy)
        policies[code] = policy
    await session.flush()

    constraint_specs = {
        "permit-compute": (
            ("purpose_code", "in", ["model_validation"], None),
            ("algorithm_digest", "eq", model_version.model_digest, None),
            ("environment_mode", "eq", "controlled_compute", None),
            ("run_count", "lte", 1, "count"),
            (
                "effective_until",
                "before",
                revision.effective_until.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                None,
            ),
        ),
        "permit-safe-result": (
            ("output_type", "in", ["model_artifact"], None),
            ("output_review_required", "eq", True, None),
        ),
        "require-audit": (("audit_level", "gte", "full", None),),
    }
    for code, specs in constraint_specs.items():
        for position, (name, operator_name, value, unit) in enumerate(specs, 1):
            session.add(
                PolicyConstraint(
                    policy_id=policies[code].id,
                    constraint_name=name,
                    operator=operator_name,
                    value=value,
                    unit=unit,
                    position_no=position,
                )
            )

    binding_roles = {
        "permit-compute": (("compute_executor", "controlled_compute_execution"),),
        "permit-safe-result": (("egress_controller", "egress_policy_enforcement"),),
        "deny-raw-export": (("egress_controller", "egress_policy_enforcement"),),
        "deny-reidentify": (
            ("compute_executor", "controlled_compute_execution"),
            ("egress_controller", "egress_policy_enforcement"),
        ),
        "deny-redistribute": (("egress_controller", "egress_policy_enforcement"),),
        "require-audit": (("audit_evidence_emitter", "audit_evidence_emit"),),
    }
    for code, specs in binding_roles.items():
        for execution_role, capability_code in specs:
            session.add(
                PolicyExecutionBinding(
                    id=_id(f"binding:{code}:{execution_role}"),
                    policy_id=policies[code].id,
                    connector_id=connector.id,
                    execution_role=execution_role,
                    required_capability_code=capability_code,
                    required_capability_version="1.0",
                    is_required=True,
                    deployment_status="pending",
                )
            )
    await session.flush()
    await propose_contract_revision(session, revision)
    command = command_for(operator, "contract-propose", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="contract.revision.proposed",
        subject_type="contract_revision",
        subject_id=revision.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase4-contract-proposed/v1",
            "contract_number": contract.contract_number,
            "revision_content_digest": revision.content_digest,
            "eligibility_digest": contract.eligibility_digest,
            "data_product_version_id": str(data_version.id),
            "model_version_id": str(model_version.id),
        },
        **command.append_kwargs(),
    )
    return revision


async def sign_phase4_contract_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    actor_role: str,
    raw_key: str,
) -> ContractRevision:
    if actor_role not in context.actors:
        raise Phase4DemoError("unknown contract signer role")
    actor = context.actors[actor_role]
    revision = await session.get(ContractRevision, _id("contract-revision"))
    party_role = {
        "data_provider": "data_provider",
        "model_provider": "model_provider",
        "data_requester": "data_requester",
        "space_operator": "operator_witness",
    }[actor_role]
    party = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == _id("contract-revision"),
            ContractParty.party_role == party_role,
        )
    )
    if revision is None or party is None or party.organization_id != actor.organization_id:
        raise Phase4DemoError("contract party is missing")
    from app.modules.contracts.models import ContractSignature

    existing = await session.scalar(
        select(ContractSignature).where(
            ContractSignature.contract_revision_id == revision.id,
            ContractSignature.contract_party_id == party.id,
        )
    )
    if existing is not None:
        return revision
    await sign_contract_revision(
        session,
        revision,
        contract_party_id=party.id,
        signer_organization_id=actor.organization_id,
        signer_user_id=actor.user_id,
        signature_value_ref=f"demo-signature://phase4/{revision.id}/{party.id}",
    )
    command = command_for(actor, f"contract-sign:{actor_role}", raw_key)
    await append_audit_event_with_outbox(
        session,
        space_id=context.space_id,
        event_type="contract.revision.signed",
        subject_type="contract_revision",
        subject_id=revision.id,
        result="success",
        evidence_snapshot={
            "schema_version": "phase4-contract-signature/v1",
            "contract_party_id": str(party.id),
            "party_role": party.party_role,
            "signed_content_digest": revision.content_digest,
            "demo_signature": True,
        },
        **command.append_kwargs(),
    )
    return revision


async def activate_phase4_contract_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    raw_key: str,
) -> ContractRevision:
    revision = await session.get(ContractRevision, _id("contract-revision"))
    if revision is None:
        raise Phase4DemoError("contract revision is missing")
    if revision.status == "active":
        return revision
    if revision.status != "signed":
        raise Phase4DemoError("all four required parties must sign before activation")
    bindings = list(
        (
            await session.scalars(
                select(PolicyExecutionBinding)
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(Policy.contract_revision_id == revision.id)
            )
        ).all()
    )
    acknowledged_at = datetime.now(timezone.utc)
    for binding in bindings:
        if binding.deployment_status == "accepted":
            continue
        binding.deployment_status = "accepted"
        binding.acknowledged_at = acknowledged_at
        binding.receipt_digest = canonical_document_digest(
            {
                "schema_version": "phase4-binding-receipt/v1",
                "binding_id": str(binding.id),
                "policy_id": str(binding.policy_id),
                "connector_id": str(binding.connector_id),
                "capability": binding.required_capability_code,
                "capability_version": binding.required_capability_version,
            }
        )
        binding.row_version += 1
    await session.flush()
    actor = context.actors["space_operator"]
    await activate_contract_revision(
        session,
        revision,
        audit_command=command_for(actor, "contract-activate", raw_key),
    )
    return revision


async def confirm_phase4_readiness_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    readiness_type: str,
    workspace: Path,
    raw_key: str,
) -> ContractReadinessConfirmation:
    actor_role = {
        "data_ready": "data_provider",
        "model_ready": "model_provider",
        "platform_ready": "space_operator",
    }.get(readiness_type)
    if actor_role is None:
        raise Phase4DemoError("unknown readiness type")
    actor = context.actors[actor_role]
    revision = await session.get(ContractRevision, _id("contract-revision"))
    if revision is None:
        raise Phase4DemoError("active contract revision is missing")
    existing = await session.scalar(
        select(ContractReadinessConfirmation).where(
            ContractReadinessConfirmation.contract_revision_id == revision.id,
            ContractReadinessConfirmation.readiness_type == readiness_type,
        )
    )
    if existing is not None:
        return existing
    if readiness_type == "data_ready":
        target = {
            "schema_version": "phase4-data-readiness-target/v1",
            "data_product_version_id": str(context.data_version_id),
            "data_snapshot_digest": (
                await session.get(DataProductVersion, context.data_version_id)
            ).snapshot_digest,
            "connector_id": str(_id("connector:data-provider")),
        }
    elif readiness_type == "model_ready":
        version = await session.get(ModelVersion, context.model_version_id)
        if version is None:
            raise Phase4DemoError("model version is missing")
        target = {
            "schema_version": "phase4-model-readiness-target/v1",
            "model_version_id": str(version.id),
            "model_snapshot_digest": version.snapshot_digest,
            "registry_digest": version.registry_digest,
        }
    else:
        target = {
            "schema_version": "phase4-platform-readiness-target/v1",
            "contract_revision_id": str(revision.id),
            "revision_content_digest": revision.content_digest,
            "required_readiness": ["data_ready", "model_ready", "platform_ready"],
        }
    return await confirm_contract_readiness(
        session,
        revision,
        readiness_type=readiness_type,
        organization_id=actor.organization_id,
        user_id=actor.user_id,
        target_snapshot=target,
        evidence_snapshot={
            "schema_version": "phase4-readiness-evidence/v1",
            "verified": True,
            "hard_isolation": False,
            "demo_only": True,
        },
        command=command_for(actor, f"readiness:{readiness_type}", raw_key),
        registry=(
            load_pathmnist_model_registry(workspace)
            if readiness_type == "model_ready"
            else None
        ),
    )


async def phase4_is_ready(session: AsyncSession) -> bool:
    revision = await session.get(ContractRevision, _id("contract-revision"))
    if revision is None or revision.status != "active":
        return False
    try:
        await require_all_readiness(session, revision.id)
    except ValueError:
        return False
    return True


async def create_phase4_compute_run_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    raw_key: str,
) -> tuple[ComputeJob, ComputeRun, bool]:
    """Create one audited, quota-reserved run; workers perform execution later."""

    revision = await session.get(ContractRevision, _id("contract-revision"))
    if revision is None or revision.status != "active":
        raise Phase4DemoError("an active contract is required")
    await require_all_readiness(session, revision.id)
    contract_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == revision.id
        )
    )
    requester_party = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.party_role == "data_requester",
        )
    )
    model_object = await session.scalar(
        select(ContractModelObject).where(
            ContractModelObject.contract_revision_id == revision.id
        )
    )
    model_version = (
        None
        if model_object is None
        else await session.get(ModelVersion, model_object.model_version_id)
    )
    requester = context.actors["data_requester"]
    if contract_object is None or requester_party is None or model_version is None:
        raise Phase4DemoError("contract execution graph is incomplete")
    algorithm_spec = {
        "schema_version": "algorithm-spec/v1",
        "algorithm_name": "PathMNIST official ResNet-18 28px",
        "algorithm_version": "1",
        "algorithm_digest": model_version.model_digest,
        "registration_digest": model_version.registry_digest,
        "entrypoint_id": model_version.entrypoint_id,
        "execution_profile": "local_builtin_cpu_inference",
        "declared_output_types": ["model_artifact"],
        "model_version_id": str(model_version.id),
        "model_snapshot_digest": model_version.snapshot_digest,
        "demo_invocation_digest": digest_idempotency_key(
            f"phase4:compute-invocation:{raw_key}"
        ),
    }
    job = await create_compute_job(
        session,
        revision_id=revision.id,
        party_id=requester_party.id,
        contract_object_id=contract_object.id,
        requester_organization_id=requester.organization_id,
        requester_user_id=requester.user_id,
        purpose_code="model_validation",
        requested_output_types=["model_artifact"],
        algorithm_spec_snapshot=algorithm_spec,
        audit_command=command_for(requester, "compute-job-create", f"{raw_key}:job"),
    )
    run_command = command_for(requester, "compute-run-reserve", f"{raw_key}:run")
    reservation_event = await session.scalar(
        select(AuditEvent).where(
            AuditEvent.space_id == context.space_id,
            AuditEvent.event_type == "compute.run.reserved",
            AuditEvent.command_id == run_command.command_id,
        )
    )
    if reservation_event is not None:
        run = await session.get(ComputeRun, reservation_event.subject_id)
        if run is None or run.compute_job_id != job.id:
            raise Phase4DemoError("idempotent run reservation is unavailable")
        return job, run, True
    await validate_compute_job(session, job)
    run = await prepare_compute_run(session, job, created_by=requester.user_id)
    await reserve_compute_run(session, run, audit_command=run_command)
    return job, run, False


async def latest_phase4_artifact(session: AsyncSession) -> Artifact | None:
    return await session.scalar(
        select(Artifact)
        .join(ComputeRun, ComputeRun.id == Artifact.compute_run_id)
        .join(ComputeJob, ComputeJob.id == ComputeRun.compute_job_id)
        .where(ComputeJob.contract_revision_id == _id("contract-revision"))
        .order_by(Artifact.created_at.desc())
    )


async def ensure_phase4_artifact_review_plan(
    session: AsyncSession,
    context: Phase4DemoContext,
) -> tuple[ArtifactReviewTask, ...]:
    artifact = await latest_phase4_artifact(session)
    if artifact is None:
        raise Phase4DemoError("completed run has not produced an Artifact")
    existing = tuple(
        (
            await session.scalars(
                select(ArtifactReviewTask)
                .where(ArtifactReviewTask.artifact_id == artifact.id)
                .order_by(ArtifactReviewTask.review_type)
            )
        ).all()
    )
    if existing:
        return existing
    return await create_artifact_review_plan(
        session,
        artifact,
        created_by=context.actors["space_operator"].user_id,
    )


async def decide_phase4_artifact_review_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    review_type: str,
    raw_key: str,
    decision: str = "approved",
) -> ArtifactReviewDecision:
    actor_role = {
        "data_provider_egress_review": "data_provider",
        "platform_compliance_review": "space_operator",
        "model_provider_quality_review": "model_provider",
    }.get(review_type)
    if actor_role is None:
        raise Phase4DemoError("unsupported Artifact review type")
    actor = context.actors[actor_role]
    tasks = await ensure_phase4_artifact_review_plan(session, context)
    task = next((item for item in tasks if item.review_type == review_type), None)
    if task is None or task.responsible_organization_id != actor.organization_id:
        raise Phase4DemoError("Artifact review task does not belong to this role")
    existing = await session.scalar(
        select(ArtifactReviewDecision).where(
            ArtifactReviewDecision.artifact_review_task_id == task.id
        )
    )
    if existing is not None:
        return existing
    await claim_artifact_review_task(session, task, user_id=actor.user_id)
    return await decide_artifact_review_task(
        session,
        task,
        decision=decision,
        reason_code="scope_verified" if decision == "approved" else "policy_conflict",
        evidence_snapshot={
            "schema_version": "phase4-artifact-review-evidence/v1",
            "aggregate_only": True,
            "raw_data_present": False,
            "patient_level_output_present": False,
            "model_weights_present": False,
        },
        command=command_for(actor, f"artifact-review:{review_type}", raw_key),
    )


def build_phase4_safe_files(
    *,
    workspace: Path,
    run_id: UUID,
) -> dict[str, bytes]:
    """Read only fixed allowlisted aggregate outputs from the trusted run workspace."""

    output_root = (
        workspace / ".runtime" / "phase4-pathmnist-workspaces" / str(run_id) / "output"
    ).resolve()
    expected_parent = (workspace / ".runtime" / "phase4-pathmnist-workspaces").resolve()
    if output_root.parent.parent != expected_parent or output_root.is_symlink():
        raise Phase4DemoError("execution output workspace is outside the trusted root")
    metrics_path = output_root / "aggregate_metrics.json"
    summary_path = output_root / "execution_summary.json"
    manifest_path = output_root / "output_manifest.json"
    if not metrics_path.is_file() or not summary_path.is_file() or not manifest_path.is_file():
        raise Phase4DemoError("allowlisted aggregate execution outputs are incomplete")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_entries = {
        item.get("name"): item
        for item in manifest.get("outputs", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for path in (metrics_path, summary_path):
        payload = path.read_bytes()
        expected = manifest_entries.get(path.name, {}).get("digest")
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if expected != actual:
            raise Phase4DemoError(f"allowlisted output digest mismatch: {path.name}")
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    matrix = metrics.get("confusion_matrix")
    if (
        not isinstance(matrix, list)
        or len(matrix) != 9
        or any(not isinstance(row, list) or len(row) != 9 for row in matrix)
    ):
        raise Phase4DemoError("aggregate confusion matrix is invalid")
    csv_lines = [",".join(["actual/predicted", *[str(index) for index in range(9)]])]
    csv_lines.extend(
        ",".join([str(index), *[str(int(value)) for value in row]])
        for index, row in enumerate(matrix)
    )
    sanitized_summary = {
        "schema_version": "phase4-safe-execution-summary/v1",
        "entrypoint_id": summary.get("entrypoint_id"),
        "sample_count": summary.get("sample_count"),
        "split": summary.get("split"),
        "model_digest": summary.get("model_digest"),
        "dataset_digest": summary.get("dataset_digest"),
        "prediction_digest": summary.get("prediction_digest"),
        "network_access": False,
        "inference_only": True,
        "non_clinical": True,
        "hard_isolation": False,
        "resource_usage": summary.get("resource_usage", {}),
    }
    return {
        "aggregate_metrics.json": json.dumps(
            metrics, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        "confusion_matrix.csv": ("\n".join(csv_lines) + "\n").encode("utf-8"),
        "execution_summary.json": json.dumps(
            sanitized_summary,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
    }


async def create_phase4_result_package_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    workspace: Path,
    object_store: Any,
    raw_key: str,
) -> ApprovedResultPackage:
    artifact = await latest_phase4_artifact(session)
    if artifact is None:
        raise Phase4DemoError("no quarantined Artifact is available")
    existing = await session.scalar(
        select(ApprovedResultPackage).where(
            ApprovedResultPackage.artifact_id == artifact.id
        )
    )
    if existing is not None:
        return existing
    requester = context.actors["data_requester"]
    run = await session.get(ComputeRun, artifact.compute_run_id)
    if run is None:
        raise Phase4DemoError("Artifact run provenance is missing")
    return await create_approved_result_package(
        session,
        artifact,
        requester_organization_id=requester.organization_id,
        created_by=context.actors["space_operator"].user_id,
        safe_files=build_phase4_safe_files(workspace=workspace, run_id=run.id),
        object_store=object_store,
        command=command_for(
            context.actors["space_operator"], "result-package-create", raw_key
        ),
    )


async def create_phase4_download_grant_command(
    session: AsyncSession,
    context: Phase4DemoContext,
    *,
    package_id: UUID,
    raw_key: str,
) -> tuple[UUID, str, datetime]:
    package = await session.get(ApprovedResultPackage, package_id)
    requester = context.actors["data_requester"]
    if package is None:
        raise Phase4DemoError("approved result package is missing")
    secret = await create_download_grant(
        session,
        package,
        requester_organization_id=requester.organization_id,
        requester_user_id=requester.user_id,
        command=command_for(requester, "result-download-grant", raw_key),
        lifetime_seconds=300,
        max_downloads=1,
    )
    return secret.grant.id, secret.token, secret.grant.expires_at

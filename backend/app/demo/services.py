from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Text, cast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.applications.models import (
    Application,
    ApplicationAttachment,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
)
from app.modules.applications.services import submit_application
from app.modules.audit import AuditCommandContext, digest_idempotency_key
from app.modules.catalog.models import (
    DataProduct,
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
from app.modules.connectors.models import Connector
from app.modules.identity.models import Organization
from app.modules.reviews.models import ReviewTask
from app.modules.reviews.services import claim_review_task, submit_review_decision
from app.modules.spaces.models import Space

DEMO_CONTRACT_NUMBER = "CTR-PATHMNIST-DEMO-V1"
PATHMNIST_MODEL_DIGEST = (
    "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
)


class DemoBaselineError(ValueError):
    pass


@dataclass(frozen=True)
class DemoBaseline:
    contract_id: UUID
    revision_id: UUID
    contract_object_id: UUID
    consumer_party_id: UUID
    requester_organization_id: UUID
    requester_user_id: UUID
    space_id: UUID
    run_limit: int
    created: bool


def _audit_command(label: str) -> AuditCommandContext:
    command_id = uuid5(NAMESPACE_URL, f"medtrust:demo-baseline:{label}")
    return AuditCommandContext(
        command_id=command_id,
        idempotency_key=digest_idempotency_key(f"demo-baseline:{label}"),
        correlation_id=command_id,
        actor_type="system",
        actor_service_code="medtrust.contract",
    )


async def _baseline_from_revision(
    session: AsyncSession,
    contract: Contract,
    revision: ContractRevision,
    *,
    created: bool,
) -> DemoBaseline:
    consumer = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.party_role == "consumer",
        )
    )
    contract_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == revision.id
        )
    )
    run_limit = await session.scalar(
        select(PolicyConstraint.value)
        .join(Policy, Policy.id == PolicyConstraint.policy_id)
        .where(
            Policy.contract_revision_id == revision.id,
            PolicyConstraint.constraint_name == "run_count",
        )
    )
    if consumer is None or contract_object is None or not isinstance(run_limit, int):
        raise DemoBaselineError("PathMNIST demo Contract graph is incomplete")
    return DemoBaseline(
        contract_id=contract.id,
        revision_id=revision.id,
        contract_object_id=contract_object.id,
        consumer_party_id=consumer.id,
        requester_organization_id=consumer.organization_id,
        requester_user_id=revision.created_by,
        space_id=contract.space_id,
        run_limit=run_limit,
        created=created,
    )


async def _refresh_demo_connector_heartbeats(
    session: AsyncSession, revision: ContractRevision
) -> None:
    """Refresh only already-verified demo connectors used by this revision.

    A heartbeat is transient runtime state, so a reusable demo baseline cannot
    safely preserve the timestamp captured in the database snapshot forever.
    This does not promote an offline or unverified connector and does not alter
    any capability status.
    """

    connectors = list(
        (
            await session.scalars(
                select(Connector)
                .join(
                    PolicyExecutionBinding,
                    PolicyExecutionBinding.connector_id == Connector.id,
                )
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(
                    Policy.contract_revision_id == revision.id,
                    PolicyExecutionBinding.is_required.is_(True),
                    PolicyExecutionBinding.deployment_status == "accepted",
                    Connector.verification_status == "verified",
                    Connector.runtime_status == "online",
                )
                .distinct()
            )
        ).all()
    )
    heartbeat_at = datetime.now(timezone.utc)
    for connector in connectors:
        connector.last_heartbeat_at = heartbeat_at
    if connectors:
        await session.flush()


async def ensure_pathmnist_demo_baseline(
    session: AsyncSession, *, run_limit: int = 20
) -> DemoBaseline:
    """Create one reusable demo Contract without mutating the authoritative run graph."""

    if run_limit < 1 or run_limit > 100:
        raise DemoBaselineError("demo run_limit must be between 1 and 100")
    existing = await session.scalar(
        select(Contract).where(Contract.contract_number == DEMO_CONTRACT_NUMBER)
    )
    if existing is not None:
        revision = await session.scalar(
            select(ContractRevision).where(
                ContractRevision.contract_id == existing.id,
                ContractRevision.status == "active",
            )
        )
        if revision is None:
            raise DemoBaselineError("existing demo Contract is not active")
        await _refresh_demo_connector_heartbeats(session, revision)
        return await _baseline_from_revision(
            session, existing, revision, created=False
        )

    source_revision = await session.scalar(
        select(ContractRevision)
        .join(Contract, Contract.id == ContractRevision.contract_id)
        .join(Policy, Policy.contract_revision_id == ContractRevision.id)
        .join(PolicyConstraint, PolicyConstraint.policy_id == Policy.id)
        .where(
            ContractRevision.status == "active",
            Contract.is_demo.is_(True),
            PolicyConstraint.constraint_name == "algorithm_digest",
            PolicyConstraint.operator == "eq",
            cast(PolicyConstraint.value, Text) == f'"{PATHMNIST_MODEL_DIGEST}"',
            Contract.contract_number != DEMO_CONTRACT_NUMBER,
        )
        .order_by(Contract.created_at.desc())
    )
    if source_revision is None:
        raise DemoBaselineError(
            "no verified PathMNIST source Contract exists in this demo database"
        )
    source_contract = await session.get(Contract, source_revision.contract_id)
    if source_contract is None:
        raise DemoBaselineError("source Contract is unavailable")

    source_application = await session.get(Application, source_contract.application_id)
    space = await session.get(Space, source_contract.space_id)
    if source_application is None or space is None:
        raise DemoBaselineError("source Application or Space is unavailable")
    source_actions = list(
        (
            await session.scalars(
                select(ApplicationRequestedAction).where(
                    ApplicationRequestedAction.application_id == source_application.id
                )
            )
        ).all()
    )
    source_outputs = list(
        (
            await session.scalars(
                select(ApplicationRequestedOutputType).where(
                    ApplicationRequestedOutputType.application_id
                    == source_application.id
                )
            )
        ).all()
    )
    source_attachments = list(
        (
            await session.scalars(
                select(ApplicationAttachment).where(
                    ApplicationAttachment.application_id == source_application.id
                )
            )
        ).all()
    )
    source_contract_object = await session.scalar(
        select(ContractObject).where(
            ContractObject.contract_revision_id == source_revision.id
        )
    )
    if source_contract_object is None:
        raise DemoBaselineError("source ContractObject is unavailable")
    source_version = await session.get(
        DataProductVersion, source_contract_object.data_product_version_id
    )
    if source_version is None:
        raise DemoBaselineError("source DataProductVersion is unavailable")
    source_product = await session.get(DataProduct, source_version.data_product_id)
    source_link = await session.scalar(
        select(DataProductSource)
        .join(DataResource, DataResource.id == DataProductSource.data_resource_id)
        .where(DataResource.data_product_version_id == source_version.id)
    )
    if source_product is None or source_link is None:
        raise DemoBaselineError("source DataProduct graph is unavailable")
    source_connector = await session.get(Connector, source_link.connector_id)
    if source_connector is None:
        raise DemoBaselineError("source Connector is unavailable")

    product = DataProduct(
        space_id=space.id,
        provider_organization_id=source_product.provider_organization_id,
        product_code="PATHMNIST-DEMO-V1",
        name="PathMNIST 结直肠组织图像分类数据产品（演示）",
        description="公开 PathMNIST 数据的固定 20 样本受控推理演示产品。",
        product_type="controlled_compute",
        domain="digital_pathology",
        lifecycle_status="draft",
        is_demo=True,
        created_by=source_revision.created_by,
    )
    session.add(product)
    await session.flush()
    default_policy = {
        "schema_version": "1.0",
        "service_modes": ["controlled_compute", "deidentified_data_delivery"],
        "allowed_actions": ["model_validation"],
        "allowed_outputs": ["model_artifact"],
        "environment_mode": "controlled_compute",
        "raw_export": False,
        "external_release": False,
    }
    version_snapshot = {
        "schema_version": "1.0",
        "product_code": product.product_code,
        "version_label": "v1.0",
        "dataset_manifest_digest": (
            "sha256:5ca3141fa3efbb1ae00e050d266fccff710aa5018cdddd77094f7ccb37c35009"
        ),
        "sample_scope": "official test split; fixed 20-sample smoke plan",
    }
    version = DataProductVersion(
        space_id=space.id,
        data_product_id=product.id,
        version_no=1,
        version_label="v1.0",
        status="draft",
        content_summary="PathMNIST 28×28 RGB，9 类；平台演示固定使用 test split 20 个索引。",
        scope_metadata={"schema_version": "1.0", "sample_count": 20},
        linkage_metadata={"schema_version": "1.0", "direct_identifiers": False},
        quality_report={"schema_version": "1.0", "manifest_validated": True},
        classification_level="public_demo",
        default_use_mode="controlled_compute",
        default_policy_template=default_policy,
        default_policy_digest=canonical_document_digest(default_policy),
        provenance_summary={
            "schema_version": "1.0",
            "source": "MedMNIST PathMNIST",
            "license": "CC BY 4.0",
            "non_clinical": True,
        },
        snapshot_digest=canonical_document_digest(version_snapshot),
        created_by=source_revision.created_by,
    )
    session.add(version)
    await session.flush()
    resource = DataResource(
        space_id=space.id,
        data_product_version_id=version.id,
        resource_code="PATHMNIST-TEST-20",
        name="PathMNIST 固定测试子集（演示）",
        resource_type="image_collection",
        modality="digital_pathology_patch",
        format="npz",
        schema_metadata={"schema_version": "1.0", "shape": [20, 28, 28, 3]},
        scope_metadata={"schema_version": "1.0", "split": "test"},
        quality_report={"schema_version": "1.0", "labels_available": True},
        classification_level="public_demo",
        resource_digest=(
            "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"
        ),
        position_no=1,
        created_by=source_revision.created_by,
    )
    session.add(resource)
    await session.flush()
    await add_product_source(
        session,
        resource,
        source_connector,
        local_resource_alias="registered://datasets/pathmnist/v1/test-20",
        source_digest=resource.resource_digest,
        source_role="primary",
        source_snapshot_at=datetime.now(timezone.utc),
    )
    await submit_version_for_review(session, version)
    await approve_version(session, version, approved_by=source_revision.created_by)
    await publish_version(
        session,
        product,
        version,
        published_by=source_revision.created_by,
        visibility="space",
    )

    demo_application = Application(
        space_id=source_application.space_id,
        application_number="APP-PATHMNIST-DEMO-V1",
        applicant_organization_id=source_application.applicant_organization_id,
        applicant_user_id=source_application.applicant_user_id,
        provider_organization_id=source_application.provider_organization_id,
        purpose="PathMNIST 固定 ResNet-18 受控推理演示",
        legal_or_ethics_basis="公开数据工程演示，不用于临床",
        algorithm_name="PathMNIST official ResNet-18 28px",
        algorithm_version="1",
        algorithm_digest=PATHMNIST_MODEL_DIGEST,
        requested_duration_seconds=source_application.requested_duration_seconds,
        requested_run_limit=run_limit,
        status="draft",
        is_demo=True,
        created_by=source_revision.created_by,
    )
    session.add(demo_application)
    await session.flush()
    session.add(
        ApplicationItem(
            application_id=demo_application.id,
            space_id=space.id,
            provider_organization_id=product.provider_organization_id,
            data_product_id=product.id,
            data_product_version_id=version.id,
            position_no=1,
            requested_product_snapshot_digest=version.snapshot_digest,
            requested_policy_digest=version.default_policy_digest,
            requested_scope={"schema_version": "1.0", "resources": ["pathmnist_test_20"]},
        )
    )
    for source in source_actions:
        session.add(
            ApplicationRequestedAction(
                application_id=demo_application.id,
                action_code=source.action_code,
                parameters=dict(source.parameters),
            )
        )
    for source in source_outputs:
        session.add(
            ApplicationRequestedOutputType(
                application_id=demo_application.id,
                output_type=source.output_type,
                requires_manual_review=source.requires_manual_review,
            )
        )
    cloned_attachments: list[tuple[ApplicationAttachment, str]] = []
    for source in source_attachments:
        target_attachment = ApplicationAttachment(
                application_id=demo_application.id,
                attachment_type=source.attachment_type,
                display_name=source.display_name,
                storage_ref=source.storage_ref,
                content_digest=source.content_digest,
                size_bytes=source.size_bytes,
                scan_status="pending",
                created_by=source_revision.created_by,
            )
        session.add(target_attachment)
        cloned_attachments.append((target_attachment, source.scan_status))
    await session.flush()
    for attachment, source_status in cloned_attachments:
        attachment.scan_status = source_status
    await session.flush()
    demo_snapshot = await submit_application(
        session, demo_application, submitted_by=source_revision.created_by
    )
    operator = await session.get(Organization, space.operator_organization_id)
    provider = await session.get(
        Organization, demo_application.provider_organization_id
    )
    if operator is None or provider is None:
        raise DemoBaselineError("demo review organizations are unavailable")
    demo_application.status = "prechecking"
    await session.flush()
    for review_type, sequence_no, assignee_organization_id in (
        ("application_precheck", 10, operator.id),
        ("provider_review", 20, provider.id),
    ):
        routing = {
            "schema_version": "1.0",
            "review_type": review_type,
            "target_digest": demo_snapshot.snapshot_digest,
        }
        task = ReviewTask(
            space_id=space.id,
            review_type=review_type,
            application_id=demo_application.id,
            application_snapshot_id=demo_snapshot.id,
            target_digest=demo_snapshot.snapshot_digest,
            assignee_organization_id=assignee_organization_id,
            task_status="pending",
            sequence_no=sequence_no,
            is_required=True,
            routing_rule_digest=canonical_document_digest(routing),
            created_by=source_revision.created_by,
        )
        session.add(task)
        await session.flush()
        claim_review_task(task, user_id=source_revision.created_by)
        await session.flush()
        await submit_review_decision(
            session,
            task,
            decision="approved",
            decided_by_user_id=source_revision.created_by,
            decided_for_organization_id=assignee_organization_id,
            evidence={"schema_version": "1.0", "is_demo": True},
        )
    demo_application.status = "provider_review"
    await session.flush()
    demo_application.status = "approved"
    demo_application.decided_at = datetime.now(timezone.utc)
    await session.flush()
    eligibility = await build_contract_eligibility_evidence(
        session, application=demo_application, snapshot=demo_snapshot
    )

    target_contract = Contract(
        space_id=source_contract.space_id,
        application_id=demo_application.id,
        application_snapshot_id=demo_snapshot.id,
        application_snapshot_digest=demo_snapshot.snapshot_digest,
        eligibility_evidence=eligibility,
        eligibility_digest=canonical_document_digest(eligibility),
        contract_number=DEMO_CONTRACT_NUMBER,
        created_by=source_revision.created_by,
        is_demo=True,
    )
    session.add(target_contract)
    await session.flush()
    target_revision = ContractRevision(
        contract_id=target_contract.id,
        revision_no=1,
        name="PathMNIST 受控推理演示协议",
        summary="仅用于公开数据、固定白名单模型和隔离聚合输出的工程演示。",
        terms_schema_version=source_revision.terms_schema_version,
        terms_document=dict(source_revision.terms_document),
        terms_digest=source_revision.terms_digest,
        status="draft",
        signing_mode=source_revision.signing_mode,
        effective_from=None,
        effective_until=None,
        created_by=source_revision.created_by,
    )
    session.add(target_revision)
    await session.flush()

    party_map: dict[UUID, ContractParty] = {}
    source_parties = list(
        (
            await session.scalars(
                select(ContractParty)
                .where(ContractParty.contract_revision_id == source_revision.id)
                .order_by(ContractParty.party_role)
            )
        ).all()
    )
    for source in source_parties:
        target = ContractParty(
            contract_revision_id=target_revision.id,
            organization_id=source.organization_id,
            party_role=source.party_role,
            signing_order=source.signing_order,
            is_required=source.is_required,
            party_name_snapshot=source.party_name_snapshot,
            identity_snapshot=dict(source.identity_snapshot),
            created_by=source_revision.created_by,
        )
        session.add(target)
        await session.flush()
        party_map[source.id] = target

    object_map: dict[UUID, ContractObject] = {}
    source_objects = [source_contract_object]
    for source in source_objects:
        target = ContractObject(
            contract_revision_id=target_revision.id,
            data_product_version_id=version.id,
            product_snapshot_digest=version.snapshot_digest,
            product_name_snapshot=product.name,
            authorized_scope={"schema_version": "1.0", "resources": ["pathmnist_test_20"]},
            authorized_scope_digest=canonical_document_digest(
                {"schema_version": "1.0", "resources": ["pathmnist_test_20"]}
            ),
            position_no=source.position_no,
            created_by=source_revision.created_by,
        )
        session.add(target)
        await session.flush()
        object_map[source.id] = target

    source_policies = list(
        (
            await session.scalars(
                select(Policy)
                .where(Policy.contract_revision_id == source_revision.id)
                .order_by(Policy.priority, Policy.policy_code)
            )
        ).all()
    )
    for source in source_policies:
        target_policy = Policy(
            contract_revision_id=target_revision.id,
            policy_code=source.policy_code,
            policy_type=source.policy_type,
            effect=source.effect,
            subject_contract_party_id=party_map[source.subject_contract_party_id].id,
            contract_object_id=object_map[source.contract_object_id].id,
            action_code=source.action_code,
            priority=source.priority,
            created_by=source_revision.created_by,
        )
        session.add(target_policy)
        await session.flush()
        constraints = list(
            (
                await session.scalars(
                    select(PolicyConstraint)
                    .where(PolicyConstraint.policy_id == source.id)
                    .order_by(PolicyConstraint.position_no)
                )
            ).all()
        )
        for constraint in constraints:
            value = run_limit if constraint.constraint_name == "run_count" else constraint.value
            session.add(
                PolicyConstraint(
                    policy_id=target_policy.id,
                    constraint_name=constraint.constraint_name,
                    operator=constraint.operator,
                    value=value,
                    unit=constraint.unit,
                    position_no=constraint.position_no,
                )
            )
        bindings = list(
            (
                await session.scalars(
                    select(PolicyExecutionBinding).where(
                        PolicyExecutionBinding.policy_id == source.id
                    )
                )
            ).all()
        )
        for binding in bindings:
            session.add(
                PolicyExecutionBinding(
                    policy_id=target_policy.id,
                    connector_id=binding.connector_id,
                    execution_role=binding.execution_role,
                    required_capability_code=binding.required_capability_code,
                    required_capability_version=binding.required_capability_version,
                    is_required=binding.is_required,
                    deployment_status="pending",
                )
            )
    await session.flush()
    await propose_contract_revision(session, target_revision)

    target_parties = list(
        (
            await session.scalars(
                select(ContractParty)
                .where(
                    ContractParty.contract_revision_id == target_revision.id,
                    ContractParty.is_required.is_(True),
                )
                .order_by(ContractParty.party_role)
            )
        ).all()
    )
    for party in target_parties:
        await sign_contract_revision(
            session,
            target_revision,
            contract_party_id=party.id,
            signer_organization_id=party.organization_id,
            signer_user_id=source_revision.created_by,
            signature_value_ref=f"demo-signature/{target_revision.id}/{party.id}",
        )

    target_bindings = list(
        (
            await session.scalars(
                select(PolicyExecutionBinding)
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(
                    Policy.contract_revision_id == target_revision.id,
                    PolicyExecutionBinding.is_required.is_(True),
                )
            )
        ).all()
    )
    acknowledged_at = datetime.now(timezone.utc)
    for binding in target_bindings:
        binding.deployment_status = "accepted"
        binding.acknowledged_at = acknowledged_at
        binding.receipt_digest = canonical_document_digest(
            {
                "schema_version": "1.0",
                "binding_id": str(binding.id),
                "policy_id": str(binding.policy_id),
                "connector_id": str(binding.connector_id),
            }
        )
        binding.row_version += 1
    await session.flush()
    await activate_contract_revision(
        session,
        target_revision,
        audit_command=_audit_command(DEMO_CONTRACT_NUMBER),
    )
    await _refresh_demo_connector_heartbeats(session, target_revision)
    return await _baseline_from_revision(
        session, target_contract, target_revision, created=True
    )

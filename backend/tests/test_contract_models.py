import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.applications import submit_application
from app.modules.audit import AuditCommandContext, digest_idempotency_key
from app.modules.catalog import approve_version, publish_version, submit_version_for_review
from app.modules.contracts import (
    Contract,
    ContractInvariantError,
    ContractObject,
    ContractParty,
    ContractRevision,
    ContractSignature,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
    activate_contract_revision,
    build_contract_eligibility_evidence,
    canonical_document_digest,
    propose_contract_revision,
    sign_contract_revision,
    withdraw_draft_revision,
)
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.identity.models import (
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
)
from app.modules.reviews.models import ReviewTask
from app.modules.reviews.services import claim_review_task, submit_review_decision
from app.modules.spaces.models import SpaceParticipant, SpaceParticipantRole
from tests.test_application_models import (
    add_minimum_usage_request,
    create_schema,
    make_application,
    make_engine,
    make_item,
)


def _system_audit_command(
    label: str, service_code: str = "medtrust.contract"
) -> AuditCommandContext:
    command_id = uuid4()
    return AuditCommandContext(
        command_id=command_id,
        idempotency_key=digest_idempotency_key(f"{label}:{command_id}"),
        correlation_id=command_id,
        actor_type="system",
        actor_service_code=service_code,
    )
from tests.test_catalog_models import create_catalog_graph


def test_contract_core_graph_can_be_created() -> None:
    asyncio.run(_create_contract_core_graph())


def test_revision_number_is_unique_per_contract() -> None:
    asyncio.run(_reject_duplicate_revision_number())


def test_contract_object_requires_exact_product_version_digest() -> None:
    asyncio.run(_reject_wrong_product_version_digest())


def test_withdrawn_revision_is_immutable() -> None:
    asyncio.run(_protect_withdrawn_revision())


def test_proposal_is_blocked_until_policy_and_binding_exist() -> None:
    asyncio.run(_block_incomplete_proposal())


def test_policy_constraint_and_binding_graph_can_be_proposed() -> None:
    asyncio.run(_create_complete_policy_graph())


def test_binding_requires_an_existing_exact_capability_version() -> None:
    asyncio.run(_reject_missing_binding_capability())


def test_policy_is_immutable_after_revision_is_proposed() -> None:
    asyncio.run(_protect_proposed_policy())


def test_required_demo_signatures_close_signing_but_do_not_auto_activate() -> None:
    asyncio.run(_complete_signature_and_activation_flow())


def test_missing_required_signature_cannot_activate() -> None:
    asyncio.run(_reject_activation_with_missing_signature())


def test_pending_binding_cannot_activate_signed_revision() -> None:
    asyncio.run(_reject_activation_with_pending_binding())


def test_active_revision_content_is_immutable() -> None:
    asyncio.run(_protect_active_revision())


async def _prepare_review_context(
    session,
    *,
    application,
    snapshot,
    provider,
    consumer,
    space,
    user,
) -> None:
    operator = await session.get(Organization, space.operator_organization_id)
    assert operator is not None
    for organization in (operator, provider, consumer):
        membership = OrganizationMember(
            organization_id=organization.id,
            user_id=user.id,
            status="active",
            valid_from=datetime.now(timezone.utc),
            created_by=user.id,
        )
        session.add(membership)
        await session.flush()
        session.add(
            OrganizationMemberRole(
                organization_member_id=membership.id,
                role_code="contract_signer",
                granted_by=user.id,
            )
        )

    for organization, role_code in ((operator, "operator"), (consumer, "consumer")):
        participant = SpaceParticipant(
            space_id=space.id,
            organization_id=organization.id,
            admission_status="admitted",
            ruleset_accepted_version=space.ruleset_version,
            admitted_at=datetime.now(timezone.utc),
            created_by=user.id,
        )
        session.add(participant)
        await session.flush()
        session.add(
            SpaceParticipantRole(
                space_participant_id=participant.id,
                role_code=role_code,
                granted_by=user.id,
            )
        )
    await session.flush()

    application.status = "prechecking"
    await session.flush()
    task_specs = (
        ("application_precheck", 10, operator.id),
        ("provider_review", 20, provider.id),
    )
    for review_type, sequence_no, assignee_organization_id in task_specs:
        routing = {
            "schema_version": "1.0",
            "review_type": review_type,
            "target_digest": snapshot.snapshot_digest,
        }
        task = ReviewTask(
            space_id=space.id,
            review_type=review_type,
            application_id=application.id,
            application_snapshot_id=snapshot.id,
            target_digest=snapshot.snapshot_digest,
            assignee_organization_id=assignee_organization_id,
            task_status="pending",
            sequence_no=sequence_no,
            is_required=True,
            routing_rule_digest=canonical_document_digest(routing),
            created_by=user.id,
        )
        session.add(task)
        await session.flush()
        claim_review_task(task, user_id=user.id)
        await session.flush()
        await submit_review_decision(
            session,
            task,
            decision="approved",
            decided_by_user_id=user.id,
            decided_for_organization_id=assignee_organization_id,
            evidence={"schema_version": "1.0", "is_demo": True},
        )
    application.status = "provider_review"
    await session.flush()
    application.status = "approved"
    application.decided_at = datetime.now(timezone.utc)
    await session.flush()


async def _make_contract_graph(
    session,
    *,
    number: str,
    with_review_evidence: bool = False,
    application_algorithm_digest: str | None = None,
    application_action_code: str = "ai_training",
):
    user, product, version, _, space, _, _ = await create_catalog_graph(session)
    version.snapshot_digest = f"sha256:{'a' * 64}"
    await submit_version_for_review(session, version)
    await approve_version(session, version, approved_by=user.id)
    await publish_version(
        session,
        product,
        version,
        published_by=user.id,
        visibility="space",
    )

    application = await make_application(
        session,
        user=user,
        provider=product.provider_organization,
        space=space,
        application_number=f"APP-CONTRACT-{uuid4().hex}",
        algorithm_digest=application_algorithm_digest,
    )
    item = make_item(
        application=application,
        product=product,
        version=version,
        position_no=1,
    )
    session.add(item)
    await add_minimum_usage_request(
        session,
        application=application,
        user_id=user.id,
        action_code=application_action_code,
    )
    snapshot = await submit_application(
        session,
        application,
        submitted_by=user.id,
    )
    if with_review_evidence:
        consumer = await session.get(Organization, application.applicant_organization_id)
        assert consumer is not None
        await _prepare_review_context(
            session,
            application=application,
            snapshot=snapshot,
            provider=product.provider_organization,
            consumer=consumer,
            space=space,
            user=user,
        )
    else:
        application.status = "prechecking"
        await session.flush()
        application.status = "provider_review"
        await session.flush()
        application.status = "approved"
        application.decided_at = datetime.now(timezone.utc)
        await session.flush()

    eligibility = (
        await build_contract_eligibility_evidence(
            session, application=application, snapshot=snapshot
        )
        if with_review_evidence
        else {
            "schema_version": "1.0",
            "application_snapshot_digest": snapshot.snapshot_digest,
            "required_decision_digests": [],
        }
    )
    contract = Contract(
        space_id=space.id,
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        application_snapshot_digest=snapshot.snapshot_digest,
        eligibility_evidence=eligibility,
        eligibility_digest=canonical_document_digest(eligibility),
        contract_number=number,
        created_by=user.id,
        is_demo=True,
    )
    session.add(contract)
    await session.flush()

    terms = {"schema_version": "1.0", "purpose": application.purpose}
    revision = ContractRevision(
        contract_id=contract.id,
        revision_no=1,
        name="数字病理受控使用协议（演示）",
        summary="仅验证 Contract Core 结构，不授予数据访问权。",
        terms_schema_version="1.0",
        terms_document=terms,
        terms_digest=canonical_document_digest(terms),
        status="draft",
        signing_mode="peer_to_peer",
        created_by=user.id,
    )
    session.add(revision)
    await session.flush()
    session.add_all(
        [
            ContractParty(
                contract_revision_id=revision.id,
                organization_id=application.provider_organization_id,
                party_role="provider",
                signing_order=1,
                is_required=True,
                party_name_snapshot="数据提供机构（演示）",
                identity_snapshot={"schema_version": "1.0"},
                created_by=user.id,
            ),
            ContractParty(
                contract_revision_id=revision.id,
                organization_id=application.applicant_organization_id,
                party_role="consumer",
                signing_order=1,
                is_required=True,
                party_name_snapshot="数据使用机构（演示）",
                identity_snapshot={"schema_version": "1.0"},
                created_by=user.id,
            ),
        ]
    )
    scope = {"schema_version": "1.0", "resources": ["wsi"]}
    contract_object = ContractObject(
        contract_revision_id=revision.id,
        data_product_version_id=version.id,
        product_snapshot_digest=version.snapshot_digest,
        product_name_snapshot=product.name,
        authorized_scope=scope,
        authorized_scope_digest=canonical_document_digest(scope),
        position_no=1,
        created_by=user.id,
    )
    session.add(contract_object)
    await session.flush()
    return contract, revision, contract_object, user


async def _create_contract_core_graph() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        contract, revision, contract_object, _ = await _make_contract_graph(
            session, number="CTR-CORE-001"
        )
        await session.commit()
        assert "status" not in Contract.__table__.c
        assert revision.status == "draft"
        assert contract_object.product_snapshot_digest.startswith("sha256:")
        assert revision.contract_id == contract.id
    await engine.dispose()


async def _reject_duplicate_revision_number() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        contract, revision, _, user = await _make_contract_graph(
            session, number="CTR-CORE-002"
        )
        withdraw_draft_revision(revision)
        await session.flush()
        terms = {"schema_version": "1.0", "purpose": "counter proposal"}
        session.add(
            ContractRevision(
                contract_id=contract.id,
                revision_no=1,
                name="重复编号（演示）",
                summary="应被数据库唯一约束拒绝。",
                terms_schema_version="1.0",
                terms_document=terms,
                terms_digest=canonical_document_digest(terms),
                status="draft",
                signing_mode="peer_to_peer",
                created_by=user.id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_wrong_product_version_digest() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, _, contract_object, _ = await _make_contract_graph(
            session, number="CTR-CORE-003"
        )
        contract_object.product_snapshot_digest = f"sha256:{'b' * 64}"
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _protect_withdrawn_revision() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, _, _ = await _make_contract_graph(
            session, number="CTR-CORE-004"
        )
        withdraw_draft_revision(revision)
        await session.commit()
        revision.name = "篡改标题"
        with pytest.raises(ContractInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _block_incomplete_proposal() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, _, _ = await _make_contract_graph(
            session, number="CTR-CORE-005"
        )
        revision.status = "proposed"
        with pytest.raises(ContractInvariantError, match="Policy and Binding"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _add_policy_graph(
    session,
    revision,
    contract_object,
    user,
    *,
    purpose_code: str = "ai_training",
):
    contract = await session.get(Contract, revision.contract_id)
    assert contract is not None
    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    consumer = next(item for item in parties if item.party_role == "consumer")
    provider = next(item for item in parties if item.party_role == "provider")
    connector = await session.scalar(
        select(Connector).where(
            Connector.space_id == contract.space_id,
            Connector.owner_organization_id == provider.organization_id,
        )
    )
    assert connector is not None
    now = datetime.now(timezone.utc)
    capabilities = {
        "compute_executor": "controlled_compute_execution",
        "egress_controller": "egress_policy_enforcement",
        "audit_evidence_emitter": "audit_evidence_emit",
    }
    capability_parameters = {
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
    for code in capabilities.values():
        session.add(
            ConnectorCapability(
                connector_id=connector.id,
                capability_code=code,
                capability_version="1.0",
                status="verified",
                parameters=capability_parameters[code],
                verified_at=now,
            )
        )
    await session.flush()

    definitions = [
        ("allow-compute", "permission", "permit", "execute_controlled_compute", ("compute_executor",)),
        ("deny-raw-export", "prohibition", "deny", "export_raw_data", ("egress_controller",)),
        ("deny-reidentify", "prohibition", "deny", "reidentify_subject", ("compute_executor", "egress_controller")),
        ("deny-redistribute", "prohibition", "deny", "redistribute_data", ("egress_controller",)),
        ("require-audit", "obligation", "require", "write_audit_log", ("audit_evidence_emitter",)),
    ]
    policies = []
    for priority, (code, policy_type, effect, action, roles) in enumerate(
        definitions, start=1
    ):
        policy = Policy(
            contract_revision_id=revision.id,
            policy_code=code,
            policy_type=policy_type,
            effect=effect,
            subject_contract_party_id=consumer.id,
            contract_object_id=contract_object.id,
            action_code=action,
            priority=priority,
            created_by=user.id,
        )
        session.add(policy)
        await session.flush()
        for role in roles:
            session.add(
                PolicyExecutionBinding(
                    policy_id=policy.id,
                    connector_id=connector.id,
                    execution_role=role,
                    required_capability_code=capabilities[role],
                    required_capability_version="1.0",
                    is_required=True,
                    deployment_status="pending",
                )
            )
        policies.append(policy)
    session.add(
        PolicyConstraint(
            policy_id=policies[0].id,
            constraint_name="purpose_code",
            operator="in",
            value=[purpose_code],
            unit=None,
            position_no=1,
        )
    )
    session.add(
        PolicyConstraint(
            policy_id=policies[-1].id,
            constraint_name="audit_level",
            operator="gte",
            value="full",
            unit=None,
            position_no=1,
        )
    )
    await session.flush()
    return policies, connector


async def _create_complete_policy_graph() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, contract_object, user = await _make_contract_graph(
            session, number="CTR-POLICY-001"
        )
        policies, _ = await _add_policy_graph(
            session, revision, contract_object, user
        )
        await propose_contract_revision(session, revision)
        await session.commit()
        assert revision.status == "proposed"
        assert revision.content_digest is not None
        assert all(policy.policy_digest for policy in policies)
    await engine.dispose()


async def _reject_missing_binding_capability() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, contract_object, user = await _make_contract_graph(
            session, number="CTR-POLICY-002"
        )
        parties = list(
            (
                await session.scalars(
                    select(ContractParty).where(
                        ContractParty.contract_revision_id == revision.id
                    )
                )
            ).all()
        )
        consumer = next(item for item in parties if item.party_role == "consumer")
        contract = await session.get(Contract, revision.contract_id)
        assert contract is not None
        connector = await session.scalar(
            select(Connector).where(Connector.space_id == contract.space_id)
        )
        assert connector is not None
        policy = Policy(
            contract_revision_id=revision.id,
            policy_code="missing-capability",
            policy_type="permission",
            effect="permit",
            subject_contract_party_id=consumer.id,
            contract_object_id=contract_object.id,
            action_code="execute_controlled_compute",
            priority=1,
            created_by=user.id,
        )
        session.add(policy)
        await session.flush()
        session.add(
            PolicyExecutionBinding(
                policy_id=policy.id,
                connector_id=connector.id,
                execution_role="compute_executor",
                required_capability_code="controlled_compute_execution",
                required_capability_version="1.0",
                is_required=True,
                deployment_status="pending",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _protect_proposed_policy() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, contract_object, user = await _make_contract_graph(
            session, number="CTR-POLICY-003"
        )
        policies, _ = await _add_policy_graph(
            session, revision, contract_object, user
        )
        await propose_contract_revision(session, revision)
        await session.commit()
        policies[0].priority = 99
        with pytest.raises(ContractInvariantError, match="draft"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _sign_required_parties(session, revision, user) -> list[ContractSignature]:
    parties = list(
        (
            await session.scalars(
                select(ContractParty)
                .where(
                    ContractParty.contract_revision_id == revision.id,
                    ContractParty.is_required.is_(True),
                )
                .order_by(ContractParty.party_role)
            )
        ).all()
    )
    signatures: list[ContractSignature] = []
    for party in parties:
        signatures.append(
            await sign_contract_revision(
                session,
                revision,
                contract_party_id=party.id,
                signer_organization_id=party.organization_id,
                signer_user_id=user.id,
                signature_value_ref=f"demo-signature/{revision.id}/{party.id}",
            )
        )
    return signatures


async def _accept_required_bindings(session, revision) -> None:
    bindings = list(
        (
            await session.scalars(
                select(PolicyExecutionBinding)
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(
                    Policy.contract_revision_id == revision.id,
                    PolicyExecutionBinding.is_required.is_(True),
                )
            )
        ).all()
    )
    acknowledged_at = datetime.now(timezone.utc)
    for binding in bindings:
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


async def _make_signed_revision(session, *, number: str):
    _, revision, contract_object, user = await _make_contract_graph(
        session, number=number, with_review_evidence=True
    )
    await _add_policy_graph(session, revision, contract_object, user)
    await propose_contract_revision(session, revision)
    signatures = await _sign_required_parties(session, revision, user)
    assert revision.status == "signed"
    return revision, user, signatures


async def _complete_signature_and_activation_flow() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        revision, _, signatures = await _make_signed_revision(
            session, number="CTR-SIGN-001"
        )
        assert len(signatures) == 2
        assert revision.status == "signed"
        assert revision.activated_at is None
        await _accept_required_bindings(session, revision)
        await activate_contract_revision(
            session,
            revision,
            audit_command=_system_audit_command("complete-contract-activation"),
        )
        await session.commit()
        assert revision.status == "active"
        assert revision.activated_at is not None
    await engine.dispose()


async def _reject_activation_with_missing_signature() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, contract_object, user = await _make_contract_graph(
            session, number="CTR-SIGN-002", with_review_evidence=True
        )
        await _add_policy_graph(session, revision, contract_object, user)
        await propose_contract_revision(session, revision)
        party = await session.scalar(
            select(ContractParty)
            .where(
                ContractParty.contract_revision_id == revision.id,
                ContractParty.is_required.is_(True),
            )
            .order_by(ContractParty.party_role)
        )
        assert party is not None
        await sign_contract_revision(
            session,
            revision,
            contract_party_id=party.id,
            signer_organization_id=party.organization_id,
            signer_user_id=user.id,
            signature_value_ref=f"demo-signature/{revision.id}/{party.id}",
        )
        assert revision.status == "proposed"
        with pytest.raises(ContractInvariantError, match="only a signed"):
            await activate_contract_revision(
                session,
                revision,
                audit_command=_system_audit_command("missing-signature-activation"),
            )
        await session.rollback()
    await engine.dispose()


async def _reject_activation_with_pending_binding() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        revision, _, _ = await _make_signed_revision(
            session, number="CTR-SIGN-003"
        )
        with pytest.raises(ContractInvariantError, match="binding is not accepted"):
            await activate_contract_revision(
                session,
                revision,
                audit_command=_system_audit_command("pending-binding-activation"),
            )
        await session.rollback()
    await engine.dispose()


async def _protect_active_revision() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        revision, _, _ = await _make_signed_revision(
            session, number="CTR-SIGN-004"
        )
        await _accept_required_bindings(session, revision)
        await activate_contract_revision(
            session,
            revision,
            audit_command=_system_audit_command("immutable-contract-activation"),
        )
        await session.commit()
        revision.summary = "attempted active revision tamper"
        with pytest.raises(ContractInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()
    activate_contract_revision,
    build_contract_eligibility_evidence,

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.modules.compute import (
    AuditEvidenceUnavailable,
    ComputeInvariantError,
    cancel_prepared_run,
    create_compute_job,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.connectors.models import ConnectorCapability
from app.modules.contracts import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
    activate_contract_revision,
    canonical_document_digest,
    propose_contract_revision,
)
from tests.test_application_models import create_schema, make_engine
from tests.test_contract_models import (
    _accept_required_bindings,
    _add_policy_graph,
    _make_contract_graph,
    _sign_required_parties,
    _system_audit_command,
)

ALGORITHM_DIGEST = f"sha256:{'c' * 64}"


def test_compute_job_and_multiple_run_attempts() -> None:
    asyncio.run(_create_job_and_runs())


def test_non_active_revision_is_rejected() -> None:
    asyncio.run(_reject_non_active_revision())


def test_cross_revision_contract_object_is_rejected() -> None:
    asyncio.run(_reject_cross_revision_object())


def test_job_snapshot_is_immutable() -> None:
    asyncio.run(_protect_job_snapshot())


def test_offline_connector_or_disabled_capability_is_rejected() -> None:
    asyncio.run(_reject_unavailable_execution_capability())


def test_audit_unavailable_blocks_reservation() -> None:
    asyncio.run(_block_reservation_without_audit())


def test_compute_history_cannot_be_deleted() -> None:
    asyncio.run(_reject_compute_history_delete())


async def _make_active_compute_contract(
    session,
    *,
    number: str,
    run_limit: int = 3,
    algorithm_digest: str = ALGORITHM_DIGEST,
    purpose_code: str = "ai_training",
):
    contract, revision, contract_object, user = await _make_contract_graph(
        session,
        number=number,
        with_review_evidence=True,
        application_algorithm_digest=algorithm_digest,
        application_action_code=purpose_code,
    )
    policies, connector = await _add_policy_graph(
        session,
        revision,
        contract_object,
        user,
        purpose_code=purpose_code,
    )
    compute_policy = policies[0]
    session.add_all(
        [
            PolicyConstraint(
                policy_id=compute_policy.id,
                constraint_name="algorithm_digest",
                operator="eq",
                value=algorithm_digest,
                position_no=2,
            ),
            PolicyConstraint(
                policy_id=compute_policy.id,
                constraint_name="environment_mode",
                operator="eq",
                value="controlled_compute",
                position_no=3,
            ),
            PolicyConstraint(
                policy_id=compute_policy.id,
                constraint_name="run_count",
                operator="lte",
                value=run_limit,
                unit="count",
                position_no=4,
            ),
        ]
    )
    consumer = await session.scalar(
        select(ContractParty).where(
            ContractParty.contract_revision_id == revision.id,
            ContractParty.party_role == "consumer",
        )
    )
    assert consumer is not None
    export_policy = Policy(
        contract_revision_id=revision.id,
        policy_code="allow-reviewed-artifact",
        policy_type="permission",
        effect="permit",
        subject_contract_party_id=consumer.id,
        contract_object_id=contract_object.id,
        action_code="export_artifact",
        priority=20,
        created_by=user.id,
    )
    session.add(export_policy)
    await session.flush()
    session.add_all(
        [
            PolicyConstraint(
                policy_id=export_policy.id,
                constraint_name="output_type",
                operator="in",
                value=["model_artifact"],
                position_no=1,
            ),
            PolicyConstraint(
                policy_id=export_policy.id,
                constraint_name="output_review_required",
                operator="eq",
                value=True,
                position_no=2,
            ),
            PolicyExecutionBinding(
                policy_id=export_policy.id,
                connector_id=connector.id,
                execution_role="egress_controller",
                required_capability_code="egress_policy_enforcement",
                required_capability_version="1.0",
                is_required=True,
                deployment_status="pending",
            ),
        ]
    )
    await propose_contract_revision(session, revision)
    await _sign_required_parties(session, revision, user)
    await _accept_required_bindings(session, revision)
    await activate_contract_revision(
        session,
        revision,
        audit_command=_system_audit_command(f"activate:{number}"),
    )
    return contract, revision, contract_object, consumer, connector, user


def _algorithm_spec() -> dict[str, object]:
    return {
        "schema_version": "algorithm-spec/v1",
        "algorithm_name": "NPC Risk Demo",
        "algorithm_version": "1.0",
        "algorithm_digest": ALGORITHM_DIGEST,
        "registry_type": "platform_demo_registry",
        "registry_reference": "demo:npc-risk:1.0",
        "execution_profile": "built_in_simulation",
        "declared_output_types": ["model_artifact"],
    }


async def _create_ready_job(session, *, number: str, run_limit: int = 3):
    _, revision, contract_object, consumer, connector, user = (
        await _make_active_compute_contract(session, number=number, run_limit=run_limit)
    )
    job = await create_compute_job(
        session,
        revision_id=revision.id,
        party_id=consumer.id,
        contract_object_id=contract_object.id,
        requester_organization_id=consumer.organization_id,
        requester_user_id=user.id,
        purpose_code="ai_training",
        requested_output_types=["model_artifact"],
        algorithm_spec_snapshot=_algorithm_spec(),
        audit_command=_system_audit_command(
            f"create-job:{number}", "medtrust.compute"
        ),
    )
    await validate_compute_job(session, job)
    return job, revision, contract_object, consumer, connector, user


async def _create_job_and_runs() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, _, _, _, _, user = await _create_ready_job(
            session, number="CTR-COMPUTE-001"
        )
        assert job.status == "ready"
        assert job.algorithm_spec_digest.startswith("sha256:")
        assert "release_status" not in job.__table__.c
        run1 = await prepare_compute_run(session, job, created_by=user.id)
        assert run1.attempt_no == 1
        await cancel_prepared_run(run1)
        await session.flush()
        run2 = await prepare_compute_run(session, job, created_by=user.id)
        assert run2.attempt_no == 2
        await cancel_prepared_run(run2)
        await session.commit()
        run2.failure_code = "tamper"
        with pytest.raises(ComputeInvariantError, match="terminal"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_non_active_revision() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        _, revision, contract_object, user = await _make_contract_graph(
            session,
            number="CTR-COMPUTE-002",
            with_review_evidence=True,
            application_algorithm_digest=ALGORITHM_DIGEST,
        )
        consumer = await session.scalar(
            select(ContractParty).where(
                ContractParty.contract_revision_id == revision.id,
                ContractParty.party_role == "consumer",
            )
        )
        assert consumer is not None
        with pytest.raises(ComputeInvariantError, match="not active"):
            await create_compute_job(
                session,
                revision_id=revision.id,
                party_id=consumer.id,
                contract_object_id=contract_object.id,
                requester_organization_id=consumer.organization_id,
                requester_user_id=user.id,
                purpose_code="ai_training",
                requested_output_types=["model_artifact"],
                algorithm_spec_snapshot=_algorithm_spec(),
            )
    await engine.dispose()


async def _reject_cross_revision_object() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        contract, revision1, object1, consumer1, _, user1 = await _make_active_compute_contract(
            session, number="CTR-COMPUTE-003-A"
        )
        terms = {"schema_version": "1.0", "purpose": "cross revision guard"}
        revision2 = ContractRevision(
            contract_id=contract.id,
            revision_no=2,
            name="Cross revision candidate (demo)",
            summary="Used only to verify the compute boundary.",
            terms_schema_version="1.0",
            terms_document=terms,
            terms_digest=canonical_document_digest(terms),
            status="draft",
            signing_mode="peer_to_peer",
            created_by=user1.id,
        )
        session.add(revision2)
        await session.flush()
        scope = {"schema_version": "1.0", "resources": ["wsi"]}
        object2 = ContractObject(
            contract_revision_id=revision2.id,
            data_product_version_id=object1.data_product_version_id,
            product_snapshot_digest=object1.product_snapshot_digest,
            product_name_snapshot="Cross revision object (demo)",
            authorized_scope=scope,
            authorized_scope_digest=canonical_document_digest(scope),
            position_no=1,
            created_by=user1.id,
        )
        session.add(object2)
        await session.flush()
        with pytest.raises(ComputeInvariantError, match="outside the Revision"):
            await create_compute_job(
                session,
                revision_id=revision1.id,
                party_id=consumer1.id,
                contract_object_id=object2.id,
                requester_organization_id=consumer1.organization_id,
                requester_user_id=user1.id,
                purpose_code="ai_training",
                requested_output_types=["model_artifact"],
                algorithm_spec_snapshot=_algorithm_spec(),
            )
    await engine.dispose()


async def _protect_job_snapshot() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, _, _, _, _, _ = await _create_ready_job(
            session, number="CTR-COMPUTE-004"
        )
        await session.commit()
        job.algorithm_spec_snapshot = {**job.algorithm_spec_snapshot, "tampered": True}
        with pytest.raises(ComputeInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_unavailable_execution_capability() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, _, _, _, connector, _ = await _create_ready_job(
            session, number="CTR-COMPUTE-005"
        )
        connector.runtime_status = "offline"
        with pytest.raises(ComputeInvariantError, match="unavailable"):
            await validate_compute_job(session, job)
        await session.rollback()

    async with factory() as session:
        job, _, _, _, connector, _ = await _create_ready_job(
            session, number="CTR-COMPUTE-006"
        )
        capability = await session.get(
            ConnectorCapability,
            (connector.id, "controlled_compute_execution", "1.0"),
        )
        assert capability is not None
        capability.status = "disabled"
        capability.verified_at = datetime.now(timezone.utc)
        with pytest.raises(ComputeInvariantError, match="unavailable"):
            await validate_compute_job(session, job)
        await session.rollback()
    await engine.dispose()


async def _block_reservation_without_audit() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, _, _, _, _, user = await _create_ready_job(
            session, number="CTR-COMPUTE-007", run_limit=1
        )
        run = await prepare_compute_run(session, job, created_by=user.id)
        with pytest.raises(AuditEvidenceUnavailable, match="AuditEvidenceUnavailable"):
            await reserve_compute_run(session, run)
        assert run.status == "prepared"
        assert run.reservation_ordinal is None
    await engine.dispose()


async def _reject_compute_history_delete() -> None:
    engine = make_engine()
    await create_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        job, _, _, _, _, user = await _create_ready_job(
            session, number="CTR-COMPUTE-008"
        )
        run = await prepare_compute_run(session, job, created_by=user.id)
        await session.flush()
        await session.delete(run)
        with pytest.raises(ComputeInvariantError, match="ComputeRun cannot be deleted"):
            await session.flush()
        await session.rollback()
    await engine.dispose()

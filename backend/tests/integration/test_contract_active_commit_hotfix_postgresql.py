from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.compute import AuditEvidenceUnavailable, prepare_compute_run, reserve_compute_run
from app.modules.catalog.models import DataProduct, DataProductVersion
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.contracts import (
    Contract,
    ContractParty,
    ContractObject,
    ContractInvariantError,
    Policy,
    PolicyExecutionBinding,
    ContractRevision,
    activate_contract_revision,
    propose_contract_revision,
)
from app.modules.identity.models import (
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole
from tests.test_compute_models import ALGORITHM_DIGEST, _create_ready_job
from tests.test_contract_models import (
    _accept_required_bindings,
    _add_policy_graph,
    _make_contract_graph,
    _make_signed_revision,
    _sign_required_parties,
    _system_audit_command,
)

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_hotfix_installs_table_specific_deferred_trigger_functions() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_hotfix_schema(TEST_DATABASE_URL))


def test_complete_active_contract_and_compute_job_commit_in_one_transaction() -> None:
    assert TEST_DATABASE_URL is not None
    run(_commit_active_contract_with_compute_job(TEST_DATABASE_URL))


def test_complete_active_contract_can_be_assembled_across_transactions() -> None:
    assert TEST_DATABASE_URL is not None
    run(_commit_active_contract_across_transactions(TEST_DATABASE_URL))


def test_deferred_signature_guard_rejects_direct_sql_and_rolls_back() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_missing_signatures_at_commit(TEST_DATABASE_URL))


def test_direct_sql_cannot_bypass_proposal_evidence_and_policy_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_missing_policy_direct_sql(TEST_DATABASE_URL))


def test_active_guard_rejects_offline_connector_and_disabled_capability() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_unavailable_connector_at_activation(TEST_DATABASE_URL))


def test_active_guard_rejects_incomplete_review_evidence() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_incomplete_review_at_activation(TEST_DATABASE_URL))


def test_active_guard_rejects_inactive_contract_party() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_inactive_party_at_activation(TEST_DATABASE_URL))


def test_active_guard_rejects_unavailable_data_product() -> None:
    assert TEST_DATABASE_URL is not None
    run(_reject_unavailable_product_at_activation(TEST_DATABASE_URL))


def _contract_number(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


async def _assert_hotfix_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == "20260725_0032"
            rows = (
                await connection.execute(
                    text(
                        "SELECT tg.tgname, p.proname "
                        "FROM pg_catalog.pg_trigger tg "
                        "JOIN pg_catalog.pg_proc p ON p.oid=tg.tgfoid "
                        "JOIN pg_catalog.pg_class c ON c.oid=tg.tgrelid "
                        "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='medtrust' AND tg.tgname IN "
                        "('trg_contract_signature_consistency', "
                        " 'trg_contract_revision_signed_consistency')"
                    )
                )
            ).all()
            assert set(rows) == {
                (
                    "trg_contract_signature_consistency",
                    "guard_contract_signature_consistency_v7",
                ),
                (
                    "trg_contract_revision_signed_consistency",
                    "guard_contract_revision_signed_consistency_v7",
                ),
            }
            assert await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_proc p "
                    "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname='medtrust' "
                    "AND p.proname='guard_contract_revision_signed_consistency_v6'"
                )
            ) == 0
    finally:
        await engine.dispose()


async def _commit_active_contract_with_compute_job(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job, revision, _, _, _, user = await _create_ready_job(
                session,
                number=_contract_number("CTR-HOTFIX-ONE-TX"),
                run_limit=1,
            )
            revision_id = revision.id
            job_id = job.id
            user_id = user.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            job = await session.get(type(job), job_id)
            assert revision is not None and revision.status == "active"
            assert job is not None and job.status == "ready"
            run_row = await prepare_compute_run(session, job, created_by=user_id)
            with pytest.raises(
                AuditEvidenceUnavailable,
                match="AuditEvidenceUnavailable",
            ):
                await reserve_compute_run(session, run_row)
            await session.rollback()
    finally:
        await engine.dispose()


async def _commit_active_contract_across_transactions(database_url: str) -> None:
    engine = create_async_engine(database_url)
    number = _contract_number("CTR-HOTFIX-MULTI-TX")
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, user = await _make_contract_graph(
                session,
                number=number,
                with_review_evidence=True,
                application_algorithm_digest=ALGORITHM_DIGEST,
            )
            revision_id = revision.id
            contract_object_id = contract_object.id
            user_id = user.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            contract_object = await session.get(ContractObject, contract_object_id)
            user = await session.get(User, user_id)
            assert revision is not None and contract_object is not None and user is not None
            await _add_policy_graph(session, revision, contract_object, user)
            await propose_contract_revision(session, revision)
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            user = await session.get(User, user_id)
            assert revision is not None and user is not None
            await _sign_required_parties(session, revision, user)
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            assert revision is not None
            await _accept_required_bindings(session, revision)
            await activate_contract_revision(
                session,
                revision,
                audit_command=_system_audit_command("hotfix-multi-tx-activate"),
            )
            await session.commit()

        async with engine.connect() as connection:
            assert await connection.scalar(
                select(ContractRevision.status).where(ContractRevision.id == revision_id)
            ) == "active"
    finally:
        await engine.dispose()


async def _reject_missing_signatures_at_commit(database_url: str) -> None:
    engine = create_async_engine(database_url)
    revision_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, user = await _make_contract_graph(
                session,
                number=_contract_number("CTR-HOTFIX-MISSING-SIGNATURE"),
                with_review_evidence=True,
            )
            await _add_policy_graph(session, revision, contract_object, user)
            await propose_contract_revision(session, revision)
            revision_id = revision.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            try:
                await session.execute(
                    update(ContractRevision)
                    .where(ContractRevision.id == revision_id)
                    .values(
                        status="signed",
                        signed_at=text("clock_timestamp()"),
                        row_version=ContractRevision.row_version + 1,
                    )
                )
                with pytest.raises(DBAPIError) as caught:
                    await session.commit()
                assert "signed revision requires every required party signature" in str(
                    caught.value.orig
                )
            finally:
                await session.rollback()

        async with engine.connect() as connection:
            assert await connection.scalar(
                select(ContractRevision.status).where(ContractRevision.id == revision_id)
            ) == "proposed"
    finally:
        await engine.dispose()


async def _reject_missing_policy_direct_sql(database_url: str) -> None:
    engine = create_async_engine(database_url)
    revision_id = None
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, _, _ = await _make_contract_graph(
                session,
                number=_contract_number("CTR-HOTFIX-MISSING-POLICY"),
                with_review_evidence=True,
            )
            revision_id = revision.id
            await session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision = await session.get(ContractRevision, revision_id)
            assert revision is not None
            with pytest.raises(ContractInvariantError, match="policy"):
                await propose_contract_revision(session, revision)
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            with pytest.raises(DBAPIError) as caught:
                await session.execute(
                    update(ContractRevision)
                    .where(ContractRevision.id == revision_id)
                    .values(
                        status="proposed",
                        proposed_at=text("clock_timestamp()"),
                        row_version=ContractRevision.row_version + 1,
                    )
                )
            assert "proposal evidence and digests are required" in str(caught.value.orig)
            await session.rollback()

        async with engine.connect() as connection:
            assert await connection.scalar(
                select(ContractRevision.status).where(ContractRevision.id == revision_id)
            ) == "draft"
    finally:
        await engine.dispose()


async def _assert_direct_activation_rejected(
    session: AsyncSession,
    revision_id: object,
    expected_message: str,
) -> None:
    with pytest.raises(DBAPIError) as caught:
        await session.execute(
            update(ContractRevision)
            .where(ContractRevision.id == revision_id)
            .values(
                status="active",
                activated_at=text("clock_timestamp()"),
                row_version=ContractRevision.row_version + 1,
            )
        )
    assert expected_message in str(caught.value.orig)


async def _reject_unavailable_connector_at_activation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session,
                number=_contract_number("CTR-HOTFIX-OFFLINE-CONNECTOR"),
            )
            await _accept_required_bindings(session, revision)
            binding = await session.scalar(
                select(PolicyExecutionBinding)
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(Policy.contract_revision_id == revision.id)
            )
            assert binding is not None
            connector = await session.get(Connector, binding.connector_id)
            assert connector is not None
            connector.runtime_status = "offline"
            await session.flush()
            await _assert_direct_activation_rejected(
                session,
                revision.id,
                "required connector capability is not executable",
            )
            await session.rollback()

        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session,
                number=_contract_number("CTR-HOTFIX-DISABLED-CAPABILITY"),
            )
            await _accept_required_bindings(session, revision)
            binding = await session.scalar(
                select(PolicyExecutionBinding)
                .join(Policy, Policy.id == PolicyExecutionBinding.policy_id)
                .where(Policy.contract_revision_id == revision.id)
            )
            assert binding is not None
            capability = await session.get(
                ConnectorCapability,
                (
                    binding.connector_id,
                    binding.required_capability_code,
                    binding.required_capability_version,
                ),
            )
            assert capability is not None
            capability.status = "disabled"
            await session.flush()
            await _assert_direct_activation_rejected(
                session,
                revision.id,
                "required connector capability is not executable",
            )
            await session.rollback()
    finally:
        await engine.dispose()


async def _reject_incomplete_review_at_activation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            _, revision, contract_object, user = await _make_contract_graph(
                session,
                number=_contract_number("CTR-HOTFIX-INCOMPLETE-REVIEW"),
                with_review_evidence=False,
            )
            await _add_contract_party_authority(session, revision, user)
            await _add_policy_graph(session, revision, contract_object, user)
            await propose_contract_revision(session, revision)
            await _sign_required_parties(session, revision, user)
            await _accept_required_bindings(session, revision)
            await _assert_direct_activation_rejected(
                session,
                revision.id,
                "review eligibility is not currently approved",
            )
            await session.rollback()
    finally:
        await engine.dispose()


async def _add_contract_party_authority(
    session: AsyncSession,
    revision: ContractRevision,
    user: User,
) -> None:
    contract = await session.get(Contract, revision.contract_id)
    assert contract is not None
    space = await session.get(Space, contract.space_id)
    assert space is not None
    parties = list(
        (
            await session.scalars(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id
                )
            )
        ).all()
    )
    now = datetime.now(timezone.utc)
    for party in parties:
        membership = await session.scalar(
            select(OrganizationMember).where(
                OrganizationMember.organization_id == party.organization_id,
                OrganizationMember.user_id == user.id,
            )
        )
        if membership is None:
            membership = OrganizationMember(
                organization_id=party.organization_id,
                user_id=user.id,
                status="active",
                valid_from=now,
                created_by=user.id,
            )
            session.add(membership)
            await session.flush()
        if await session.get(
            OrganizationMemberRole,
            (membership.id, "contract_signer"),
        ) is None:
            session.add(
                OrganizationMemberRole(
                    organization_member_id=membership.id,
                    role_code="contract_signer",
                    granted_by=user.id,
                )
            )

        participant = await session.scalar(
            select(SpaceParticipant).where(
                SpaceParticipant.space_id == contract.space_id,
                SpaceParticipant.organization_id == party.organization_id,
            )
        )
        if participant is None:
            participant = SpaceParticipant(
                space_id=contract.space_id,
                organization_id=party.organization_id,
                admission_status="admitted",
                ruleset_accepted_version=space.ruleset_version,
                admitted_at=now,
                created_by=user.id,
            )
            session.add(participant)
            await session.flush()
        role_code = "provider" if party.party_role == "provider" else "consumer"
        if await session.get(
            SpaceParticipantRole,
            (participant.id, role_code),
        ) is None:
            session.add(
                SpaceParticipantRole(
                    space_participant_id=participant.id,
                    role_code=role_code,
                    granted_by=user.id,
                )
            )
    await session.flush()


async def _reject_inactive_party_at_activation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session,
                number=_contract_number("CTR-HOTFIX-INACTIVE-PARTY"),
            )
            await _accept_required_bindings(session, revision)
            party = await session.scalar(
                select(ContractParty).where(
                    ContractParty.contract_revision_id == revision.id,
                    ContractParty.party_role == "consumer",
                )
            )
            assert party is not None
            organization = await session.get(Organization, party.organization_id)
            assert organization is not None
            organization.status = "suspended"
            await session.flush()
            await _assert_direct_activation_rejected(
                session,
                revision.id,
                "contract party is not currently admitted",
            )
            await session.rollback()
    finally:
        await engine.dispose()


async def _reject_unavailable_product_at_activation(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            revision, _, _ = await _make_signed_revision(
                session,
                number=_contract_number("CTR-HOTFIX-UNAVAILABLE-PRODUCT"),
            )
            await _accept_required_bindings(session, revision)
            contract_object = await session.scalar(
                select(ContractObject).where(
                    ContractObject.contract_revision_id == revision.id
                )
            )
            assert contract_object is not None
            version = await session.get(
                DataProductVersion,
                contract_object.data_product_version_id,
            )
            assert version is not None
            product = await session.get(DataProduct, version.data_product_id)
            assert product is not None
            product.lifecycle_status = "suspended"
            await session.flush()
            await _assert_direct_activation_rejected(
                session,
                revision.id,
                "contracted product version is unavailable",
            )
            await session.rollback()
    finally:
        await engine.dispose()


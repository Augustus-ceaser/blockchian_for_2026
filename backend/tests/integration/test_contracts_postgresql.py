from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.applications import submit_application
from app.modules.catalog import (
    approve_version,
    publish_version,
    submit_version_for_review,
)
from app.modules.contracts import (
    Contract,
    ContractObject,
    ContractParty,
    ContractRevision,
    canonical_document_digest,
)
from tests.integration.test_applications_postgresql import (
    _add_minimum_usage_request,
    _create_consumer,
    _make_application,
    _make_item,
)
from tests.integration.test_catalog_postgresql import _create_catalog_fixture

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")

CONTRACT_TABLES = {
    "contracts",
    "contract_revisions",
    "contract_parties",
    "contract_objects",
    "policies",
    "policy_constraints",
    "policy_execution_bindings",
    "contract_signatures",
}
CONTRACT_TRIGGERS = {
    "trg_contract_source_immutable",
    "trg_contract_revision_core",
    "trg_contract_party_core",
    "trg_contract_object_core",
    "trg_policy_structure",
    "trg_policy_constraint_structure",
    "trg_policy_binding",
    "trg_contract_signature_append_only",
    "trg_contract_signature_consistency",
    "trg_contract_revision_signed_consistency",
    "trg_contract_revision_activation",
}
CONTRACT_FUNCTIONS = {
    "guard_contract_source",
    "guard_contract_revision_core",
    "guard_contract_party_core",
    "guard_contract_object_core",
    "guard_policy_structure",
    "guard_policy_constraint_structure",
    "guard_policy_binding",
    "validate_policy_constraint_v1",
    "guard_contract_signature_append_only_v6",
    "assert_contract_revision_signed_consistency_v7",
    "guard_contract_signature_consistency_v7",
    "guard_contract_revision_signed_consistency_v7",
    "guard_contract_revision_activation_v6",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_contract_schema_objects_exist_on_migrated_postgresql() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_contract_schema_objects(TEST_DATABASE_URL))


def test_contract_core_postgresql_guards_reject_direct_sql_bypass() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_contract_core_guards(TEST_DATABASE_URL))


async def _assert_contract_schema_objects(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "20260725_0032"
            )
            tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert len(tables) == 54
            assert CONTRACT_TABLES <= tables

            triggers = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tg.tgname FROM pg_catalog.pg_trigger tg "
                            "JOIN pg_catalog.pg_class c ON c.oid = tg.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'medtrust' AND NOT tg.tgisinternal"
                        )
                    )
                ).all()
            )
            assert CONTRACT_TRIGGERS <= triggers

            functions = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT p.proname FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = 'medtrust'"
                        )
                    )
                ).all()
            )
            assert CONTRACT_FUNCTIONS <= functions

            candidate_key = await connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_constraint con "
                    "JOIN pg_catalog.pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_catalog.pg_namespace n ON n.oid = rel.relnamespace "
                    "WHERE n.nspname = 'medtrust' "
                    "AND rel.relname = 'data_product_versions' "
                    "AND con.conname = 'uq_product_versions_id_digest'"
                )
            )
            assert candidate_key == 1
    finally:
        await engine.dispose()


async def _create_contract_fixture(session: AsyncSession):
    fixture = await _create_catalog_fixture(session, approve=False)
    fixture.versions[0].snapshot_digest = f"sha256:{'a' * 64}"
    await submit_version_for_review(session, fixture.versions[0])
    await approve_version(
        session,
        fixture.versions[0],
        approved_by=fixture.user.id,
    )
    await publish_version(
        session,
        fixture.product,
        fixture.versions[0],
        published_by=fixture.user.id,
        visibility="space",
    )
    consumer = await _create_consumer(session, fixture.user.id)
    application = _make_application(
        fixture=fixture,
        consumer=consumer,
        application_number=f"APP-CONTRACT-PG-{uuid4().hex}",
    )
    session.add(application)
    await session.flush()
    session.add(_make_item(application=application, fixture=fixture))
    await _add_minimum_usage_request(
        session,
        application=application,
        user_id=fixture.user.id,
    )
    snapshot = await submit_application(
        session,
        application,
        submitted_by=fixture.user.id,
    )
    application.status = "prechecking"
    await session.flush()
    application.status = "provider_review"
    await session.flush()
    application.status = "approved"
    application.decided_at = datetime.now(timezone.utc)
    await session.flush()

    eligibility = {
        "schema_version": "1.0",
        "application_snapshot_digest": snapshot.snapshot_digest,
        "required_decision_digests": [],
    }
    contract = Contract(
        space_id=fixture.space.id,
        application_id=application.id,
        application_snapshot_id=snapshot.id,
        application_snapshot_digest=snapshot.snapshot_digest,
        eligibility_evidence=eligibility,
        eligibility_digest=canonical_document_digest(eligibility),
        contract_number=f"CTR-PG-{uuid4().hex}",
        created_by=fixture.user.id,
        is_demo=True,
    )
    session.add(contract)
    await session.flush()
    terms = {"schema_version": "1.0", "purpose": application.purpose}
    revision = ContractRevision(
        contract_id=contract.id,
        revision_no=1,
        name="Contract PostgreSQL 鏍稿績楠岃瘉锛堟紨绀猴級",
        summary="Core contract structure verification only; no access rights granted.",
        terms_schema_version="1.0",
        terms_document=terms,
        terms_digest=canonical_document_digest(terms),
        status="draft",
        signing_mode="peer_to_peer",
        created_by=fixture.user.id,
    )
    session.add(revision)
    await session.flush()
    session.add_all(
        [
            ContractParty(
                contract_revision_id=revision.id,
                organization_id=fixture.provider.id,
                party_role="provider",
                signing_order=1,
                is_required=True,
                party_name_snapshot=fixture.provider.display_name,
                identity_snapshot={"schema_version": "1.0"},
                created_by=fixture.user.id,
            ),
            ContractParty(
                contract_revision_id=revision.id,
                organization_id=consumer.id,
                party_role="consumer",
                signing_order=1,
                is_required=True,
                party_name_snapshot=consumer.display_name,
                identity_snapshot={"schema_version": "1.0"},
                created_by=fixture.user.id,
            ),
        ]
    )
    scope = {"schema_version": "1.0"}
    contract_object = ContractObject(
        contract_revision_id=revision.id,
        data_product_version_id=fixture.versions[0].id,
        product_snapshot_digest=fixture.versions[0].snapshot_digest,
        product_name_snapshot=fixture.product.name,
        authorized_scope=scope,
        authorized_scope_digest=canonical_document_digest(scope),
        position_no=1,
        created_by=fixture.user.id,
    )
    session.add(contract_object)
    await session.flush()
    return fixture, consumer, contract, revision, contract_object


async def _assert_contract_core_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture, consumer, contract, revision, contract_object = (
                await _create_contract_fixture(session)
            )

            attempts = [
                (
                    update(Contract)
                    .where(Contract.id == contract.id)
                    .values(contract_number="CTR-TAMPERED"),
                    "contract source evidence is immutable",
                ),
                (
                    update(ContractRevision)
                    .where(ContractRevision.id == revision.id)
                    .values(status="proposed"),
                    "proposal evidence and digests are required",
                ),
                (
                    update(ContractObject)
                    .where(ContractObject.id == contract_object.id)
                    .values(
                        authorized_scope={
                            "schema_version": "1.0",
                            "raw_export": True,
                        }
                    ),
                    "authorized scope must narrow the requested scope",
                ),
            ]
            for statement, message in attempts:
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(statement)
                    assert message in str(caught.value.orig)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()

            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        text(
                            "INSERT INTO medtrust.contract_parties "
                            "(id, contract_revision_id, organization_id, party_role, "
                            "signing_order, is_required, party_name_snapshot, "
                            "identity_snapshot, created_by) VALUES "
                            "(:id, :revision_id, :consumer_id, 'provider', 2, true, "
                            "'Wrong provider (demo)', '{}'::jsonb, :created_by)"
                        ),
                        {
                            "id": uuid4(),
                            "revision_id": revision.id,
                            "consumer_id": consumer.id,
                            "created_by": fixture.user.id,
                        },
                    )
                assert "provider party must match" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

            await session.execute(
                update(ContractRevision)
                .where(ContractRevision.id == revision.id)
                .values(
                    status="withdrawn",
                    ended_at=datetime.now(timezone.utc),
                    row_version=2,
                )
            )
            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(ContractRevision)
                        .where(ContractRevision.id == revision.id)
                        .values(name="Tamper with terminal revision")
                    )
                assert "immutable in D2" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


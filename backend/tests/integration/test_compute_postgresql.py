from __future__ import annotations

import asyncio
import os
from uuid import UUID

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.compute import (
    AuditEvidenceUnavailable,
    ComputeInvariantError,
    ComputeJob,
    ComputeRun,
    create_compute_job,
    evaluate_compute_authorization,
    prepare_compute_run,
    reserve_compute_run,
    validate_compute_job,
)
from app.modules.contracts import canonical_document_digest
from tests.test_compute_models import (
    _algorithm_spec,
    _create_ready_job,
)
from tests.test_contract_models import _system_audit_command

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")
RUN_CONCURRENCY_TEST = os.getenv("MEDTRUST_RUN_COMPUTE_CONCURRENCY_TEST") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not TEST_DATABASE_URL,
        reason="MEDTRUST_TEST_DATABASE_URL is not configured",
    ),
]


def run(coroutine: object) -> None:
    asyncio.run(coroutine)


def test_compute_schema_and_audit_fail_closed() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_schema_and_audit_gate(TEST_DATABASE_URL))


def test_compute_direct_sql_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_direct_sql_guards(TEST_DATABASE_URL))


@pytest.mark.skipif(
    not RUN_CONCURRENCY_TEST,
    reason="set MEDTRUST_RUN_COMPUTE_CONCURRENCY_TEST=1 for the committed race test",
)
def test_run_count_atomic_reservation_and_rollback() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_atomic_run_count(TEST_DATABASE_URL))


async def _assert_schema_and_audit_gate(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT version_num FROM alembic_version")) == "20260725_0032"
            tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname='medtrust'"
                        )
                    )
                ).all()
            )
            assert {"compute_jobs", "compute_runs"} <= tables
            triggers = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tg.tgname FROM pg_catalog.pg_trigger tg "
                            "JOIN pg_catalog.pg_class c ON c.oid=tg.tgrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='medtrust' AND NOT tg.tgisinternal"
                        )
                    )
                ).all()
            )
            assert {"trg_compute_job_guard", "trg_compute_run_guard"} <= triggers

        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                job, _, _, _, _, user = await _create_ready_job(
                    session, number="CTR-COMPUTE-PG-AUDIT", run_limit=1
                )
                run_row = await prepare_compute_run(session, job, created_by=user.id)
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(AuditEvidenceUnavailable, match="AuditEvidenceUnavailable"):
                        await reserve_compute_run(session, run_row)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _assert_direct_sql_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                job, _, _, _, _, user = await _create_ready_job(
                    session, number="CTR-COMPUTE-PG-SQL", run_limit=2
                )
                run_row = await prepare_compute_run(session, job, created_by=user.id)
                values = await _reservation_values(session, job, run_row)
                values["compute_binding_id"] = values["egress_binding_id"]
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(
                            update(ComputeRun)
                            .where(ComputeRun.id == run_row.id)
                            .values(**values)
                        )
                    assert "AuditEvidenceUnavailable" in str(caught.value.orig)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()

                await session.execute(
                    update(ComputeRun)
                    .where(ComputeRun.id == run_row.id)
                    .values(
                        status="cancelled",
                        finished_at=text("CURRENT_TIMESTAMP"),
                        row_version=ComputeRun.row_version + 1,
                    )
                )
                savepoint = await session.begin_nested()
                try:
                    with pytest.raises(DBAPIError) as caught:
                        await session.execute(
                            update(ComputeRun)
                            .where(ComputeRun.id == run_row.id)
                            .values(
                                failure_code="direct-sql-tamper",
                                row_version=ComputeRun.row_version + 1,
                            )
                        )
                    assert "terminal ComputeRun is immutable" in str(caught.value.orig)
                finally:
                    if savepoint.is_active:
                        await savepoint.rollback()
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _assert_atomic_run_count(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            job1, revision, contract_object, consumer, _, user = await _create_ready_job(
                session, number="CTR-COMPUTE-PG-RACE", run_limit=1
            )
            algorithm2 = {**_algorithm_spec(), "registry_reference": "demo:npc-risk:race-2"}
            job2 = await create_compute_job(
                session,
                revision_id=revision.id,
                party_id=consumer.id,
                contract_object_id=contract_object.id,
                requester_organization_id=consumer.organization_id,
                requester_user_id=user.id,
                purpose_code="ai_training",
                requested_output_types=["model_artifact"],
                algorithm_spec_snapshot=algorithm2,
                audit_command=_system_audit_command(
                    "compute-race-job-2", "medtrust.compute"
                ),
            )
            await validate_compute_job(session, job2)
            run1 = await prepare_compute_run(session, job1, created_by=user.id)
            run2 = await prepare_compute_run(session, job2, created_by=user.id)
            run1_id, run2_id = run1.id, run2.id

            await session.commit()

        # A rolled-back reservation must not leave a consumed ordinal. The
        # same prepared row can subsequently compete for ordinal 1.
        async with AsyncSession(engine, expire_on_commit=False) as session:
            run1_row = await session.get(ComputeRun, run1_id)
            assert run1_row is not None
            await reserve_compute_run(
                session,
                run1_row,
                audit_command=_system_audit_command(
                    "compute-race-rolled-back-reservation", "medtrust.compute"
                ),
            )
            assert await session.scalar(
                select(ComputeRun.reservation_ordinal).where(ComputeRun.id == run1_id)
            ) == 1
            await session.rollback()

        ready = asyncio.Event()

        async def reserve(run_id: UUID, label: str) -> str:
            await ready.wait()
            try:
                async with AsyncSession(engine, expire_on_commit=False) as session:
                    run_row = await session.get(ComputeRun, run_id)
                    assert run_row is not None
                    await reserve_compute_run(
                        session,
                        run_row,
                        audit_command=_system_audit_command(label, "medtrust.compute"),
                    )
                    await session.commit()
                return "reserved"
            except (DBAPIError, ComputeInvariantError) as error:
                assert "run_count" in str(error)
                return "exhausted"

        attempts = [
            asyncio.create_task(reserve(run1_id, "compute-race-reserve-1")),
            asyncio.create_task(reserve(run2_id, "compute-race-reserve-2")),
        ]
        ready.set()
        assert sorted(await asyncio.gather(*attempts)) == ["exhausted", "reserved"]

        async with engine.connect() as connection:
            ordinals = list(
                (
                    await connection.scalars(
                        select(ComputeRun.reservation_ordinal).where(
                            ComputeRun.id.in_((run1_id, run2_id)),
                            ComputeRun.reservation_ordinal.is_not(None),
                        )
                    )
                ).all()
            )
            assert ordinals == [1]

    finally:
        await engine.dispose()


async def _reservation_values(
    session: AsyncSession,
    job: ComputeJob,
    run_row: ComputeRun,
) -> dict[str, object]:
    context = await evaluate_compute_authorization(
        session,
        revision_id=job.contract_revision_id,
        party_id=job.requester_contract_party_id,
        contract_object_id=job.contract_object_id,
        requester_organization_id=job.requester_organization_id,
        requester_user_id=job.requester_user_id,
        purpose_code=job.purpose_code,
        algorithm_digest=job.algorithm_spec_snapshot["algorithm_digest"],
        requested_output_types=job.requested_output_types,
    )
    scope = {
        "schema_version": "quota-scope/v1",
        "contract_revision_id": str(job.contract_revision_id),
        "quota_policy_id": str(context.quota_policy.id),
        "requester_contract_party_id": str(job.requester_contract_party_id),
        "contract_object_id": str(job.contract_object_id),
    }
    evaluation_digest = canonical_document_digest(context.evaluation)
    environment_digest = canonical_document_digest(context.execution_environment)
    reservation = {
        **scope,
        "compute_run_id": str(run_row.id),
        "attempt_no": run_row.attempt_no,
        "authorization_evaluation_digest": evaluation_digest,
        "execution_environment_digest": environment_digest,
    }
    return {
        "status": "reserved",
        "quota_policy_id": context.quota_policy.id,
        "run_count_constraint_id": context.run_count_constraint.id,
        "run_limit_snapshot": context.run_limit,
        "quota_scope_digest": canonical_document_digest(scope),
        "quota_reservation_digest": canonical_document_digest(reservation),
        "start_authorization_evaluation": context.evaluation,
        "start_authorization_evaluation_digest": evaluation_digest,
        "compute_binding_id": context.compute_binding.id,
        "egress_binding_id": context.egress_binding.id,
        "audit_binding_id": context.audit_binding.id,
        "execution_environment_snapshot": context.execution_environment,
        "execution_environment_digest": environment_digest,
        "row_version": ComputeRun.row_version + 1,
    }


async def _disable_audit_gate_for_test(executor: object) -> None:
    await executor.execute(  # type: ignore[attr-defined]
        text(
            "CREATE OR REPLACE FUNCTION medtrust.assert_compute_audit_ready_v7() "
            "RETURNS void LANGUAGE plpgsql AS $$ BEGIN RETURN; END; $$"
        )
    )


async def _restore_audit_gate(executor: object) -> None:
    await executor.execute(  # type: ignore[attr-defined]
        text(
            "CREATE OR REPLACE FUNCTION medtrust.assert_compute_audit_ready_v7() "
            "RETURNS void LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'AuditEvidenceUnavailable'; END; $$"
        )
    )


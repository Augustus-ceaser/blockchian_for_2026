from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.modules.connectors.models import ConnectorCapability
from app.modules.contracts import (
    ContractParty,
    Policy,
    PolicyConstraint,
    PolicyExecutionBinding,
    propose_contract_revision,
)
from tests.integration.test_contracts_postgresql import _create_contract_fixture

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


def test_contract_policy_proposal_and_postgresql_guards() -> None:
    assert TEST_DATABASE_URL is not None
    run(_assert_policy_proposal_and_guards(TEST_DATABASE_URL))


async def _add_complete_policy_graph(
    session: AsyncSession, fixture, revision, contract_object
) -> list[Policy]:
    from sqlalchemy import select

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
    role_capability = {
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
    now = datetime.now(timezone.utc)
    session.add_all(
        [
            ConnectorCapability(
                connector_id=fixture.connector.id,
                capability_code=code,
                capability_version="1.0",
                status="verified",
                parameters=capability_parameters[code],
                verified_at=now,
            )
            for code in role_capability.values()
        ]
    )
    await session.flush()
    definitions = [
        ("allow-compute", "permission", "permit", "execute_controlled_compute", ("compute_executor",)),
        ("deny-raw", "prohibition", "deny", "export_raw_data", ("egress_controller",)),
        ("deny-reidentify", "prohibition", "deny", "reidentify_subject", ("compute_executor", "egress_controller")),
        ("deny-redistribute", "prohibition", "deny", "redistribute_data", ("egress_controller",)),
        ("require-audit", "obligation", "require", "write_audit_log", ("audit_evidence_emitter",)),
    ]
    policies: list[Policy] = []
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
            created_by=fixture.user.id,
        )
        session.add(policy)
        await session.flush()
        session.add_all(
            [
                PolicyExecutionBinding(
                    policy_id=policy.id,
                    connector_id=fixture.connector.id,
                    execution_role=role,
                    required_capability_code=role_capability[role],
                    required_capability_version="1.0",
                    is_required=True,
                    deployment_status="pending",
                )
                for role in roles
            ]
        )
        policies.append(policy)
    session.add(
        PolicyConstraint(
            policy_id=policies[0].id,
            constraint_name="purpose_code",
            operator="in",
            value=["ai_training"],
            position_no=1,
        )
    )
    await session.flush()
    return policies


async def _assert_policy_proposal_and_guards(database_url: str) -> None:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            fixture, _, _, revision, contract_object = await _create_contract_fixture(
                session
            )
            policies = await _add_complete_policy_graph(
                session, fixture, revision, contract_object
            )
            # A missing exact capability version is rejected by the composite FK.
            savepoint = await session.begin_nested()
            try:
                with pytest.raises(IntegrityError):
                    await session.execute(
                        text(
                            "INSERT INTO medtrust.policy_execution_bindings "
                            "(id, policy_id, connector_id, execution_role, "
                            "required_capability_code, required_capability_version, "
                            "is_required, deployment_status) VALUES "
                            "(:id, :policy_id, :connector_id, 'compute_executor', "
                            "'controlled_compute_execution', '1.0', true, 'pending')"
                        ),
                        {
                            "id": uuid4(),
                            "policy_id": policies[0].id,
                            "connector_id": uuid4(),
                        },
                    )
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()

            await propose_contract_revision(session, revision)
            assert revision.status == "proposed"

            savepoint = await session.begin_nested()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await session.execute(
                        update(Policy)
                        .where(Policy.id == policies[0].id)
                        .values(priority=99)
                    )
                assert "policies can only change in draft" in str(caught.value.orig)
            finally:
                if savepoint.is_active:
                    await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()

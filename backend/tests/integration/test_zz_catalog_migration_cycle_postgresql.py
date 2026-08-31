from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

TEST_DATABASE_URL = os.getenv("MEDTRUST_TEST_DATABASE_URL")
MIGRATION_CYCLE_DATABASE_URL = os.getenv("MEDTRUST_MIGRATION_CYCLE_DATABASE_URL")
RUN_MIGRATION_CYCLE = os.getenv("MEDTRUST_RUN_CATALOG_MIGRATION_CYCLE") == "1"
BACKEND_ROOT = Path(__file__).resolve().parents[2]
CATALOG_TABLES = {
    "data_products",
    "data_product_versions",
    "data_resources",
    "product_sources",
    "data_product_publications",
}
APPLICATION_EXTENSION_TABLES = {
    "application_requested_actions",
    "application_requested_output_types",
    "application_attachments",
}
CONTRACT_CORE_TABLES = {
    "contracts",
    "contract_revisions",
    "contract_parties",
    "contract_objects",
}
CONTRACT_POLICY_TABLES = {
    "policies",
    "policy_constraints",
    "policy_execution_bindings",
}
CONTRACT_SIGNATURE_TABLES = {"contract_signatures"}
COMPUTE_TABLES = {"compute_jobs", "compute_runs"}
ARTIFACT_TABLES = {"artifacts", "artifact_reviews"}
AUDIT_TABLES = {"audit_events", "outbox_messages"}
INBOX_TABLES = {"consumer_inbox_entries"}
CALLBACK_INBOX_TABLES = {"execution_callback_inbox_entries"}
V6_CONSISTENCY_FUNCTIONS = {"guard_contract_revision_signed_consistency_v6"}
V7_CONSISTENCY_FUNCTIONS = {
    "assert_contract_revision_signed_consistency_v7",
    "guard_contract_signature_consistency_v7",
    "guard_contract_revision_signed_consistency_v7",
}

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not MIGRATION_CYCLE_DATABASE_URL,
        reason="MEDTRUST_MIGRATION_CYCLE_DATABASE_URL is not configured",
    ),
    pytest.mark.skipif(
        not RUN_MIGRATION_CYCLE,
        reason="set MEDTRUST_RUN_CATALOG_MIGRATION_CYCLE=1 for the destructive cycle",
    ),
]


def test_catalog_alembic_upgrade_downgrade_cycle() -> None:
    """Cycle Catalog on a disposable database and always restore the current head."""

    assert MIGRATION_CYCLE_DATABASE_URL is not None
    if TEST_DATABASE_URL is not None:
        assert _database_identity(MIGRATION_CYCLE_DATABASE_URL) != _database_identity(
            TEST_DATABASE_URL
        ), "migration cycle database must be isolated from MEDTRUST_TEST_DATABASE_URL"

    database_url = MIGRATION_CYCLE_DATABASE_URL
    _run_alembic(database_url, "upgrade", "head")
    assert asyncio.run(_catalog_tables(database_url)) == CATALOG_TABLES
    assert (
        asyncio.run(_application_extension_tables(database_url))
        == APPLICATION_EXTENSION_TABLES
    )
    assert asyncio.run(_contract_core_tables(database_url)) == CONTRACT_CORE_TABLES
    assert asyncio.run(_contract_policy_tables(database_url)) == CONTRACT_POLICY_TABLES
    assert asyncio.run(_contract_signature_tables(database_url)) == CONTRACT_SIGNATURE_TABLES
    assert asyncio.run(_compute_tables(database_url)) == COMPUTE_TABLES
    assert asyncio.run(_artifact_tables(database_url)) == ARTIFACT_TABLES
    assert asyncio.run(_audit_tables(database_url)) == AUDIT_TABLES
    assert asyncio.run(_inbox_tables(database_url)) == INBOX_TABLES
    assert asyncio.run(_callback_inbox_tables(database_url)) == CALLBACK_INBOX_TABLES
    try:
        assert asyncio.run(_reservation_gate_state(database_url)) == (True, 1, 0)
        _run_alembic(database_url, "downgrade", "20260722_0018")
        assert asyncio.run(_callback_inbox_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_callback_inbox_tables(database_url)) == CALLBACK_INBOX_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0014")
        assert asyncio.run(_reservation_gate_state(database_url)) == (False, 0, 2)
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_reservation_gate_state(database_url)) == (True, 1, 0)
        assert asyncio.run(_contract_consistency_functions(database_url)) == V7_CONSISTENCY_FUNCTIONS
        _run_alembic(database_url, "downgrade", "20260722_0013")
        assert asyncio.run(_audit_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_audit_tables(database_url)) == AUDIT_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0012")
        assert asyncio.run(_artifact_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_artifact_tables(database_url)) == ARTIFACT_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0011")
        assert asyncio.run(_contract_consistency_functions(database_url)) == V6_CONSISTENCY_FUNCTIONS
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_contract_consistency_functions(database_url)) == V7_CONSISTENCY_FUNCTIONS
        _run_alembic(database_url, "downgrade", "20260722_0010")
        assert asyncio.run(_compute_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_compute_tables(database_url)) == COMPUTE_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0009")
        assert asyncio.run(_contract_signature_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_contract_signature_tables(database_url)) == CONTRACT_SIGNATURE_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0008")
        assert asyncio.run(_contract_policy_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert asyncio.run(_contract_policy_tables(database_url)) == CONTRACT_POLICY_TABLES
        _run_alembic(database_url, "downgrade", "20260722_0007")
        assert asyncio.run(_contract_core_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert (
            asyncio.run(_contract_core_tables(database_url))
            == CONTRACT_CORE_TABLES
        )
        _run_alembic(database_url, "downgrade", "20260722_0005")
        assert asyncio.run(_application_extension_tables(database_url)) == set()
        _run_alembic(database_url, "upgrade", "head")
        assert (
            asyncio.run(_application_extension_tables(database_url))
            == APPLICATION_EXTENSION_TABLES
        )
        _run_alembic(database_url, "downgrade", "20260722_0003")
        assert asyncio.run(_catalog_tables(database_url)) == set()
    finally:
        _run_alembic(database_url, "upgrade", "head")
    assert asyncio.run(_catalog_tables(database_url)) == CATALOG_TABLES
    assert (
        asyncio.run(_application_extension_tables(database_url))
        == APPLICATION_EXTENSION_TABLES
    )
    assert asyncio.run(_contract_core_tables(database_url)) == CONTRACT_CORE_TABLES
    assert asyncio.run(_contract_policy_tables(database_url)) == CONTRACT_POLICY_TABLES
    assert asyncio.run(_contract_signature_tables(database_url)) == CONTRACT_SIGNATURE_TABLES
    assert asyncio.run(_compute_tables(database_url)) == COMPUTE_TABLES
    assert asyncio.run(_artifact_tables(database_url)) == ARTIFACT_TABLES
    assert asyncio.run(_audit_tables(database_url)) == AUDIT_TABLES
    assert asyncio.run(_contract_consistency_functions(database_url)) == V7_CONSISTENCY_FUNCTIONS
    assert asyncio.run(_inbox_tables(database_url)) == INBOX_TABLES
    assert asyncio.run(_callback_inbox_tables(database_url)) == CALLBACK_INBOX_TABLES
    assert asyncio.run(_alembic_head(database_url)) == "20260725_0032"


async def _callback_inbox_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                text(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname='medtrust' AND tablename='execution_callback_inbox_entries'"
                )
            )
            return {row.tablename for row in rows}
    finally:
        await engine.dispose()


def _database_identity(database_url: str) -> tuple[str | None, int | None, str | None]:
    url = make_url(database_url)
    return url.host, url.port, url.database


def _run_alembic(database_url: str, command: str, target: str) -> None:
    environment = os.environ.copy()
    environment["MEDTRUST_DATABASE_URL"] = database_url
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", command, target],
        cwd=BACKEND_ROOT,
        env=environment,
        capture_output=True,
        check=False,
    )
    output = (completed.stdout + completed.stderr).decode("utf-8", errors="replace")
    assert completed.returncode == 0, output


async def _catalog_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & CATALOG_TABLES
    finally:
        await engine.dispose()


async def _application_extension_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & APPLICATION_EXTENSION_TABLES
    finally:
        await engine.dispose()


async def _contract_core_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & CONTRACT_CORE_TABLES
    finally:
        await engine.dispose()


async def _contract_policy_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & CONTRACT_POLICY_TABLES
    finally:
        await engine.dispose()


async def _contract_signature_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & CONTRACT_SIGNATURE_TABLES
    finally:
        await engine.dispose()


async def _compute_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & COMPUTE_TABLES
    finally:
        await engine.dispose()


async def _artifact_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & ARTIFACT_TABLES
    finally:
        await engine.dispose()


async def _audit_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & AUDIT_TABLES
    finally:
        await engine.dispose()


async def _inbox_tables(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            schema_tables = set(
                (
                    await connection.scalars(
                        text(
                            "SELECT tablename FROM pg_catalog.pg_tables "
                            "WHERE schemaname = 'medtrust'"
                        )
                    )
                ).all()
            )
            return schema_tables & INBOX_TABLES
    finally:
        await engine.dispose()


async def _reservation_gate_state(database_url: str) -> tuple[bool, int, int]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            v8_exists = bool(
                await connection.scalar(
                    text(
                        "SELECT to_regprocedure("
                        "'medtrust.assert_compute_run_reservation_audit_v8(uuid)') "
                        "IS NOT NULL"
                    )
                )
            )
            definition = await connection.scalar(
                text(
                    "SELECT pg_get_functiondef("
                    "'medtrust.guard_compute_run_v7()'::regprocedure)"
                )
            )
            assert definition is not None
            return (
                v8_exists,
                definition.count("assert_compute_run_reservation_audit_v8"),
                definition.count("assert_compute_audit_ready_v7"),
            )
    finally:
        await engine.dispose()


async def _alembic_head(database_url: str) -> str | None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.scalar(text("SELECT version_num FROM alembic_version"))
    finally:
        await engine.dispose()


async def _contract_consistency_functions(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return set(
                (
                    await connection.scalars(
                        text(
                            "SELECT p.proname FROM pg_catalog.pg_proc p "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace "
                            "WHERE n.nspname='medtrust' "
                            "AND p.proname LIKE '%contract%consistency%v%'"
                        )
                    )
                ).all()
            )
    finally:
        await engine.dispose()


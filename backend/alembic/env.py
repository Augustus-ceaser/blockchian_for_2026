from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings
from app.db.base import Base
from app.modules.applications import models as application_models  # noqa: F401
from app.modules.audit import models as audit_models  # noqa: F401
from app.modules.callback_inbox import models as callback_inbox_models  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.connectors import models as connector_models  # noqa: F401
from app.modules.connector_control import models as connector_control_models  # noqa: F401
from app.modules.contracts import models as contract_models  # noqa: F401
from app.modules.compute import models as compute_models  # noqa: F401
from app.modules.identity import models as identity_models  # noqa: F401
from app.modules.inbox import models as inbox_models  # noqa: F401
from app.modules.lifecycle import models as lifecycle_models  # noqa: F401
from app.modules.external_catalog import models as external_catalog_models  # noqa: F401
from app.modules.dataset_model_evidence import models as dataset_model_evidence_models  # noqa: F401
from app.modules.marketplace import models as marketplace_models  # noqa: F401
from app.modules.policy_control import models as policy_control_models  # noqa: F401
from app.modules.reviews import models as review_models  # noqa: F401
from app.modules.role_assistant import models as role_assistant_models  # noqa: F401
from app.modules.service_access import models as service_access_models  # noqa: F401
from app.modules.commerce import models as commerce_models  # noqa: F401
from app.modules.spaces import models as space_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

# Phase 2-B.2 will import model modules before autogeneration.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

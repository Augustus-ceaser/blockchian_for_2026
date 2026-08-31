import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.modules.applications import (
    Application,
    ApplicationAttachment,
    ApplicationInvariantError,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
    submit_application,
)
from app.modules.applications.services import _snapshot_digest
from app.modules.catalog import (
    DataProduct,
    DataProductVersion,
    approve_version,
    publish_version,
    submit_version_for_review,
)
from app.modules.identity.models import Organization
from tests.test_catalog_models import create_catalog_graph


def test_create_application_with_multiple_product_versions() -> None:
    asyncio.run(_create_application_with_multiple_versions())


def test_duplicate_version_in_one_application_is_rejected() -> None:
    asyncio.run(_reject_duplicate_version_in_one_application())


def test_same_version_can_be_requested_by_separate_applications() -> None:
    asyncio.run(_allow_same_version_in_separate_applications())


def test_cross_space_application_item_is_rejected() -> None:
    asyncio.run(_reject_cross_space_item())


def test_provider_mismatch_application_item_is_rejected() -> None:
    asyncio.run(_reject_provider_mismatch_item())


def test_submission_creates_one_immutable_snapshot() -> None:
    asyncio.run(_create_and_protect_snapshot())


def test_snapshot_cannot_be_created_for_draft_application() -> None:
    asyncio.run(_reject_snapshot_for_draft_application())


def test_invalid_application_status_is_rejected() -> None:
    asyncio.run(_reject_invalid_status())


def test_application_extension_vocabularies_and_uniqueness() -> None:
    asyncio.run(_verify_extension_vocabularies_and_uniqueness())


def test_attachment_lifecycle_and_digest_validation() -> None:
    asyncio.run(_verify_attachment_lifecycle_and_digest_validation())


def test_snapshot_includes_extensions_with_stable_digest() -> None:
    asyncio.run(_verify_extended_snapshot_and_digest())


def make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        execution_options={"schema_translate_map": {"medtrust": None}},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure_sqlite(dbapi_connection, _: object) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

        def jsonb_typeof(value: object) -> str | None:
            if value is None:
                return None
            parsed = json.loads(value) if isinstance(value, str) else value
            return "object" if isinstance(parsed, dict) else "array"

        dbapi_connection.create_function("jsonb_typeof", 1, jsonb_typeof)

    return engine


async def create_schema(engine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def make_application(
    session,
    *,
    user,
    provider,
    space,
    application_number: str,
    provider_override=None,
    algorithm_digest: str | None = None,
) -> Application:
    consumer = Organization(
        legal_name=f"Application 使用机构 {uuid4().hex}（演示）",
        display_name="Application 使用机构（演示）",
        organization_type="ai_company",
        verification_status="verified",
        status="active",
        is_demo=True,
        created_by=user.id,
    )
    session.add(consumer)
    await session.flush()
    application = Application(
        space_id=space.id,
        application_number=application_number,
        applicant_organization_id=consumer.id,
        applicant_user_id=user.id,
        provider_organization_id=(provider_override or provider).id,
        purpose="验证数字病理模型的复发风险分层能力（演示）",
        legal_or_ethics_basis="演示授权依据",
        algorithm_name="NPC-Risk-Demo",
        algorithm_version="1.0",
        algorithm_digest=algorithm_digest or f"sha256:algorithm-{uuid4().hex}",
        requested_duration_seconds=30 * 24 * 60 * 60,
        requested_run_limit=3,
        status="draft",
        is_demo=True,
        created_by=user.id,
    )
    session.add(application)
    await session.flush()
    return application


def make_item(
    *,
    application: Application,
    product: DataProduct,
    version: DataProductVersion,
    position_no: int,
    space_id=None,
    provider_organization_id=None,
) -> ApplicationItem:
    return ApplicationItem(
        application_id=application.id,
        space_id=space_id or application.space_id,
        provider_organization_id=(
            provider_organization_id or application.provider_organization_id
        ),
        data_product_id=product.id,
        data_product_version_id=version.id,
        position_no=position_no,
        requested_product_snapshot_digest=(
            version.snapshot_digest or f"sha256:draft-{uuid4().hex}"
        ),
        requested_policy_digest=version.default_policy_digest,
        requested_scope={"schema_version": "1.0", "resources": ["wsi"]},
    )


def make_action(
    *, application: Application, action_code: str = "ai_training"
) -> ApplicationRequestedAction:
    return ApplicationRequestedAction(
        application_id=application.id,
        action_code=action_code,
        parameters={"schema_version": "1.0", "metrics": ["auc"]},
    )


def make_output(
    *, application: Application, output_type: str = "model_artifact"
) -> ApplicationRequestedOutputType:
    return ApplicationRequestedOutputType(
        application_id=application.id,
        output_type=output_type,
        requires_manual_review=False,
    )


def make_attachment(
    *,
    application: Application,
    user_id,
    attachment_type: str = "research_protocol",
    digest_seed: str = "1",
) -> ApplicationAttachment:
    return ApplicationAttachment(
        application_id=application.id,
        attachment_type=attachment_type,
        display_name=f"{attachment_type}.pdf",
        storage_ref=f"application/{application.id}/{attachment_type}",
        content_digest=f"sha256:{digest_seed * 64}",
        size_bytes=1024,
        scan_status="pending",
        created_by=user_id,
    )


async def add_minimum_usage_request(
    session,
    *,
    application: Application,
    user_id,
    action_code: str = "ai_training",
) -> ApplicationAttachment:
    action = make_action(application=application, action_code=action_code)
    output = make_output(application=application)
    attachment = make_attachment(application=application, user_id=user_id)
    session.add_all([action, output, attachment])
    await session.flush()
    attachment.scan_status = "clean"
    await session.flush()
    return attachment


def make_second_product_version(*, first_product, first_version, user_id):
    product = DataProduct(
        id=uuid4(),
        space_id=first_product.space_id,
        provider_organization_id=first_product.provider_organization_id,
        product_code=f"NPC-MRI-{uuid4().hex}",
        name="鼻咽癌 MRI 研究产品（演示）",
        description="同一提供方的第二个独立产品。",
        product_type="controlled_compute",
        domain="medical_imaging",
        lifecycle_status="draft",
        is_demo=True,
        created_by=user_id,
    )
    version = DataProductVersion(
        space_id=first_version.space_id,
        data_product_id=product.id,
        version_no=1,
        version_label="v1.0",
        status="draft",
        content_summary="MRI 演示版本。",
        scope_metadata={"schema_version": "1.0"},
        linkage_metadata={"schema_version": "1.0"},
        quality_report={"schema_version": "1.0"},
        classification_level="sensitive_personal_information",
        default_use_mode="controlled_compute",
        default_policy_template={"schema_version": "1.0"},
        default_policy_digest=f"sha256:policy-{uuid4().hex}",
        provenance_summary={"schema_version": "1.0"},
        snapshot_digest=f"sha256:version-{uuid4().hex}",
        created_by=user_id,
    )
    return product, version


async def _create_application_with_multiple_versions() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
        second_product, second_version = make_second_product_version(
            first_product=product,
            first_version=version,
            user_id=user.id,
        )
        session.add(second_product)
        await session.flush()
        session.add(second_version)
        await session.flush()
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-MULTI-001",
        )
        session.add_all(
            [
                make_item(
                    application=application,
                    product=product,
                    version=version,
                    position_no=1,
                ),
                make_item(
                    application=application,
                    product=second_product,
                    version=second_version,
                    position_no=2,
                ),
            ]
        )
        await session.commit()

    async with session_factory() as session:
        stored = await session.scalar(
            select(Application).options(selectinload(Application.items))
        )
        assert stored is not None
        assert stored.status == "draft"
        assert [item.position_no for item in stored.items] == [1, 2]
        assert len({item.data_product_version_id for item in stored.items}) == 2
    await engine.dispose()


async def _reject_duplicate_version_in_one_application() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-DUPLICATE-001",
        )
        session.add_all(
            [
                make_item(
                    application=application,
                    product=product,
                    version=version,
                    position_no=1,
                ),
                make_item(
                    application=application,
                    product=product,
                    version=version,
                    position_no=2,
                ),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    await engine.dispose()


async def _allow_same_version_in_separate_applications() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
        first = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-SEPARATE-001",
        )
        second = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-SEPARATE-002",
        )
        session.add_all(
            [
                make_item(
                    application=first,
                    product=product,
                    version=version,
                    position_no=1,
                ),
                make_item(
                    application=second,
                    product=product,
                    version=version,
                    position_no=1,
                ),
            ]
        )
        await session.commit()
        assert (
            await session.scalar(select(ApplicationItem).where(
                ApplicationItem.application_id == first.id
            ))
            is not None
        )
        assert (
            await session.scalar(select(ApplicationItem).where(
                ApplicationItem.application_id == second.id
            ))
            is not None
        )
    await engine.dispose()


async def _reject_cross_space_item() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, second_space, _ = await create_catalog_graph(
            session
        )
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-CROSS-SPACE-001",
        )
        session.add(
            make_item(
                application=application,
                product=product,
                version=version,
                position_no=1,
                space_id=second_space.id,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    await engine.dispose()


async def _reject_provider_mismatch_item() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
        other_provider = Organization(
            legal_name=f"错误提供方 {uuid4().hex}（演示）",
            display_name="错误提供方（演示）",
            organization_type="hospital",
            verification_status="verified",
            status="active",
            is_demo=True,
            created_by=user.id,
        )
        session.add(other_provider)
        await session.flush()
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            provider_override=other_provider,
            space=space,
            application_number="APP-WRONG-PROVIDER-001",
        )
        session.add(
            make_item(
                application=application,
                product=product,
                version=version,
                position_no=1,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    await engine.dispose()


async def _create_and_protect_snapshot() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
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
            application_number="APP-SNAPSHOT-001",
        )
        session.add(
            make_item(
                application=application,
                product=product,
                version=version,
                position_no=1,
            )
        )
        await add_minimum_usage_request(
            session,
            application=application,
            user_id=user.id,
        )
        snapshot = await submit_application(
            session,
            application,
            submitted_by=user.id,
        )
        await session.commit()
        snapshot_id = snapshot.id
        assert application.status == "submitted"
        assert snapshot.manifest["items"][0]["data_product_version_id"] == str(
            version.id
        )
        assert snapshot.snapshot_digest.startswith("sha256:")
        assert snapshot.manifest["requested_actions"][0]["action_code"] == "ai_training"
        assert (
            snapshot.manifest["requested_output_types"][0][
                "requires_manual_review"
            ]
            is True
        )
        assert snapshot.manifest["attachments"][0]["scan_status"] == "clean"
        assert "storage_ref" not in snapshot.manifest["attachments"][0]

    async with session_factory() as session:
        snapshot = await session.get(ApplicationSnapshot, snapshot_id)
        assert snapshot is not None
        snapshot.snapshot_digest = "sha256:tampered"
        with pytest.raises(ApplicationInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        snapshot = await session.get(ApplicationSnapshot, snapshot_id)
        assert snapshot is not None
        await session.delete(snapshot)
        with pytest.raises(ApplicationInvariantError, match="immutable"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_snapshot_for_draft_application() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-DRAFT-SNAPSHOT-001",
        )
        session.add(
            ApplicationSnapshot(
                application_id=application.id,
                schema_version="1.0",
                manifest={"schema_version": "1.0"},
                snapshot_digest=f"sha256:{uuid4().hex}",
                captured_by=user.id,
            )
        )
        with pytest.raises(ApplicationInvariantError, match="during submission"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _reject_invalid_status() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-INVALID-STATUS-001",
        )
        application.status = "invalid"
        with pytest.raises(ApplicationInvariantError):
            await session.commit()
        await session.rollback()
    await engine.dispose()


async def _verify_extension_vocabularies_and_uniqueness() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-EXTENSION-VOCAB-001",
        )
        session.add_all(
            [
                make_action(application=application),
                make_action(application=application),
            ]
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-EXTENSION-VOCAB-002",
        )
        session.add(make_action(application=application, action_code="AI-Training"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-EXTENSION-VOCAB-003",
        )
        session.add(make_output(application=application, output_type="feature_data"))
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-EXTENSION-VOCAB-004",
        )
        action = make_action(application=application)
        action.parameters = {"metrics": ["auc"]}
        session.add(action)
        with pytest.raises(ApplicationInvariantError, match="schema_version"):
            await session.flush()
        await session.rollback()
    await engine.dispose()


async def _verify_attachment_lifecycle_and_digest_validation() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-ATTACHMENT-001",
        )
        attachment = make_attachment(application=application, user_id=user.id)
        session.add(attachment)
        await session.flush()
        attachment.scan_status = "clean"
        await session.flush()
        attachment.scan_status = "pending"
        with pytest.raises(ApplicationInvariantError, match="invalid attachment"):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-ATTACHMENT-002",
        )
        attachment = make_attachment(application=application, user_id=user.id)
        attachment.content_digest = "sha256:not-a-valid-digest"
        session.add(attachment)
        with pytest.raises(ApplicationInvariantError, match="content_digest"):
            await session.flush()
        await session.rollback()

    async with session_factory() as session:
        user, product, _, _, space, _, _ = await create_catalog_graph(session)
        application = await make_application(
            session,
            user=user,
            provider=product.provider_organization,
            space=space,
            application_number="APP-ATTACHMENT-003",
        )
        attachment = make_attachment(application=application, user_id=user.id)
        attachment.attachment_type = "patient_record"
        session.add(attachment)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()
    await engine.dispose()


async def _verify_extended_snapshot_and_digest() -> None:
    engine = make_engine()
    await create_schema(engine)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        user, product, version, _, space, _, _ = await create_catalog_graph(session)
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
            application_number="APP-EXTENDED-SNAPSHOT-001",
        )
        session.add(make_item(
            application=application,
            product=product,
            version=version,
            position_no=1,
        ))
        session.add_all(
            [
                make_action(application=application, action_code="research_analysis"),
                make_action(application=application, action_code="ai_training"),
                make_output(application=application, output_type="model_artifact"),
                make_output(
                    application=application,
                    output_type="aggregate_statistics",
                ),
            ]
        )
        first_attachment = make_attachment(
            application=application,
            user_id=user.id,
            attachment_type="ethics",
            digest_seed="2",
        )
        second_attachment = make_attachment(
            application=application,
            user_id=user.id,
            attachment_type="research_protocol",
            digest_seed="1",
        )
        session.add_all([first_attachment, second_attachment])
        await session.flush()
        first_attachment.scan_status = "clean"
        second_attachment.scan_status = "clean"
        await session.flush()

        snapshot = await submit_application(
            session,
            application,
            submitted_by=user.id,
        )
        await session.commit()

        assert [
            value["action_code"] for value in snapshot.manifest["requested_actions"]
        ] == ["ai_training", "research_analysis"]
        assert [
            value["output_type"]
            for value in snapshot.manifest["requested_output_types"]
        ] == ["aggregate_statistics", "model_artifact"]
        assert [
            value["attachment_type"] for value in snapshot.manifest["attachments"]
        ] == ["ethics", "research_protocol"]
        outputs = {
            value["output_type"]: value
            for value in snapshot.manifest["requested_output_types"]
        }
        assert outputs["aggregate_statistics"]["requires_manual_review"] is False
        assert outputs["model_artifact"]["requires_manual_review"] is True
        assert outputs["model_artifact"]["review_rule_digest"].startswith("sha256:")
        assert snapshot.snapshot_digest == _snapshot_digest(snapshot.manifest)
        reordered = dict(reversed(list(snapshot.manifest.items())))
        assert snapshot.snapshot_digest == _snapshot_digest(reordered)
    await engine.dispose()

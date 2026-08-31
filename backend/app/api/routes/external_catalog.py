from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalCatalogSyncRun,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
    ExternalDatasetDuplicateResolution,
    ExternalDatasetGovernanceProfile,
    ExternalDatasetGovernanceReview,
    DUPLICATE_RESOLUTION_TYPES,
    DataProductExternalSourceLink,
)
from app.modules.external_catalog.governance import (
    GovernanceError,
    create_review,
    recalculate_profiles,
)
from app.modules.external_catalog.services import (
    ExternalCatalogError,
    ensure_configured_source,
    synchronize_catalog,
)
from app.modules.audit.models import AuditEvent
from app.modules.audit.services import (
    append_audit_event_with_outbox,
    digest_idempotency_key,
)
from app.demo.phase4 import command_for
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.external_catalog.productization import (
    ExternalProductDraftError,
    create_external_metadata_draft,
    discard_external_metadata_draft,
)
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)

router = APIRouter(prefix="/external-catalog", tags=["external-catalog"])


class GovernanceReviewRequest(BaseModel):
    dimension: str = Field(min_length=3, max_length=32)
    decision: str = Field(min_length=2, max_length=64)
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_type: str = Field(min_length=2, max_length=40)
    evidence_reference: str | None = Field(default=None, max_length=2000)
    evidence_note: str = Field(min_length=3, max_length=4000)


class DuplicateResolutionRequest(BaseModel):
    canonical_record_id: UUID | None = None
    resolution_type: str = Field(min_length=3, max_length=40)
    rationale: str = Field(min_length=3, max_length=4000)


class ExternalProductDraftRequest(BaseModel):
    curator_note: str = Field(default="", max_length=2000)


class ExternalProductDraftDiscardRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=1000)


async def _actor(
    session: AsyncSession, identity: str, *, operator: bool = False
) -> tuple[Any, DemoActor]:
    if operator and identity != "space_operator":
        raise HTTPException(status_code=403, detail="Only the space operator can sync catalogs.")
    context = await get_phase4_context(session)
    actor = context.actors.get(identity)
    if actor is None:
        raise HTTPException(status_code=403, detail="Unknown authenticated role.")
    try:
        await require_actor(
            session,
            space_id=context.space_id,
            organization_id=actor.organization_id,
            user_id=actor.user_id,
            role_code=identity,
        )
    except MarketplaceServiceError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return context, actor


def _source(row: ExternalCatalogSource) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source_code": row.source_code,
        "display_name": row.display_name,
        "source_type": row.source_type,
        "auth_mode": row.auth_mode,
        "enabled": row.enabled,
        "expected_schema_version": row.expected_schema_version,
        "last_successful_catalog_version": row.last_successful_catalog_version,
        "last_successful_etag": row.last_successful_etag,
        "last_successful_digest": row.last_successful_digest,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "status": row.status,
    }


def _run(row: ExternalCatalogSyncRun) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "source_id": str(row.source_id),
        "status": row.status,
        "started_at": row.started_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "request_etag": row.request_etag,
        "response_etag": row.response_etag,
        "http_status": row.http_status,
        "schema_version": row.schema_version,
        "catalog_version": row.catalog_version,
        "expected_record_count": row.expected_record_count,
        "received_record_count": row.received_record_count,
        "manifest_digest": row.manifest_digest,
        "datasets_digest": row.datasets_digest,
        "inserted_count": row.inserted_count,
        "updated_count": row.updated_count,
        "unchanged_count": row.unchanged_count,
        "stale_count": row.stale_count,
        "error_code": row.error_code,
        "error_summary": row.error_summary,
    }


def _record(row: ExternalDatasetRecord, *, detail: bool = False) -> dict[str, Any]:
    value = {
        "id": str(row.id),
        "external_id": row.external_id,
        "canonical_name": row.canonical_name,
        "display_name_cn": row.display_name_cn,
        "display_name_en": row.display_name_en,
        "source_catalog": row.source_catalog,
        "modalities": row.modalities,
        "disease_areas": row.disease_areas,
        "organs": row.organs,
        "sample_count": row.sample_count,
        "patient_count": row.patient_count,
        "approximate_size_bytes": row.approximate_size_bytes,
        "license_name": row.license_name,
        "license_status": row.license_status,
        "access_level": row.access_level,
        "link_status": row.link_status,
        "quality_flags": row.quality_flags,
        "duplicate_group_id": row.duplicate_group_id,
        "first_seen_at": row.first_seen_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
        "status": row.status,
        "materialized_as_data_product": False,
    }
    if detail:
        value.update(
            {
                "official_source_name": row.official_source_name,
                "official_source_url": row.official_source_url,
                "catalog_source_url": row.catalog_source_url,
                "task_types": row.task_types,
                "species": row.species,
                "file_count": row.file_count,
                "data_formats": row.data_formats,
                "license_url": row.license_url,
                "registration_required": row.registration_required,
                "dataset_version": row.dataset_version,
                "upstream_updated_at": (
                    row.upstream_updated_at.isoformat() if row.upstream_updated_at else None
                ),
            }
        )
    return value


def _profile(row: ExternalDatasetGovernanceProfile) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "record_id": str(row.record_id),
        "primary_status": row.primary_status,
        "source_review_status": row.source_review_status,
        "license_review_status": row.license_review_status,
        "access_review_status": row.access_review_status,
        "metadata_completeness_score": row.metadata_completeness_score,
        "metadata_missing_fields": row.metadata_missing_fields,
        "link_review_status": row.link_review_status,
        "duplicate_review_status": row.duplicate_review_status,
        "productization_eligible": row.productization_eligible,
        "blocking_reasons": row.blocking_reasons,
        "warning_reasons": row.warning_reasons,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        "last_reviewed_by": str(row.last_reviewed_by) if row.last_reviewed_by else None,
    }


def _review(row: ExternalDatasetGovernanceReview) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "record_id": str(row.record_id),
        "review_dimension": row.review_dimension,
        "previous_value": row.previous_value,
        "decision": row.decision,
        "decision_payload": row.decision_payload,
        "evidence_type": row.evidence_type,
        "evidence_reference": row.evidence_reference,
        "evidence_note": row.evidence_note,
        "reviewer_user_id": str(row.reviewer_user_id),
        "reviewer_organization_id": str(row.reviewer_organization_id),
        "reviewed_at": row.reviewed_at.isoformat(),
        "source_record_digest": row.source_record_digest,
        "supersedes_review_id": (
            str(row.supersedes_review_id) if row.supersedes_review_id else None
        ),
    }


async def _external_draft_payload(
    session: AsyncSession, link: DataProductExternalSourceLink
) -> dict[str, Any]:
    product = await session.get(DataProduct, link.data_product_id)
    version = await session.get(DataProductVersion, link.data_product_version_id)
    if product is None or version is None:
        raise HTTPException(status_code=409, detail="External product draft graph is incomplete.")
    return {
        "id": str(link.id),
        "product": {
            "id": str(product.id),
            "product_code": product.product_code,
            "name": product.name,
            "product_type": product.product_type,
            "lifecycle_status": product.lifecycle_status,
            "provider_organization_id": str(product.provider_organization_id),
        },
        "version": {
            "id": str(version.id),
            "version_label": version.version_label,
            "status": version.status,
            "default_use_mode": version.default_use_mode,
            "snapshot_digest": version.snapshot_digest,
            "linkage_metadata": version.linkage_metadata,
            "quality_report": version.quality_report,
        },
        "source_link": {
            "external_dataset_record_id": str(link.external_dataset_record_id),
            "external_dataset_version_id": str(link.external_dataset_version_id),
            "external_catalog_source_id": str(link.external_catalog_source_id),
            "external_id": link.external_id,
            "catalog_version": link.catalog_version,
            "source_record_digest": link.source_record_digest,
            "governance_profile_id": str(link.governance_profile_id),
            "governance_snapshot_digest": link.governance_snapshot_digest,
            "source_review_id": str(link.source_review_id),
            "license_review_id": str(link.license_review_id),
            "access_review_id": str(link.access_review_id),
            "productization_review_id": str(link.productization_review_id),
            "upstream_official_url": link.upstream_official_url,
            "upstream_rights_holder": link.upstream_rights_holder,
            "curator_organization_id": str(link.curator_organization_id),
            "materialization_status": link.materialization_status,
            "data_holder_status": link.data_holder_status,
            "redistribution_status": link.redistribution_status,
            "execution_readiness": link.execution_readiness,
            "created_at": link.created_at.isoformat(),
        },
    }


async def _governance_record(
    session: AsyncSession, *, record_id: UUID, space_id: UUID
) -> tuple[ExternalDatasetRecord, ExternalCatalogSource]:
    record = await session.get(ExternalDatasetRecord, record_id)
    source = await session.get(ExternalCatalogSource, record.source_id) if record else None
    if record is None or source is None or source.space_id != space_id:
        raise HTTPException(status_code=404, detail="External dataset not found.")
    return record, source


@router.post("/sources/configured")
async def configure_source(
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        try:
            source = await ensure_configured_source(
                session, settings=request.app.state.settings, space_id=context.space_id
            )
            existing_event = await session.scalar(
                select(AuditEvent.event_id).where(
                    AuditEvent.event_type == "external_catalog.source.created",
                    AuditEvent.subject_id == source.id,
                )
            )
            if existing_event is None:
                raw_key = (idempotency_key or "").strip()
                if len(raw_key) < 8:
                    raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
                command = command_for(
                    actor, f"external-catalog-source:{source.id}", raw_key
                )
                await append_audit_event_with_outbox(
                    session,
                    space_id=context.space_id,
                    event_type="external_catalog.source.created",
                    subject_type="external_catalog_source",
                    subject_id=source.id,
                    result="success",
                    evidence_snapshot={
                        "schema_version": "phase5.11.2/external-catalog-source/v1",
                        "source_code": source.source_code,
                        "auth_mode": source.auth_mode,
                        "expected_schema_version": source.expected_schema_version,
                    },
                    **command.append_kwargs(),
                )
        except ExternalCatalogError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _source(source)


@router.get("/sources")
async def list_sources(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    rows = (
        await session.scalars(
            select(ExternalCatalogSource)
            .where(ExternalCatalogSource.space_id == context.space_id)
            .order_by(ExternalCatalogSource.created_at)
        )
    ).all()
    return {"items": [_source(row) for row in rows]}


@router.get("/sources/{source_id}")
async def source_detail(
    source_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    row = await session.get(ExternalCatalogSource, source_id)
    if row is None or row.space_id != context.space_id:
        raise HTTPException(status_code=404, detail="Catalog source not found.")
    return _source(row)


@router.post("/sources/{source_id}/sync")
async def sync_source(
    source_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, operator=True)
            source = await session.get(ExternalCatalogSource, source_id)
            if (
                source is None
                or source.space_id != context.space_id
                or not source.enabled
                or source.source_type != "versioned_rest_catalog"
            ):
                raise HTTPException(status_code=404, detail="Enabled catalog source not found.")
            row = await synchronize_catalog(
                session,
                settings=request.app.state.settings,
                source=source,
                actor=actor,
                raw_key=idempotency_key.strip(),
            )
        return _run(row)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail="A sync is already active.") from exc


def _matches_disease_or_organ(
    disease_areas: list[Any] | None,
    organs: list[Any] | None,
    query: str | None,
) -> bool:
    query_text = (query or "").strip().casefold()
    if not query_text:
        return True
    values = [*(disease_areas or []), *(organs or [])]
    return any(query_text in str(value).casefold() for value in values)


@router.get("/datasets")
async def list_datasets(
    identity: str = Header(alias="X-Demo-Identity"),
    q: str | None = None,
    modality: str | None = None,
    disease: str | None = None,
    disease_or_organ: str | None = Query(default=None, max_length=120),
    license_status: str | None = None,
    quality_flag: str | None = None,
    status: str | None = "active",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    rows = list(
        (
            await session.scalars(
                select(ExternalDatasetRecord)
                .where(ExternalDatasetRecord.source_id.in_(source_ids))
                .order_by(
                    ExternalDatasetRecord.canonical_name,
                    ExternalDatasetRecord.external_id,
                    ExternalDatasetRecord.source_id,
                    ExternalDatasetRecord.id,
                )
            )
        ).all()
    )
    query_text = (q or "").strip().casefold()
    filtered = [
        row
        for row in rows
        if (not status or row.status == status)
        and (
            not query_text
            or query_text in row.canonical_name.casefold()
            or query_text in row.external_id.casefold()
        )
        and (not modality or modality in row.modalities)
        and (not disease or disease in row.disease_areas)
        and _matches_disease_or_organ(
            row.disease_areas,
            row.organs,
            disease_or_organ,
        )
        and (not license_status or row.license_status == license_status)
        and (not quality_flag or quality_flag in row.quality_flags)
    ]
    page = filtered[offset : offset + limit]
    published_versions_by_record: dict[UUID, UUID] = {}
    if page:
        publication_rows = (
            await session.execute(
                select(
                    DataProductExternalSourceLink.external_dataset_record_id,
                    DataProductPublication.data_product_version_id,
                )
                .join(
                    DataProductPublication,
                    DataProductPublication.data_product_version_id
                    == DataProductExternalSourceLink.data_product_version_id,
                )
                .where(
                    DataProductExternalSourceLink.external_dataset_record_id.in_(
                        [row.id for row in page]
                    ),
                    DataProductPublication.space_id == context.space_id,
                    DataProductPublication.status == "active",
                )
            )
        ).all()
        published_versions_by_record = dict(publication_rows)

    items = []
    for row in page:
        item = _record(row)
        published_version_id = published_versions_by_record.get(row.id)
        item["published_product_version_id"] = (
            str(published_version_id) if published_version_id is not None else None
        )
        items.append(item)
    return {
        "items": items,
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
    }


@router.get("/datasets/{record_id}")
async def dataset_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    row = await session.get(ExternalDatasetRecord, record_id)
    source = await session.get(ExternalCatalogSource, row.source_id) if row else None
    if row is None or source is None or source.space_id != context.space_id:
        raise HTTPException(status_code=404, detail="External dataset not found.")
    versions = (
        await session.scalars(
            select(ExternalDatasetVersion)
            .where(ExternalDatasetVersion.record_id == row.id)
            .order_by(ExternalDatasetVersion.observed_at.desc())
        )
    ).all()
    value = _record(row, detail=True)
    value["versions"] = [
        {
            "id": str(version.id),
            "catalog_version": version.catalog_version,
            "record_digest": version.record_digest,
            "observed_at": version.observed_at.isoformat(),
            "is_current": version.is_current,
        }
        for version in versions
    ]
    return value


@router.get("/sync-runs")
async def list_sync_runs(
    identity: str = Header(alias="X-Demo-Identity"),
    limit: int = Query(default=20, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    rows = (
        await session.scalars(
            select(ExternalCatalogSyncRun)
            .where(ExternalCatalogSyncRun.source_id.in_(source_ids))
            .order_by(ExternalCatalogSyncRun.started_at.desc())
            .limit(limit)
        )
    ).all()
    return {"items": [_run(row) for row in rows]}


@router.get("/sync-runs/{sync_run_id}")
async def sync_run_detail(
    sync_run_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    row = await session.get(ExternalCatalogSyncRun, sync_run_id)
    source = await session.get(ExternalCatalogSource, row.source_id) if row else None
    if row is None or source is None or source.space_id != context.space_id:
        raise HTTPException(status_code=404, detail="Catalog sync run not found.")
    return _run(row)


@router.get("/governance/summary")
async def governance_summary(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    record_ids = select(ExternalDatasetRecord.id).where(
        ExternalDatasetRecord.source_id.in_(source_ids)
    )
    total = await session.scalar(
        select(func.count()).select_from(ExternalDatasetGovernanceProfile).where(
            ExternalDatasetGovernanceProfile.record_id.in_(record_ids)
        )
    )
    status_rows = (
        await session.execute(
            select(
                ExternalDatasetGovernanceProfile.primary_status,
                func.count(ExternalDatasetGovernanceProfile.id),
            )
            .where(ExternalDatasetGovernanceProfile.record_id.in_(record_ids))
            .group_by(ExternalDatasetGovernanceProfile.primary_status)
        )
    ).all()
    eligible = await session.scalar(
        select(func.count()).select_from(ExternalDatasetGovernanceProfile).where(
            ExternalDatasetGovernanceProfile.record_id.in_(record_ids),
            ExternalDatasetGovernanceProfile.productization_eligible.is_(True),
        )
    )
    reviews = await session.scalar(
        select(func.count()).select_from(ExternalDatasetGovernanceReview).where(
            ExternalDatasetGovernanceReview.record_id.in_(record_ids)
        )
    )
    duplicate_groups = await session.scalar(
        select(func.count(func.distinct(ExternalDatasetRecord.duplicate_group_id))).where(
            ExternalDatasetRecord.source_id.in_(source_ids),
            ExternalDatasetRecord.duplicate_group_id.is_not(None),
        )
    )
    records = list(
        (
            await session.scalars(
                select(ExternalDatasetRecord).where(
                    ExternalDatasetRecord.source_id.in_(source_ids)
                )
            )
        ).all()
    )
    duplicate_name_groups = {
        row.duplicate_group_id
        for row in records
        if row.duplicate_group_id and "duplicate_name" in row.quality_flags
    }
    duplicate_url_groups = {
        row.duplicate_group_id
        for row in records
        if row.duplicate_group_id and "duplicate_url" in row.quality_flags
    }
    return {
        "total_profiles": total or 0,
        "eligible_for_draft": eligible or 0,
        "formal_reviews": reviews or 0,
        "duplicate_groups": duplicate_groups or 0,
        "by_primary_status": {status: count for status, count in status_rows},
        "quality": {
            "duplicate_name_groups": len(duplicate_name_groups),
            "duplicate_url_groups": len(duplicate_url_groups),
            "missing_links": sum(row.link_status == "missing" for row in records),
            "malformed_links": sum(row.link_status == "malformed" for row in records),
            "legacy_http_links": sum(row.link_status == "legacy_http" for row in records),
            "unknown_licenses": sum(row.license_status == "unknown" for row in records),
        },
    }


@router.get("/governance/datasets")
async def list_governance_datasets(
    identity: str = Header(alias="X-Demo-Identity"),
    q: str | None = None,
    primary_status: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    rows = (
        await session.execute(
            select(ExternalDatasetRecord, ExternalDatasetGovernanceProfile)
            .join(
                ExternalDatasetGovernanceProfile,
                ExternalDatasetGovernanceProfile.record_id == ExternalDatasetRecord.id,
            )
            .where(ExternalDatasetRecord.source_id.in_(source_ids))
            .order_by(ExternalDatasetRecord.canonical_name)
        )
    ).all()
    query_text = (q or "").strip().casefold()
    filtered = [
        (record, profile)
        for record, profile in rows
        if (not primary_status or profile.primary_status == primary_status)
        and (
            not query_text
            or query_text in record.canonical_name.casefold()
            or query_text in record.external_id.casefold()
        )
    ]
    draft_statuses: dict[UUID, str] = {}
    if filtered:
        draft_rows = (
            await session.execute(
                select(
                    DataProductExternalSourceLink.external_dataset_record_id,
                    DataProduct.lifecycle_status,
                )
                .join(
                    DataProduct,
                    DataProduct.id == DataProductExternalSourceLink.data_product_id,
                )
                .where(
                    DataProductExternalSourceLink.external_dataset_record_id.in_(
                        [record.id for record, _ in filtered]
                    )
                )
            )
        ).all()
        draft_statuses = {record_id: lifecycle_status for record_id, lifecycle_status in draft_rows}
    return {
        "items": [
            {
                "dataset": {
                    **_record(record),
                    "data_product_draft_status": draft_statuses.get(record.id),
                },
                "governance": _profile(profile),
            }
            for record, profile in filtered[offset : offset + limit]
        ],
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
    }


@router.get("/datasets/{record_id}/governance")
@router.get("/governance/datasets/{record_id}", include_in_schema=False)
async def governance_dataset_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record, _ = await _governance_record(
        session, record_id=record_id, space_id=context.space_id
    )
    profile = await session.scalar(
        select(ExternalDatasetGovernanceProfile).where(
            ExternalDatasetGovernanceProfile.record_id == record.id
        )
    )
    if profile is None:
        raise HTTPException(status_code=409, detail="Governance profile is not initialized.")
    reviews = (
        await session.scalars(
            select(ExternalDatasetGovernanceReview)
            .where(ExternalDatasetGovernanceReview.record_id == record.id)
            .order_by(ExternalDatasetGovernanceReview.reviewed_at.desc())
        )
    ).all()
    draft_link = await session.scalar(
        select(DataProductExternalSourceLink).where(
            DataProductExternalSourceLink.external_dataset_record_id == record.id
        )
    )
    return {
        "dataset": _record(record, detail=True),
        "governance": _profile(profile),
        "reviews": [_review(row) for row in reviews],
        "data_product_draft": (
            await _external_draft_payload(session, draft_link) if draft_link else None
        ),
    }


@router.get("/datasets/{record_id}/data-product-draft")
async def external_data_product_draft_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record, _ = await _governance_record(
        session, record_id=record_id, space_id=context.space_id
    )
    link = await session.scalar(
        select(DataProductExternalSourceLink).where(
            DataProductExternalSourceLink.external_dataset_record_id == record.id
        )
    )
    return {
        "exists": link is not None,
        "draft": await _external_draft_payload(session, link) if link else None,
    }


@router.post(
    "/datasets/{record_id}/data-product-draft",
    status_code=status.HTTP_201_CREATED,
)
async def create_external_data_product_draft(
    record_id: UUID,
    payload: ExternalProductDraftRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, operator=True)
            record, source = await _governance_record(
                session, record_id=record_id, space_id=context.space_id
            )
            profile = await session.scalar(
                select(ExternalDatasetGovernanceProfile).where(
                    ExternalDatasetGovernanceProfile.record_id == record.id
                )
            )
            version = (
                await session.get(ExternalDatasetVersion, record.current_version_id)
                if record.current_version_id
                else None
            )
            reviews = list(
                (
                    await session.scalars(
                        select(ExternalDatasetGovernanceReview)
                        .where(ExternalDatasetGovernanceReview.record_id == record.id)
                        .order_by(ExternalDatasetGovernanceReview.reviewed_at.desc())
                    )
                ).all()
            )
            result = await create_external_metadata_draft(
                session,
                space_id=context.space_id,
                actor=actor,
                record=record,
                source=source,
                version=version,
                profile=profile,
                reviews=reviews,
                curator_note=payload.curator_note,
                raw_key=raw_key,
            )
        return await _external_draft_payload(session, result.link)
    except HTTPException:
        raise
    except ExternalProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/datasets/{record_id}/data-product-draft/discard")
async def discard_external_data_product_draft(
    record_id: UUID,
    payload: ExternalProductDraftDiscardRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, operator=True)
            await _governance_record(session, record_id=record_id, space_id=context.space_id)
            event = await discard_external_metadata_draft(
                session,
                space_id=context.space_id,
                actor=actor,
                record_id=record_id,
                reason=payload.reason,
                raw_key=raw_key,
            )
        return {"record_id": str(record_id), "status": "archived", "event_id": str(event.event_id)}
    except HTTPException:
        raise
    except ExternalProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/duplicate-groups")
@router.get("/governance/duplicate-groups", include_in_schema=False)
async def governance_duplicate_groups(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    records = (
        await session.scalars(
            select(ExternalDatasetRecord)
            .where(
                ExternalDatasetRecord.source_id.in_(source_ids),
                ExternalDatasetRecord.duplicate_group_id.is_not(None),
            )
            .order_by(ExternalDatasetRecord.duplicate_group_id, ExternalDatasetRecord.canonical_name)
        )
    ).all()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record.duplicate_group_id or "", []).append(_record(record))
    resolutions = (
        await session.scalars(
            select(ExternalDatasetDuplicateResolution).order_by(
                ExternalDatasetDuplicateResolution.resolved_at.desc()
            )
        )
    ).all()
    latest = {}
    for resolution in resolutions:
        latest.setdefault(resolution.duplicate_group_id, resolution)
    return {
        "items": [
            {
                "duplicate_group_id": group_id,
                "records": items,
                "resolution": (
                    {
                        "id": str(latest[group_id].id),
                        "resolution_status": latest[group_id].resolution_status,
                        "canonical_record_id": (
                            str(latest[group_id].canonical_record_id)
                            if latest[group_id].canonical_record_id else None
                        ),
                        "resolution_type": latest[group_id].resolution_type,
                        "rationale": latest[group_id].rationale,
                    }
                    if group_id in latest else None
                ),
            }
            for group_id, items in grouped.items()
        ]
    }


@router.get("/duplicate-groups/{group_id}")
async def governance_duplicate_group_detail(
    group_id: str,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source_ids = select(ExternalCatalogSource.id).where(
        ExternalCatalogSource.space_id == context.space_id
    )
    records = (
        await session.scalars(
            select(ExternalDatasetRecord)
            .where(
                ExternalDatasetRecord.source_id.in_(source_ids),
                ExternalDatasetRecord.duplicate_group_id == group_id,
            )
            .order_by(ExternalDatasetRecord.canonical_name)
        )
    ).all()
    if not records:
        raise HTTPException(status_code=404, detail="Duplicate group not found.")
    resolution = await session.scalar(
        select(ExternalDatasetDuplicateResolution)
        .where(ExternalDatasetDuplicateResolution.duplicate_group_id == group_id)
        .order_by(ExternalDatasetDuplicateResolution.resolved_at.desc())
    )
    return {
        "duplicate_group_id": group_id,
        "records": [_record(row, detail=True) for row in records],
        "resolution": (
            {
                "id": str(resolution.id),
                "resolution_status": resolution.resolution_status,
                "canonical_record_id": (
                    str(resolution.canonical_record_id)
                    if resolution.canonical_record_id else None
                ),
                "resolution_type": resolution.resolution_type,
                "rationale": resolution.rationale,
            }
            if resolution else None
        ),
    }


@router.post("/governance/recalculate")
async def governance_recalculate(
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        source = await session.scalar(
            select(ExternalCatalogSource).where(
                ExternalCatalogSource.space_id == context.space_id
            ).order_by(ExternalCatalogSource.created_at)
        )
        if source is None:
            raise HTTPException(status_code=409, detail="Catalog source is not configured.")
        command = command_for(actor, f"external-governance-recalculate:{source.id}", raw_key)
        existing_event = await session.scalar(
            select(AuditEvent).where(AuditEvent.command_id == command.command_id)
        )
        if existing_event is not None:
            evidence = existing_event.evidence_snapshot
            return {
                "created_profiles": evidence["created_profiles"],
                "total_profiles": evidence["total_profiles"],
            }
        created, total = await recalculate_profiles(session)
        initialized_event_exists = await session.scalar(
            select(AuditEvent.event_id).where(
                AuditEvent.event_type
                == "external_catalog.governance.profile.initialized",
                AuditEvent.subject_id == source.id,
            )
        )
        event_type = (
            "external_catalog.governance.profile.initialized"
            if created or initialized_event_exists is None
            else "external_catalog.governance.recalculated"
        )
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type=event_type,
            subject_type="external_catalog_source",
            subject_id=source.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.3a/governance-recalculation/v1",
                "created_profiles": created,
                "initialized_profiles": total,
                "total_profiles": total,
                "formal_review_count_changed": 0,
            },
            **command.append_kwargs(),
        )
    return {"created_profiles": created, "total_profiles": total}


@router.post("/datasets/{record_id}/reviews")
@router.post("/governance/datasets/{record_id}/reviews", include_in_schema=False)
async def governance_create_review(
    record_id: UUID,
    payload: GovernanceReviewRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        record, source = await _governance_record(
            session, record_id=record_id, space_id=context.space_id
        )
        try:
            review = await create_review(
                session,
                record=record,
                dimension=payload.dimension,
                decision=payload.decision,
                decision_payload=payload.decision_payload,
                evidence_type=payload.evidence_type,
                evidence_reference=payload.evidence_reference,
                evidence_note=payload.evidence_note,
                reviewer_user_id=actor.user_id,
                reviewer_organization_id=actor.organization_id,
                raw_key=raw_key,
            )
        except GovernanceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        event_type = (
            "external_catalog.governance.review.superseded"
            if review.supersedes_review_id
            else "external_catalog.governance.review.created"
        )
        command = command_for(actor, f"external-governance-review:{review.id}", raw_key)
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type=event_type,
            subject_type="external_catalog_source",
            subject_id=source.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.3a/governance-review/v1",
                "review_id": str(review.id),
                "record_id": str(record.id),
                "dimension": review.review_dimension,
                "decision": review.decision,
                "source_record_digest": review.source_record_digest,
                "supersedes_review_id": (
                    str(review.supersedes_review_id)
                    if review.supersedes_review_id else None
                ),
            },
            **command.append_kwargs(),
        )
    return _review(review)


@router.post("/duplicate-groups/{group_id}/resolve")
@router.post("/governance/duplicate-groups/{group_id}/resolve", include_in_schema=False)
async def governance_resolve_duplicate_group(
    group_id: str,
    payload: DuplicateResolutionRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        records = (
            await session.scalars(
                select(ExternalDatasetRecord).where(
                    ExternalDatasetRecord.duplicate_group_id == group_id
                )
            )
        ).all()
        if not records:
            raise HTTPException(status_code=404, detail="Duplicate group not found.")
        source = await session.get(ExternalCatalogSource, records[0].source_id)
        if source is None or source.space_id != context.space_id:
            raise HTTPException(status_code=404, detail="Duplicate group not found.")
        if payload.canonical_record_id and payload.canonical_record_id not in {
            row.id for row in records
        }:
            raise HTTPException(status_code=422, detail="Canonical record is outside the group.")
        digest = digest_idempotency_key(raw_key)
        resolution = await session.scalar(
            select(ExternalDatasetDuplicateResolution).where(
                ExternalDatasetDuplicateResolution.idempotency_digest == digest
            )
        )
        if resolution is not None and (
            resolution.duplicate_group_id != group_id
            or resolution.canonical_record_id != payload.canonical_record_id
            or resolution.resolution_type != payload.resolution_type
            or resolution.rationale != payload.rationale.strip()
        ):
            raise HTTPException(
                status_code=409,
                detail="Idempotency key maps to a different duplicate resolution.",
            )
        if resolution is None:
            if payload.resolution_type not in DUPLICATE_RESOLUTION_TYPES:
                raise HTTPException(status_code=422, detail="Resolution type is invalid.")
            from datetime import datetime, timezone

            resolution = ExternalDatasetDuplicateResolution(
                duplicate_group_id=group_id,
                resolution_status=(
                    "unresolved" if payload.resolution_type == "unresolved" else "resolved"
                ),
                canonical_record_id=payload.canonical_record_id,
                resolution_type=payload.resolution_type,
                rationale=payload.rationale.strip(),
                resolved_by=actor.user_id,
                resolved_at=datetime.now(timezone.utc),
                idempotency_digest=digest,
            )
            session.add(resolution)
            await session.flush()
            await recalculate_profiles(session)
        command = command_for(actor, f"external-duplicate-resolution:{resolution.id}", raw_key)
        await append_audit_event_with_outbox(
            session,
            space_id=context.space_id,
            event_type="external_catalog.duplicate.resolved",
            subject_type="external_catalog_source",
            subject_id=source.id,
            result="success",
            evidence_snapshot={
                "schema_version": "phase5.11.3a/duplicate-resolution/v1",
                "resolution_id": str(resolution.id),
                "duplicate_group_id": group_id,
                "canonical_record_id": (
                    str(resolution.canonical_record_id)
                    if resolution.canonical_record_id else None
                ),
                "resolution_type": resolution.resolution_type,
            },
            **command.append_kwargs(),
        )
    return {
        "id": str(resolution.id),
        "duplicate_group_id": resolution.duplicate_group_id,
        "resolution_status": resolution.resolution_status,
        "canonical_record_id": (
            str(resolution.canonical_record_id) if resolution.canonical_record_id else None
        ),
        "resolution_type": resolution.resolution_type,
        "rationale": resolution.rationale,
    }

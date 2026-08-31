from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import command_for
from app.modules.audit.models import AuditEvent
from app.modules.audit.services import append_audit_event_with_outbox
from app.modules.external_catalog.model_services import (
    ExternalCatalogError,
    SOURCE_CODE as MODEL_SOURCE_CODE,
    ensure_configured_model_source,
    synchronize_model_catalog,
)
from app.modules.external_catalog.model_governance import (
    ModelGovernanceError,
    create_model_review,
    recalculate_model_profiles,
    resolve_model_family,
)
from app.modules.external_catalog.model_productization import (
    ExternalModelProductDraftError,
    approve_and_publish_external_model_metadata_product,
    create_external_model_metadata_draft,
    return_external_model_metadata_product,
    submit_external_model_metadata_product,
)
from app.modules.external_catalog.models import (
    ExternalCatalogSource,
    ExternalCatalogSyncRun,
    ExternalModelRecord,
    ExternalModelVersion,
    ExternalModelFamilyResolution,
    ExternalModelGovernanceProfile,
    ExternalModelGovernanceReview,
    ModelMetadataPublicationReviewTask,
    ModelProductExternalSourceLink,
)
from app.modules.marketplace.models import ModelProduct, ModelPublication, ModelVersion
from app.api.routes.external_catalog import _actor

router = APIRouter(prefix="/external-model-catalog", tags=["external-model-catalog"])


class ModelGovernanceReviewRequest(BaseModel):
    review_dimension: str
    decision: str
    decision_payload: dict[str, Any] = Field(default_factory=dict)
    evidence_type: str = Field(min_length=2, max_length=40)
    evidence_reference: str | None = Field(default=None, max_length=1000)
    evidence_note: str = Field(min_length=2, max_length=2000)


class ModelFamilyResolutionRequest(BaseModel):
    resolution_status: str
    canonical_record_id: UUID | None = None
    resolution_type: str
    member_record_ids: list[UUID] = Field(min_length=1)
    rationale: str = Field(min_length=2, max_length=2000)


class ExternalModelProductDraftRequest(BaseModel):
    curator_note: str = Field(default="", max_length=2000)


class ExternalModelPublicationReviewRequest(BaseModel):
    allow_catalog: bool
    review_opinion: str = Field(min_length=5, max_length=2000)
    risk_level: str = Field(pattern="^(low|medium|high)$")
    additional_conditions: str = Field(default="", max_length=2000)


def _source(row: ExternalCatalogSource, *, operator: bool = False) -> dict[str, Any]:
    value = {
        "id": str(row.id), "source_code": row.source_code,
        "display_name": row.display_name, "source_type": row.source_type,
        "resource_kind": row.resource_kind, "expected_schema_version": row.expected_schema_version,
        "last_successful_catalog_version": row.last_successful_catalog_version,
        "last_successful_etag": row.last_successful_etag,
        "last_successful_digest": row.last_successful_digest,
        "last_synced_at": row.last_synced_at.isoformat() if row.last_synced_at else None,
        "status": row.status,
    }
    if operator:
        value["base_url"] = row.base_url
        value["auth_mode"] = row.auth_mode
    return value


def _run(row: ExternalCatalogSyncRun) -> dict[str, Any]:
    return {
        "id": str(row.id), "source_id": str(row.source_id),
        "resource_kind": row.resource_kind, "status": row.status,
        "started_at": row.started_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "request_etag": row.request_etag, "response_etag": row.response_etag,
        "http_status": row.http_status, "schema_version": row.schema_version,
        "catalog_version": row.catalog_version,
        "expected_record_count": row.expected_record_count,
        "received_record_count": row.received_record_count,
        "manifest_digest": row.manifest_digest, "models_digest": row.models_digest,
        "inserted_count": row.inserted_count, "updated_count": row.updated_count,
        "unchanged_count": row.unchanged_count, "stale_count": row.stale_count,
        "error_code": row.error_code, "error_summary": row.error_summary,
    }


def _model(row: ExternalModelRecord, *, detail: bool = False) -> dict[str, Any]:
    value = {
        "id": str(row.id), "external_model_id": row.external_model_id,
        "catalog_status": "cataloged", "canonical_name": row.canonical_name,
        "display_name_cn": row.display_name_cn, "display_name_en": row.display_name_en,
        "model_categories": row.model_categories, "modalities": row.modalities,
        "task_types": row.task_types, "disease_areas": row.disease_areas,
        "organs": row.organs, "framework": row.framework,
        "license_name": row.license_name, "license_status": row.license_status,
        "access_status": row.access_status, "weights_status": row.weights_status,
        "estimated_weights_size_bytes": row.estimated_weights_size_bytes,
        "revision": row.revision, "gated": row.gated,
        "execution_status": row.execution_status, "local_materialized": False,
        "execution_image": None, "platform_validation": "not_validated",
        "quality_flags": row.quality_flags, "status": row.status,
        "first_seen_at": row.first_seen_at.isoformat(),
        "last_seen_at": row.last_seen_at.isoformat(),
    }
    if detail:
        value.update({
            "source_catalog": row.source_catalog, "species": row.species,
            "paper_title": row.paper_title, "paper_doi": row.paper_doi,
            "paper_url": row.paper_url, "code_repository_url": row.code_repository_url,
            "model_card_url": row.model_card_url, "upstream_provider": row.upstream_provider,
            "library_name": row.library_name, "architecture": row.architecture,
            "pipeline_tag": row.pipeline_tag, "input_schema": row.input_schema,
            "output_schema": row.output_schema,
            "preprocessing_summary": row.preprocessing_summary,
            "training_dataset_references": row.training_dataset_references,
            "evaluation_dataset_references": row.evaluation_dataset_references,
            "metrics_summary": row.metrics_summary, "license_url": row.license_url,
            "weights_files": row.weights_files, "commit_sha": row.commit_sha,
            "release_tag": row.release_tag, "clinical_use_status": row.clinical_use_status,
            "intended_use_summary": row.intended_use_summary,
            "limitations_summary": row.limitations_summary,
            "record_digest": row.raw_record_digest,
        })
    return value


def _governance_profile(row: ExternalModelGovernanceProfile) -> dict[str, Any]:
    return {
        "id": str(row.id), "record_id": str(row.record_id),
        "primary_status": row.primary_status,
        "source_review_status": row.source_review_status,
        "paper_review_status": row.paper_review_status,
        "repository_review_status": row.repository_review_status,
        "model_card_review_status": row.model_card_review_status,
        "license_review_status": row.license_review_status,
        "weight_review_status": row.weight_review_status,
        "revision_review_status": row.revision_review_status,
        "technical_contract_score": row.technical_contract_score,
        "technical_missing_fields": row.technical_missing_fields,
        "clinical_boundary_status": row.clinical_boundary_status,
        "security_review_status": row.security_review_status,
        "security_risk_flags": row.security_risk_flags,
        "model_family_status": row.model_family_status,
        "potential_family_key": row.potential_family_key,
        "productization_eligible": row.productization_eligible,
        "blocking_reasons": row.blocking_reasons,
        "warning_reasons": row.warning_reasons,
        "last_reviewed_at": row.last_reviewed_at.isoformat() if row.last_reviewed_at else None,
        "last_reviewed_by": str(row.last_reviewed_by) if row.last_reviewed_by else None,
    }


def _governance_review(row: ExternalModelGovernanceReview) -> dict[str, Any]:
    return {
        "id": str(row.id), "record_id": str(row.record_id),
        "review_dimension": row.review_dimension, "previous_value": row.previous_value,
        "decision": row.decision, "decision_payload": row.decision_payload,
        "evidence_type": row.evidence_type,
        "evidence_reference": row.evidence_reference,
        "evidence_note": row.evidence_note,
        "reviewer_user_id": str(row.reviewer_user_id),
        "reviewer_organization_id": str(row.reviewer_organization_id),
        "reviewed_at": row.reviewed_at.isoformat(),
        "source_record_digest": row.source_record_digest,
        "supersedes_review_id": str(row.supersedes_review_id) if row.supersedes_review_id else None,
    }


async def _model_source(session: AsyncSession, space_id: UUID) -> ExternalCatalogSource:
    source = await session.scalar(select(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == space_id,
        ExternalCatalogSource.resource_kind == "model",
        ExternalCatalogSource.source_code == MODEL_SOURCE_CODE,
    ))
    if source is None:
        raise HTTPException(status_code=409, detail="Model catalog source is not configured.")
    return source


async def _model_product_draft_payload(
    session: AsyncSession, link: ModelProductExternalSourceLink
) -> dict[str, Any]:
    product = await session.get(ModelProduct, link.model_product_id)
    version = await session.get(ModelVersion, link.model_version_id)
    if product is None or version is None:
        raise HTTPException(status_code=409, detail="External model draft graph is incomplete.")
    publication = await session.scalar(select(ModelPublication).where(
        ModelPublication.model_version_id == version.id,
        ModelPublication.status == "active",
    ))
    review_task = await session.scalar(
        select(ModelMetadataPublicationReviewTask)
        .where(ModelMetadataPublicationReviewTask.model_version_id == version.id)
        .order_by(ModelMetadataPublicationReviewTask.sequence_no.desc())
        .limit(1)
    )
    return {
        "id": str(link.id),
        "product": {
            "id": str(product.id),
            "product_code": product.product_code,
            "name": product.name,
            "lifecycle_status": product.lifecycle_status,
            "provider_organization_id": str(product.provider_organization_id),
            "source_kind": "external_public_model",
        },
        "version": {
            "id": str(version.id),
            "version_label": version.version_label,
            "status": version.status,
            "entrypoint_id": version.entrypoint_id,
            "runtime": version.runtime,
            "compatibility_metadata": version.compatibility_metadata,
        },
        "publication": (
            None
            if publication is None
            else {
                "id": str(publication.id),
                "status": publication.status,
                "visibility": publication.visibility,
                "published_at": publication.published_at.isoformat(),
            }
        ),
        "publication_review": (
            None
            if review_task is None
            else {
                "id": str(review_task.id),
                "sequence_no": review_task.sequence_no,
                "task_status": review_task.task_status,
                "decision": review_task.decision,
                "submitter_organization_id": str(
                    review_task.submitter_organization_id
                ),
                "submitter_user_id": str(review_task.submitter_user_id),
                "reviewer_organization_id": (
                    str(review_task.reviewer_organization_id)
                    if review_task.reviewer_organization_id
                    else None
                ),
                "reviewer_user_id": (
                    str(review_task.reviewer_user_id)
                    if review_task.reviewer_user_id
                    else None
                ),
                "submitted_at": review_task.submitted_at.isoformat(),
                "decided_at": (
                    review_task.decided_at.isoformat()
                    if review_task.decided_at
                    else None
                ),
            }
        ),
        "source_link": {
            "external_model_record_id": str(link.external_model_record_id),
            "external_model_version_id": str(link.external_model_version_id),
            "source_record_digest": link.source_record_digest,
            "governance_profile_id": str(link.governance_profile_id),
            "governance_snapshot_digest": link.governance_snapshot_digest,
            "review_ids": link.review_ids,
            "upstream_official_url": link.upstream_official_url,
            "upstream_provider": link.upstream_provider,
            "curator_organization_id": str(link.curator_organization_id),
            "materialization_status": link.materialization_status,
            "weight_holder_status": link.weight_holder_status,
            "execution_readiness": link.execution_readiness,
            "platform_validation": link.platform_validation,
            "created_at": link.created_at.isoformat(),
        },
    }


@router.post("/sources/configured")
async def configure_model_source(
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        try:
            source = await ensure_configured_model_source(
                session, settings=request.app.state.settings, space_id=context.space_id
            )
        except ExternalCatalogError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        existing = await session.scalar(select(AuditEvent.event_id).where(
            AuditEvent.event_type == "external_catalog.source.created",
            AuditEvent.subject_id == source.id,
        ))
        if existing is None:
            command = command_for(actor, f"external-model-catalog-source:{source.id}", idempotency_key.strip())
            await append_audit_event_with_outbox(
                session, space_id=context.space_id,
                event_type="external_catalog.source.created",
                subject_type="external_catalog_source", subject_id=source.id,
                result="success", evidence_snapshot={
                    "schema_version": "phase5.12.2/model-catalog-source/v1",
                    "source_code": source.source_code, "resource_kind": "model",
                }, **command.append_kwargs(),
            )
    return _source(source, operator=True)


@router.get("/sources")
async def list_model_sources(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, operator=True)
    rows = (await session.scalars(select(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSource.resource_kind == "model",
    ))).all()
    return {"items": [_source(row, operator=True) for row in rows]}


@router.post("/sources/{source_id}/sync")
async def sync_model_source(
    source_id: UUID, request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        source = await session.get(ExternalCatalogSource, source_id)
        if (
            source is None
            or source.space_id != context.space_id
            or source.resource_kind != "model"
            or not source.enabled
            or source.source_type != "versioned_rest_model_catalog"
        ):
            raise HTTPException(status_code=404, detail="Model catalog source not found.")
        run = await synchronize_model_catalog(
            session, settings=request.app.state.settings, source=source,
            actor=actor, raw_key=idempotency_key.strip(),
        )
        event_type = {
            "succeeded": "external_catalog.sync.succeeded",
            "not_modified": "external_catalog.sync.not_modified",
        }.get(run.status, "external_catalog.sync.failed")
        command = command_for(actor, f"external-model-catalog-sync:{run.id}", idempotency_key.strip())
        await append_audit_event_with_outbox(
            session, space_id=context.space_id, event_type=event_type,
            subject_type="external_catalog_sync_run", subject_id=run.id,
            result="failure" if run.status == "failed" else "success",
            evidence_snapshot={
                "schema_version": "phase5.12.2/model-catalog-sync/v1",
                "source_code": source.source_code, "resource_kind": "model",
                "status": run.status, "received": run.received_record_count,
                "inserted": run.inserted_count, "updated": run.updated_count,
                "stale": run.stale_count, "models_digest": run.models_digest,
                "error_code": run.error_code,
            }, **command.append_kwargs(),
        )
    return _run(run)


@router.get("/models")
async def list_models(
    identity: str = Header(alias="X-Demo-Identity"),
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=100),
    weights_status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    query = select(ExternalModelRecord).join(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSource.resource_kind == "model",
    )
    if q:
        query = query.where(or_(
            ExternalModelRecord.canonical_name.ilike(f"%{q}%"),
            ExternalModelRecord.paper_title.ilike(f"%{q}%"),
        ))
    if category:
        query = query.where(ExternalModelRecord.model_categories.contains([category]))
    if weights_status:
        query = query.where(ExternalModelRecord.weights_status == weights_status)
    total = int(await session.scalar(select(func.count()).select_from(query.subquery())) or 0)
    rows = (
        await session.scalars(
            query.order_by(
                ExternalModelRecord.canonical_name,
                ExternalModelRecord.source_id,
                ExternalModelRecord.id,
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    published_versions_by_record: dict[UUID, UUID] = {}
    if rows:
        publication_rows = (
            await session.execute(
                select(
                    ModelProductExternalSourceLink.external_model_record_id,
                    ModelPublication.model_version_id,
                )
                .join(
                    ModelPublication,
                    ModelPublication.model_version_id
                    == ModelProductExternalSourceLink.model_version_id,
                )
                .where(
                    ModelProductExternalSourceLink.external_model_record_id.in_(
                        [row.id for row in rows]
                    ),
                    ModelPublication.space_id == context.space_id,
                    ModelPublication.status == "active",
                )
            )
        ).all()
        published_versions_by_record = dict(publication_rows)

    items = []
    for row in rows:
        item = _model(row)
        published_version_id = published_versions_by_record.get(row.id)
        item["published_product_version_id"] = (
            str(published_version_id) if published_version_id is not None else None
        )
        items.append(item)
    return {"items": items, "total": total}


@router.get("/models/{record_id}")
async def model_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    row = await session.scalar(select(ExternalModelRecord).join(ExternalCatalogSource).where(
        ExternalModelRecord.id == record_id,
        ExternalCatalogSource.space_id == context.space_id,
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="External model not found.")
    versions = (await session.scalars(select(ExternalModelVersion).where(
        ExternalModelVersion.record_id == row.id
    ).order_by(ExternalModelVersion.observed_at.desc()))).all()
    value = _model(row, detail=True)
    value["versions"] = [{
        "id": str(version.id), "catalog_version": version.catalog_version,
        "record_digest": version.record_digest, "is_current": version.is_current,
        "observed_at": version.observed_at.isoformat(),
        "source_evidence": version.source_evidence,
    } for version in versions]
    return value


@router.get("/sync-runs")
async def list_model_sync_runs(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, operator=True)
    rows = (await session.scalars(select(ExternalCatalogSyncRun).join(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSyncRun.resource_kind == "model",
    ).order_by(ExternalCatalogSyncRun.started_at.desc()))).all()
    return {"items": [_run(row) for row in rows]}


@router.get("/sync-runs/{sync_run_id}")
async def model_sync_run_detail(
    sync_run_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, operator=True)
    row = await session.scalar(select(ExternalCatalogSyncRun).join(ExternalCatalogSource).where(
        ExternalCatalogSyncRun.id == sync_run_id,
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSyncRun.resource_kind == "model",
    ))
    if row is None:
        raise HTTPException(status_code=404, detail="Model catalog sync run not found.")
    return _run(row)


@router.get("/governance/summary")
async def model_governance_summary(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record_ids = select(ExternalModelRecord.id).join(ExternalCatalogSource).where(
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSource.resource_kind == "model",
    )
    total = int(await session.scalar(select(func.count()).select_from(record_ids.subquery())) or 0)
    profile_total = int(await session.scalar(select(func.count()).select_from(
        ExternalModelGovernanceProfile
    ).where(ExternalModelGovernanceProfile.record_id.in_(record_ids))) or 0)
    status_rows = (await session.execute(
        select(ExternalModelGovernanceProfile.primary_status, func.count())
        .where(ExternalModelGovernanceProfile.record_id.in_(record_ids))
        .group_by(ExternalModelGovernanceProfile.primary_status)
    )).all()
    profiles = list((await session.scalars(select(ExternalModelGovernanceProfile).where(
        ExternalModelGovernanceProfile.record_id.in_(record_ids)
    ))).all())
    external_product_total = int(
        await session.scalar(
            select(func.count()).select_from(ModelProductExternalSourceLink)
        )
        or 0
    )
    external_published = int(
        await session.scalar(
            select(func.count())
            .select_from(ModelPublication)
            .join(
                ModelProductExternalSourceLink,
                ModelProductExternalSourceLink.model_version_id
                == ModelPublication.model_version_id,
            )
            .where(ModelPublication.status == "active")
        )
        or 0
    )
    return {
        "total_models": total, "profile_total": profile_total,
        "status_counts": {status: count for status, count in status_rows},
        "weight_public_not_downloaded": int(await session.scalar(select(func.count()).select_from(
            ExternalModelRecord
        ).where(ExternalModelRecord.id.in_(record_ids), ExternalModelRecord.weights_status == "public_available")) or 0),
        "license_unknown": sum(1 for item in profiles if item.license_review_status in {"unknown", "unverified"}),
        "revision_unpinned": sum(1 for item in profiles if item.revision_review_status in {"unknown", "unpinned"}),
        "input_missing": sum(1 for item in profiles if "input_schema" in item.technical_missing_fields),
        "output_missing": sum(1 for item in profiles if "output_schema" in item.technical_missing_fields),
        "preprocessing_missing": sum(1 for item in profiles if "preprocessing" in item.technical_missing_fields),
        "external_model_products": external_product_total,
        "published_metadata_only": external_published,
        "remaining_external_drafts": external_product_total - external_published,
        "materialized_external_models": 0,
        "execution_ready_external_models": 0,
    }


@router.get("/models/{record_id}/governance")
async def model_governance_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record = await session.scalar(select(ExternalModelRecord).join(ExternalCatalogSource).where(
        ExternalModelRecord.id == record_id,
        ExternalCatalogSource.space_id == context.space_id,
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="External model not found.")
    profile = await session.scalar(select(ExternalModelGovernanceProfile).where(
        ExternalModelGovernanceProfile.record_id == record.id
    ))
    reviews = (await session.scalars(select(ExternalModelGovernanceReview).where(
        ExternalModelGovernanceReview.record_id == record.id
    ).order_by(ExternalModelGovernanceReview.reviewed_at.desc()))).all()
    draft_link = await session.scalar(select(ModelProductExternalSourceLink).where(
        ModelProductExternalSourceLink.external_model_record_id == record.id
    ))
    return {
        "model": _model(record, detail=True),
        "profile": _governance_profile(profile) if profile else None,
        "reviews": [_governance_review(item) for item in reviews],
        "model_product_draft": (
            await _model_product_draft_payload(session, draft_link) if draft_link else None
        ),
        "boundaries": {
            "local_weights": "not_downloaded", "execution_image": None,
            "platform_validation": "not_validated", "executable": False,
            "eligible_explanation": "Eligibility permits only a metadata-only ModelProduct draft.",
        },
    }


@router.get("/models/{record_id}/model-product-draft")
async def external_model_product_draft_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record = await session.scalar(select(ExternalModelRecord).join(ExternalCatalogSource).where(
        ExternalModelRecord.id == record_id,
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSource.resource_kind == "model",
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="External model not found.")
    link = await session.scalar(select(ModelProductExternalSourceLink).where(
        ModelProductExternalSourceLink.external_model_record_id == record.id
    ))
    return {
        "exists": link is not None,
        "draft": await _model_product_draft_payload(session, link) if link else None,
    }


@router.post("/models/{record_id}/model-product-draft", status_code=201)
async def create_external_model_product_draft(
    record_id: UUID,
    payload: ExternalModelProductDraftRequest,
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
            source = await _model_source(session, context.space_id)
            record = await session.scalar(select(ExternalModelRecord).where(
                ExternalModelRecord.id == record_id,
                ExternalModelRecord.source_id == source.id,
            ))
            if record is None:
                raise HTTPException(status_code=404, detail="External model not found.")
            version = (
                await session.get(ExternalModelVersion, record.current_version_id)
                if record.current_version_id else None
            )
            profile = await session.scalar(select(ExternalModelGovernanceProfile).where(
                ExternalModelGovernanceProfile.record_id == record.id
            ))
            reviews = list((await session.scalars(select(ExternalModelGovernanceReview).where(
                ExternalModelGovernanceReview.record_id == record.id
            ))).all())
            result = await create_external_model_metadata_draft(
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
        return await _model_product_draft_payload(session, result.link)
    except HTTPException:
        raise
    except ExternalModelProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/models/{record_id}/model-product-publication")
async def external_model_product_publication_detail(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    record = await session.scalar(select(ExternalModelRecord).join(
        ExternalCatalogSource
    ).where(
        ExternalModelRecord.id == record_id,
        ExternalCatalogSource.space_id == context.space_id,
        ExternalCatalogSource.resource_kind == "model",
    ))
    if record is None:
        raise HTTPException(status_code=404, detail="External model not found.")
    link = await session.scalar(select(ModelProductExternalSourceLink).where(
        ModelProductExternalSourceLink.external_model_record_id == record.id
    ))
    if link is None:
        raise HTTPException(status_code=404, detail="External model product not found.")
    return await _model_product_draft_payload(session, link)


@router.post("/models/{record_id}/model-product-publication/submit")
async def submit_external_model_product_publication(
    record_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            context, actor = await _actor(session, identity)
            if identity != "catalog_curator":
                raise HTTPException(
                    status_code=403,
                    detail="Only the independent catalog curator may submit.",
                )
            record = await session.scalar(select(ExternalModelRecord).join(
                ExternalCatalogSource
            ).where(
                ExternalModelRecord.id == record_id,
                ExternalCatalogSource.space_id == context.space_id,
            ))
            if record is None:
                raise HTTPException(status_code=404, detail="External model not found.")
            task, native, external = await submit_external_model_metadata_product(
                session, record_id=record.id, actor=actor, raw_key=raw_key
            )
        return {
            "record_id": str(record_id),
            "review_task_id": str(task.id),
            "status": "under_review",
            "native_event_id": str(native.event_id),
            "external_event_id": str(external.event_id),
        }
    except HTTPException:
        raise
    except ExternalModelProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/models/{record_id}/model-product-publication/return")
async def return_external_model_product_publication(
    record_id: UUID,
    payload: ExternalModelPublicationReviewRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, operator=True)
            task, native, external = await return_external_model_metadata_product(
                session,
                record_id=record_id,
                actor=actor,
                review=payload.model_dump(mode="json"),
                raw_key=raw_key,
            )
        return {
            "record_id": str(record_id),
            "review_task_id": str(task.id),
            "status": "draft",
            "native_event_id": str(native.event_id),
            "external_event_id": str(external.event_id),
        }
    except HTTPException:
        raise
    except ExternalModelProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/models/{record_id}/model-product-publication/approve")
async def approve_external_model_product_publication(
    record_id: UUID,
    payload: ExternalModelPublicationReviewRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    raw_key = (idempotency_key or "").strip()
    if len(raw_key) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, operator=True)
            task, publication, approved, published, external = (
                await approve_and_publish_external_model_metadata_product(
                    session,
                    record_id=record_id,
                    actor=actor,
                    review=payload.model_dump(mode="json"),
                    raw_key=raw_key,
                )
            )
        return {
            "record_id": str(record_id),
            "review_task_id": str(task.id),
            "status": "published",
            "publication_id": str(publication.id),
            "published_at": publication.published_at.isoformat(),
            "approved_event_id": str(approved.event_id),
            "published_event_id": str(published.event_id),
            "external_event_id": str(external.event_id),
        }
    except HTTPException:
        raise
    except ExternalModelProductDraftError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/governance/recalculate")
async def model_governance_recalculate(
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        source = await _model_source(session, context.space_id)
        command = command_for(
            actor,
            f"external-model-governance:{source.id}",
            idempotency_key.strip(),
        )
        existing_event = await session.scalar(
            select(AuditEvent)
            .where(AuditEvent.command_id == command.command_id)
            .order_by(AuditEvent.occurred_at)
        )
        if existing_event is not None:
            evidence = existing_event.evidence_snapshot
            return {
                "created": evidence["profiles_created"],
                "total": evidence["profiles_total"],
            }
        created, total = await recalculate_model_profiles(session)
        event_type = (
            "external_model_catalog.governance.profile.initialized"
            if created else "external_model_catalog.governance.recalculated"
        )
        await append_audit_event_with_outbox(
            session, space_id=context.space_id, event_type=event_type,
            subject_type="external_catalog_source", subject_id=source.id,
            result="success", evidence_snapshot={
                "schema_version": "phase5.12.3A/model-governance/v1",
                "profiles_created": created, "profiles_total": total,
            }, **command.append_kwargs(),
        )
    return {"created": created, "total": total}


@router.post("/models/{record_id}/reviews")
async def create_governance_review(
    record_id: UUID, payload: ModelGovernanceReviewRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        source = await _model_source(session, context.space_id)
        record = await session.scalar(select(ExternalModelRecord).where(
            ExternalModelRecord.id == record_id, ExternalModelRecord.source_id == source.id
        ))
        if record is None:
            raise HTTPException(status_code=404, detail="External model not found.")
        try:
            review, created = await create_model_review(
                session, record=record, dimension=payload.review_dimension,
                decision=payload.decision, decision_payload=payload.decision_payload,
                evidence_type=payload.evidence_type,
                evidence_reference=payload.evidence_reference,
                evidence_note=payload.evidence_note,
                reviewer_user_id=actor.user_id,
                reviewer_organization_id=actor.organization_id,
                raw_key=idempotency_key.strip(),
            )
        except ModelGovernanceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if created:
            event_type = (
                "external_model_catalog.governance.review.superseded"
                if review.supersedes_review_id
                else "external_model_catalog.governance.review.created"
            )
            command = command_for(actor, f"external-model-review:{review.id}", idempotency_key.strip())
            await append_audit_event_with_outbox(
                session, space_id=context.space_id, event_type=event_type,
                subject_type="external_catalog_source", subject_id=source.id,
                result="success", evidence_snapshot={
                    "schema_version": "phase5.12.3A/model-review/v1",
                    "record_id": str(record.id), "dimension": review.review_dimension,
                    "decision": review.decision,
                }, **command.append_kwargs(),
            )
    return _governance_review(review)


@router.get("/model-families")
async def list_model_families(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity)
    source = await _model_source(session, context.space_id)
    profiles = (await session.scalars(select(ExternalModelGovernanceProfile).join(
        ExternalModelRecord, ExternalModelRecord.id == ExternalModelGovernanceProfile.record_id
    ).where(
        ExternalModelRecord.source_id == source.id,
        ExternalModelGovernanceProfile.potential_family_key.is_not(None),
    ))).all()
    groups: dict[str, list[str]] = {}
    for profile in profiles:
        groups.setdefault(str(profile.potential_family_key), []).append(str(profile.record_id))
    resolutions = {
        row.model_family_key: row
        for row in (await session.scalars(select(ExternalModelFamilyResolution))).all()
    }
    return {"items": [
        {
            "family_key": key, "member_record_ids": members,
            "potential": len(members) > 1,
            "resolution_status": resolutions[key].resolution_status if key in resolutions else None,
        }
        for key, members in groups.items() if len(members) > 1 or key in resolutions
    ]}


@router.get("/model-families/{family_key}")
async def model_family_detail(
    family_key: str,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    await _actor(session, identity)
    resolution = await session.scalar(select(ExternalModelFamilyResolution).where(
        ExternalModelFamilyResolution.model_family_key == family_key
    ))
    profiles = (await session.scalars(select(ExternalModelGovernanceProfile).where(
        ExternalModelGovernanceProfile.potential_family_key == family_key
    ))).all()
    if not profiles and resolution is None:
        raise HTTPException(status_code=404, detail="Model family not found.")
    return {
        "family_key": family_key,
        "member_record_ids": [str(item.record_id) for item in profiles],
        "resolution": None if resolution is None else {
            "status": resolution.resolution_status,
            "type": resolution.resolution_type,
            "canonical_record_id": str(resolution.canonical_record_id) if resolution.canonical_record_id else None,
            "rationale": resolution.rationale,
        },
    }


@router.post("/model-families/{family_key}/resolve")
async def resolve_governance_family(
    family_key: str, payload: ModelFamilyResolutionRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    if not idempotency_key or len(idempotency_key.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
    async with session.begin():
        context, actor = await _actor(session, identity, operator=True)
        source = await _model_source(session, context.space_id)
        try:
            resolution, created = await resolve_model_family(
                session, family_key=family_key,
                resolution_status=payload.resolution_status,
                canonical_record_id=payload.canonical_record_id,
                resolution_type=payload.resolution_type,
                member_record_ids=payload.member_record_ids,
                rationale=payload.rationale, resolved_by=actor.user_id,
                raw_key=idempotency_key.strip(),
            )
        except ModelGovernanceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if created:
            command = command_for(actor, f"external-model-family:{resolution.id}", idempotency_key.strip())
            await append_audit_event_with_outbox(
                session, space_id=context.space_id,
                event_type="external_model_catalog.family.resolved",
                subject_type="external_catalog_source", subject_id=source.id,
                result="success", evidence_snapshot={
                    "schema_version": "phase5.12.3A/model-family/v1",
                    "family_key": family_key,
                    "resolution_status": resolution.resolution_status,
                    "member_count": len(resolution.member_record_ids),
                }, **command.append_kwargs(),
            )
    return {"id": str(resolution.id), "family_key": resolution.model_family_key, "status": resolution.resolution_status}

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.demo.phase4 import DemoActor, get_phase4_context
from app.modules.applications.lifecycle import (
    REQUEST_SCHEMA,
    ApplicationLifecycleError,
    create_application_draft,
    decide_application_review,
    registered_data_modality,
    run_compatibility_check,
    submit_application_for_review,
    update_application_draft,
)
from app.modules.applications.demand_assistant import recommend_research_demand
from app.modules.applications.models import (
    Application,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationSnapshot,
)
from app.modules.audit.models import AuditEvent, OutboxMessage
from app.modules.catalog.models import (
    DataProduct,
    DataProductPublication,
    DataProductVersion,
)
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ExternalCatalogSource,
    ExternalDatasetRecord,
    ExternalDatasetVersion,
    ExternalModelRecord,
    ExternalModelVersion,
    ModelProductExternalSourceLink,
)
from app.modules.dataset_model_evidence.models import DatasetModelRelation
from app.modules.contracts.models import Contract
from app.modules.identity.models import Organization, User
from app.modules.marketplace.models import (
    ApplicationModelSelection,
    ModelProduct,
    ModelPublication,
    ModelVersion,
)
from app.modules.marketplace.service_modes import (
    CONTROLLED_COMPUTE,
    service_mode_enabled,
)
from app.modules.marketplace.services import MarketplaceServiceError, require_actor
from app.modules.reviews.models import ReviewDecision, ReviewTask


router = APIRouter(tags=["application-lifecycle"])
DEMO_ROLES = {"space_operator", "data_provider", "model_provider", "data_requester"}
CLIENT_SELECTION_SNAPSHOT_SCHEMA = "phase5.14/client-selection-snapshot/v1"


class ApplicationProfile(BaseModel):
    demand_name: str = Field(min_length=4, max_length=160)
    project_type: Literal[
        "model_external_validation",
        "research_analysis",
        "multicenter_validation",
        "teaching_demo",
        "algorithm_performance_evaluation",
        "other",
    ]
    project_summary: str = Field(min_length=20, max_length=2000)
    project_lead: str = Field(min_length=2, max_length=120)
    contact: str = Field(min_length=2, max_length=160)
    is_demo: bool = True
    purpose_code: Literal[
        "research_analysis",
        "model_validation",
        "external_performance_validation",
        "teaching_demo",
        "commercial_validation",
    ]
    research_purpose: str = Field(min_length=20, max_length=2000)
    use_background: str = Field(min_length=10, max_length=1500)
    expected_value: str = Field(min_length=10, max_length=1500)
    clinical_diagnosis: bool = False
    research_publication: bool = False
    commercial_validation: bool = False
    ethics_or_approval_statement: str = Field(min_length=5, max_length=1500)
    project_reference: str = Field(default="", max_length=160)
    data_minimization: str = Field(min_length=10, max_length=1500)


class DataScopeRequest(BaseModel):
    scope_type: Literal["all_approved_demo_data", "described_subset"]
    subset_description: str = Field(default="", max_length=1000)
    sample_count: int | None = Field(default=None, ge=1, le=1_000_000_000)
    selection_criteria: str = Field(default="", max_length=1000)


class ExecutionRequest(BaseModel):
    run_count: int = Field(ge=1, le=10000)
    valid_days: int = Field(ge=1, le=3650)
    environment_requirements: str = Field(min_length=2, max_length=1000)
    internet_required: bool = False
    fixed_data_version: bool = True
    fixed_model_version: bool = True
    requested_outputs: list[
        Literal["aggregate_metrics", "confusion_matrix", "execution_summary"]
    ] = Field(min_length=1, max_length=3)

    @field_validator("requested_outputs")
    @classmethod
    def unique_outputs(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("requested outputs must be unique")
        return value


class ReviewRequirements(BaseModel):
    hospital_egress_review: bool = True
    model_technical_confirmation: bool = True
    result_review_notes: str = Field(min_length=5, max_length=1000)
    output_recipient: str = Field(min_length=2, max_length=160)


class ApplicantDeclarations(BaseModel):
    no_raw_data_download: bool
    no_model_weight_download: bool
    approved_purpose_only: bool
    accept_multiparty_review: bool
    accept_result_isolation: bool
    accept_full_audit: bool


class ApplicationClientSelectionSnapshot(BaseModel):
    schema_version: Literal[CLIENT_SELECTION_SNAPSHOT_SCHEMA] = (
        CLIENT_SELECTION_SNAPSHOT_SCHEMA
    )
    evidence_kind: Literal["client_selection_snapshot"] = "client_selection_snapshot"
    verification_status: Literal["client_asserted_unverified"] = (
        "client_asserted_unverified"
    )
    authority: Literal["client_assertion_only"] = "client_assertion_only"
    source: Literal["role_assistant"] = "role_assistant"
    selected_by_user: Literal[True] = True
    selected_pair_key: str = Field(min_length=3, max_length=320)
    data_version_id: UUID
    model_version_id: UUID
    rank: int = Field(ge=1, le=100)
    score: int = Field(ge=0, le=100)
    score_max: int = Field(default=100, ge=1, le=100)
    ruleset_version: str = Field(min_length=1, max_length=160)
    pair_schema_version: str = Field(min_length=1, max_length=160)
    stage: Literal[
        "catalog_only",
        "static_candidate",
        "application_candidate",
        "execution_ready",
        "verified_pair",
    ]
    hard_gate_status: Literal["pass", "hold", "fail"]
    reasons: list[str] = Field(default_factory=list, max_length=8)
    limitations: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("reasons", "limitations")
    @classmethod
    def bounded_explanations(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 400 for item in cleaned):
            raise ValueError("recommendation explanations must not exceed 400 characters")
        return cleaned

    @model_validator(mode="after")
    def internally_consistent_claim(self) -> "ApplicationClientSelectionSnapshot":
        expected_pair_key = f"{self.data_version_id}:{self.model_version_id}"
        if self.selected_pair_key != expected_pair_key:
            raise ValueError("client selection pair key must match its version ids")
        if self.score > self.score_max:
            raise ValueError("client selection score must not exceed score_max")
        return self


class ApplicationDraftRequest(BaseModel):
    schema_version: Literal[REQUEST_SCHEMA] = REQUEST_SCHEMA
    data_version_id: UUID
    model_version_id: UUID
    profile: ApplicationProfile
    data_scope: DataScopeRequest
    execution: ExecutionRequest
    review_requirements: ReviewRequirements
    declarations: ApplicantDeclarations
    recommendation_context: ApplicationClientSelectionSnapshot | None = Field(
        default=None,
        description=(
            "Unverified client selection snapshot. Server compatibility results remain "
            "the only authoritative eligibility evidence."
        ),
    )

    @model_validator(mode="after")
    def recommendation_matches_selection(self) -> "ApplicationDraftRequest":
        context = self.recommendation_context
        if context is None:
            return self
        if (
            context.data_version_id != self.data_version_id
            or context.model_version_id != self.model_version_id
        ):
            raise ValueError("client selection snapshot must match the selected versions")
        return self


class ApplicationUpdateRequest(ApplicationDraftRequest):
    expected_row_version: int = Field(ge=1)


class ApplicationSubmitRequest(BaseModel):
    warnings_acknowledged: bool = False


class ResearchDemandRecommendationRequest(BaseModel):
    demand_text: str = Field(min_length=10, max_length=2000)

    @field_validator("demand_text", mode="before")
    @classmethod
    def strip_demand_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class ReviewEvidence(BaseModel):
    completeness_check: str = Field(default="", max_length=500)
    compatibility_conclusion: str = Field(default="", max_length=500)
    purpose_assessment: str = Field(default="", max_length=500)
    output_risk: str = Field(default="", max_length=500)
    risk_level: Literal["low", "medium", "high"] | None = None
    approved_scope: str = Field(default="", max_length=1000)
    max_runs: int | None = Field(default=None, ge=1, le=10000)
    valid_days: int | None = Field(default=None, ge=1, le=3650)
    allowed_outputs: list[str] = Field(default_factory=list, max_length=12)
    prohibited_outputs: list[str] = Field(default_factory=list, max_length=16)
    requires_egress_review: bool | None = None
    allowed_environment: str = Field(default="", max_length=500)
    requires_technical_confirmation: bool | None = None
    additional_conditions: str = Field(default="", max_length=1500)
    requested_materials: str = Field(default="", max_length=1500)


class ReviewDecisionRequest(BaseModel):
    action: Literal["approve", "return", "reject"]
    reason_code: Literal[
        "incomplete_materials",
        "missing_ethics_material",
        "subject_not_eligible",
        "policy_conflict",
        "purpose_not_justified",
        "compliance_requirement_not_met",
        "ethics_requirement_not_met",
        "conflict_of_interest",
        "other",
    ] | None = None
    comment: str = Field(min_length=5, max_length=2000)
    evidence: ReviewEvidence


def _enabled(request: Request) -> None:
    if not request.app.state.settings.demo_api_enabled:
        raise HTTPException(status_code=403, detail="Application command API is disabled")


def _key(value: str | None) -> str:
    if value is None or len(value.strip()) < 8:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")
    return value.strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _is_pathmnist_data_sample(
    product: DataProduct, version: DataProductVersion
) -> bool:
    identity = " ".join(
        str(value)
        for value in (
            product.product_code,
            product.name,
            version.linkage_metadata.get("short_name"),
            version.linkage_metadata.get("resource_identifier"),
        )
        if value
    ).lower()
    return product.is_demo and "pathmnist" in identity


def _is_pathmnist_resnet_sample(
    product: ModelProduct, version: ModelVersion
) -> bool:
    identity = " ".join(
        str(value)
        for value in (
            product.product_code,
            product.name,
            version.entrypoint_id,
        )
        if value
    ).lower()
    return (
        product.is_demo
        and "pathmnist" in identity
        and "resnet" in identity
    )


async def _actor(
    session: AsyncSession,
    identity: str,
    expected: str | None = None,
) -> tuple[Any, DemoActor]:
    if identity not in DEMO_ROLES or (expected is not None and identity != expected):
        raise HTTPException(status_code=403, detail="Demo identity is not authorized")
    context = await get_phase4_context(session)
    actor = context.actors[identity]
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


async def _components(
    session: AsyncSession, application: Application
) -> tuple[ApplicationItem, ApplicationRequestedAction, ApplicationModelSelection]:
    item = await session.scalar(
        select(ApplicationItem).where(ApplicationItem.application_id == application.id)
    )
    action = await session.scalar(
        select(ApplicationRequestedAction).where(
            ApplicationRequestedAction.application_id == application.id
        )
    )
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application.id
        )
    )
    if item is None or action is None or selection is None:
        raise HTTPException(status_code=409, detail="Application aggregate is incomplete")
    return item, action, selection


async def _for_access(
    session: AsyncSession,
    application_id: UUID,
    *,
    identity: str,
    actor: DemoActor,
) -> Application:
    application = await session.get(Application, application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="Application not found")
    selection = await session.scalar(
        select(ApplicationModelSelection).where(
            ApplicationModelSelection.application_id == application.id
        )
    )
    allowed = (
        identity == "space_operator"
        or (
            identity == "data_requester"
            and application.applicant_organization_id == actor.organization_id
        )
        or (
            identity == "data_provider"
            and application.status != "draft"
            and application.provider_organization_id == actor.organization_id
        )
        or (
            identity == "model_provider"
            and application.status != "draft"
            and selection is not None
            and selection.model_provider_organization_id == actor.organization_id
        )
    )
    if not allowed:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


async def _reviews_payload(
    session: AsyncSession, application_id: UUID
) -> list[dict[str, Any]]:
    tasks = list(
        (
            await session.scalars(
                select(ReviewTask)
                .where(ReviewTask.application_id == application_id)
                .order_by(ReviewTask.sequence_no, ReviewTask.created_at)
            )
        ).all()
    )
    result: list[dict[str, Any]] = []
    for task in tasks:
        decision = await session.scalar(
            select(ReviewDecision).where(ReviewDecision.review_task_id == task.id)
        )
        organization = await session.get(Organization, task.assignee_organization_id)
        result.append(
            {
                "task_id": str(task.id),
                "review_type": task.review_type,
                "sequence_no": task.sequence_no,
                "status": task.task_status,
                "organization": organization.display_name if organization else "",
                "organization_id": str(task.assignee_organization_id),
                "row_version": task.row_version,
                "decision": (
                    None
                    if decision is None
                    else {
                        "id": str(decision.id),
                        "decision": decision.decision,
                        "reason_code": decision.reason_code,
                        "comment": decision.comment,
                        "remediation": decision.remediation,
                        "evidence": decision.evidence,
                        "decided_at": _iso(decision.decided_at),
                    }
                ),
            }
        )
    return result


async def _detail_payload(
    session: AsyncSession,
    application: Application,
    *,
    identity: str,
) -> dict[str, Any]:
    item, action, selection = await _components(session, application)
    data_version = await session.get(DataProductVersion, item.data_product_version_id)
    data_product = (
        None
        if data_version is None
        else await session.get(DataProduct, data_version.data_product_id)
    )
    model_version = await session.get(ModelVersion, selection.model_version_id)
    model_product = (
        None
        if model_version is None
        else await session.get(ModelProduct, model_version.model_product_id)
    )
    applicant = await session.get(Organization, application.applicant_organization_id)
    data_provider = await session.get(Organization, application.provider_organization_id)
    model_provider = await session.get(
        Organization, selection.model_provider_organization_id
    )
    contract = await session.scalar(
        select(Contract).where(Contract.application_id == application.id)
    )
    snapshot = await session.scalar(
        select(ApplicationSnapshot).where(
            ApplicationSnapshot.application_id == application.id
        )
    )
    reviews = await _reviews_payload(session, application.id)
    request_document = action.parameters.get("request", {})
    compatibility = action.parameters.get("compatibility")
    allowed_actions: list[str] = []
    if identity == "data_requester" and application.status == "draft":
        allowed_actions = ["edit", "compatibility", "submit"]
    return {
        "application_id": str(application.id),
        "application_number": application.application_number,
        "demand_name": request_document.get("profile", {}).get("demand_name"),
        "status": application.status,
        "row_version": application.row_version,
        "created_at": _iso(application.created_at),
        "updated_at": _iso(application.updated_at),
        "submitted_at": _iso(application.submitted_at),
        "decided_at": _iso(application.decided_at),
        "decision_summary": application.decision_summary,
        "is_demo": application.is_demo,
        "applicant": {
            "id": str(application.applicant_organization_id),
            "name": applicant.display_name if applicant else "",
        },
        "data_provider": {
            "id": str(application.provider_organization_id),
            "name": data_provider.display_name if data_provider else "",
        },
        "model_provider": {
            "id": str(selection.model_provider_organization_id),
            "name": model_provider.display_name if model_provider else "",
        },
        "data_product": {
            "product_id": str(item.data_product_id),
            "version_id": str(item.data_product_version_id),
            "name": data_product.name if data_product else "",
            "version": data_version.version_label if data_version else "",
            "snapshot_digest": item.requested_product_snapshot_digest,
            "policy_digest": item.requested_policy_digest,
        },
        "model_product": {
            "product_id": str(selection.model_product_id),
            "version_id": str(selection.model_version_id),
            "name": model_product.name if model_product else "",
            "version": model_version.version_label if model_version else "",
            "snapshot_digest": selection.model_snapshot_digest,
            "policy_digest": selection.requested_model_policy_digest,
            "registry_digest": selection.registry_digest,
        },
        "request": request_document,
        "client_selection_snapshot_receipt": action.parameters.get(
            "client_selection_snapshot_receipt"
        ),
        "compatibility": compatibility,
        "warning_acknowledged": action.parameters.get(
            "warning_acknowledged", False
        ),
        "snapshot": (
            None
            if snapshot is None
            else {
                "id": str(snapshot.id),
                "digest": snapshot.snapshot_digest,
                "captured_at": _iso(snapshot.captured_at),
            }
        ),
        "reviews": reviews,
        "review_progress": {
            "completed": sum(item["status"] == "decided" for item in reviews),
            "total": len(reviews),
            "current": next(
                (
                    item["review_type"]
                    for item in reviews
                    if item["status"] in {"pending", "claimed"}
                ),
                "digital_contract" if application.status == "approved" else None,
            ),
        },
        "contract": (
            None
            if contract is None
            else {"id": str(contract.id), "number": contract.contract_number}
        ),
        "next_step": (
            "digital_contract"
            if application.status == "approved"
            else "revise_replacement"
            if application.status == "rejected"
            else "review"
            if application.status in {"prechecking", "provider_review"}
            else "complete_and_submit"
        ),
        "allowed_actions": allowed_actions,
        "capability": {
            "hard_isolation": False,
            "raw_data_download": False,
            "model_download": False,
            "compute_job_creation": False,
            "clinical_use": False,
        },
    }


async def _application_options_payload(
    session: AsyncSession, space_id: UUID
) -> dict[str, Any]:
    data_rows = (
        await session.execute(
            select(DataProduct, DataProductVersion, Organization)
            .join(DataProductVersion, DataProductVersion.data_product_id == DataProduct.id)
            .join(
                DataProductPublication,
                DataProductPublication.data_product_version_id == DataProductVersion.id,
            )
            .join(Organization, Organization.id == DataProduct.provider_organization_id)
            .where(
                DataProduct.space_id == space_id,
                DataProductVersion.status == "approved",
                DataProductPublication.status == "active",
                ~select(DataProductExternalSourceLink.id)
                .where(
                    DataProductExternalSourceLink.data_product_version_id
                    == DataProductVersion.id
                )
                .exists(),
            )
            .order_by(DataProduct.name, DataProductVersion.version_no.desc())
        )
    ).all()
    data_rows = [
        row
        for row in data_rows
        if service_mode_enabled(
            "data", row[1].default_policy_template, CONTROLLED_COMPUTE
        )
    ]
    model_rows = (
        await session.execute(
            select(ModelProduct, ModelVersion, Organization)
            .join(ModelVersion, ModelVersion.model_product_id == ModelProduct.id)
            .join(
                ModelPublication,
                ModelPublication.model_version_id == ModelVersion.id,
            )
            .join(Organization, Organization.id == ModelProduct.provider_organization_id)
            .where(
                ModelProduct.space_id == space_id,
                ModelProduct.lifecycle_status == "active",
                ModelVersion.status == "approved",
                ModelPublication.status == "active",
                ~select(ModelProductExternalSourceLink.id)
                .where(
                    ModelProductExternalSourceLink.model_version_id
                    == ModelVersion.id
                )
                .exists(),
            )
            .order_by(ModelProduct.name, ModelVersion.version_no.desc())
        )
    ).all()
    model_rows = [
        row
        for row in model_rows
        if service_mode_enabled(
            "model", row[1].default_policy_template, CONTROLLED_COMPUTE
        )
    ]
    data_items = [
        {
            "candidate_source": "internal_catalog",
            "product_id": str(product.id),
            "version_id": str(version.id),
            "product_code": product.product_code,
            "name": product.name,
            "provider": provider.display_name,
            "provider_organization_id": str(provider.id),
            "disease_domain": product.domain,
            "modality": registered_data_modality(product, version, None),
            "scale": version.scope_metadata,
            "scope_metadata": version.scope_metadata,
            "linkage_metadata": version.linkage_metadata,
            "quality": version.quality_report,
            "policy": version.default_policy_template,
            "profile": version.scope_metadata.get("medtrust_profile", {}),
            "snapshot_digest": version.snapshot_digest,
            "application_eligible": True,
            "materialization_status": "materialized",
            "version": version.version_label,
            "is_demo": product.is_demo,
        }
        for product, version, provider in data_rows
    ]
    model_items = [
        {
            "candidate_source": "internal_catalog",
            "product_id": str(product.id),
            "version_id": str(version.id),
            "product_code": product.product_code,
            "name": product.name,
            "provider": provider.display_name,
            "provider_organization_id": str(provider.id),
            "disease_domain": product.domain,
            "task_type": version.compatibility_metadata.get("task_type"),
            "modality": version.compatibility_metadata.get("modality"),
            "input_schema": version.compatibility_metadata.get("input_schema"),
            "output_schema": version.compatibility_metadata.get("output_schema"),
            "compatibility_metadata": version.compatibility_metadata,
            "policy": version.default_policy_template,
            "license": version.license_metadata,
            "license_metadata": version.license_metadata,
            "profile": version.compatibility_metadata.get("medtrust_profile", {}),
            "entrypoint_id": version.entrypoint_id,
            "snapshot_digest": version.snapshot_digest,
            "application_eligible": True,
            "materialization_status": "materialized",
            "version": version.version_label,
            "is_demo": product.is_demo,
            "non_clinical": version.compatibility_metadata.get("non_clinical", True),
        }
        for product, version, provider in model_rows
    ]
    return {
        "data_products": data_items,
        "model_products": model_items,
        "sample": {
            "data_version_id": next(
                (
                    item["version_id"]
                    for (product, version, _), item in zip(
                        data_rows, data_items, strict=True
                    )
                    if _is_pathmnist_data_sample(product, version)
                ),
                None,
            ),
            "model_version_id": next(
                (
                    item["version_id"]
                    for (product, version, _), item in zip(
                        model_rows, model_items, strict=True
                    )
                    if _is_pathmnist_resnet_sample(product, version)
                ),
                None,
            ),
        },
    }


def _medtrust_profile(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    profile = payload.get("medtrust_profile")
    return dict(profile) if isinstance(profile, dict) else {}


async def _assistant_pair_catalog_payload(
    session: AsyncSession, space_id: UUID
) -> dict[str, Any]:
    data_rows = (
        await session.execute(
            select(ExternalDatasetRecord, ExternalDatasetVersion, ExternalCatalogSource)
            .join(
                ExternalDatasetVersion,
                ExternalDatasetVersion.id == ExternalDatasetRecord.current_version_id,
            )
            .join(ExternalCatalogSource, ExternalCatalogSource.id == ExternalDatasetRecord.source_id)
            .where(
                ExternalCatalogSource.space_id == space_id,
                ExternalCatalogSource.enabled.is_(True),
                ExternalDatasetRecord.status == "active",
                ExternalDatasetVersion.is_current.is_(True),
            )
            .order_by(ExternalDatasetRecord.canonical_name, ExternalDatasetVersion.id)
        )
    ).all()
    model_rows = (
        await session.execute(
            select(ExternalModelRecord, ExternalModelVersion, ExternalCatalogSource)
            .join(
                ExternalModelVersion,
                ExternalModelVersion.id == ExternalModelRecord.current_version_id,
            )
            .join(ExternalCatalogSource, ExternalCatalogSource.id == ExternalModelRecord.source_id)
            .where(
                ExternalCatalogSource.space_id == space_id,
                ExternalCatalogSource.enabled.is_(True),
                ExternalModelRecord.status == "active",
                ExternalModelVersion.is_current.is_(True),
            )
            .order_by(ExternalModelRecord.canonical_name, ExternalModelVersion.id)
        )
    ).all()
    relation_rows = list(
        (
            await session.scalars(
                select(DatasetModelRelation).where(
                    DatasetModelRelation.space_id == space_id,
                    DatasetModelRelation.active.is_(True),
                    DatasetModelRelation.public_visible.is_(True),
                )
            )
        ).all()
    )
    return {
        "data_products": [
            {
                "candidate_source": "external_catalog",
                "product_id": str(record.id),
                "version_id": str(version.id),
                "product_code": f"external:{source.source_code}:{record.external_id}",
                "name": record.display_name_cn or record.display_name_en or record.canonical_name,
                "provider": record.official_source_name or source.display_name,
                "disease_domain": record.disease_areas,
                "modality": record.modalities,
                "task_type": record.task_types,
                "scale": {
                    "sample_count": record.sample_count,
                    "patient_count": record.patient_count,
                    "file_count": record.file_count,
                    "approximate_size_bytes": record.approximate_size_bytes,
                },
                "quality": record.quality_flags,
                "policy": {},
                "license": {
                    "name": record.license_name,
                    "status": record.license_status,
                    "access_level": record.access_level,
                    "registration_required": record.registration_required,
                },
                "profile": _medtrust_profile(version.normalized_payload),
                "record_digest": version.record_digest,
                "version": record.dataset_version or version.catalog_version,
                "application_eligible": False,
                "materialization_status": "not_materialized",
                "non_clinical": True,
            }
            for record, version, source in data_rows
        ],
        "model_products": [
            {
                "candidate_source": "external_catalog",
                "product_id": str(record.id),
                "version_id": str(version.id),
                "product_code": f"external:{source.source_code}:{record.external_model_id}",
                "name": record.display_name_cn or record.display_name_en or record.canonical_name,
                "provider": record.upstream_provider or source.display_name,
                "disease_domain": record.disease_areas,
                "modality": record.modalities,
                "task_type": record.task_types,
                "input_schema": record.input_schema,
                "output_schema": record.output_schema,
                "policy": {},
                "license": {
                    "name": record.license_name,
                    "status": record.license_status,
                    "access_status": record.access_status,
                    "weights_status": record.weights_status,
                },
                "profile": _medtrust_profile(version.normalized_payload),
                "record_digest": version.record_digest,
                "version": record.revision or record.release_tag or version.catalog_version,
                "application_eligible": False,
                "materialization_status": record.execution_status,
                "non_clinical": record.clinical_use_status
                in {"not_assessed", "research_only", "non_clinical"},
            }
            for record, version, source in model_rows
        ],
        "pair_relations": [
            {
                "id": str(relation.id),
                "data_version_id": str(relation.data_product_version_id),
                "model_version_id": str(relation.model_product_version_id),
                "current_status": relation.current_status,
                "strongest_evidence_level": relation.strongest_evidence_level,
                "public_visible": relation.public_visible,
            }
            for relation in relation_rows
        ],
    }


@router.get("/application-options")
async def application_options(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, "data_requester")
    return await _application_options_payload(session, context.space_id)


@router.post("/application-assistant/recommend")
async def recommend_application_demand(
    payload: ResearchDemandRecommendationRequest,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, _ = await _actor(session, identity, "data_requester")
    options = await _application_options_payload(session, context.space_id)
    pair_catalog = await _assistant_pair_catalog_payload(session, context.space_id)
    try:
        return recommend_research_demand(
            payload.demand_text,
            data_products=options["data_products"],
            model_products=options["model_products"],
            pair_data_products=[
                *options["data_products"],
                *pair_catalog["data_products"],
            ],
            pair_model_products=[
                *options["model_products"],
                *pair_catalog["model_products"],
            ],
            pair_relations=pair_catalog["pair_relations"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/application-drafts", status_code=status.HTTP_201_CREATED)
async def create_application(
    payload: ApplicationDraftRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            context, actor = await _actor(session, identity, "data_requester")
            application, event = await create_application_draft(
                session,
                space_id=context.space_id,
                actor=actor,
                document=payload.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {
            "application_id": str(application.id),
            "application_number": application.application_number,
            "status": application.status,
            "row_version": application.row_version,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (ApplicationLifecycleError, MarketplaceServiceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/application-drafts/{application_id}")
async def update_application(
    application_id: UUID,
    payload: ApplicationUpdateRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            application = await _for_access(
                session, application_id, identity=identity, actor=actor
            )
            document = payload.model_dump(mode="json", exclude={"expected_row_version"})
            application, event = await update_application_draft(
                session,
                application,
                actor=actor,
                document=document,
                expected_row_version=payload.expected_row_version,
                raw_key=_key(idempotency_key),
            )
        return {
            "application_id": str(application.id),
            "application_number": application.application_number,
            "status": application.status,
            "row_version": application.row_version,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (ApplicationLifecycleError, MarketplaceServiceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/application-drafts/{application_id}/compatibility")
async def check_application_compatibility(
    application_id: UUID,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            application = await _for_access(
                session, application_id, identity=identity, actor=actor
            )
            report, event = await run_compatibility_check(
                session,
                application,
                actor=actor,
                raw_key=_key(idempotency_key),
            )
        return {**report, "event_id": str(event.event_id)}
    except HTTPException:
        raise
    except (ApplicationLifecycleError, MarketplaceServiceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/application-drafts/{application_id}/submit")
async def submit_application(
    application_id: UUID,
    payload: ApplicationSubmitRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity, "data_requester")
            application = await _for_access(
                session, application_id, identity=identity, actor=actor
            )
            snapshot, event = await submit_application_for_review(
                session,
                application,
                actor=actor,
                warnings_acknowledged=payload.warnings_acknowledged,
                raw_key=_key(idempotency_key),
            )
        return {
            "application_id": str(application.id),
            "status": application.status,
            "snapshot_id": str(snapshot.id),
            "snapshot_digest": snapshot.snapshot_digest,
            "event_id": str(event.event_id),
        }
    except HTTPException:
        raise
    except (ApplicationLifecycleError, MarketplaceServiceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/application-management")
async def application_management(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    query = (
        select(Application)
        .where(Application.space_id == context.space_id)
        .order_by(Application.updated_at.desc(), Application.created_at.desc())
    )
    if identity == "data_requester":
        query = query.where(
            Application.applicant_organization_id == actor.organization_id
        )
    elif identity == "data_provider":
        query = query.where(
            Application.provider_organization_id == actor.organization_id,
            Application.status != "draft",
        )
    elif identity == "model_provider":
        query = query.join(
            ApplicationModelSelection,
            ApplicationModelSelection.application_id == Application.id,
        ).where(
            ApplicationModelSelection.model_provider_organization_id
            == actor.organization_id,
            Application.status != "draft",
        )
    rows = list((await session.scalars(query)).unique().all())
    items = [
        await _detail_payload(session, application, identity=identity)
        for application in rows
    ]
    return {"items": items, "total": len(items)}


@router.get("/application-review-queue")
async def application_review_queue(
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    expected_type = {
        "space_operator": "application_precheck",
        "data_provider": "data_provider_review",
        "model_provider": "model_provider_review",
    }.get(identity)
    if expected_type is None:
        raise HTTPException(status_code=403, detail="Requester has no review queue")
    tasks = list(
        (
            await session.scalars(
                select(ReviewTask)
                .where(
                    ReviewTask.space_id == context.space_id,
                    ReviewTask.review_type == expected_type,
                    ReviewTask.assignee_organization_id == actor.organization_id,
                    ReviewTask.task_status.in_(("pending", "claimed")),
                )
                .order_by(ReviewTask.created_at)
            )
        ).all()
    )
    items = []
    for task in tasks:
        prior_open = int(
            await session.scalar(
                select(func.count(ReviewTask.id)).where(
                    ReviewTask.application_id == task.application_id,
                    ReviewTask.is_required.is_(True),
                    ReviewTask.sequence_no < task.sequence_no,
                    ReviewTask.task_status != "decided",
                )
            )
            or 0
        )
        application = await session.get(Application, task.application_id)
        if application is None:
            continue
        detail = await _detail_payload(session, application, identity=identity)
        items.append(
            {
                "task_id": str(task.id),
                "review_type": task.review_type,
                "sequence_no": task.sequence_no,
                "task_status": task.task_status,
                "actionable": prior_open == 0,
                "application": detail,
            }
        )
    return {"items": items, "total": len(items)}


@router.post("/application-review-tasks/{task_id}/decide")
async def decide_review(
    task_id: UUID,
    payload: ReviewDecisionRequest,
    request: Request,
    identity: str = Header(alias="X-Demo-Identity"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_db_session),
):
    _enabled(request)
    try:
        async with session.begin():
            _, actor = await _actor(session, identity)
            task = await session.get(ReviewTask, task_id)
            if task is None:
                raise HTTPException(status_code=404, detail="Review task not found")
            application = await _for_access(
                session, task.application_id, identity=identity, actor=actor
            )
            decision, replacement, event = await decide_application_review(
                session,
                task,
                application=application,
                actor=actor,
                action=payload.action,
                reason_code=payload.reason_code,
                comment=payload.comment,
                evidence=payload.evidence.model_dump(mode="json"),
                raw_key=_key(idempotency_key),
            )
        return {
            "application_id": str(application.id),
            "application_status": application.status,
            "decision_id": str(decision.id),
            "decision": decision.decision,
            "replacement_application_id": (
                str(replacement.id) if replacement is not None else None
            ),
            "event_id": str(event.event_id),
            "next_step": (
                "digital_contract"
                if application.status == "approved"
                else "replacement_draft"
                if replacement is not None
                else "next_review"
            ),
        }
    except HTTPException:
        raise
    except (ApplicationLifecycleError, MarketplaceServiceError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/applications/{application_id}")
async def application_detail(
    application_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    session: AsyncSession = Depends(get_db_session),
):
    _, actor = await _actor(session, identity)
    application = await _for_access(
        session, application_id, identity=identity, actor=actor
    )
    return await _detail_payload(session, application, identity=identity)


@router.get("/applications/{application_id}/audit-events")
async def application_audit_events(
    application_id: UUID,
    identity: str = Header(alias="X-Demo-Identity"),
    limit: int = Query(default=100, ge=1, le=200),
    session: AsyncSession = Depends(get_db_session),
):
    context, actor = await _actor(session, identity)
    await _for_access(session, application_id, identity=identity, actor=actor)
    events = list(
        (
            await session.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.space_id == context.space_id,
                    or_(
                        (
                            (AuditEvent.subject_type == "application")
                            & (AuditEvent.subject_id == application_id)
                        ),
                        (
                            (AuditEvent.subject_type == "review_decision")
                            & (
                                AuditEvent.evidence_snapshot[
                                    "application_id"
                                ].as_string()
                                == str(application_id)
                            )
                        ),
                    ),
                )
                .order_by(AuditEvent.stream_sequence.desc())
                .limit(limit)
            )
        ).all()
    )
    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": context.space_id},
        )
    ).one()
    items = []
    for event in events:
        organization = (
            None
            if event.actor_organization_id is None
            else await session.get(Organization, event.actor_organization_id)
        )
        user = (
            None
            if event.actor_user_id is None
            else await session.get(User, event.actor_user_id)
        )
        outbox = list(
            (
                await session.scalars(
                    select(OutboxMessage).where(
                        OutboxMessage.audit_event_id == event.event_id
                    )
                )
            ).all()
        )
        items.append(
            {
                "event_id": str(event.event_id),
                "sequence": event.stream_sequence,
                "event_type": event.event_type,
                "result": event.result,
                "occurred_at": _iso(event.occurred_at),
                "actor": user.display_name if user else event.actor_service_code,
                "organization": (
                    organization.display_name if organization else None
                ),
                "subject_type": event.subject_type,
                "subject_id": str(event.subject_id),
                "state_before": event.evidence_snapshot.get("state_before"),
                "state_after": event.evidence_snapshot.get("state_after"),
                "review_task_id": event.evidence_snapshot.get("review_task_id"),
                "compatibility_input_digest": event.evidence_snapshot.get(
                    "compatibility_input_digest"
                )
                or event.evidence_snapshot.get("input_digest"),
                "correlation_id": str(event.correlation_id),
                "previous_hash": event.previous_event_digest,
                "current_hash": event.event_digest,
                "evidence_digest": event.evidence_digest,
                "outbox": [
                    {
                        "message_id": str(message.message_id),
                        "destination": message.destination,
                        "status": message.status,
                    }
                    for message in outbox
                ],
            }
        )
    return {
        "items": items,
        "audit_chain_valid": bool(chain.is_valid),
        "total": len(items),
    }

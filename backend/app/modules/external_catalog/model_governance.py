from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.services import digest_idempotency_key
from app.modules.external_catalog.models import (
    MODEL_LICENSE_REVIEW_STATUSES,
    MODEL_CARD_REVIEW_STATUSES,
    MODEL_CLINICAL_BOUNDARY_STATUSES,
    MODEL_FAMILY_RESOLUTION_TYPES,
    MODEL_PAPER_REVIEW_STATUSES,
    MODEL_REPOSITORY_REVIEW_STATUSES,
    MODEL_REVISION_REVIEW_STATUSES,
    MODEL_SECURITY_REVIEW_STATUSES,
    MODEL_SOURCE_REVIEW_STATUSES,
    MODEL_WEIGHT_REVIEW_STATUSES,
    ExternalModelFamilyResolution,
    ExternalModelGovernanceProfile,
    ExternalModelGovernanceReview,
    ExternalModelRecord,
)

DIMENSION_VALUES = {
    "source": set(MODEL_SOURCE_REVIEW_STATUSES),
    "paper": set(MODEL_PAPER_REVIEW_STATUSES),
    "repository": set(MODEL_REPOSITORY_REVIEW_STATUSES),
    "model_card": set(MODEL_CARD_REVIEW_STATUSES),
    "license": set(MODEL_LICENSE_REVIEW_STATUSES),
    "weights": set(MODEL_WEIGHT_REVIEW_STATUSES),
    "revision": set(MODEL_REVISION_REVIEW_STATUSES),
    "technical_contract": {"accepted", "incomplete", "blocked"},
    "clinical_boundary": set(MODEL_CLINICAL_BOUNDARY_STATUSES),
    "security": set(MODEL_SECURITY_REVIEW_STATUSES),
    "model_family": {"none", "potential", "pending", "resolved", "disputed"},
    "productization": {"approved", "rejected", "unreviewed"},
}

TECHNICAL_FIELDS = (
    ("framework", lambda row: bool(row.framework)),
    ("architecture", lambda row: bool(row.architecture)),
    ("input_schema", lambda row: bool(row.input_schema)),
    ("output_schema", lambda row: bool(row.output_schema)),
    ("preprocessing", lambda row: bool(row.preprocessing_summary)),
    ("training_references", lambda row: bool(row.training_dataset_references)),
    ("evaluation_references", lambda row: bool(row.evaluation_dataset_references)),
    ("revision", lambda row: bool(row.revision or row.commit_sha or row.release_tag)),
    ("weight_file_metadata", lambda row: bool(row.weights_files)),
    ("license", lambda row: bool(row.license_name or row.license_url)),
    ("limitations", lambda row: bool(row.limitations_summary)),
    ("clinical_boundary", lambda row: row.clinical_use_status != "not_assessed"),
)


class ModelGovernanceError(ValueError):
    pass


def _latest(reviews: list[ExternalModelGovernanceReview]) -> dict[str, ExternalModelGovernanceReview]:
    result: dict[str, ExternalModelGovernanceReview] = {}
    for review in sorted(reviews, key=lambda item: item.reviewed_at):
        result[review.review_dimension] = review
    return result


def potential_family_key(record: ExternalModelRecord) -> str | None:
    basis = record.paper_doi or record.code_repository_url or record.model_card_url
    if not basis:
        return None
    normalized = basis.strip().lower().rstrip("/")
    return f"family:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:24]}"


def calculate_model_profile(
    record: ExternalModelRecord,
    reviews: list[ExternalModelGovernanceReview],
    *,
    family_status: str = "none",
) -> dict[str, Any]:
    latest = _latest(reviews)
    decisions = {key: value.decision for key, value in latest.items()}
    payloads = {key: value.decision_payload for key, value in latest.items()}
    source = decisions.get("source", "unreviewed")
    paper = decisions.get("paper", "unreviewed")
    repository = decisions.get("repository", "unreviewed")
    model_card = decisions.get("model_card", "unreviewed")
    license_status = decisions.get("license", "unknown")
    weights = decisions.get("weights", "unknown")
    revision = decisions.get("revision", "unknown")
    clinical = decisions.get("clinical_boundary", "not_assessed")
    security = decisions.get("security", "unreviewed")
    family = decisions.get("model_family", family_status)

    confirmed_fields = set(payloads.get("technical_contract", {}).get("confirmed_fields", []))
    known_technical_fields = {name for name, _ in TECHNICAL_FIELDS}
    confirmed_fields &= known_technical_fields
    missing = [
        name
        for name, present in TECHNICAL_FIELDS
        if not present(record) and name not in confirmed_fields
    ]
    risks: list[str] = []
    flags = {str(value).lower() for value in record.quality_flags}
    for value in (
        "dynamic_code_required", "trust_remote_code_required",
        "custom_native_extension", "external_network_required",
        "dependency_unpinned", "unsupported_framework",
        "weight_integrity_unknown", "executable_code_unreviewed",
    ):
        if value in flags:
            risks.append(value)
    if not record.preprocessing_summary and "preprocessing" not in confirmed_fields:
        risks.append("preprocessing_missing")
    if not record.output_schema and "output_schema" not in confirmed_fields:
        risks.append("label_schema_missing")
    if (
        revision in {"unknown", "unpinned"}
        and not record.commit_sha
        and not record.release_tag
        and not record.revision
    ):
        risks.append("dependency_unpinned")
    weight_payload = payloads.get("weights", {})
    if (
        not record.weights_files
        and not weight_payload.get("files")
        and not weight_payload.get("integrity_metadata_present")
    ):
        risks.append("weight_integrity_unknown")
    resolved_flags = set(payloads.get("security", {}).get("resolved_flags", []))
    risks = [value for value in risks if value not in resolved_flags]
    risks = sorted(set(risks))

    blocking: list[str] = []
    warnings: list[str] = []
    if record.execution_status != "not_materialized":
        blocking.append("unexpected_execution_status")
    if license_status in {"restricted", "redistribution_prohibited"}:
        blocking.append(f"license_{license_status}")
    if weights in {"unavailable", "not_released"}:
        blocking.append(f"weights_{weights}")
    if clinical == "prohibited":
        blocking.append("clinical_use_prohibited")
    if risks:
        warnings.extend(risks)
    if missing:
        warnings.append("technical_contract_incomplete")
    if family in {"potential", "pending", "disputed"}:
        warnings.append("family_resolution_pending")

    productization = decisions.get("productization", "unreviewed")
    traceable = (
        paper == "official_paper_confirmed"
        or source in {"official_source_confirmed", "author_source_confirmed"}
    )
    repository_or_card = (
        repository == "official_repository_confirmed"
        or model_card == "official_model_card_confirmed"
    )
    eligible = (
        source in {"official_source_confirmed", "author_source_confirmed"}
        and traceable and repository_or_card
        and license_status not in {"unknown", "unverified"}
        and weights != "unknown" and revision not in {"unknown", "unpinned"}
        and not any(
            name in missing for name in ("input_schema", "output_schema", "preprocessing")
        )
        and clinical not in {"not_assessed", "unclear", "clinical_claimed_by_source"}
        and security == "cleared" and family not in {"potential", "pending", "disputed"}
        and not risks and not blocking and productization == "approved"
    )

    if productization == "rejected":
        primary = "rejected"
    elif blocking:
        primary = "blocked"
    elif family in {"potential", "pending", "disputed"}:
        primary = "family_resolution_pending"
    elif security in {"review_required", "blocked"} or risks:
        primary = "security_review_required"
    elif eligible:
        primary = "eligible_for_model_draft"
    elif missing:
        primary = "technical_contract_incomplete"
    elif clinical in {"not_assessed", "unclear", "clinical_claimed_by_source"}:
        primary = "clinical_boundary_unclear"
    elif license_status in {"unknown", "unverified"}:
        primary = "needs_license_review"
    elif source == "unreviewed":
        primary = "needs_source_review"
    elif weights == "unknown":
        primary = "needs_weight_review"
    elif revision in {"unknown", "unpinned"}:
        primary = "needs_revision_review"
    elif model_card == "unreviewed":
        primary = "needs_model_card_review"
    elif repository == "unreviewed":
        primary = "needs_repository_review"
    elif paper == "unreviewed":
        primary = "needs_paper_review"
    elif reviews:
        primary = "in_review"
    else:
        primary = "unreviewed"

    return {
        "primary_status": primary,
        "source_review_status": source,
        "paper_review_status": paper,
        "repository_review_status": repository,
        "model_card_review_status": model_card,
        "license_review_status": license_status,
        "weight_review_status": weights,
        "revision_review_status": revision,
        "technical_contract_score": round(100 * (len(TECHNICAL_FIELDS) - len(missing)) / len(TECHNICAL_FIELDS)),
        "technical_missing_fields": missing,
        "clinical_boundary_status": clinical,
        "security_review_status": security,
        "security_risk_flags": risks,
        "model_family_status": family,
        "potential_family_key": potential_family_key(record),
        "productization_eligible": eligible,
        "blocking_reasons": blocking,
        "warning_reasons": sorted(set(warnings)),
    }


async def recalculate_model_profiles(session: AsyncSession) -> tuple[int, int]:
    records = list((await session.scalars(select(ExternalModelRecord))).all())
    profiles = {
        item.record_id: item
        for item in (await session.scalars(select(ExternalModelGovernanceProfile))).all()
    }
    reviews_by_record: dict[UUID, list[ExternalModelGovernanceReview]] = {}
    for review in (await session.scalars(select(ExternalModelGovernanceReview))).all():
        reviews_by_record.setdefault(review.record_id, []).append(review)
    keys: dict[str, int] = {}
    for record in records:
        key = potential_family_key(record)
        if key:
            keys[key] = keys.get(key, 0) + 1
    resolutions = {
        item.model_family_key: item
        for item in (await session.scalars(select(ExternalModelFamilyResolution))).all()
    }
    created = 0
    for record in records:
        key = potential_family_key(record)
        family_status = "none"
        if key and keys.get(key, 0) > 1:
            family_status = "resolved" if key in resolutions else "potential"
        values = calculate_model_profile(
            record, reviews_by_record.get(record.id, []), family_status=family_status
        )
        profile = profiles.get(record.id)
        if profile is None:
            session.add(ExternalModelGovernanceProfile(record_id=record.id, **values))
            created += 1
        else:
            for name, value in values.items():
                setattr(profile, name, value)
    await session.flush()
    return created, len(records)


async def create_model_review(
    session: AsyncSession, *, record: ExternalModelRecord, dimension: str,
    decision: str, decision_payload: dict[str, Any], evidence_type: str,
    evidence_reference: str | None, evidence_note: str,
    reviewer_user_id: UUID, reviewer_organization_id: UUID, raw_key: str,
) -> tuple[ExternalModelGovernanceReview, bool]:
    if dimension not in DIMENSION_VALUES or decision not in DIMENSION_VALUES[dimension]:
        raise ModelGovernanceError("Review dimension or decision is invalid.")
    digest = digest_idempotency_key(raw_key)
    existing = await session.scalar(
        select(ExternalModelGovernanceReview).where(
            ExternalModelGovernanceReview.idempotency_digest == digest
        )
    )
    if existing is not None:
        if (
            existing.record_id != record.id
            or existing.review_dimension != dimension
            or existing.decision != decision
            or existing.decision_payload != decision_payload
            or existing.evidence_type != evidence_type
            or existing.evidence_reference != evidence_reference
            or existing.evidence_note != evidence_note
            or existing.reviewer_user_id != reviewer_user_id
            or existing.reviewer_organization_id != reviewer_organization_id
        ):
            raise ModelGovernanceError("Idempotency-Key maps to another review.")
        return existing, False
    previous = await session.scalar(
        select(ExternalModelGovernanceReview)
        .where(
            ExternalModelGovernanceReview.record_id == record.id,
            ExternalModelGovernanceReview.review_dimension == dimension,
        )
        .order_by(ExternalModelGovernanceReview.reviewed_at.desc())
        .limit(1)
    )
    now = datetime.now(timezone.utc)
    review = ExternalModelGovernanceReview(
        record_id=record.id, review_dimension=dimension,
        previous_value=previous.decision if previous else None,
        decision=decision, decision_payload=decision_payload,
        evidence_type=evidence_type, evidence_reference=evidence_reference,
        evidence_note=evidence_note, reviewer_user_id=reviewer_user_id,
        reviewer_organization_id=reviewer_organization_id, reviewed_at=now,
        source_record_digest=record.raw_record_digest,
        supersedes_review_id=previous.id if previous else None,
        idempotency_digest=digest,
    )
    session.add(review)
    await session.flush()
    await recalculate_model_profiles(session)
    return review, True


async def resolve_model_family(
    session: AsyncSession, *, family_key: str, resolution_status: str,
    canonical_record_id: UUID | None, resolution_type: str,
    member_record_ids: list[UUID], rationale: str, resolved_by: UUID,
    raw_key: str,
) -> tuple[ExternalModelFamilyResolution, bool]:
    if resolution_status not in {"resolved", "unresolved", "disputed"}:
        raise ModelGovernanceError("Family resolution status is invalid.")
    if resolution_type not in MODEL_FAMILY_RESOLUTION_TYPES:
        raise ModelGovernanceError("Family resolution type is invalid.")
    digest = digest_idempotency_key(raw_key)
    existing = await session.scalar(
        select(ExternalModelFamilyResolution).where(
            ExternalModelFamilyResolution.idempotency_digest == digest
        )
    )
    if existing is not None:
        if (
            existing.model_family_key != family_key
            or existing.resolution_status != resolution_status
            or existing.canonical_record_id != canonical_record_id
            or existing.resolution_type != resolution_type
            or set(existing.member_record_ids) != {str(item) for item in member_record_ids}
            or existing.rationale != rationale
            or existing.resolved_by != resolved_by
        ):
            raise ModelGovernanceError("Idempotency-Key maps to another family resolution.")
        return existing, False
    if not member_record_ids:
        raise ModelGovernanceError("Family members are required.")
    if canonical_record_id is not None and canonical_record_id not in member_record_ids:
        raise ModelGovernanceError("Canonical record must be a family member.")
    known = set((await session.scalars(
        select(ExternalModelRecord.id).where(ExternalModelRecord.id.in_(member_record_ids))
    )).all())
    if known != set(member_record_ids):
        raise ModelGovernanceError("Family member is missing.")
    row = ExternalModelFamilyResolution(
        model_family_key=family_key, resolution_status=resolution_status,
        canonical_record_id=canonical_record_id, resolution_type=resolution_type,
        member_record_ids=[str(item) for item in member_record_ids],
        rationale=rationale, resolved_by=resolved_by,
        resolved_at=datetime.now(timezone.utc), idempotency_digest=digest,
    )
    session.add(row)
    await session.flush()
    await recalculate_model_profiles(session)
    return row, True

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit.services import digest_idempotency_key
from app.modules.external_catalog.models import (
    ACCESS_REVIEW_STATUSES,
    DUPLICATE_REVIEW_STATUSES,
    LICENSE_REVIEW_STATUSES,
    SOURCE_REVIEW_STATUSES,
    ExternalDatasetDuplicateResolution,
    ExternalDatasetGovernanceProfile,
    ExternalDatasetGovernanceReview,
    ExternalDatasetRecord,
)

DIMENSION_VALUES = {
    "source": set(SOURCE_REVIEW_STATUSES),
    "license": set(LICENSE_REVIEW_STATUSES),
    "access": set(ACCESS_REVIEW_STATUSES),
    "duplicate": set(DUPLICATE_REVIEW_STATUSES),
    "metadata": {"accepted", "incomplete", "blocked"},
    "link": {"missing", "malformed", "legacy_http", "syntactically_valid_https", "unchecked"},
    "productization": {"approved", "rejected", "unreviewed"},
}

METADATA_FIELDS = (
    ("canonical_name", lambda r: bool(r.canonical_name)),
    ("official_source", lambda r: bool(r.official_source_name or r.official_source_url)),
    ("modality", lambda r: bool(r.modalities)),
    ("disease_or_organ", lambda r: bool(r.disease_areas or r.organs)),
    ("dataset_version", lambda r: bool(r.dataset_version)),
    ("license", lambda r: r.license_status != "unknown"),
    ("access_level", lambda r: r.access_level != "unknown"),
    ("sample_count", lambda r: r.sample_count is not None),
    ("patient_count", lambda r: r.patient_count is not None),
    ("approximate_size", lambda r: r.approximate_size_bytes is not None),
    ("data_format", lambda r: bool(r.data_formats)),
)


class GovernanceError(ValueError):
    pass


def _latest_reviews(
    reviews: list[ExternalDatasetGovernanceReview],
) -> dict[str, ExternalDatasetGovernanceReview]:
    values: dict[str, ExternalDatasetGovernanceReview] = {}
    for review in sorted(reviews, key=lambda row: row.reviewed_at):
        values[review.review_dimension] = review
    return values


def _latest_decisions(reviews: list[ExternalDatasetGovernanceReview]) -> dict[str, str]:
    return {
        dimension: review.decision
        for dimension, review in _latest_reviews(reviews).items()
    }


def _verified_official_source_url(
    record: ExternalDatasetRecord,
    latest: dict[str, ExternalDatasetGovernanceReview],
) -> str | None:
    if record.official_source_url:
        return record.official_source_url
    review = latest.get("source")
    if review is None or review.decision != "official_source_confirmed":
        return None
    payload_url = review.decision_payload.get("official_source_url")
    if isinstance(payload_url, str) and payload_url.strip():
        return payload_url.strip()
    return review.evidence_reference


def _missing_fields(
    record: ExternalDatasetRecord,
    latest: dict[str, ExternalDatasetGovernanceReview],
) -> list[str]:
    decisions = {dimension: review.decision for dimension, review in latest.items()}
    missing: list[str] = []
    for name, present in METADATA_FIELDS:
        if name == "official_source":
            value = bool(
                record.official_source_name
                or _verified_official_source_url(record, latest)
            )
        elif name == "license":
            value = decisions.get("license", record.license_status) not in {
                "unknown",
                "unverified",
            }
        elif name == "access_level":
            value = decisions.get("access", record.access_level) != "unknown"
        else:
            value = present(record)
        if not value:
            missing.append(name)
    return missing


def calculate_profile(
    record: ExternalDatasetRecord,
    reviews: list[ExternalDatasetGovernanceReview],
    *,
    duplicate_resolved: bool = False,
) -> dict[str, Any]:
    latest = _latest_reviews(reviews)
    decisions = {
        dimension: review.decision for dimension, review in latest.items()
    }
    source = decisions.get("source", "unreviewed")
    license_status = decisions.get("license", "unknown")
    access = decisions.get("access", "unknown")
    duplicate = decisions.get(
        "duplicate",
        "duplicate_unresolved" if record.duplicate_group_id and not duplicate_resolved else "not_duplicate",
    )
    official_source_url = _verified_official_source_url(record, latest)
    missing = _missing_fields(record, latest)
    blocking: list[str] = []
    warnings: list[str] = []
    if record.link_status in {"missing", "malformed"}:
        blocking.append(f"link_{record.link_status}")
    if source in {"source_missing", "source_malformed", "source_disputed"}:
        blocking.append(source)
    if license_status == "redistribution_prohibited":
        blocking.append("redistribution_prohibited")
    if access == "unavailable":
        blocking.append("access_unavailable")
    if record.link_status == "legacy_http":
        warnings.append("legacy_http")
    if missing:
        warnings.append("metadata_incomplete")
    if duplicate == "duplicate_unresolved":
        warnings.append("duplicate_unresolved")

    productization_review = decisions.get("productization", "unreviewed")
    eligible = (
        source in {"official_source_confirmed", "aggregator_only"}
        and license_status not in {"unknown", "unverified"}
        and access != "unknown"
        and bool(record.canonical_name)
        and bool(record.modalities)
        and bool(official_source_url)
        and duplicate != "duplicate_unresolved"
        and not blocking
        and productization_review == "approved"
    )
    if decisions.get("productization") == "rejected":
        primary = "rejected"
    elif blocking:
        primary = "blocked"
    elif duplicate == "duplicate_unresolved":
        primary = "duplicate_pending"
    elif reviews and productization_review == "unreviewed":
        primary = "in_review"
    elif license_status in {"unknown", "unverified"}:
        primary = "needs_license_review"
    elif source == "unreviewed":
        primary = "needs_source_review"
    elif access == "unknown":
        primary = "needs_access_review"
    elif eligible:
        primary = "eligible_for_draft"
    elif missing:
        primary = "metadata_incomplete"
    else:
        primary = "unreviewed"
    return {
        "primary_status": primary,
        "source_review_status": source,
        "license_review_status": license_status,
        "access_review_status": access,
        "metadata_completeness_score": round(
            100 * (len(METADATA_FIELDS) - len(missing)) / len(METADATA_FIELDS)
        ),
        "metadata_missing_fields": missing,
        "link_review_status": decisions.get("link", record.link_status),
        "duplicate_review_status": duplicate,
        "productization_eligible": eligible,
        "blocking_reasons": blocking,
        "warning_reasons": warnings,
    }


async def recalculate_profiles(session: AsyncSession) -> tuple[int, int]:
    records = list((await session.scalars(select(ExternalDatasetRecord))).all())
    profiles = {
        row.record_id: row
        for row in (await session.scalars(select(ExternalDatasetGovernanceProfile))).all()
    }
    reviews_by_record: dict[UUID, list[ExternalDatasetGovernanceReview]] = {}
    for review in (await session.scalars(select(ExternalDatasetGovernanceReview))).all():
        reviews_by_record.setdefault(review.record_id, []).append(review)
    resolved_groups = set(
        (await session.scalars(select(ExternalDatasetDuplicateResolution.duplicate_group_id))).all()
    )
    created = 0
    for record in records:
        values = calculate_profile(
            record,
            reviews_by_record.get(record.id, []),
            duplicate_resolved=bool(record.duplicate_group_id in resolved_groups),
        )
        profile = profiles.get(record.id)
        if profile is None:
            profile = ExternalDatasetGovernanceProfile(record_id=record.id, **values)
            session.add(profile)
            created += 1
        else:
            for key, value in values.items():
                setattr(profile, key, value)
    await session.flush()
    return created, len(records)


async def create_review(
    session: AsyncSession,
    *,
    record: ExternalDatasetRecord,
    dimension: str,
    decision: str,
    decision_payload: dict[str, Any] | None,
    evidence_type: str,
    evidence_reference: str | None,
    evidence_note: str,
    reviewer_user_id: UUID,
    reviewer_organization_id: UUID,
    raw_key: str,
) -> ExternalDatasetGovernanceReview:
    if dimension not in DIMENSION_VALUES or decision not in DIMENSION_VALUES[dimension]:
        raise GovernanceError("Review dimension or decision is invalid.")
    if dimension == "source" and decision == "official_source_confirmed":
        payload_url = (decision_payload or {}).get("official_source_url")
        official_url = payload_url or evidence_reference
        parsed = urlparse(official_url or "")
        if parsed.scheme != "https" or not parsed.netloc:
            raise GovernanceError(
                "Confirmed official sources require an HTTPS official source URL."
            )
    digest = digest_idempotency_key(raw_key)
    existing = await session.scalar(
        select(ExternalDatasetGovernanceReview).where(
            ExternalDatasetGovernanceReview.idempotency_digest == digest
        )
    )
    if existing:
        if (
            existing.record_id != record.id
            or existing.review_dimension != dimension
            or existing.decision != decision
            or existing.decision_payload != (decision_payload or {})
            or existing.evidence_type != evidence_type
            or existing.evidence_reference != evidence_reference
            or existing.evidence_note != evidence_note.strip()
        ):
            raise GovernanceError("Idempotency key maps to a different review.")
        return existing
    previous = await session.scalar(
        select(ExternalDatasetGovernanceReview)
        .where(
            ExternalDatasetGovernanceReview.record_id == record.id,
            ExternalDatasetGovernanceReview.review_dimension == dimension,
        )
        .order_by(ExternalDatasetGovernanceReview.reviewed_at.desc())
        .limit(1)
    )
    review = ExternalDatasetGovernanceReview(
        record_id=record.id,
        review_dimension=dimension,
        previous_value=previous.decision if previous else None,
        decision=decision,
        decision_payload=decision_payload or {},
        evidence_type=evidence_type,
        evidence_reference=evidence_reference,
        evidence_note=evidence_note.strip(),
        reviewer_user_id=reviewer_user_id,
        reviewer_organization_id=reviewer_organization_id,
        reviewed_at=datetime.now(timezone.utc),
        source_record_digest=record.raw_record_digest,
        supersedes_review_id=previous.id if previous else None,
        idempotency_digest=digest,
    )
    session.add(review)
    await session.flush()
    await recalculate_profiles(session)
    profile = await session.scalar(
        select(ExternalDatasetGovernanceProfile).where(
            ExternalDatasetGovernanceProfile.record_id == record.id
        )
    )
    if profile:
        profile.last_reviewed_at = review.reviewed_at
        profile.last_reviewed_by = reviewer_user_id
    return review

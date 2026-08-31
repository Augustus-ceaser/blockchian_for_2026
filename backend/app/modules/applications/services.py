from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import NO_VALUE

from app.modules.applications.models import (
    Application,
    ApplicationAttachment,
    ApplicationItem,
    ApplicationRequestedAction,
    ApplicationRequestedOutputType,
    ApplicationSnapshot,
)
from app.modules.catalog.models import DataProductPublication, DataProductVersion
from app.modules.external_catalog.eligibility import (
    ExternalDataProductEligibilityError,
    ExternalModelProductEligibilityError,
    require_materialized_data_product,
    require_materialized_model_product,
)


class ApplicationInvariantError(ValueError):
    """Raised when an Application mutation violates a frozen invariant."""


CONTENT_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
OUTPUT_REVIEW_BASELINE = {
    "aggregate_statistics": False,
    "model_artifact": True,
    "feature_dataset": True,
    "risk_scoring_model": True,
}


def _changed_columns(target: object) -> set[str]:
    state = inspect(target)
    return {
        attribute.key
        for attribute in state.mapper.column_attrs
        if state.attrs[attribute.key].history.has_changes()
    }


def _old_value(target: object, attribute_name: str, current: str) -> str:
    history = inspect(target).attrs[attribute_name].history
    return history.deleted[0] if history.deleted else current


def _application_for_component(
    session: Session,
    component: ApplicationItem
    | ApplicationRequestedAction
    | ApplicationRequestedOutputType
    | ApplicationAttachment,
) -> Application | None:
    loaded_application = inspect(component).attrs.application.loaded_value
    if loaded_application is not NO_VALUE:
        return loaded_application
    if component.application_id is None:
        return None
    return session.get(Application, component.application_id)


def _application_for_snapshot(
    session: Session, snapshot: ApplicationSnapshot
) -> Application | None:
    loaded_application = inspect(snapshot).attrs.application.loaded_value
    if loaded_application is not NO_VALUE:
        return loaded_application
    if snapshot.application_id is None:
        return None
    return session.get(Application, snapshot.application_id)


def _guard_application_update(application: Application) -> None:
    changed = _changed_columns(application)
    if not changed:
        return

    old_status = _old_value(application, "status", application.status)
    allowed_transitions = {
        "draft": {"draft", "submitted", "withdrawn"},
        "submitted": {"prechecking", "withdrawn"},
        "prechecking": {"provider_review", "rejected", "withdrawn"},
        "provider_review": {"approved", "rejected", "withdrawn"},
        "approved": set(),
        "rejected": set(),
        "withdrawn": set(),
    }
    if application.status not in allowed_transitions[old_status]:
        raise ApplicationInvariantError(
            f"invalid application transition: {old_status} -> {application.status}"
        )

    if old_status != "draft":
        allowed = {
            "status",
            "submitted_at",
            "decided_at",
            "withdrawn_at",
            "decision_summary",
            "updated_at",
            "row_version",
        }
        if changed - allowed:
            raise ApplicationInvariantError(
                "submitted application content is immutable"
            )


def _require_draft_component(
    session: Session,
    component: ApplicationItem
    | ApplicationRequestedAction
    | ApplicationRequestedOutputType
    | ApplicationAttachment,
) -> None:
    parent = _application_for_component(session, component)
    if parent is not None and parent.status != "draft":
        raise ApplicationInvariantError(
            "application components can only change while the application is draft"
        )


def _validate_action(action: ApplicationRequestedAction) -> None:
    if action.parameters is None:
        action.parameters = {"schema_version": "1.0"}
    if not isinstance(action.parameters, dict):
        raise ApplicationInvariantError("action parameters must be an object")
    schema_version = action.parameters.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version:
        raise ApplicationInvariantError(
            "action parameters require a string schema_version"
        )
    try:
        json.dumps(action.parameters, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ApplicationInvariantError(
            "action parameters must be canonical JSON values"
        ) from error


def _validate_output(output: ApplicationRequestedOutputType) -> None:
    if not isinstance(output.requires_manual_review, bool):
        raise ApplicationInvariantError(
            "requires_manual_review must be derived as a boolean"
        )


def _validate_new_attachment(attachment: ApplicationAttachment) -> None:
    if attachment.scan_status not in (None, "pending"):
        raise ApplicationInvariantError("new attachment must start as pending")
    if not attachment.display_name or not attachment.display_name.strip():
        raise ApplicationInvariantError("attachment display_name is required")
    if not attachment.storage_ref or not attachment.storage_ref.strip():
        raise ApplicationInvariantError("attachment storage_ref is required")
    if not CONTENT_DIGEST_PATTERN.fullmatch(attachment.content_digest or ""):
        raise ApplicationInvariantError("attachment content_digest must be sha256")
    if attachment.size_bytes is None or attachment.size_bytes < 0:
        raise ApplicationInvariantError("attachment size_bytes must be nonnegative")


def _guard_attachment_update(attachment: ApplicationAttachment) -> None:
    changed = _changed_columns(attachment)
    if changed - {"scan_status"}:
        raise ApplicationInvariantError(
            "attachment content is immutable; replace the draft row instead"
        )
    if "scan_status" not in changed:
        return
    old_status = _old_value(attachment, "scan_status", attachment.scan_status)
    allowed = {
        "pending": {"pending", "clean", "rejected"},
        "clean": {"clean"},
        "rejected": {"rejected"},
    }
    if attachment.scan_status not in allowed.get(old_status, set()):
        raise ApplicationInvariantError(
            f"invalid attachment scan transition: {old_status} -> "
            f"{attachment.scan_status}"
        )


@event.listens_for(Session, "before_flush")
def guard_application_mutations(
    session: Session, _flush_context: object, _instances: object
) -> None:
    for target in session.new:
        if isinstance(target, Application) and target.status not in (None, "draft"):
            raise ApplicationInvariantError("new application must start as draft")
        if isinstance(target, ApplicationItem):
            _require_draft_component(session, target)
            if not isinstance(target.requested_scope, dict):
                raise ApplicationInvariantError("requested_scope must be an object")
        if isinstance(target, ApplicationRequestedAction):
            _require_draft_component(session, target)
            _validate_action(target)
        if isinstance(target, ApplicationRequestedOutputType):
            _require_draft_component(session, target)
            _validate_output(target)
        if isinstance(target, ApplicationAttachment):
            _require_draft_component(session, target)
            _validate_new_attachment(target)
        if isinstance(target, ApplicationSnapshot):
            parent = _application_for_snapshot(session, target)
            if parent is None or parent.status != "submitted":
                raise ApplicationInvariantError(
                    "application snapshot can only be created during submission"
                )
            if not isinstance(target.manifest, dict):
                raise ApplicationInvariantError("snapshot manifest must be an object")

    for target in session.dirty:
        if isinstance(target, Application):
            _guard_application_update(target)
        elif isinstance(target, ApplicationItem):
            _require_draft_component(session, target)
            if not isinstance(target.requested_scope, dict):
                raise ApplicationInvariantError("requested_scope must be an object")
        elif isinstance(target, ApplicationRequestedAction):
            _require_draft_component(session, target)
            _validate_action(target)
        elif isinstance(target, ApplicationRequestedOutputType):
            _require_draft_component(session, target)
            _validate_output(target)
        elif isinstance(target, ApplicationAttachment):
            _require_draft_component(session, target)
            _guard_attachment_update(target)
        elif isinstance(target, ApplicationSnapshot):
            raise ApplicationInvariantError("application snapshot is immutable")

    for target in session.deleted:
        if isinstance(target, Application) and target.status != "draft":
            raise ApplicationInvariantError("only a draft application can be deleted")
        if isinstance(
            target,
            (
                ApplicationItem,
                ApplicationRequestedAction,
                ApplicationRequestedOutputType,
                ApplicationAttachment,
            ),
        ):
            _require_draft_component(session, target)
        if isinstance(target, ApplicationSnapshot):
            raise ApplicationInvariantError("application snapshot is immutable")


def _canonical_snapshot_manifest(
    application: Application,
    items: list[ApplicationItem],
    actions: list[ApplicationRequestedAction],
    outputs: list[ApplicationRequestedOutputType],
    attachments: list[ApplicationAttachment],
    review_rule_digests: dict[str, str],
    model_selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": "1.0",
        "application": {
            "id": str(application.id),
            "space_id": str(application.space_id),
            "application_number": application.application_number,
            "applicant_organization_id": str(application.applicant_organization_id),
            "provider_organization_id": str(application.provider_organization_id),
            "purpose": application.purpose,
            "legal_or_ethics_basis": application.legal_or_ethics_basis,
            "algorithm": {
                "name": application.algorithm_name,
                "version": application.algorithm_version,
                "digest": application.algorithm_digest,
            },
            "requested_duration_seconds": application.requested_duration_seconds,
            "requested_run_limit": application.requested_run_limit,
        },
        "items": [
            {
                "position_no": item.position_no,
                "data_product_id": str(item.data_product_id),
                "data_product_version_id": str(item.data_product_version_id),
                "product_snapshot_digest": item.requested_product_snapshot_digest,
                "policy_digest": item.requested_policy_digest,
                "requested_scope": item.requested_scope,
            }
            for item in sorted(items, key=lambda current: current.position_no)
        ],
        "requested_actions": [
            {
                "action_code": action.action_code,
                "parameters": action.parameters,
            }
            for action in sorted(actions, key=lambda current: current.action_code)
        ],
        "requested_output_types": [
            {
                "output_type": output.output_type,
                "requires_manual_review": output.requires_manual_review,
                "review_rule_digest": review_rule_digests[output.output_type],
            }
            for output in sorted(outputs, key=lambda current: current.output_type)
        ],
        "attachments": [
            {
                "attachment_type": attachment.attachment_type,
                "display_name": attachment.display_name,
                "content_digest": attachment.content_digest,
                "size_bytes": attachment.size_bytes,
                "scan_status": attachment.scan_status,
            }
            for attachment in sorted(
                attachments,
                key=lambda current: (
                    current.attachment_type,
                    current.content_digest,
                ),
            )
        ],
    }
    if model_selection is not None:
        manifest["model_selection"] = model_selection
    return manifest


def _snapshot_digest(manifest: dict[str, Any]) -> str:
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _derive_output_review(output_type: str) -> tuple[bool, str]:
    try:
        requires_manual_review = OUTPUT_REVIEW_BASELINE[output_type]
    except KeyError as error:
        raise ApplicationInvariantError(
            f"unknown requested output type: {output_type}"
        ) from error
    rule_document = {
        "schema_version": "1.0",
        "rule_set": "application_output_review_baseline_v1",
        "output_type": output_type,
        "requires_manual_review": requires_manual_review,
    }
    return requires_manual_review, _snapshot_digest(rule_document)


async def submit_application(
    session: AsyncSession,
    application: Application,
    *,
    submitted_by: UUID,
) -> ApplicationSnapshot:
    """Freeze the complete V1 Application aggregate and move it to submitted."""

    if application.status != "draft":
        raise ApplicationInvariantError("only a draft application can be submitted")

    await session.flush()
    items = list(
        (
            await session.scalars(
                select(ApplicationItem)
                .where(ApplicationItem.application_id == application.id)
                .order_by(ApplicationItem.position_no)
            )
        ).all()
    )
    if not items:
        raise ApplicationInvariantError("application requires at least one item")

    actions = list(
        (
            await session.scalars(
                select(ApplicationRequestedAction)
                .where(ApplicationRequestedAction.application_id == application.id)
                .order_by(ApplicationRequestedAction.action_code)
            )
        ).all()
    )
    if not actions:
        raise ApplicationInvariantError("application requires at least one action")

    outputs = list(
        (
            await session.scalars(
                select(ApplicationRequestedOutputType)
                .where(
                    ApplicationRequestedOutputType.application_id == application.id
                )
                .order_by(ApplicationRequestedOutputType.output_type)
            )
        ).all()
    )
    if not outputs:
        raise ApplicationInvariantError(
            "application requires at least one requested output type"
        )

    attachments = list(
        (
            await session.scalars(
                select(ApplicationAttachment)
                .where(ApplicationAttachment.application_id == application.id)
                .order_by(
                    ApplicationAttachment.attachment_type,
                    ApplicationAttachment.content_digest,
                )
            )
        ).all()
    )
    if any(attachment.scan_status != "clean" for attachment in attachments):
        raise ApplicationInvariantError(
            "all submitted attachments must have a clean scan status"
        )

    review_rule_digests: dict[str, str] = {}
    for output in outputs:
        requires_manual_review, rule_digest = _derive_output_review(
            output.output_type
        )
        output.requires_manual_review = requires_manual_review
        review_rule_digests[output.output_type] = rule_digest
    await session.flush()

    for item in items:
        try:
            await require_materialized_data_product(
                session, item.data_product_version_id
            )
        except ExternalDataProductEligibilityError as exc:
            raise ApplicationInvariantError(str(exc)) from exc
        version = await session.get(DataProductVersion, item.data_product_version_id)
        if version is None or version.data_product_id != item.data_product_id:
            raise ApplicationInvariantError("application item version does not exist")
        if version.status != "approved":
            raise ApplicationInvariantError("application item version must be approved")
        if version.snapshot_digest != item.requested_product_snapshot_digest:
            raise ApplicationInvariantError("product snapshot digest does not match")
        if version.default_policy_digest != item.requested_policy_digest:
            raise ApplicationInvariantError("product policy digest does not match")
        publication_id = await session.scalar(
            select(DataProductPublication.id).where(
                DataProductPublication.data_product_id == item.data_product_id,
                DataProductPublication.data_product_version_id
                == item.data_product_version_id,
                DataProductPublication.status == "active",
            )
        )
        if publication_id is None:
            raise ApplicationInvariantError(
                "application item version requires an active publication"
            )

    # Phase 4 extends, rather than replaces, the legacy Application aggregate.
    # A demand may carry one immutable model selection.  Legacy applications
    # remain valid when the extension row is absent.
    from app.db.base import Base

    phase4_loaded = (
        session.bind is not None
        and session.bind.dialect.name == "postgresql"
    ) or f"medtrust.application_model_selections" in Base.metadata.tables
    selection = None
    if phase4_loaded:
        from app.modules.marketplace.models import ApplicationModelSelection

        selection = await session.scalar(
            select(ApplicationModelSelection).where(
                ApplicationModelSelection.application_id == application.id
            )
        )
    model_selection_manifest: dict[str, Any] | None = None
    if selection is not None:
        from app.modules.marketplace.models import (
            ModelProduct,
            ModelPublication,
            ModelVersion,
        )
        try:
            await require_materialized_model_product(session, selection.model_version_id)
        except ExternalModelProductEligibilityError as exc:
            raise ApplicationInvariantError(str(exc)) from exc
        model_version = await session.get(ModelVersion, selection.model_version_id)
        model_product = await session.get(ModelProduct, selection.model_product_id)
        model_publication = await session.scalar(
            select(ModelPublication).where(
                ModelPublication.model_product_id == selection.model_product_id,
                ModelPublication.model_version_id == selection.model_version_id,
                ModelPublication.status == "active",
            )
        )
        if (
            model_version is None
            or model_product is None
            or model_publication is None
            or model_version.status != "approved"
            or model_product.lifecycle_status != "active"
            or model_version.snapshot_digest != selection.model_snapshot_digest
            or model_version.default_policy_digest
            != selection.requested_model_policy_digest
            or model_version.registry_digest != selection.registry_digest
            or model_product.provider_organization_id
            != selection.model_provider_organization_id
        ):
            raise ApplicationInvariantError(
                "selected model version is not an active immutable publication"
            )
        if application.algorithm_digest != model_version.model_digest:
            raise ApplicationInvariantError(
                "application algorithm digest must match selected model version"
            )
        model_selection_manifest = {
            "model_product_id": str(selection.model_product_id),
            "model_version_id": str(selection.model_version_id),
            "model_provider_organization_id": str(
                selection.model_provider_organization_id
            ),
            "model_snapshot_digest": selection.model_snapshot_digest,
            "model_policy_digest": selection.requested_model_policy_digest,
            "registry_digest": selection.registry_digest,
            "entrypoint_id": model_version.entrypoint_id,
            "model_digest": model_version.model_digest,
            "input_schema_version": model_version.input_schema_version,
            "output_schema_version": model_version.output_schema_version,
        }

    manifest = _canonical_snapshot_manifest(
        application,
        items,
        actions,
        outputs,
        attachments,
        review_rule_digests,
        model_selection_manifest,
    )
    snapshot = ApplicationSnapshot(
        application_id=application.id,
        schema_version="1.0",
        manifest=manifest,
        snapshot_digest=_snapshot_digest(manifest),
        digest_algorithm="sha256",
        captured_by=submitted_by,
    )
    application.status = "submitted"
    application.submitted_at = datetime.now(timezone.utc)
    await session.flush()
    session.add(snapshot)
    await session.flush()
    return snapshot

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.session import get_db_session
from app.demo.phase4 import (
    approve_data_listing_command,
    approve_model_listing_command,
    ensure_phase4_demo_initial,
    submit_data_listing_command,
    submit_model_listing_command,
)
from app.main import create_app
from app.modules.applications.models import Application, ApplicationSnapshot
from app.modules.audit.models import AuditEvent
from app.modules.catalog.models import DataProductVersion
from app.modules.compute.models import ComputeJob
from app.modules.contracts.models import Contract, ContractRevision, ContractSignature
from app.modules.marketplace.models import ModelVersion
from app.modules.reviews.models import ReviewTask


DATABASE_URL = os.getenv("MEDTRUST_PHASE5_TEST_DATABASE_URL")
WORKSPACE = Path(__file__).resolve().parents[3]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not DATABASE_URL,
        reason="MEDTRUST_PHASE5_TEST_DATABASE_URL is not configured",
    ),
]


def _application_payload(
    data_version_id: str, model_version_id: str, suffix: str
) -> dict:
    return {
        "schema_version": "phase5.3/application-request/v1",
        "data_version_id": data_version_id,
        "model_version_id": model_version_id,
        "profile": {
            "demand_name": f"PathMNIST external validation {suffix}",
            "project_type": "model_external_validation",
            "project_summary": (
                "Validate the fixed non-clinical PathMNIST model against the "
                "published demonstration data through controlled compute."
            ),
            "project_lead": "Demo research lead",
            "contact": "Research validation department",
            "is_demo": True,
            "purpose_code": "model_validation",
            "research_purpose": (
                "Measure aggregate classification performance for an internal "
                "engineering demonstration without clinical use."
            ),
            "use_background": "Pre-contract technical feasibility validation.",
            "expected_value": "Provide aggregate evidence for a later contract decision.",
            "clinical_diagnosis": False,
            "research_publication": False,
            "commercial_validation": False,
            "ethics_or_approval_statement": (
                "Public demonstration data only; no patient-level material is used."
            ),
            "project_reference": f"PHASE53-{suffix}",
            "data_minimization": (
                "Use only the approved fixed demonstration subset and aggregate outputs."
            ),
        },
        "data_scope": {
            "scope_type": "all_approved_demo_data",
            "subset_description": "The fixed approved PathMNIST demonstration subset.",
            "sample_count": 20,
            "selection_criteria": "Use the immutable published demonstration scope.",
        },
        "execution": {
            "run_count": 1,
            "valid_days": 30,
            "environment_requirements": "Fixed CPU local built-in executor.",
            "internet_required": False,
            "fixed_data_version": True,
            "fixed_model_version": True,
            "requested_outputs": [
                "aggregate_metrics",
                "confusion_matrix",
                "execution_summary",
            ],
        },
        "review_requirements": {
            "hospital_egress_review": True,
            "model_technical_confirmation": True,
            "result_review_notes": "Review aggregate outputs before any release.",
            "output_recipient": "Research validation department",
        },
        "declarations": {
            "no_raw_data_download": True,
            "no_model_weight_download": True,
            "approved_purpose_only": True,
            "accept_multiparty_review": True,
            "accept_result_isolation": True,
            "accept_full_audit": True,
        },
    }


def _review_payload(action: str = "approve") -> dict:
    return {
        "action": action,
        "reason_code": None if action == "approve" else "incomplete_materials",
        "comment": (
            "The registered purpose, scope, version locks and output boundaries "
            "are complete for this engineering demonstration."
        ),
        "evidence": {
            "completeness_check": "Complete",
            "compatibility_conclusion": "Server report reviewed",
            "purpose_assessment": "Non-clinical purpose accepted",
            "output_risk": "Aggregate-only output",
            "risk_level": "low",
            "approved_scope": "Fixed published demonstration scope",
            "max_runs": 1,
            "valid_days": 30,
            "allowed_outputs": [
                "aggregate_metrics",
                "confusion_matrix",
                "execution_summary",
            ],
            "prohibited_outputs": ["raw_images", "model_weights"],
            "requires_egress_review": True,
            "allowed_environment": "local_builtin CPU",
            "requires_technical_confirmation": True,
            "additional_conditions": "Proceed only to the digital contract stage.",
            "requested_materials": "",
        },
    }


def _queue_item(client: TestClient, headers: dict[str, str], application_id: str) -> dict:
    response = client.get("/api/v1/application-review-queue", headers=headers)
    assert response.status_code == 200
    return next(
        item
        for item in response.json()["items"]
        if item["application"]["application_id"] == application_id
    )


async def _prepare_published_options(database_url: str) -> None:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            async with session.begin():
                context = await ensure_phase4_demo_initial(session, workspace=WORKSPACE)
                data_version = await session.get(
                    DataProductVersion, context.data_version_id
                )
                model_version = await session.get(ModelVersion, context.model_version_id)
                assert data_version is not None and model_version is not None
                if data_version.status == "draft":
                    data_version.linkage_metadata = {
                        **data_version.linkage_metadata,
                        "modality": "digital_pathology",
                    }
                    data_version.scope_metadata = {
                        **data_version.scope_metadata,
                        "image_specification": "28 x 28 RGB",
                    }
                    data_version.default_policy_template = {
                        "schema_version": "phase5.3/test-data-policy/v1",
                        "max_runs": 5,
                        "valid_days": 30,
                        "allowed_outputs": [
                            "aggregate_metrics",
                            "confusion_matrix",
                            "execution_summary",
                        ],
                        "allowed_purposes": [
                            "research_analysis",
                            "model_validation",
                            "external_performance_validation",
                            "teaching_demo",
                        ],
                        "requires_egress_review": True,
                        "hard_isolation": False,
                    }
                    from app.modules.audit import canonical_json_digest_v1

                    data_version.default_policy_digest = canonical_json_digest_v1(
                        data_version.default_policy_template
                    )
                    await submit_data_listing_command(
                        session, context, raw_key="phase53-data-submit"
                    )
                    await approve_data_listing_command(
                        session, context, raw_key="phase53-data-approve"
                    )
                if model_version.status == "draft":
                    model_version.compatibility_metadata = {
                        "schema_version": "phase5.3/test-model-compatibility/v1",
                        "task_type": "image_classification",
                        "modality": "digital_pathology",
                        "input_schema": {
                            "type": "image",
                            "modality": "digital_pathology",
                            "dtype": "uint8",
                            "width": 28,
                            "height": 28,
                            "channels": 3,
                            "batch_supported": True,
                        },
                        "output_schema": {"type": "aggregate_classification_metrics"},
                        "allowed_outputs": [
                            "aggregate_metrics",
                            "confusion_matrix",
                            "execution_summary",
                        ],
                        "asset_ready": True,
                        "executor_type": "local_builtin",
                        "non_clinical": True,
                    }
                    model_version.license_metadata = {
                        "schema_version": "phase5.3/test-license/v1",
                        "allowed_purposes": ["model validation", "teaching demo"],
                        "provider_result_confirmation": True,
                        "non_clinical": True,
                    }
                    model_version.default_policy_template = {
                        "schema_version": "phase5.3/test-model-policy/v1",
                        "max_runs": 5,
                        "valid_days": 30,
                        "allowed_outputs": [
                            "aggregate_metrics",
                            "confusion_matrix",
                            "execution_summary",
                        ],
                        "allowed_purposes": ["model validation", "teaching demo"],
                        "hard_isolation": False,
                    }
                    from app.modules.audit import canonical_json_digest_v1

                    model_version.default_policy_digest = canonical_json_digest_v1(
                        model_version.default_policy_template
                    )
                    await submit_model_listing_command(
                        session,
                        context,
                        workspace=WORKSPACE,
                        raw_key="phase53-model-submit",
                    )
                    await approve_model_listing_command(
                        session,
                        context,
                        workspace=WORKSPACE,
                        raw_key="phase53-model-approve",
                    )
    finally:
        await engine.dispose()


async def _compute_job_count(database_url: str) -> int:
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return int(
                await session.scalar(select(func.count(ComputeJob.id)))
                or 0
            )
    finally:
        await engine.dispose()


def test_phase5_application_lifecycle_authorization_idempotency_and_reviews() -> None:
    assert DATABASE_URL is not None
    asyncio.run(_prepare_published_options(DATABASE_URL))
    compute_job_count_before = asyncio.run(_compute_job_count(DATABASE_URL))
    engine = create_async_engine(DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with factory() as session:
            yield session

    app = create_app(
        Settings(app_env="test", database_url=DATABASE_URL, demo_api_enabled=True)
    )
    app.dependency_overrides[get_db_session] = test_session
    requester = {"X-Demo-Identity": "data_requester"}
    operator = {"X-Demo-Identity": "space_operator"}
    hospital = {"X-Demo-Identity": "data_provider"}
    model_provider = {"X-Demo-Identity": "model_provider"}
    suffix = uuid4().hex[:10]
    try:
        with TestClient(app) as client:
            options = client.get("/api/v1/application-options", headers=requester)
            assert options.status_code == 200
            data_version_id = options.json()["sample"]["data_version_id"]
            model_version_id = options.json()["sample"]["model_version_id"]
            assert data_version_id and model_version_id
            payload = _application_payload(data_version_id, model_version_id, suffix)

            denied = client.post(
                "/api/v1/application-drafts",
                headers={**hospital, "Idempotency-Key": f"denied-app-{suffix}"},
                json=payload,
            )
            assert denied.status_code == 403

            create_key = f"create-app-{suffix}"
            created = client.post(
                "/api/v1/application-drafts",
                headers={**requester, "Idempotency-Key": create_key},
                json=payload,
            )
            replay = client.post(
                "/api/v1/application-drafts",
                headers={**requester, "Idempotency-Key": create_key},
                json=payload,
            )
            assert created.status_code == replay.status_code == 201
            assert created.json() == replay.json()
            application_id = created.json()["application_id"]

            hidden_draft = client.get(
                f"/api/v1/applications/{application_id}", headers=hospital
            )
            assert hidden_draft.status_code == 404
            hospital_list = client.get(
                "/api/v1/application-management", headers=hospital
            )
            assert application_id not in {
                item["application_id"] for item in hospital_list.json()["items"]
            }

            compatibility = client.post(
                f"/api/v1/application-drafts/{application_id}/compatibility",
                headers={
                    **requester,
                    "Idempotency-Key": f"compatibility-a-{suffix}",
                },
            )
            assert compatibility.status_code == 200
            assert compatibility.json()["overall"] == "WARNING"
            assert compatibility.json()["counts"]["blocker"] == 0

            payload["profile"]["project_summary"] += " Updated before submission."
            payload["expected_row_version"] = created.json()["row_version"]
            update_key = f"update-app-{suffix}"
            updated = client.patch(
                f"/api/v1/application-drafts/{application_id}",
                headers={**requester, "Idempotency-Key": update_key},
                json=payload,
            )
            update_replay = client.patch(
                f"/api/v1/application-drafts/{application_id}",
                headers={**requester, "Idempotency-Key": update_key},
                json=payload,
            )
            assert updated.status_code == update_replay.status_code == 200
            assert updated.json() == update_replay.json()

            stale_submit = client.post(
                f"/api/v1/application-drafts/{application_id}/submit",
                headers={**requester, "Idempotency-Key": f"submit-stale-{suffix}"},
                json={"warnings_acknowledged": True},
            )
            assert stale_submit.status_code == 409

            compatibility = client.post(
                f"/api/v1/application-drafts/{application_id}/compatibility",
                headers={
                    **requester,
                    "Idempotency-Key": f"compatibility-b-{suffix}",
                },
            )
            assert compatibility.status_code == 200
            submit_key = f"submit-app-{suffix}"
            submitted = client.post(
                f"/api/v1/application-drafts/{application_id}/submit",
                headers={**requester, "Idempotency-Key": submit_key},
                json={"warnings_acknowledged": True},
            )
            submit_replay = client.post(
                f"/api/v1/application-drafts/{application_id}/submit",
                headers={**requester, "Idempotency-Key": submit_key},
                json={"warnings_acknowledged": True},
            )
            assert submitted.status_code == submit_replay.status_code == 200
            assert submitted.json() == submit_replay.json()
            assert submitted.json()["status"] == "prechecking"

            hospital_task = _queue_item(client, hospital, application_id)
            assert hospital_task["actionable"] is False
            early_hospital = client.post(
                f"/api/v1/application-review-tasks/{hospital_task['task_id']}/decide",
                headers={**hospital, "Idempotency-Key": f"early-hospital-{suffix}"},
                json=_review_payload(),
            )
            assert early_hospital.status_code == 409

            operator_task = _queue_item(client, operator, application_id)
            operator_decision = client.post(
                f"/api/v1/application-review-tasks/{operator_task['task_id']}/decide",
                headers={**operator, "Idempotency-Key": f"operator-{suffix}"},
                json=_review_payload(),
            )
            assert operator_decision.status_code == 200
            assert operator_decision.json()["application_status"] == "provider_review"

            hospital_task = _queue_item(client, hospital, application_id)
            assert hospital_task["actionable"] is True
            hospital_decision = client.post(
                f"/api/v1/application-review-tasks/{hospital_task['task_id']}/decide",
                headers={**hospital, "Idempotency-Key": f"hospital-{suffix}"},
                json=_review_payload(),
            )
            assert hospital_decision.status_code == 200

            model_task = _queue_item(client, model_provider, application_id)
            assert model_task["actionable"] is True
            model_key = f"model-{suffix}"
            model_decision = client.post(
                f"/api/v1/application-review-tasks/{model_task['task_id']}/decide",
                headers={**model_provider, "Idempotency-Key": model_key},
                json=_review_payload(),
            )
            model_replay = client.post(
                f"/api/v1/application-review-tasks/{model_task['task_id']}/decide",
                headers={**model_provider, "Idempotency-Key": model_key},
                json=_review_payload(),
            )
            assert model_decision.status_code == model_replay.status_code == 200
            assert model_decision.json() == model_replay.json()
            assert model_decision.json()["application_status"] == "approved"
            assert model_decision.json()["next_step"] == "digital_contract"

            detail = client.get(
                f"/api/v1/applications/{application_id}", headers=requester
            )
            assert detail.status_code == 200
            assert detail.json()["status"] == "approved"
            assert detail.json()["next_step"] == "digital_contract"
            assert detail.json()["capability"]["compute_job_creation"] is False
            assert detail.json()["snapshot"]["digest"] == submitted.json()[
                "snapshot_digest"
            ]

            audit = client.get(
                f"/api/v1/applications/{application_id}/audit-events",
                headers=requester,
            )
            assert audit.status_code == 200
            assert audit.json()["audit_chain_valid"] is True
            event_types = [item["event_type"] for item in audit.json()["items"]]
            assert "application.created" in event_types
            assert "application.updated" in event_types
            assert "application.compatibility.checked" in event_types
            assert "application.submitted" in event_types
            assert event_types.count("application.review.decided") == 3
            assert "application.approved" in event_types

            denied_contract = client.post(
                f"/api/v1/applications/{application_id}/contract",
                headers={
                    **requester,
                    "Idempotency-Key": f"contract-denied-{suffix}",
                },
            )
            assert denied_contract.status_code == 403
            contract_key = f"contract-create-{suffix}"
            contract_created = client.post(
                f"/api/v1/applications/{application_id}/contract",
                headers={**operator, "Idempotency-Key": contract_key},
            )
            contract_replay = client.post(
                f"/api/v1/applications/{application_id}/contract",
                headers={**operator, "Idempotency-Key": contract_key},
            )
            assert contract_created.status_code == contract_replay.status_code == 200
            contract = contract_created.json()
            replay_contract = contract_replay.json()
            assert {**contract, "security_validation": None} == {
                **replay_contract,
                "security_validation": None,
            }
            contract_id = contract["contract_id"]
            revision_id = contract["revision_id"]
            content_digest = contract["content_digest"]
            assert contract["status"] == "proposed"
            assert contract["policy_convergence"]["blockers"] == []
            assert contract["confirmation_progress"] == {"completed": 0, "required": 4}
            assert contract["security_validation"]["overall"] == "PENDING", [
                item
                for item in contract["security_validation"]["checks"]
                if item["result"] == "BLOCKER"
            ]
            assert contract["security_validation"]["profile_version"] == (
                "medtrust.controlled-compute-usage-policy/v1"
            )
            assert {
                item["code"] for item in contract["security_validation"]["checks"]
            } >= {
                "terms_integrity",
                "party_authority",
                "asset_integrity",
                "policy_integrity",
                "content_integrity",
                "effective_window",
                "signature_binding",
                "execution_binding",
            }

            stale_digest = client.post(
                f"/api/v1/digital-contracts/{contract_id}/confirm",
                headers={
                    **requester,
                    "Idempotency-Key": f"contract-stale-{suffix}",
                },
                json={
                    "contract_revision_id": revision_id,
                    "content_digest": "sha256:" + "0" * 64,
                    "declaration_accepted": True,
                },
            )
            assert stale_digest.status_code == 409
            early_operator = client.post(
                f"/api/v1/digital-contracts/{contract_id}/confirm",
                headers={
                    **operator,
                    "Idempotency-Key": f"contract-operator-early-{suffix}",
                },
                json={
                    "contract_revision_id": revision_id,
                    "content_digest": content_digest,
                    "declaration_accepted": True,
                },
            )
            assert early_operator.status_code == 409

            for role_headers, role in (
                (requester, "requester"),
                (hospital, "hospital"),
                (model_provider, "model"),
            ):
                confirmed = client.post(
                    f"/api/v1/digital-contracts/{contract_id}/confirm",
                    headers={
                        **role_headers,
                        "Idempotency-Key": f"contract-confirm-{role}-{suffix}",
                    },
                    json={
                        "contract_revision_id": revision_id,
                        "content_digest": content_digest,
                        "declaration_accepted": True,
                    },
                )
                assert confirmed.status_code == 200
                assert confirmed.json()["status"] == "proposed"

            operator_confirmed = client.post(
                f"/api/v1/digital-contracts/{contract_id}/confirm",
                headers={
                    **operator,
                    "Idempotency-Key": f"contract-confirm-operator-{suffix}",
                },
                json={
                    "contract_revision_id": revision_id,
                    "content_digest": content_digest,
                    "declaration_accepted": True,
                },
            )
            assert operator_confirmed.status_code == 200
            assert operator_confirmed.json()["status"] == "signed"
            assert operator_confirmed.json()["confirmation_progress"] == {
                "completed": 4,
                "required": 4,
            }
            assert operator_confirmed.json()["security_validation"]["overall"] == "PASS", [
                item
                for item in operator_confirmed.json()["security_validation"]["checks"]
                if item["result"] != "PASS"
            ]
            activated = client.post(
                f"/api/v1/digital-contracts/{contract_id}/activate",
                headers={
                    **operator,
                    "Idempotency-Key": f"contract-activate-{suffix}",
                },
            )
            activate_replay = client.post(
                f"/api/v1/digital-contracts/{contract_id}/activate",
                headers={
                    **operator,
                    "Idempotency-Key": f"contract-activate-{suffix}",
                },
            )
            assert activated.status_code == activate_replay.status_code == 200
            assert activated.json()["status"] == "active"
            assert activated.json()["next_step"] == "waiting_for_data_and_model_readiness"
            assert activated.json()["capability"]["compute_job_creation"] is False
            assert activated.json()["capability"]["readiness_implemented"] is True
            assert activated.json()["security_validation"]["overall"] == "PASS"
            contract_audit = client.get(
                f"/api/v1/digital-contracts/{contract_id}/audit-events",
                headers=requester,
            )
            assert contract_audit.status_code == 200
            contract_event_types = [
                item["event_type"] for item in contract_audit.json()["items"]
            ]
            assert "contract.draft.generated" in contract_event_types
            assert "contract.policy.converged" in contract_event_types
            assert contract_event_types.count("contract.revision.signed") == 4
            assert "contract.revision.activated" in contract_event_types

            blocked_payload = _application_payload(
                data_version_id, model_version_id, f"blocked-{suffix}"
            )
            blocked_payload["execution"]["run_count"] = 6
            blocked = client.post(
                "/api/v1/application-drafts",
                headers={
                    **requester,
                    "Idempotency-Key": f"create-blocked-{suffix}",
                },
                json=blocked_payload,
            )
            assert blocked.status_code == 201
            blocked_report = client.post(
                f"/api/v1/application-drafts/{blocked.json()['application_id']}/compatibility",
                headers={
                    **requester,
                    "Idempotency-Key": f"check-blocked-{suffix}",
                },
            )
            assert blocked_report.status_code == 200
            assert "run_limit" in blocked_report.json()["blockers"]
            blocked_submit = client.post(
                f"/api/v1/application-drafts/{blocked.json()['application_id']}/submit",
                headers={
                    **requester,
                    "Idempotency-Key": f"submit-blocked-{suffix}",
                },
                json={"warnings_acknowledged": True},
            )
            assert blocked_submit.status_code == 409

            returned_payload = _application_payload(
                data_version_id, model_version_id, f"returned-{suffix}"
            )
            returned_created = client.post(
                "/api/v1/application-drafts",
                headers={
                    **requester,
                    "Idempotency-Key": f"create-returned-{suffix}",
                },
                json=returned_payload,
            )
            assert returned_created.status_code == 201
            returned_application_id = returned_created.json()["application_id"]
            returned_check = client.post(
                f"/api/v1/application-drafts/{returned_application_id}/compatibility",
                headers={
                    **requester,
                    "Idempotency-Key": f"check-returned-{suffix}",
                },
            )
            assert returned_check.status_code == 200
            returned_submit = client.post(
                f"/api/v1/application-drafts/{returned_application_id}/submit",
                headers={
                    **requester,
                    "Idempotency-Key": f"submit-returned-{suffix}",
                },
                json={"warnings_acknowledged": True},
            )
            assert returned_submit.status_code == 200
            return_task = _queue_item(client, operator, returned_application_id)
            returned_decision = client.post(
                f"/api/v1/application-review-tasks/{return_task['task_id']}/decide",
                headers={
                    **operator,
                    "Idempotency-Key": f"return-application-{suffix}",
                },
                json=_review_payload("return"),
            )
            assert returned_decision.status_code == 200
            assert returned_decision.json()["application_status"] == "rejected"
            replacement_application_id = returned_decision.json()[
                "replacement_application_id"
            ]
            assert replacement_application_id
            returned_detail = client.get(
                f"/api/v1/applications/{returned_application_id}",
                headers=requester,
            )
            replacement_detail = client.get(
                f"/api/v1/applications/{replacement_application_id}",
                headers=requester,
            )
            assert returned_detail.status_code == replacement_detail.status_code == 200
            assert returned_detail.json()["status"] == "rejected"
            assert returned_detail.json()["snapshot"]["digest"] == returned_submit.json()[
                "snapshot_digest"
            ]
            assert replacement_detail.json()["status"] == "draft"
            assert replacement_detail.json()["compatibility"] is None
            assert replacement_detail.json()["snapshot"] is None
            assert replacement_detail.json()["request"] == returned_detail.json()["request"]
            returned_audit = client.get(
                f"/api/v1/applications/{returned_application_id}/audit-events",
                headers=requester,
            )
            assert returned_audit.status_code == 200
            returned_events = {
                item["event_type"] for item in returned_audit.json()["items"]
            }
            assert {
                "application.submitted",
                "application.review.decided",
                "application.returned",
            }.issubset(returned_events)

            rejected_payload = _application_payload(
                data_version_id, model_version_id, f"rejected-{suffix}"
            )
            rejected_created = client.post(
                "/api/v1/application-drafts",
                headers={
                    **requester,
                    "Idempotency-Key": f"create-rejected-{suffix}",
                },
                json=rejected_payload,
            )
            assert rejected_created.status_code == 201
            rejected_application_id = rejected_created.json()["application_id"]
            rejected_check = client.post(
                f"/api/v1/application-drafts/{rejected_application_id}/compatibility",
                headers={
                    **requester,
                    "Idempotency-Key": f"check-rejected-{suffix}",
                },
            )
            assert rejected_check.status_code == 200
            rejected_submit = client.post(
                f"/api/v1/application-drafts/{rejected_application_id}/submit",
                headers={
                    **requester,
                    "Idempotency-Key": f"submit-rejected-{suffix}",
                },
                json={"warnings_acknowledged": True},
            )
            assert rejected_submit.status_code == 200
            reject_task = _queue_item(client, operator, rejected_application_id)
            rejected_decision = client.post(
                f"/api/v1/application-review-tasks/{reject_task['task_id']}/decide",
                headers={
                    **operator,
                    "Idempotency-Key": f"reject-application-{suffix}",
                },
                json=_review_payload("reject"),
            )
            assert rejected_decision.status_code == 200
            assert rejected_decision.json()["application_status"] == "rejected"
            assert rejected_decision.json()["replacement_application_id"] is None
            rejected_detail = client.get(
                f"/api/v1/applications/{rejected_application_id}",
                headers=requester,
            )
            assert rejected_detail.status_code == 200
            assert rejected_detail.json()["status"] == "rejected"
            rejected_audit = client.get(
                f"/api/v1/applications/{rejected_application_id}/audit-events",
                headers=requester,
            )
            assert rejected_audit.status_code == 200
            rejected_events = {
                item["event_type"] for item in rejected_audit.json()["items"]
            }
            assert {
                "application.submitted",
                "application.review.decided",
                "application.rejected",
            }.issubset(rejected_events)

        async def verify_database() -> None:
            verify_engine = create_async_engine(DATABASE_URL)
            verify_factory = async_sessionmaker(
                verify_engine, expire_on_commit=False
            )
            try:
                async with verify_factory() as session:
                    application = await session.get(Application, application_id)
                    assert application is not None and application.status == "approved"
                    assert (
                        await session.scalar(
                            select(func.count(ApplicationSnapshot.id)).where(
                                ApplicationSnapshot.application_id == application.id
                            )
                        )
                        == 1
                    )
                    assert (
                        await session.scalar(
                            select(func.count(ReviewTask.id)).where(
                                ReviewTask.application_id == application.id
                            )
                        )
                        == 3
                    )
                    assert (
                        await session.scalar(select(func.count(ComputeJob.id)))
                        == compute_job_count_before
                    )
                    contract = await session.scalar(
                        select(Contract).where(
                            Contract.application_id == application.id
                        )
                    )
                    assert contract is not None
                    revision = await session.scalar(
                        select(ContractRevision).where(
                            ContractRevision.contract_id == contract.id
                        )
                    )
                    assert revision is not None and revision.status == "active"
                    assert (
                        await session.scalar(
                            select(func.count(ContractSignature.id)).where(
                                ContractSignature.contract_revision_id == revision.id
                            )
                        )
                        == 4
                    )
                    replacement = await session.get(
                        Application, replacement_application_id
                    )
                    assert replacement is not None
                    assert replacement.status == "draft"
                    assert (
                        await session.scalar(
                            select(func.count(ApplicationSnapshot.id)).where(
                                ApplicationSnapshot.application_id
                                == returned_application_id
                            )
                        )
                        == 1
                    )
                    assert (
                        await session.scalar(
                            select(func.count(ReviewTask.id)).where(
                                ReviewTask.application_id == returned_application_id
                            )
                        )
                        == 3
                    )
                    assert (
                        await session.scalar(
                            select(func.count(AuditEvent.event_id)).where(
                                AuditEvent.subject_type == "application",
                                AuditEvent.subject_id == application.id,
                            )
                        )
                        >= 6
                    )
            finally:
                await verify_engine.dispose()

        asyncio.run(verify_database())
    finally:
        app.dependency_overrides.clear()

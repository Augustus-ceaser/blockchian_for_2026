from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SPACE_ID = UUID("0967d9f7-509a-5583-b214-df66c2eae6de")
REFERENCE_RELATION_ID = UUID("df7ec70c-f4cb-5df7-842d-bf2af6d66961")


COUNT_QUERIES = {
    "external_dataset_records": "SELECT count(*) FROM medtrust.external_dataset_records",
    "external_dataset_versions": "SELECT count(*) FROM medtrust.external_dataset_versions",
    "dataset_governance_profiles": "SELECT count(*) FROM medtrust.external_dataset_governance_profiles",
    "dataset_governance_reviews": "SELECT count(*) FROM medtrust.external_dataset_governance_reviews",
    "external_model_records": "SELECT count(*) FROM medtrust.external_model_records",
    "external_model_versions": "SELECT count(*) FROM medtrust.external_model_versions",
    "model_governance_profiles": "SELECT count(*) FROM medtrust.external_model_governance_profiles",
    "model_governance_reviews": "SELECT count(*) FROM medtrust.external_model_governance_reviews",
    "data_products": "SELECT count(*) FROM medtrust.data_products",
    "model_products": "SELECT count(*) FROM medtrust.model_products",
    "applications": "SELECT count(*) FROM medtrust.applications",
    "contracts": "SELECT count(*) FROM medtrust.contracts",
    "compute_jobs": "SELECT count(*) FROM medtrust.compute_jobs",
    "compute_runs": "SELECT count(*) FROM medtrust.compute_runs",
    "artifacts": "SELECT count(*) FROM medtrust.artifacts",
    "release_packages": "SELECT count(*) FROM medtrust.approved_result_packages",
    "download_grants": "SELECT count(*) FROM medtrust.result_download_grants",
    "relations": "SELECT count(*) FROM medtrust.dataset_model_relations",
    "evidences": "SELECT count(*) FROM medtrust.dataset_model_evidence",
    "materialization_plans": "SELECT count(*) FROM medtrust.asset_materialization_plans",
    "audit_events": "SELECT count(*) FROM medtrust.audit_events",
}


async def _count(session: AsyncSession, query: str) -> int:
    return int(await session.scalar(text(query)) or 0)


async def read_business_state(session: AsyncSession) -> dict[str, Any]:
    counts = {
        name: await _count(session, query)
        for name, query in COUNT_QUERIES.items()
    }
    status_counts = {
        "published_external_data_products": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.data_product_external_source_links l
            JOIN medtrust.data_product_publications p
              ON p.data_product_version_id=l.data_product_version_id
            WHERE p.status='active'
            """,
        ),
        "draft_external_data_products": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.data_product_external_source_links l
            JOIN medtrust.data_product_versions v ON v.id=l.data_product_version_id
            WHERE v.status='draft'
            """,
        ),
        "archived_external_data_products": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.data_product_external_source_links l
            JOIN medtrust.data_products p ON p.id=l.data_product_id
            WHERE p.lifecycle_status='archived'
            """,
        ),
        "published_external_model_products": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.model_product_external_source_links l
            JOIN medtrust.model_publications p ON p.model_version_id=l.model_version_id
            WHERE p.status='active'
            """,
        ),
        "draft_external_model_products": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.model_product_external_source_links l
            JOIN medtrust.model_versions v ON v.id=l.model_version_id
            WHERE v.status='draft'
            """,
        ),
        "static_transformation_relations": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.dataset_model_relations
            WHERE current_status='static_schema_compatible_with_transformation'
            """,
        ),
        "static_incompatible_relations": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.dataset_model_relations
            WHERE current_status='static_schema_incompatible'
            """,
        ),
        "executed_evidences": await _count(
            session,
            "SELECT count(*) FROM medtrust.dataset_model_evidence WHERE evidence_type='executed'",
        ),
        "verified_evidences": await _count(
            session,
            "SELECT count(*) FROM medtrust.dataset_model_evidence WHERE evidence_type='verified'",
        ),
        "execution_failed_evidences": await _count(
            session,
            "SELECT count(*) FROM medtrust.dataset_model_evidence WHERE evidence_type='execution_failed'",
        ),
        "approved_materialization_plans": await _count(
            session,
            "SELECT count(*) FROM medtrust.asset_materialization_plans WHERE plan_status='approved'",
        ),
        "external_model_executors": await _count(
            session,
            """
            SELECT count(*) FROM medtrust.model_product_external_source_links l
            JOIN medtrust.model_versions v ON v.id=l.model_version_id
            WHERE v.entrypoint_id<>'external-metadata-only'
               OR v.runtime<>'external_metadata_only'
            """,
        ),
        "materialized_external_models": await _count(
            session,
            "SELECT count(*) FROM medtrust.external_model_records WHERE execution_status<>'not_materialized'",
        ),
    }
    relation = (
        await session.execute(
            text(
                """
                SELECT r.id, r.current_status, r.strongest_evidence_level,
                       r.data_product_version_id, r.model_product_version_id,
                       dp.name AS data_name, mp.name AS model_name,
                       e.structured_assessment, e.evidence_reference, e.created_at
                FROM medtrust.dataset_model_relations r
                JOIN medtrust.data_products dp ON dp.id=r.data_product_id
                JOIN medtrust.model_products mp ON mp.id=r.model_product_id
                JOIN medtrust.dataset_model_evidence e ON e.id=r.current_evidence_id
                WHERE r.id=:relation_id
                """
            ),
            {"relation_id": REFERENCE_RELATION_ID},
        )
    ).mappings().one_or_none()
    audit = (
        await session.execute(
            text(
                """
                SELECT stream_sequence, event_digest, event_type, occurred_at
                FROM medtrust.audit_events
                WHERE space_id=:space_id
                ORDER BY stream_sequence DESC LIMIT 1
                """
            ),
            {"space_id": SPACE_ID},
        )
    ).mappings().one()
    chain = (
        await session.execute(
            text("SELECT * FROM medtrust.verify_audit_space_chain_v1(:space_id)"),
            {"space_id": SPACE_ID},
        )
    ).mappings().one()
    external_models = list(
        (
            await session.execute(
                text(
                    """
                    SELECT p.name, p.product_code, p.lifecycle_status,
                           v.runtime, v.entrypoint_id,
                           r.gated, r.weights_status, r.execution_status
                    FROM medtrust.model_product_external_source_links l
                    JOIN medtrust.model_products p ON p.id=l.model_product_id
                    JOIN medtrust.model_versions v ON v.id=l.model_version_id
                    JOIN medtrust.external_model_records r
                      ON r.id=l.external_model_record_id
                    WHERE p.name IN ('CONCH','UNI')
                    ORDER BY p.name
                    """
                )
            )
        ).mappings().all()
    )
    alembic = await session.scalar(text("SELECT version_num FROM alembic_version"))
    return {
        "schema_version": "phase5.12.7/business-state/v1",
        "alembic_head": alembic,
        "counts": counts,
        "status_counts": status_counts,
        "reference_relation": dict(relation) if relation else None,
        "external_models": [dict(item) for item in external_models],
        "audit": {
            "head_sequence": audit["stream_sequence"],
            "head_digest": audit["event_digest"],
            "head_event_type": audit["event_type"],
            "head_occurred_at": audit["occurred_at"],
            "chain_valid": bool(chain["is_valid"]),
            "invalid_sequence": chain["invalid_sequence"],
            "reason": chain["reason"],
        },
        "boundaries": {
            "hard_isolation": False,
            "clinical_use": False,
            "external_data_materialized": False,
            "external_model_materialized": status_counts["materialized_external_models"] > 0,
            "external_executor_registered": status_counts["external_model_executors"] > 0,
        },
    }

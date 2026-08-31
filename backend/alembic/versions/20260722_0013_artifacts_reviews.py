"""Add quarantined Artifacts and terminal Artifact reviews.

Revision ID: 20260722_0013
Revises: 20260722_0012
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0013"
down_revision: str | None = "20260722_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("compute_job_id", sa.Uuid(), nullable=False),
        sa.Column("compute_run_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_no", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=32), nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("storage_reference", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("classification_level", sa.String(length=32), nullable=False),
        sa.Column("output_policy_evaluation", postgresql.JSONB(), nullable=False),
        sa.Column("output_policy_evaluation_digest", sa.Text(), nullable=False),
        sa.Column(
            "release_status",
            sa.String(length=16),
            nullable=False,
            server_default="quarantined",
        ),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("release_evidence_digest", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("destroyed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "artifact_type IN ('aggregate_statistics','model_artifact','feature_dataset','risk_scoring_model')",
            name="ck_artifacts_artifact_type",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_artifacts_size_nonnegative"),
        sa.CheckConstraint(
            "classification_level <> ''", name="ck_artifacts_classification_nonempty"
        ),
        sa.CheckConstraint(
            "release_status IN ('quarantined','released','revoked','destroyed')",
            name="ck_artifacts_release_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_artifacts_row_version_positive"),
        sa.CheckConstraint(
            "(release_status='quarantined' AND release_evidence IS NULL "
            "AND release_evidence_digest IS NULL AND released_at IS NULL "
            "AND revoked_at IS NULL AND destroyed_at IS NULL) OR "
            "(release_status='released' AND release_evidence IS NOT NULL "
            "AND release_evidence_digest IS NOT NULL AND released_at IS NOT NULL "
            "AND revoked_at IS NULL AND destroyed_at IS NULL) OR "
            "(release_status='revoked' AND release_evidence IS NOT NULL "
            "AND release_evidence_digest IS NOT NULL AND released_at IS NOT NULL "
            "AND revoked_at IS NOT NULL AND destroyed_at IS NULL) OR "
            "(release_status='destroyed' AND destroyed_at IS NOT NULL)",
            name="ck_artifacts_release_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["compute_run_id", "compute_job_id", "space_id"],
            [
                "medtrust.compute_runs.id",
                "medtrust.compute_runs.compute_job_id",
                "medtrust.compute_runs.space_id",
            ],
            name="fk_artifacts_run_job_space",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifacts"),
        sa.UniqueConstraint("compute_run_id", "artifact_no", name="uq_artifacts_run_no"),
        sa.UniqueConstraint(
            "compute_run_id",
            "artifact_type",
            "content_digest",
            name="uq_artifacts_run_type_digest",
        ),
        sa.UniqueConstraint(
            "id", "space_id", "content_digest", name="uq_artifacts_review_scope"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifacts_space_status_created",
        "artifacts",
        ["space_id", "release_status", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifacts_job_created",
        "artifacts",
        ["compute_job_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifacts_run_no",
        "artifacts",
        ["compute_run_id", "artifact_no"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifacts_content_digest",
        "artifacts",
        ["content_digest"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifacts_retention_open",
        "artifacts",
        ["retention_until"],
        postgresql_where=sa.text(
            "release_status IN ('quarantined','released','revoked')"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "artifact_reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("target_content_digest", sa.Text(), nullable=False),
        sa.Column("responsible_organization_id", sa.Uuid(), nullable=False),
        sa.Column("claimed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("routing_rule_digest", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("decision_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("decision_digest", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('pending','claimed','decided','cancelled')",
            name="ck_artifact_reviews_status",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('approved','rejected')",
            name="ck_artifact_reviews_decision",
        ),
        sa.CheckConstraint(
            "row_version >= 1", name="ck_artifact_reviews_row_version_positive"
        ),
        sa.CheckConstraint(
            "(status='pending' AND claimed_by_user_id IS NULL AND claimed_at IS NULL "
            "AND decision IS NULL AND reason_code IS NULL AND decision_evidence IS NULL "
            "AND decision_digest IS NULL AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status='claimed' AND claimed_by_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decision IS NULL AND reason_code IS NULL AND decision_evidence IS NULL "
            "AND decision_digest IS NULL AND decided_at IS NULL AND cancelled_at IS NULL) OR "
            "(status='decided' AND claimed_by_user_id IS NOT NULL AND claimed_at IS NOT NULL "
            "AND decision IS NOT NULL AND reason_code IS NOT NULL AND decision_evidence IS NOT NULL "
            "AND decision_digest IS NOT NULL AND decided_at IS NOT NULL AND cancelled_at IS NULL) OR "
            "(status='cancelled' AND decision IS NULL AND decision_evidence IS NULL "
            "AND decision_digest IS NULL AND decided_at IS NULL AND cancelled_at IS NOT NULL)",
            name="ck_artifact_reviews_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "space_id", "target_content_digest"],
            ["medtrust.artifacts.id", "medtrust.artifacts.space_id", "medtrust.artifacts.content_digest"],
            name="fk_artifact_reviews_artifact_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "responsible_organization_id"],
            ["medtrust.space_participants.space_id", "medtrust.space_participants.organization_id"],
            name="fk_artifact_reviews_responsible_participant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["responsible_organization_id", "claimed_by_user_id"],
            ["medtrust.organization_members.organization_id", "medtrust.organization_members.user_id"],
            name="fk_artifact_reviews_claimed_member",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_reviews"),
        sa.UniqueConstraint("artifact_id", name="uq_artifact_reviews_artifact"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_reviews_space_status",
        "artifact_reviews",
        ["space_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_reviews_responsible_status",
        "artifact_reviews",
        ["responsible_organization_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_reviews_claimed_by",
        "artifact_reviews",
        ["claimed_by_user_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_artifact_reviews_decided_at",
        "artifact_reviews",
        ["decided_at"],
        schema=SCHEMA,
    )

    _create_release_audit_gate()
    _create_release_assertion()
    _create_artifact_guard()
    _create_artifact_review_guard()


def _create_release_audit_gate() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_artifact_release_audit_ready_v7()
        RETURNS void LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'AuditEvidenceUnavailable';
        END;
        $$;
        """
    )


def _create_release_assertion() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.assert_artifact_release_ready_v7(p_artifact_id uuid)
        RETURNS void LANGUAGE plpgsql AS $$
        DECLARE
            a medtrust.artifacts%ROWTYPE;
            evaluation_binding uuid;
        BEGIN
            SELECT * INTO a FROM medtrust.artifacts WHERE id=p_artifact_id FOR UPDATE;
            IF NOT FOUND OR a.release_status <> 'quarantined' THEN
                RAISE EXCEPTION 'Artifact is not quarantined';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM medtrust.artifact_reviews ar
                 WHERE ar.artifact_id=a.id AND ar.space_id=a.space_id
                   AND ar.target_content_digest=a.content_digest
                   AND ar.status='decided' AND ar.decision='approved'
            ) THEN RAISE EXCEPTION 'Artifact requires an approved terminal review'; END IF;
            IF a.output_policy_evaluation->>'decision' <> 'permit' OR
               jsonb_array_length(COALESCE(a.output_policy_evaluation->'deny_policy_digests','[]'::jsonb)) <> 0 THEN
                RAISE EXCEPTION 'Policy deny blocks Artifact release';
            END IF;
            IF NOT EXISTS (
                SELECT 1
                  FROM medtrust.compute_runs cr
                  JOIN medtrust.compute_jobs cj ON cj.id=cr.compute_job_id
                  JOIN medtrust.contract_revisions r ON r.id=cj.contract_revision_id
                 WHERE cr.id=a.compute_run_id AND cr.compute_job_id=a.compute_job_id
                   AND cr.space_id=a.space_id AND cr.status='succeeded'
                   AND r.status='active'
                   AND (r.effective_from IS NULL OR r.effective_from <= clock_timestamp())
                   AND (r.effective_until IS NULL OR r.effective_until > clock_timestamp())
            ) THEN RAISE EXCEPTION 'Artifact current Contract scope is unavailable'; END IF;
            evaluation_binding := (a.output_policy_evaluation->>'egress_binding_id')::uuid;
            IF NOT EXISTS (
                SELECT 1
                  FROM medtrust.compute_jobs cj
                  JOIN medtrust.policies p
                    ON p.contract_revision_id=cj.contract_revision_id
                   AND p.subject_contract_party_id=cj.requester_contract_party_id
                   AND p.contract_object_id=cj.contract_object_id
                   AND p.action_code='export_artifact'
                   AND p.policy_type='permission' AND p.effect='permit'
                  JOIN medtrust.policy_constraints pc
                    ON pc.policy_id=p.id AND pc.constraint_name='output_type'
                  JOIN medtrust.policy_execution_bindings b
                    ON b.policy_id=p.id AND b.id=evaluation_binding
                   AND b.execution_role='egress_controller'
                   AND b.required_capability_code='egress_policy_enforcement'
                   AND b.required_capability_version='1.0'
                   AND b.deployment_status='accepted' AND b.receipt_digest IS NOT NULL
                  JOIN medtrust.connectors cn ON cn.id=b.connector_id
                  JOIN medtrust.connector_capabilities cap
                    ON cap.connector_id=b.connector_id
                   AND cap.capability_code=b.required_capability_code
                   AND cap.capability_version=b.required_capability_version
                 WHERE cj.id=a.compute_job_id
                   AND ((pc.operator='eq' AND pc.value #>> '{}' = a.artifact_type)
                     OR (pc.operator='in' AND pc.value ? a.artifact_type))
                   AND cn.space_id=a.space_id AND cn.verification_status='verified'
                   AND cn.runtime_status='online'
                   AND cn.last_heartbeat_at >= clock_timestamp() - interval '5 minutes'
                   AND cap.status='verified' AND cap.verified_at IS NOT NULL
            ) THEN RAISE EXCEPTION 'Artifact egress capability is unavailable'; END IF;
            IF EXISTS (
                SELECT 1
                  FROM medtrust.compute_jobs cj
                  JOIN medtrust.policies p
                    ON p.contract_revision_id=cj.contract_revision_id
                   AND p.subject_contract_party_id=cj.requester_contract_party_id
                   AND p.contract_object_id=cj.contract_object_id
                   AND p.action_code='export_artifact' AND p.effect='deny'
                 WHERE cj.id=a.compute_job_id
                   AND (NOT EXISTS (
                        SELECT 1 FROM medtrust.policy_constraints pc
                         WHERE pc.policy_id=p.id AND pc.constraint_name='output_type'
                   ) OR EXISTS (
                        SELECT 1 FROM medtrust.policy_constraints pc
                         WHERE pc.policy_id=p.id AND pc.constraint_name='output_type'
                           AND ((pc.operator='eq' AND pc.value #>> '{}' = a.artifact_type)
                             OR (pc.operator='in' AND pc.value ? a.artifact_type))
                   ))
            ) THEN RAISE EXCEPTION 'current Policy deny blocks Artifact release'; END IF;
            PERFORM medtrust.assert_artifact_release_audit_ready_v7();
        END;
        $$;
        """
    )


def _create_artifact_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_artifact_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            permit_count integer;
            deny_count integer;
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'Artifact cannot be deleted'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.release_status <> 'quarantined' OR NEW.release_evidence IS NOT NULL OR
                   NEW.release_evidence_digest IS NOT NULL OR NEW.released_at IS NOT NULL OR
                   NEW.revoked_at IS NOT NULL OR NEW.destroyed_at IS NOT NULL THEN
                    RAISE EXCEPTION 'new Artifact must start as quarantined';
                END IF;
                IF NEW.content_digest !~ '^sha256:[0-9a-f]{64}$' OR
                   NEW.output_policy_evaluation_digest !~ '^sha256:[0-9a-f]{64}$' OR
                   jsonb_typeof(NEW.output_policy_evaluation) <> 'object' THEN
                    RAISE EXCEPTION 'Artifact digest or Policy evaluation is invalid';
                END IF;
                IF NEW.storage_reference='' OR position('://' in lower(NEW.storage_reference))>0 OR
                   position('?' in NEW.storage_reference)>0 OR position(E'\\\\' in NEW.storage_reference)>0 OR
                   NEW.storage_reference LIKE '/%' OR NEW.storage_reference ~ '^[A-Za-z]:/' OR
                   NEW.storage_reference ~ '(^|/)\\.\\.(/|$)' OR
                   lower(NEW.storage_reference) ~ '(x-amz-|signature=|token=|secret|access_key)' THEN
                    RAISE EXCEPTION 'storage_reference must be opaque';
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM medtrust.compute_runs cr
                    JOIN medtrust.compute_jobs cj ON cj.id=cr.compute_job_id
                    WHERE cr.id=NEW.compute_run_id AND cr.compute_job_id=NEW.compute_job_id
                      AND cr.space_id=NEW.space_id AND cr.status='succeeded'
                      AND cj.space_id=NEW.space_id
                      AND cj.requested_output_types ? NEW.artifact_type
                      AND NEW.output_policy_evaluation->>'compute_job_id'=cj.id::text
                      AND NEW.output_policy_evaluation->>'compute_run_id'=cr.id::text
                      AND NEW.output_policy_evaluation->>'contract_revision_id'=cj.contract_revision_id::text
                      AND NEW.output_policy_evaluation->>'contract_object_id'=cj.contract_object_id::text
                      AND NEW.output_policy_evaluation->>'artifact_type'=NEW.artifact_type
                ) THEN RAISE EXCEPTION 'Artifact Run, Job or evidence scope is inconsistent'; END IF;

                SELECT count(*) INTO permit_count
                  FROM medtrust.compute_jobs cj
                  JOIN medtrust.policies p
                    ON p.contract_revision_id=cj.contract_revision_id
                   AND p.subject_contract_party_id=cj.requester_contract_party_id
                   AND p.contract_object_id=cj.contract_object_id
                   AND p.action_code='export_artifact' AND p.effect='permit'
                  JOIN medtrust.policy_constraints pc
                    ON pc.policy_id=p.id AND pc.constraint_name='output_type'
                 WHERE cj.id=NEW.compute_job_id
                   AND ((pc.operator='eq' AND pc.value #>> '{}'=NEW.artifact_type)
                     OR (pc.operator='in' AND pc.value ? NEW.artifact_type))
                   AND p.policy_digest IS NOT NULL
                   AND NEW.output_policy_evaluation->'permit_policy_digests' ? p.policy_digest;
                IF permit_count=0 OR permit_count <> jsonb_array_length(
                    COALESCE(NEW.output_policy_evaluation->'permit_policy_digests','[]'::jsonb)
                ) THEN RAISE EXCEPTION 'Artifact permit Policy evidence is incomplete'; END IF;

                SELECT count(*) INTO deny_count
                  FROM medtrust.compute_jobs cj
                  JOIN medtrust.policies p
                    ON p.contract_revision_id=cj.contract_revision_id
                   AND p.subject_contract_party_id=cj.requester_contract_party_id
                   AND p.contract_object_id=cj.contract_object_id
                   AND p.action_code='export_artifact' AND p.effect='deny'
                 WHERE cj.id=NEW.compute_job_id AND p.policy_digest IS NOT NULL
                   AND (NOT EXISTS (
                        SELECT 1 FROM medtrust.policy_constraints pc
                         WHERE pc.policy_id=p.id AND pc.constraint_name='output_type'
                   ) OR EXISTS (
                        SELECT 1 FROM medtrust.policy_constraints pc
                         WHERE pc.policy_id=p.id AND pc.constraint_name='output_type'
                           AND ((pc.operator='eq' AND pc.value #>> '{}'=NEW.artifact_type)
                             OR (pc.operator='in' AND pc.value ? NEW.artifact_type))
                   ))
                   AND NEW.output_policy_evaluation->'deny_policy_digests' ? p.policy_digest;
                IF deny_count <> jsonb_array_length(
                    COALESCE(NEW.output_policy_evaluation->'deny_policy_digests','[]'::jsonb)
                ) THEN RAISE EXCEPTION 'Artifact deny Policy evidence is incomplete'; END IF;
                IF (deny_count=0 AND NEW.output_policy_evaluation->>'decision'<>'permit') OR
                   (deny_count>0 AND NEW.output_policy_evaluation->>'decision'<>'deny') THEN
                    RAISE EXCEPTION 'Artifact Policy decision is inconsistent';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.space_id,NEW.compute_job_id,NEW.compute_run_id,NEW.artifact_no,
                   NEW.artifact_type,NEW.content_digest,NEW.storage_reference,NEW.size_bytes,
                   NEW.classification_level,NEW.output_policy_evaluation,
                   NEW.output_policy_evaluation_digest,NEW.retention_until,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.space_id,OLD.compute_job_id,OLD.compute_run_id,OLD.artifact_no,
                   OLD.artifact_type,OLD.content_digest,OLD.storage_reference,OLD.size_bytes,
                   OLD.classification_level,OLD.output_policy_evaluation,
                   OLD.output_policy_evaluation_digest,OLD.retention_until,OLD.created_at) THEN
                RAISE EXCEPTION 'Artifact identity and Policy evidence are immutable';
            END IF;
            IF OLD.release_evidence IS NOT NULL AND ROW(NEW.release_evidence,
               NEW.release_evidence_digest,NEW.released_at) IS DISTINCT FROM
               ROW(OLD.release_evidence,OLD.release_evidence_digest,OLD.released_at) THEN
                RAISE EXCEPTION 'Artifact release evidence is immutable';
            END IF;
            IF NEW.row_version <> OLD.row_version+1 THEN
                RAISE EXCEPTION 'Artifact transition requires row_version increment';
            END IF;
            IF NEW.release_status=OLD.release_status THEN RETURN NEW; END IF;
            IF NOT ((OLD.release_status='quarantined' AND NEW.release_status IN ('released','destroyed')) OR
                    (OLD.release_status='released' AND NEW.release_status='revoked') OR
                    (OLD.release_status='revoked' AND NEW.release_status='destroyed')) THEN
                RAISE EXCEPTION 'illegal Artifact release transition';
            END IF;
            IF OLD.release_status='quarantined' AND NEW.release_status='released' THEN
                PERFORM medtrust.assert_artifact_release_ready_v7(OLD.id);
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_artifact_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON medtrust.artifacts FOR EACH ROW EXECUTE FUNCTION medtrust.guard_artifact_v7()"
    )


def _create_artifact_review_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION medtrust.guard_artifact_review_v7()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'ArtifactReview cannot be deleted'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.status<>'pending' THEN RAISE EXCEPTION 'new ArtifactReview must start as pending'; END IF;
                IF NEW.routing_rule_digest !~ '^sha256:[0-9a-f]{64}$' OR
                   NEW.target_content_digest !~ '^sha256:[0-9a-f]{64}$' THEN
                    RAISE EXCEPTION 'ArtifactReview digest is invalid';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.artifacts a
                      JOIN medtrust.compute_jobs cj ON cj.id=a.compute_job_id
                      JOIN medtrust.contract_objects co ON co.id=cj.contract_object_id
                       AND co.contract_revision_id=cj.contract_revision_id
                      JOIN medtrust.data_product_versions v ON v.id=co.data_product_version_id
                      JOIN medtrust.data_products dp ON dp.id=v.data_product_id
                      JOIN medtrust.contract_parties cp
                        ON cp.contract_revision_id=cj.contract_revision_id
                       AND cp.organization_id=dp.provider_organization_id
                       AND cp.party_role='provider'
                      JOIN medtrust.space_participants sp
                        ON sp.space_id=a.space_id AND sp.organization_id=dp.provider_organization_id
                       AND sp.admission_status='admitted'
                      JOIN medtrust.space_participant_roles spr
                        ON spr.space_participant_id=sp.id AND spr.role_code='provider'
                     WHERE a.id=NEW.artifact_id AND a.space_id=NEW.space_id
                       AND a.content_digest=NEW.target_content_digest
                       AND a.release_status='quarantined'
                       AND dp.provider_organization_id=NEW.responsible_organization_id
                ) THEN RAISE EXCEPTION 'ArtifactReview responsible organization is invalid'; END IF;
                RETURN NEW;
            END IF;
            IF OLD.status IN ('decided','cancelled') THEN
                RAISE EXCEPTION 'terminal ArtifactReview is immutable';
            END IF;
            IF ROW(NEW.space_id,NEW.artifact_id,NEW.target_content_digest,
                   NEW.responsible_organization_id,NEW.routing_rule_digest,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.space_id,OLD.artifact_id,OLD.target_content_digest,
                   OLD.responsible_organization_id,OLD.routing_rule_digest,OLD.created_at) THEN
                RAISE EXCEPTION 'ArtifactReview target and routing are immutable';
            END IF;
            IF NEW.row_version <> OLD.row_version+1 THEN
                RAISE EXCEPTION 'ArtifactReview transition requires row_version increment';
            END IF;
            IF NOT ((OLD.status='pending' AND NEW.status IN ('claimed','cancelled')) OR
                    (OLD.status='claimed' AND NEW.status IN ('decided','cancelled'))) THEN
                RAISE EXCEPTION 'illegal ArtifactReview status transition';
            END IF;
            IF NEW.status IN ('claimed','decided') AND NOT EXISTS (
                SELECT 1 FROM medtrust.organization_members om
                 WHERE om.organization_id=NEW.responsible_organization_id
                   AND om.user_id=NEW.claimed_by_user_id AND om.status='active'
                   AND (om.valid_from IS NULL OR om.valid_from <= clock_timestamp())
                   AND (om.valid_until IS NULL OR om.valid_until > clock_timestamp())
            ) THEN RAISE EXCEPTION 'ArtifactReview claimant is not an active responsible member'; END IF;
            IF NEW.status='decided' THEN
                IF NEW.decision_digest !~ '^sha256:[0-9a-f]{64}$' OR
                   jsonb_typeof(NEW.decision_evidence)<>'object' THEN
                    RAISE EXCEPTION 'ArtifactReview terminal evidence is invalid';
                END IF;
                IF NEW.decision='approved' AND EXISTS (
                    SELECT 1 FROM medtrust.artifacts a WHERE a.id=NEW.artifact_id
                     AND (a.output_policy_evaluation->>'decision'<>'permit' OR
                          jsonb_array_length(COALESCE(a.output_policy_evaluation->'deny_policy_digests','[]'::jsonb))>0)
                ) THEN RAISE EXCEPTION 'Policy deny cannot be overridden by human approval'; END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_artifact_review_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON medtrust.artifact_reviews FOR EACH ROW EXECUTE FUNCTION medtrust.guard_artifact_review_v7()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_artifact_review_guard ON medtrust.artifact_reviews"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_artifact_review_v7()")
    op.execute("DROP TRIGGER IF EXISTS trg_artifact_guard ON medtrust.artifacts")
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_artifact_v7()")
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.assert_artifact_release_ready_v7(uuid)"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS medtrust.assert_artifact_release_audit_ready_v7()"
    )
    op.drop_table("artifact_reviews", schema=SCHEMA)
    op.drop_table("artifacts", schema=SCHEMA)

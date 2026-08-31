"""Add Phase 4 model catalog, demand selection and contract model object.

Revision ID: 20260723_0021
Revises: 20260722_0020
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0021"
down_revision: str | None = "20260722_0020"
branch_labels: str | None = None
depends_on: str | None = None

SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        "role_code IN ('provider','consumer','service_provider','operator',"
        "'space_operator','data_provider','model_provider','data_requester')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_review_tasks_review_type"), "review_tasks", schema=SCHEMA, type_="check"
    )
    op.create_check_constraint(
        op.f("ck_review_tasks_review_type"),
        "review_tasks",
        "review_type IN ('application_precheck','provider_review','data_provider_review',"
        "'model_provider_review','compliance_review','ethics_review')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_contract_parties_party_role"),
        "contract_parties",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_contract_parties_party_role"),
        "contract_parties",
        "party_role IN ('provider','consumer','service_provider','operator_witness',"
        "'data_provider','model_provider','data_requester')",
        schema=SCHEMA,
    )

    op.create_table(
        "model_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("lifecycle_status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "lifecycle_status IN ('draft','active','suspended','archived')",
            name="ck_model_products_lifecycle_status",
        ),
        sa.CheckConstraint("row_version >= 1", name="ck_model_products_row_version_positive"),
        sa.ForeignKeyConstraint(["space_id"], [f"{SCHEMA}.spaces.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["provider_organization_id"], [f"{SCHEMA}.organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_model_products"),
        sa.UniqueConstraint("space_id", "product_code", name="uq_model_products_space_code"),
        sa.UniqueConstraint("space_id", "id", name="uq_model_products_space_id"),
        sa.UniqueConstraint(
            "space_id", "provider_organization_id", "id",
            name="uq_model_products_space_provider_id",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_model_products_space_status_domain",
        "model_products",
        ["space_id", "lifecycle_status", "domain"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_review_task_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            app_status text;
            applicant_org uuid;
            provider_org uuid;
            model_provider_org uuid;
            operator_org uuid;
            participant_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'review task cannot be deleted'; END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.task_status <> 'pending' THEN RAISE EXCEPTION 'new review task must start pending'; END IF;
                SELECT a.status, a.applicant_organization_id, a.provider_organization_id,
                       s.operator_organization_id, ms.model_provider_organization_id
                  INTO app_status, applicant_org, provider_org, operator_org, model_provider_org
                  FROM medtrust.applications a
                  JOIN medtrust.spaces s ON s.id=a.space_id
                  LEFT JOIN medtrust.application_model_selections ms ON ms.application_id=a.id
                 WHERE a.id=NEW.application_id AND a.space_id=NEW.space_id;
                IF app_status IS NULL OR app_status NOT IN ('submitted','prechecking','provider_review') THEN
                    RAISE EXCEPTION 'review task requires a submitted reviewable application';
                END IF;
                IF NEW.assignee_organization_id=applicant_org THEN RAISE EXCEPTION 'applicant organization cannot review itself'; END IF;
                IF NEW.review_type='application_precheck' AND (NEW.assignee_organization_id<>operator_org OR NEW.sequence_no<>10) THEN
                    RAISE EXCEPTION 'application precheck must route to operator at sequence 10';
                END IF;
                IF NEW.review_type IN ('provider_review','data_provider_review') AND (NEW.assignee_organization_id<>provider_org OR NEW.sequence_no<>20) THEN
                    RAISE EXCEPTION 'data provider review must route to provider at sequence 20';
                END IF;
                IF NEW.review_type='model_provider_review' AND (model_provider_org IS NULL OR NEW.assignee_organization_id<>model_provider_org OR NEW.sequence_no<>20) THEN
                    RAISE EXCEPTION 'model provider review must route to model provider at sequence 20';
                END IF;
                IF NEW.review_type IN ('compliance_review','ethics_review') AND NEW.sequence_no<>20 THEN
                    RAISE EXCEPTION 'conditional review must use sequence 20';
                END IF;
                SELECT admission_status INTO participant_status FROM medtrust.space_participants
                 WHERE space_id=NEW.space_id AND organization_id=NEW.assignee_organization_id;
                IF participant_status IS DISTINCT FROM 'admitted' THEN RAISE EXCEPTION 'review organization must be an admitted participant'; END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.space_id,NEW.review_type,NEW.application_id,NEW.application_snapshot_id,
                   NEW.target_digest,NEW.assignee_organization_id,NEW.sequence_no,NEW.is_required,
                   NEW.routing_rule_digest,NEW.created_by,NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.space_id,OLD.review_type,OLD.application_id,OLD.application_snapshot_id,
                   OLD.target_digest,OLD.assignee_organization_id,OLD.sequence_no,OLD.is_required,
                   OLD.routing_rule_digest,OLD.created_by,OLD.created_at) THEN
                RAISE EXCEPTION 'review task target and routing fields are immutable';
            END IF;
            IF OLD.task_status='pending' AND NEW.task_status NOT IN ('claimed','cancelled') THEN RAISE EXCEPTION 'invalid pending review task transition';
            ELSIF OLD.task_status='claimed' AND NEW.task_status NOT IN ('pending','decided','cancelled') THEN RAISE EXCEPTION 'invalid claimed review task transition';
            ELSIF OLD.task_status IN ('decided','cancelled') THEN RAISE EXCEPTION 'terminal review task is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.create_index(
        "ix_model_products_provider_status",
        "model_products",
        ["provider_organization_id", "lifecycle_status"],
        schema=SCHEMA,
    )

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("version_label", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), server_default="draft", nullable=False),
        sa.Column("entrypoint_id", sa.String(96), nullable=False),
        sa.Column("model_digest", sa.String(71), nullable=False),
        sa.Column("manifest_digest", sa.String(71), nullable=False),
        sa.Column("registry_digest", sa.String(71), nullable=False),
        sa.Column("runtime", sa.Text(), nullable=False),
        sa.Column("input_schema_version", sa.Text(), nullable=False),
        sa.Column("output_schema_version", sa.Text(), nullable=False),
        sa.Column("compatibility_metadata", JSONB, nullable=False),
        sa.Column("license_metadata", JSONB, nullable=False),
        sa.Column("default_policy_template", JSONB, nullable=False),
        sa.Column("default_policy_digest", sa.String(71), nullable=False),
        sa.Column("snapshot_digest", sa.String(71), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint("version_no > 0", name="ck_model_versions_version_no_positive"),
        sa.CheckConstraint(
            "status IN ('draft','under_review','approved','retired')",
            name="ck_model_versions_status",
        ),
        sa.CheckConstraint(
            "model_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "manifest_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "registry_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "default_policy_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_model_versions_digest_formats",
        ),
        sa.CheckConstraint(
            "snapshot_digest IS NULL OR snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_model_versions_snapshot_digest_format",
        ),
        sa.CheckConstraint(
            "status = 'draft' OR snapshot_digest IS NOT NULL",
            name="ck_model_versions_snapshot_required_after_draft",
        ),
        sa.CheckConstraint(
            "(approved_at IS NULL AND approved_by IS NULL) OR "
            "(approved_at IS NOT NULL AND approved_by IS NOT NULL)",
            name="ck_model_versions_approval_pair",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "model_product_id"],
            [f"{SCHEMA}.model_products.space_id", f"{SCHEMA}.model_products.id"],
            name="fk_model_versions_space_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["approved_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_model_versions"),
        sa.UniqueConstraint("model_product_id", "version_no", name="uq_model_versions_product_no"),
        sa.UniqueConstraint("model_product_id", "version_label", name="uq_model_versions_product_label"),
        sa.UniqueConstraint("model_product_id", "id", name="uq_model_versions_product_id"),
        sa.UniqueConstraint("space_id", "id", name="uq_model_versions_space_id"),
        sa.UniqueConstraint("id", "snapshot_digest", name="uq_model_versions_id_digest"),
        sa.UniqueConstraint(
            "entrypoint_id", "model_digest", "registry_digest",
            name="uq_model_versions_registry_binding",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_model_versions_product_status_no",
        "model_versions",
        ["model_product_id", "status", sa.text("version_no DESC")],
        schema=SCHEMA,
    )
    op.create_index("ix_model_versions_model_digest", "model_versions", ["model_digest"], schema=SCHEMA)

    op.create_table(
        "model_publications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("visibility", sa.String(24), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('active','withdrawn','expired')",
            name="ck_model_publications_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('space','restricted','invitation_only')",
            name="ck_model_publications_visibility",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND ended_at IS NULL) OR "
            "(status <> 'active' AND ended_at IS NOT NULL)",
            name="ck_model_publications_lifecycle_shape",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "model_product_id"],
            [f"{SCHEMA}.model_products.space_id", f"{SCHEMA}.model_products.id"],
            name="fk_model_publications_space_product",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_product_id", "model_version_id"],
            [f"{SCHEMA}.model_versions.model_product_id", f"{SCHEMA}.model_versions.id"],
            name="fk_model_publications_product_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["published_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_model_publications"),
        sa.UniqueConstraint(
            "model_product_id", "model_version_id", name="uq_model_publications_version"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_model_publications_active_product",
        "model_publications",
        ["model_product_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_model_publications_space_status",
        "model_publications",
        ["space_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "application_model_selections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("space_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_provider_organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("requested_model_policy_digest", sa.String(71), nullable=False),
        sa.Column("registry_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "model_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "requested_model_policy_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "registry_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_application_model_selections_digest_formats",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_application_model_selection_application_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["space_id", "model_provider_organization_id", "model_product_id"],
            [
                f"{SCHEMA}.model_products.space_id",
                f"{SCHEMA}.model_products.provider_organization_id",
                f"{SCHEMA}.model_products.id",
            ],
            name="fk_application_model_selection_product_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_product_id", "model_version_id"],
            [f"{SCHEMA}.model_versions.model_product_id", f"{SCHEMA}.model_versions.id"],
            name="fk_application_model_selection_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_application_model_selections"),
        sa.UniqueConstraint("application_id", name="uq_application_model_selection_application"),
        sa.UniqueConstraint(
            "application_id", "id", "model_version_id",
            name="uq_application_model_selection_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_model_selection_version",
        "application_model_selections",
        ["model_version_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_application_model_selection_provider",
        "application_model_selections",
        ["model_provider_organization_id", "application_id"],
        schema=SCHEMA,
    )

    op.create_table(
        "contract_model_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contract_revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_snapshot_digest", sa.String(71), nullable=False),
        sa.Column("model_name_snapshot", sa.Text(), nullable=False),
        sa.Column("authorized_scope", JSONB, nullable=False),
        sa.Column("authorized_scope_digest", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "model_snapshot_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "authorized_scope_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_contract_model_objects_digest_formats",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"], [f"{SCHEMA}.contract_revisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"], [f"{SCHEMA}.model_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["created_by"], [f"{SCHEMA}.users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_contract_model_objects"),
        sa.UniqueConstraint(
            "contract_revision_id", name="uq_contract_model_objects_revision"
        ),
        sa.UniqueConstraint(
            "contract_revision_id", "id", "model_version_id",
            name="uq_contract_model_objects_scope",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_model_objects_model_version",
        "contract_model_objects",
        ["model_version_id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("contract_model_objects", schema=SCHEMA)
    op.drop_table("application_model_selections", schema=SCHEMA)
    op.drop_table("model_publications", schema=SCHEMA)
    op.drop_table("model_versions", schema=SCHEMA)
    op.drop_table("model_products", schema=SCHEMA)

    op.execute(
        """
        CREATE OR REPLACE FUNCTION medtrust.guard_review_task_lifecycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE app_status text; applicant_org uuid; provider_org uuid; operator_org uuid; participant_status text;
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'review task cannot be deleted'; END IF;
            IF TG_OP='INSERT' THEN
                IF NEW.task_status<>'pending' THEN RAISE EXCEPTION 'new review task must start pending'; END IF;
                SELECT a.status,a.applicant_organization_id,a.provider_organization_id,s.operator_organization_id
                  INTO app_status,applicant_org,provider_org,operator_org
                  FROM medtrust.applications a JOIN medtrust.spaces s ON s.id=a.space_id
                 WHERE a.id=NEW.application_id AND a.space_id=NEW.space_id;
                IF app_status IS NULL OR app_status NOT IN ('submitted','prechecking','provider_review') THEN RAISE EXCEPTION 'review task requires a submitted reviewable application'; END IF;
                IF NEW.assignee_organization_id=applicant_org THEN RAISE EXCEPTION 'applicant organization cannot review itself'; END IF;
                IF NEW.review_type='application_precheck' AND (NEW.assignee_organization_id<>operator_org OR NEW.sequence_no<>10) THEN RAISE EXCEPTION 'application precheck must route to operator at sequence 10'; END IF;
                IF NEW.review_type='provider_review' AND (NEW.assignee_organization_id<>provider_org OR NEW.sequence_no<>20) THEN RAISE EXCEPTION 'provider review must route to provider at sequence 20'; END IF;
                IF NEW.review_type IN ('compliance_review','ethics_review') AND NEW.sequence_no<>20 THEN RAISE EXCEPTION 'conditional review must use sequence 20'; END IF;
                SELECT admission_status INTO participant_status FROM medtrust.space_participants WHERE space_id=NEW.space_id AND organization_id=NEW.assignee_organization_id;
                IF participant_status IS DISTINCT FROM 'admitted' THEN RAISE EXCEPTION 'review organization must be an admitted participant'; END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.space_id,NEW.review_type,NEW.application_id,NEW.application_snapshot_id,NEW.target_digest,NEW.assignee_organization_id,NEW.sequence_no,NEW.is_required,NEW.routing_rule_digest,NEW.created_by,NEW.created_at)
               IS DISTINCT FROM ROW(OLD.space_id,OLD.review_type,OLD.application_id,OLD.application_snapshot_id,OLD.target_digest,OLD.assignee_organization_id,OLD.sequence_no,OLD.is_required,OLD.routing_rule_digest,OLD.created_by,OLD.created_at) THEN RAISE EXCEPTION 'review task target and routing fields are immutable'; END IF;
            IF OLD.task_status='pending' AND NEW.task_status NOT IN ('claimed','cancelled') THEN RAISE EXCEPTION 'invalid pending review task transition';
            ELSIF OLD.task_status='claimed' AND NEW.task_status NOT IN ('pending','decided','cancelled') THEN RAISE EXCEPTION 'invalid claimed review task transition';
            ELSIF OLD.task_status IN ('decided','cancelled') THEN RAISE EXCEPTION 'terminal review task is immutable'; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.drop_constraint(op.f("ck_contract_parties_party_role"), "contract_parties", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_contract_parties_party_role"),
        "contract_parties",
        "party_role IN ('provider','consumer','service_provider','operator_witness')",
        schema=SCHEMA,
    )
    op.drop_constraint(op.f("ck_review_tasks_review_type"), "review_tasks", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_review_tasks_review_type"),
        "review_tasks",
        "review_type IN ('application_precheck','provider_review','compliance_review','ethics_review')",
        schema=SCHEMA,
    )
    op.drop_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_space_participant_roles_ck_space_participant_roles_role_code"),
        "space_participant_roles",
        "role_code IN ('provider','consumer','service_provider','operator')",
        schema=SCHEMA,
    )

"""Create the Phase 2-B.5-B1 Contract Core tables.

Revision ID: 20260722_0008
Revises: 20260722_0007
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0008"
down_revision: str | None = "20260722_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_product_versions_id_digest",
        "data_product_versions",
        ["id", "snapshot_digest"],
        schema=SCHEMA,
    )

    op.create_table(
        "contracts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("application_snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("application_snapshot_digest", sa.Text(), nullable=False),
        sa.Column(
            "eligibility_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("eligibility_digest", sa.Text(), nullable=False),
        sa.Column("contract_number", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "is_demo",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.CheckConstraint("is_demo = true", name="demo_only"),
        sa.CheckConstraint(
            "application_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="snapshot_digest_shape",
        ),
        sa.CheckConstraint(
            "eligibility_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="eligibility_digest_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(eligibility_evidence) = 'object'",
            name="eligibility_evidence_object",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"],
            [f"{SCHEMA}.spaces.id"],
            name="fk_contracts_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_contracts_application_space",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "application_id",
                "application_snapshot_id",
                "application_snapshot_digest",
            ],
            [
                f"{SCHEMA}.application_snapshots.application_id",
                f"{SCHEMA}.application_snapshots.id",
                f"{SCHEMA}.application_snapshots.snapshot_digest",
            ],
            name="fk_contracts_snapshot_evidence",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_contracts_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contracts"),
        sa.UniqueConstraint("application_id", name="uq_contracts_application"),
        sa.UniqueConstraint(
            "space_id", "contract_number", name="uq_contracts_space_number"
        ),
        sa.UniqueConstraint("id", "space_id", name="uq_contracts_id_space"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contracts_space_created",
        "contracts",
        ["space_id", sa.text("created_at DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contracts_snapshot_digest",
        "contracts",
        ["application_snapshot_digest"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contracts_eligibility_digest",
        "contracts",
        ["eligibility_digest"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contracts_created_by",
        "contracts",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "contract_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("supersedes_revision_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("terms_schema_version", sa.Text(), nullable=False),
        sa.Column(
            "terms_document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("terms_digest", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="draft", nullable=False
        ),
        sa.Column("signing_mode", sa.String(length=24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "handoff_guard_evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("handoff_guard_digest", sa.Text(), nullable=True),
        sa.Column("content_digest", sa.Text(), nullable=True),
        sa.Column("proposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("revision_no > 0", name="revision_no_positive"),
        sa.CheckConstraint(
            "status IN ('draft','proposed','signed','active','suspended',"
            "'expired','terminated','superseded','withdrawn')",
            name="status",
        ),
        sa.CheckConstraint(
            "signing_mode IN ('peer_to_peer','platform_mediated','multi_party')",
            name="signing_mode",
        ),
        sa.CheckConstraint("terms_schema_version <> ''", name="terms_schema_nonempty"),
        sa.CheckConstraint(
            "jsonb_typeof(terms_document) = 'object'",
            name="terms_document_object",
        ),
        sa.CheckConstraint(
            "terms_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="terms_digest_shape",
        ),
        sa.CheckConstraint(
            "handoff_guard_evidence IS NULL OR "
            "jsonb_typeof(handoff_guard_evidence) = 'object'",
            name="handoff_evidence_object",
        ),
        sa.CheckConstraint(
            "handoff_guard_digest IS NULL OR "
            "handoff_guard_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="handoff_digest_shape",
        ),
        sa.CheckConstraint(
            "content_digest IS NULL OR content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="content_digest_shape",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NOT NULL",
            name="effective_from_required",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_window",
        ),
        sa.CheckConstraint(
            "supersedes_revision_id IS NULL OR supersedes_revision_id <> id",
            name="not_self_superseding",
        ),
        sa.CheckConstraint("row_version >= 1", name="row_version_positive"),
        sa.ForeignKeyConstraint(
            ["contract_id"],
            [f"{SCHEMA}.contracts.id"],
            name="fk_contract_revisions_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_revision_id", "contract_id"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.contract_id",
            ],
            name="fk_contract_revisions_supersedes",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_contract_revisions_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_revisions"),
        sa.UniqueConstraint(
            "contract_id", "revision_no", name="uq_contract_revisions_contract_no"
        ),
        sa.UniqueConstraint(
            "id", "contract_id", name="uq_contract_revisions_id_contract"
        ),
        sa.UniqueConstraint(
            "id", "content_digest", name="uq_contract_revisions_id_digest"
        ),
        sa.UniqueConstraint(
            "contract_id",
            "content_digest",
            name="uq_contract_revisions_contract_digest",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_contract_revisions_open_candidate",
        "contract_revisions",
        ["contract_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('draft','proposed','signed')"),
    )
    op.create_index(
        "uq_contract_revisions_live",
        "contract_revisions",
        ["contract_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('active','suspended')"),
    )
    op.create_index(
        "ix_contract_revisions_contract_no_desc",
        "contract_revisions",
        ["contract_id", sa.text("revision_no DESC")],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_revisions_status_until",
        "contract_revisions",
        ["status", "effective_until"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_revisions_created_by",
        "contract_revisions",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "contract_parties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("party_role", sa.String(length=24), nullable=False),
        sa.Column("signing_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_required",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("party_name_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "identity_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "party_role IN ('provider','consumer','service_provider','operator_witness')",
            name="party_role",
        ),
        sa.CheckConstraint("signing_order > 0", name="signing_order_positive"),
        sa.CheckConstraint(
            "jsonb_typeof(identity_snapshot) = 'object'",
            name="identity_snapshot_object",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"],
            [f"{SCHEMA}.contract_revisions.id"],
            name="fk_contract_parties_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            [f"{SCHEMA}.organizations.id"],
            name="fk_contract_parties_organization",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_contract_parties_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_parties"),
        sa.UniqueConstraint(
            "contract_revision_id",
            "organization_id",
            "party_role",
            name="uq_contract_parties_revision_org_role",
        ),
        sa.UniqueConstraint(
            "contract_revision_id", "id", name="uq_contract_parties_revision_id"
        ),
        sa.UniqueConstraint(
            "contract_revision_id",
            "id",
            "organization_id",
            name="uq_contract_parties_revision_id_org",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_parties_org_role_revision",
        "contract_parties",
        ["organization_id", "party_role", "contract_revision_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_parties_revision_order_role",
        "contract_parties",
        ["contract_revision_id", "signing_order", "party_role"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_parties_created_by",
        "contract_parties",
        ["created_by"],
        schema=SCHEMA,
    )

    op.create_table(
        "contract_objects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("data_product_version_id", sa.Uuid(), nullable=False),
        sa.Column("product_snapshot_digest", sa.Text(), nullable=False),
        sa.Column("product_name_snapshot", sa.Text(), nullable=False),
        sa.Column(
            "authorized_scope",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("authorized_scope_digest", sa.Text(), nullable=False),
        sa.Column("position_no", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.CheckConstraint("position_no > 0", name="position_no_positive"),
        sa.CheckConstraint(
            "product_snapshot_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="product_digest_shape",
        ),
        sa.CheckConstraint(
            "authorized_scope_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="scope_digest_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(authorized_scope) = 'object'",
            name="authorized_scope_object",
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"],
            [f"{SCHEMA}.contract_revisions.id"],
            name="fk_contract_objects_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["data_product_version_id", "product_snapshot_digest"],
            [
                f"{SCHEMA}.data_product_versions.id",
                f"{SCHEMA}.data_product_versions.snapshot_digest",
            ],
            name="fk_contract_objects_version_digest",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            [f"{SCHEMA}.users.id"],
            name="fk_contract_objects_created_by",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_objects"),
        sa.UniqueConstraint(
            "contract_revision_id",
            "data_product_version_id",
            name="uq_contract_objects_revision_version",
        ),
        sa.UniqueConstraint(
            "contract_revision_id", "position_no", name="uq_contract_objects_revision_pos"
        ),
        sa.UniqueConstraint(
            "contract_revision_id", "id", name="uq_contract_objects_revision_id"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_objects_version_revision",
        "contract_objects",
        ["data_product_version_id", "contract_revision_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_objects_created_by",
        "contract_objects",
        ["created_by"],
        schema=SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_source()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            application_status text;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'contract series cannot be deleted';
            END IF;

            IF TG_OP = 'INSERT' THEN
                SELECT status INTO application_status
                  FROM medtrust.applications
                 WHERE id = NEW.application_id AND space_id = NEW.space_id;
                IF application_status IS DISTINCT FROM 'approved' THEN
                    RAISE EXCEPTION 'contract requires an approved application';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.space_id, NEW.application_id, NEW.application_snapshot_id,
                   NEW.application_snapshot_digest, NEW.eligibility_evidence,
                   NEW.eligibility_digest, NEW.contract_number, NEW.created_at,
                   NEW.created_by, NEW.is_demo)
               IS DISTINCT FROM
               ROW(OLD.space_id, OLD.application_id, OLD.application_snapshot_id,
                   OLD.application_snapshot_digest, OLD.eligibility_evidence,
                   OLD.eligibility_digest, OLD.contract_number, OLD.created_at,
                   OLD.created_by, OLD.is_demo) THEN
                RAISE EXCEPTION 'contract source evidence is immutable';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_source_immutable "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.contracts "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_source()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_revision_core()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' THEN
                    RAISE EXCEPTION 'new contract revision must start as draft';
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft' THEN
                    RAISE EXCEPTION 'only an unproposed draft revision can be deleted';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.status <> 'draft' THEN
                RAISE EXCEPTION 'proposed or terminal revision is immutable in B1';
            END IF;
            IF NEW.status = 'draft' THEN
                RETURN NEW;
            END IF;
            IF NEW.status = 'withdrawn' THEN
                IF NEW.ended_at IS NULL THEN
                    RAISE EXCEPTION 'withdrawn revision requires ended_at';
                END IF;
                IF ROW(NEW.contract_id, NEW.revision_no, NEW.supersedes_revision_id,
                       NEW.name, NEW.summary, NEW.terms_schema_version,
                       NEW.terms_document, NEW.terms_digest, NEW.signing_mode,
                       NEW.effective_from, NEW.effective_until,
                       NEW.handoff_guard_evidence, NEW.handoff_guard_digest,
                       NEW.content_digest, NEW.created_at, NEW.created_by)
                   IS DISTINCT FROM
                   ROW(OLD.contract_id, OLD.revision_no, OLD.supersedes_revision_id,
                       OLD.name, OLD.summary, OLD.terms_schema_version,
                       OLD.terms_document, OLD.terms_digest, OLD.signing_mode,
                       OLD.effective_from, OLD.effective_until,
                       OLD.handoff_guard_evidence, OLD.handoff_guard_digest,
                       OLD.content_digest, OLD.created_at, OLD.created_by) THEN
                    RAISE EXCEPTION 'withdrawing a draft cannot change revision content';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'proposal is unavailable until Policy and Binding exist';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_revision_core "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.contract_revisions "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_revision_core()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_party_core()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_status text;
            provider_org uuid;
            consumer_org uuid;
        BEGIN
            SELECT r.status, a.provider_organization_id, a.applicant_organization_id
              INTO parent_status, provider_org, consumer_org
              FROM medtrust.contract_revisions r
              JOIN medtrust.contracts c ON c.id = r.contract_id
              JOIN medtrust.applications a ON a.id = c.application_id
             WHERE r.id = COALESCE(NEW.contract_revision_id, OLD.contract_revision_id);
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'contract parties can only change in draft';
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.party_role = 'provider' AND
               NEW.organization_id IS DISTINCT FROM provider_org THEN
                RAISE EXCEPTION 'provider party must match the application provider';
            END IF;
            IF TG_OP <> 'DELETE' AND NEW.party_role = 'consumer' AND
               NEW.organization_id IS DISTINCT FROM consumer_org THEN
                RAISE EXCEPTION 'consumer party must match the application applicant';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_party_core "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.contract_parties "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_party_core()"
    )

    op.execute(
        """
        CREATE FUNCTION medtrust.guard_contract_object_core()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent_status text;
            source_application_id uuid;
            requested_digest text;
            source_requested_scope jsonb;
        BEGIN
            SELECT r.status, c.application_id
              INTO parent_status, source_application_id
              FROM medtrust.contract_revisions r
              JOIN medtrust.contracts c ON c.id = r.contract_id
             WHERE r.id = COALESCE(NEW.contract_revision_id, OLD.contract_revision_id);
            IF parent_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'contract objects can only change in draft';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;

            SELECT ai.requested_product_snapshot_digest, ai.requested_scope
              INTO requested_digest, source_requested_scope
              FROM medtrust.application_items ai
             WHERE ai.application_id = source_application_id
               AND ai.data_product_version_id = NEW.data_product_version_id;
            IF requested_digest IS NULL THEN
                RAISE EXCEPTION 'contract object is outside the approved application';
            END IF;
            IF NEW.product_snapshot_digest IS DISTINCT FROM requested_digest THEN
                RAISE EXCEPTION 'contract object digest differs from application item';
            END IF;
            IF NOT (source_requested_scope @> NEW.authorized_scope) THEN
                RAISE EXCEPTION 'authorized scope must narrow the requested scope';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER trg_contract_object_core "
        "BEFORE INSERT OR UPDATE OR DELETE ON medtrust.contract_objects "
        "FOR EACH ROW EXECUTE FUNCTION medtrust.guard_contract_object_core()"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_object_core "
        "ON medtrust.contract_objects"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_object_core()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_party_core "
        "ON medtrust.contract_parties"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_party_core()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_revision_core "
        "ON medtrust.contract_revisions"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_revision_core()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_contract_source_immutable "
        "ON medtrust.contracts"
    )
    op.execute("DROP FUNCTION IF EXISTS medtrust.guard_contract_source()")

    op.drop_table("contract_objects", schema=SCHEMA)
    op.drop_table("contract_parties", schema=SCHEMA)
    op.drop_table("contract_revisions", schema=SCHEMA)
    op.drop_table("contracts", schema=SCHEMA)
    op.drop_constraint(
        "uq_product_versions_id_digest",
        "data_product_versions",
        schema=SCHEMA,
        type_="unique",
    )

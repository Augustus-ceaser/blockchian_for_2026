"""phase 5.12.3A external model governance

Revision ID: 20260727_0043
Revises: 20260727_0042
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260727_0043"
down_revision = "20260727_0042"
branch_labels = None
depends_on = None
SCHEMA = "medtrust"
JSONB = postgresql.JSONB(astext_type=sa.Text())
EVENTS = (
    "external_model_catalog.governance.profile.initialized",
    "external_model_catalog.governance.review.created",
    "external_model_catalog.governance.review.superseded",
    "external_model_catalog.family.resolved",
    "external_model_catalog.governance.recalculated",
    "external_model_catalog.productization.eligibility.changed",
)


def _check(column: str, values: tuple[str, ...], name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} IN ({','.join(repr(value) for value in values)})", name=name
    )


def _audit_values() -> list[str]:
    definition = op.get_bind().execute(sa.text(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class t ON t.oid=c.conrelid JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname='medtrust' AND t.relname='audit_events' "
        "AND c.conname='ck_audit_events_ck_audit_events_event_type'"
    )).scalar_one()
    start = definition.index("ARRAY[") + 6
    end = definition.index("])", start)
    return [item.strip().split("::", 1)[0].strip().strip("'")
            for item in definition[start:end].split(",") if item.strip()]


def _replace_audit_values(values: list[str]) -> None:
    rendered = ",".join(repr(value) for value in values)
    op.execute("ALTER TABLE medtrust.audit_events DROP CONSTRAINT ck_audit_events_ck_audit_events_event_type")
    op.execute(
        "ALTER TABLE medtrust.audit_events ADD CONSTRAINT "
        "ck_audit_events_ck_audit_events_event_type "
        f"CHECK (event_type IN ({rendered}))"
    )


def _change_audit(enable: bool) -> None:
    values = _audit_values()
    if enable:
        values.extend(value for value in EVENTS if value not in values)
    else:
        values = [value for value in values if value not in EVENTS]
    _replace_audit_values(values)
    guard = op.get_bind().execute(sa.text(
        "SELECT pg_get_functiondef('medtrust.guard_audit_event_v8()'::regprocedure)"
    )).scalar_one()
    cases = "".join(
        f"""
                WHEN '{event}' THEN
                    IF NEW.subject_type<>'external_catalog_source' OR NEW.result<>'success' THEN RAISE EXCEPTION 'invalid model governance event shape' USING ERRCODE='23514'; END IF;
                    SELECT EXISTS(SELECT 1 FROM medtrust.external_catalog_sources s WHERE s.id=NEW.subject_id AND s.space_id=NEW.space_id AND s.resource_kind='model') INTO v_subject_ok;
"""
        for event in EVENTS
    )
    marker = "                WHEN 'contract.revision.proposed' THEN"
    if enable:
        op.execute(guard.replace(marker, cases + marker, 1))
    else:
        op.execute(guard.replace(cases, "", 1))


def upgrade() -> None:
    op.create_table(
        "external_model_governance_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT"), nullable=False, unique=True),
        sa.Column("primary_status", sa.String(48), nullable=False),
        sa.Column("source_review_status", sa.String(48), nullable=False),
        sa.Column("paper_review_status", sa.String(48), nullable=False),
        sa.Column("repository_review_status", sa.String(48), nullable=False),
        sa.Column("model_card_review_status", sa.String(48), nullable=False),
        sa.Column("license_review_status", sa.String(48), nullable=False),
        sa.Column("weight_review_status", sa.String(48), nullable=False),
        sa.Column("revision_review_status", sa.String(48), nullable=False),
        sa.Column("technical_contract_score", sa.Integer(), nullable=False),
        sa.Column("technical_missing_fields", JSONB, nullable=False, server_default="[]"),
        sa.Column("clinical_boundary_status", sa.String(48), nullable=False),
        sa.Column("security_review_status", sa.String(32), nullable=False),
        sa.Column("security_risk_flags", JSONB, nullable=False, server_default="[]"),
        sa.Column("model_family_status", sa.String(24), nullable=False),
        sa.Column("potential_family_key", sa.Text()),
        sa.Column("productization_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocking_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("warning_reasons", JSONB, nullable=False, server_default="[]"),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("last_reviewed_by", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _check("primary_status", ("unreviewed","needs_source_review","needs_paper_review","needs_repository_review","needs_model_card_review","needs_license_review","needs_weight_review","needs_revision_review","technical_contract_incomplete","clinical_boundary_unclear","security_review_required","family_resolution_pending","in_review","eligible_for_model_draft","blocked","rejected","archived"), "ck_external_model_governance_profiles_primary_status"),
        _check("source_review_status", ("unreviewed","official_source_confirmed","author_source_confirmed","aggregator_only","source_missing","source_disputed"), "ck_external_model_governance_profiles_source_status"),
        _check("paper_review_status", ("unreviewed","official_paper_confirmed","preprint_only","paper_missing","paper_disputed","not_applicable"), "ck_external_model_governance_profiles_paper_status"),
        _check("repository_review_status", ("unreviewed","official_repository_confirmed","repository_archived","fork_only","repository_missing","repository_disputed","not_applicable"), "ck_external_model_governance_profiles_repository_status"),
        _check("model_card_review_status", ("unreviewed","official_model_card_confirmed","incomplete","missing","not_applicable"), "ck_external_model_governance_profiles_model_card_status"),
        _check("license_review_status", ("unknown","permissive","research_only","noncommercial","custom_terms","restricted","redistribution_prohibited","unverified","not_applicable"), "ck_external_model_governance_profiles_license_status"),
        _check("weight_review_status", ("unknown","not_released","metadata_only","public_available","gated","registration_required","request_required","author_request","unavailable"), "ck_external_model_governance_profiles_weight_status"),
        _check("revision_review_status", ("unknown","unpinned","commit_pinned","release_tag_pinned","model_revision_pinned","conflicting_versions"), "ck_external_model_governance_profiles_revision_status"),
        _check("clinical_boundary_status", ("not_assessed","research_only","non_clinical","clinical_claimed_by_source","regulatory_cleared","prohibited","unclear"), "ck_external_model_governance_profiles_clinical_status"),
        _check("security_review_status", ("unreviewed","review_required","cleared","blocked"), "ck_external_model_governance_profiles_security_status"),
        _check("model_family_status", ("none","potential","pending","resolved","disputed"), "ck_external_model_governance_profiles_family_status"),
        sa.CheckConstraint("technical_contract_score BETWEEN 0 AND 100", name="ck_external_model_governance_profiles_technical_score"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_model_governance_primary", "external_model_governance_profiles", ["primary_status"], schema=SCHEMA)
    op.create_index("ix_external_model_governance_dimensions", "external_model_governance_profiles", ["license_review_status","weight_review_status","revision_review_status"], schema=SCHEMA)
    op.create_index("ix_external_model_governance_family", "external_model_governance_profiles", ["model_family_status","potential_family_key"], schema=SCHEMA)

    op.create_table(
        "external_model_governance_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("record_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("review_dimension", sa.String(32), nullable=False),
        sa.Column("previous_value", sa.Text()),
        sa.Column("decision", sa.String(64), nullable=False),
        sa.Column("decision_payload", JSONB, nullable=False, server_default="{}"),
        sa.Column("evidence_type", sa.String(40), nullable=False),
        sa.Column("evidence_reference", sa.Text()),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("reviewer_user_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewer_organization_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_digest", sa.String(64), nullable=False),
        sa.Column("supersedes_review_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_model_governance_reviews.id", ondelete="RESTRICT")),
        sa.Column("idempotency_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _check("review_dimension", ("source","paper","repository","model_card","license","weights","revision","technical_contract","clinical_boundary","security","model_family","productization"), "ck_external_model_governance_reviews_dimension"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_model_reviews_record_time", "external_model_governance_reviews", ["record_id","reviewed_at"], schema=SCHEMA)
    op.create_index("ix_external_model_reviews_dimension", "external_model_governance_reviews", ["review_dimension","decision"], schema=SCHEMA)
    op.execute("CREATE OR REPLACE FUNCTION medtrust.guard_external_model_review_append_only() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'external model governance reviews are append-only' USING ERRCODE='55000'; END $$")
    op.execute("CREATE TRIGGER trg_external_model_review_append_only BEFORE UPDATE OR DELETE ON medtrust.external_model_governance_reviews FOR EACH ROW EXECUTE FUNCTION medtrust.guard_external_model_review_append_only()")

    op.create_table(
        "external_model_family_resolutions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("model_family_key", sa.Text(), nullable=False, unique=True),
        sa.Column("resolution_status", sa.String(24), nullable=False),
        sa.Column("canonical_record_id", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.external_model_records.id", ondelete="RESTRICT")),
        sa.Column("resolution_type", sa.String(48), nullable=False),
        sa.Column("member_record_ids", JSONB, nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), sa.ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_digest", sa.String(71), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        _check("resolution_status", ("resolved","unresolved","disputed"), "ck_external_model_family_resolutions_status"),
        _check("resolution_type", ("same_model_aliases","model_variants","backbone_and_task_model","different_models_same_paper","repository_fork","false_positive","unresolved"), "ck_external_model_family_resolutions_type"),
        schema=SCHEMA,
    )
    op.create_index("ix_external_model_family_status", "external_model_family_resolutions", ["resolution_status","model_family_key"], schema=SCHEMA)
    _change_audit(True)


def downgrade() -> None:
    _change_audit(False)
    op.drop_table("external_model_family_resolutions", schema=SCHEMA)
    op.drop_table("external_model_governance_reviews", schema=SCHEMA)
    op.execute("DROP FUNCTION medtrust.guard_external_model_review_append_only()")
    op.drop_table("external_model_governance_profiles", schema=SCHEMA)

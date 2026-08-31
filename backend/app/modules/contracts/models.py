from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now

SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

CONTRACT_REVISION_STATUSES = (
    "draft",
    "proposed",
    "signed",
    "active",
    "suspended",
    "expired",
    "terminated",
    "superseded",
    "withdrawn",
)
CONTRACT_SIGNING_MODES = (
    "peer_to_peer",
    "platform_mediated",
    "multi_party",
)
CONTRACT_PARTY_ROLES = (
    "provider",
    "consumer",
    "service_provider",
    "operator_witness",
    "data_provider",
    "model_provider",
    "data_requester",
)
POLICY_TYPES = ("permission", "prohibition", "obligation")
POLICY_EFFECTS = ("permit", "deny", "require")
POLICY_ACTION_CODES = (
    "read_catalog_metadata",
    "execute_controlled_compute",
    "export_artifact",
    "export_raw_data",
    "reidentify_subject",
    "redistribute_data",
    "retain_intermediate",
    "delete_intermediate",
    "write_audit_log",
)
POLICY_CONSTRAINT_NAMES = (
    "purpose_code",
    "algorithm_digest",
    "environment_mode",
    "run_count",
    "effective_until",
    "output_type",
    "output_review_required",
    "retention_seconds",
    "region",
    "network_zone",
    "audit_level",
)
POLICY_CONSTRAINT_OPERATORS = ("eq", "in", "lte", "gte", "before", "after")
POLICY_EXECUTION_ROLES = (
    "compute_executor",
    "egress_controller",
    "audit_evidence_emitter",
)
POLICY_CAPABILITY_CODES = (
    "controlled_compute_execution",
    "egress_policy_enforcement",
    "audit_evidence_emit",
)
POLICY_BINDING_STATUSES = ("pending", "accepted", "rejected", "revoked")
CONTRACT_SIGNATURE_TYPES = ("demo",)
CONTRACT_SIGNATURE_VERIFICATION_STATUSES = ("verified",)


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.spaces.id",
            name="fk_contracts_space",
            ondelete="RESTRICT",
        )
    )
    application_id: Mapped[UUID] = mapped_column()
    application_snapshot_id: Mapped[UUID] = mapped_column()
    application_snapshot_digest: Mapped[str] = mapped_column(Text)
    eligibility_evidence: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    eligibility_digest: Mapped[str] = mapped_column(Text)
    contract_number: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_contracts_created_by",
            ondelete="RESTRICT",
        )
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_demo: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )

    revisions: Mapped[list[ContractRevision]] = relationship(
        back_populates="contract",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ContractRevision.revision_no",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["application_id", "space_id"],
            [f"{SCHEMA}.applications.id", f"{SCHEMA}.applications.space_id"],
            name="fk_contracts_application_space",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        CheckConstraint("is_demo = true", name="demo_only"),
        UniqueConstraint("application_id", name="uq_contracts_application"),
        UniqueConstraint(
            "space_id", "contract_number", name="uq_contracts_space_number"
        ),
        UniqueConstraint("id", "space_id", name="uq_contracts_id_space"),
        Index("ix_contracts_space_created", "space_id", text("created_at DESC")),
        Index("ix_contracts_snapshot_digest", "application_snapshot_digest"),
        Index("ix_contracts_eligibility_digest", "eligibility_digest"),
        Index("ix_contracts_created_by", "created_by"),
    )


class ContractRevision(Base):
    __tablename__ = "contract_revisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_id: Mapped[UUID] = mapped_column()
    revision_no: Mapped[int] = mapped_column(Integer)
    supersedes_revision_id: Mapped[UUID | None] = mapped_column()
    name: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    terms_schema_version: Mapped[str] = mapped_column(Text)
    terms_document: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    terms_digest: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft"
    )
    signing_mode: Mapped[str] = mapped_column(String(24))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handoff_guard_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DOCUMENT
    )
    handoff_guard_digest: Mapped[str | None] = mapped_column(Text)
    content_digest: Mapped[str | None] = mapped_column(Text)
    proposed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_contract_revisions_created_by",
            ondelete="RESTRICT",
        )
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    contract: Mapped[Contract] = relationship(back_populates="revisions")
    parties: Mapped[list[ContractParty]] = relationship(
        back_populates="revision",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by=("ContractParty.signing_order, ContractParty.party_role"),
    )
    objects: Mapped[list[ContractObject]] = relationship(
        back_populates="revision",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="ContractObject.position_no",
    )
    policies: Mapped[list[Policy]] = relationship(
        back_populates="revision",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by=lambda: (Policy.priority.desc(), Policy.policy_code),
    )
    signatures: Mapped[list[ContractSignature]] = relationship(
        back_populates="revision",
        cascade="save-update, merge",
        passive_deletes=True,
        primaryjoin="ContractRevision.id == ContractSignature.contract_revision_id",
        foreign_keys="ContractSignature.contract_revision_id",
        order_by="ContractSignature.signed_at",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_id"],
            [f"{SCHEMA}.contracts.id"],
            name="fk_contract_revisions_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_revision_id", "contract_id"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.contract_id",
            ],
            name="fk_contract_revisions_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision_no > 0", name="revision_no_positive"),
        CheckConstraint(
            f"status IN ({sql_values(CONTRACT_REVISION_STATUSES)})", name="status"
        ),
        CheckConstraint(
            f"signing_mode IN ({sql_values(CONTRACT_SIGNING_MODES)})",
            name="signing_mode",
        ),
        CheckConstraint("terms_schema_version <> ''", name="terms_schema_nonempty"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NOT NULL",
            name="effective_from_required",
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="effective_window",
        ),
        CheckConstraint(
            "supersedes_revision_id IS NULL OR supersedes_revision_id <> id",
            name="not_self_superseding",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "contract_id", "revision_no", name="uq_contract_revisions_contract_no"
        ),
        UniqueConstraint(
            "id", "contract_id", name="uq_contract_revisions_id_contract"
        ),
        UniqueConstraint(
            "id", "content_digest", name="uq_contract_revisions_id_digest"
        ),
        UniqueConstraint(
            "contract_id",
            "content_digest",
            name="uq_contract_revisions_contract_digest",
        ),
        Index(
            "uq_contract_revisions_open_candidate",
            "contract_id",
            unique=True,
            postgresql_where=text("status IN ('draft','proposed','signed')"),
            sqlite_where=text("status IN ('draft','proposed','signed')"),
        ),
        Index(
            "uq_contract_revisions_live",
            "contract_id",
            unique=True,
            postgresql_where=text("status IN ('active','suspended')"),
            sqlite_where=text("status IN ('active','suspended')"),
        ),
        Index(
            "ix_contract_revisions_contract_no_desc",
            "contract_id",
            text("revision_no DESC"),
        ),
        Index("ix_contract_revisions_status_until", "status", "effective_until"),
        Index("ix_contract_revisions_created_by", "created_by"),
    )


class ContractParty(Base):
    __tablename__ = "contract_parties"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_revisions.id",
            name="fk_contract_parties_revision",
            ondelete="RESTRICT",
        )
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.organizations.id",
            name="fk_contract_parties_organization",
            ondelete="RESTRICT",
        )
    )
    party_role: Mapped[str] = mapped_column(String(24))
    signing_order: Mapped[int] = mapped_column(Integer)
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    party_name_snapshot: Mapped[str] = mapped_column(Text)
    identity_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_contract_parties_created_by",
            ondelete="RESTRICT",
        )
    )

    revision: Mapped[ContractRevision] = relationship(back_populates="parties")

    __table_args__ = (
        CheckConstraint(
            f"party_role IN ({sql_values(CONTRACT_PARTY_ROLES)})", name="party_role"
        ),
        CheckConstraint("signing_order > 0", name="signing_order_positive"),
        UniqueConstraint(
            "contract_revision_id",
            "organization_id",
            "party_role",
            name="uq_contract_parties_revision_org_role",
        ),
        UniqueConstraint(
            "contract_revision_id", "id", name="uq_contract_parties_revision_id"
        ),
        UniqueConstraint(
            "contract_revision_id",
            "id",
            "organization_id",
            name="uq_contract_parties_revision_id_org",
        ),
        Index(
            "ix_contract_parties_org_role_revision",
            "organization_id",
            "party_role",
            "contract_revision_id",
        ),
        Index(
            "ix_contract_parties_revision_order_role",
            "contract_revision_id",
            "signing_order",
            "party_role",
        ),
        Index("ix_contract_parties_created_by", "created_by"),
    )


class ContractObject(Base):
    __tablename__ = "contract_objects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_revisions.id",
            name="fk_contract_objects_revision",
            ondelete="RESTRICT",
        )
    )
    data_product_version_id: Mapped[UUID] = mapped_column()
    product_snapshot_digest: Mapped[str] = mapped_column(Text)
    product_name_snapshot: Mapped[str] = mapped_column(Text)
    authorized_scope: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    authorized_scope_digest: Mapped[str] = mapped_column(Text)
    position_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_contract_objects_created_by",
            ondelete="RESTRICT",
        )
    )

    revision: Mapped[ContractRevision] = relationship(back_populates="objects")

    __table_args__ = (
        ForeignKeyConstraint(
            ["data_product_version_id", "product_snapshot_digest"],
            [
                f"{SCHEMA}.data_product_versions.id",
                f"{SCHEMA}.data_product_versions.snapshot_digest",
            ],
            name="fk_contract_objects_version_digest",
            ondelete="RESTRICT",
        ),
        CheckConstraint("position_no > 0", name="position_no_positive"),
        UniqueConstraint(
            "contract_revision_id",
            "data_product_version_id",
            name="uq_contract_objects_revision_version",
        ),
        UniqueConstraint(
            "contract_revision_id",
            "position_no",
            name="uq_contract_objects_revision_pos",
        ),
        UniqueConstraint(
            "contract_revision_id", "id", name="uq_contract_objects_revision_id"
        ),
        Index(
            "ix_contract_objects_version_revision",
            "data_product_version_id",
            "contract_revision_id",
        ),
        Index("ix_contract_objects_created_by", "created_by"),
    )


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.contract_revisions.id",
            name="fk_policies_revision",
            ondelete="RESTRICT",
        )
    )
    policy_code: Mapped[str] = mapped_column(Text)
    policy_type: Mapped[str] = mapped_column(String(16))
    effect: Mapped[str] = mapped_column(String(8))
    subject_contract_party_id: Mapped[UUID] = mapped_column()
    contract_object_id: Mapped[UUID] = mapped_column()
    action_code: Mapped[str] = mapped_column(String(40))
    priority: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    policy_digest: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.users.id",
            name="fk_policies_created_by",
            ondelete="RESTRICT",
        )
    )

    revision: Mapped[ContractRevision] = relationship(back_populates="policies")
    constraints: Mapped[list[PolicyConstraint]] = relationship(
        back_populates="policy",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by="PolicyConstraint.position_no",
    )
    execution_bindings: Mapped[list[PolicyExecutionBinding]] = relationship(
        back_populates="policy",
        cascade="save-update, merge",
        passive_deletes=True,
        order_by=lambda: (
            PolicyExecutionBinding.execution_role,
            PolicyExecutionBinding.connector_id,
        ),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["contract_revision_id", "subject_contract_party_id"],
            [
                f"{SCHEMA}.contract_parties.contract_revision_id",
                f"{SCHEMA}.contract_parties.id",
            ],
            name="fk_policies_subject_party_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "contract_object_id"],
            [
                f"{SCHEMA}.contract_objects.contract_revision_id",
                f"{SCHEMA}.contract_objects.id",
            ],
            name="fk_policies_object_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("policy_code <> ''", name="policy_code_nonempty"),
        CheckConstraint(
            "(policy_type = 'permission' AND effect = 'permit') OR "
            "(policy_type = 'prohibition' AND effect = 'deny') OR "
            "(policy_type = 'obligation' AND effect = 'require')",
            name="type_effect_pair",
        ),
        CheckConstraint(
            f"action_code IN ({sql_values(POLICY_ACTION_CODES)})",
            name="action_code",
        ),
        CheckConstraint("priority >= 0", name="priority_nonnegative"),
        UniqueConstraint(
            "contract_revision_id", "policy_code", name="uq_policies_revision_code"
        ),
        UniqueConstraint(
            "contract_revision_id", "id", name="uq_policies_revision_id"
        ),
        Index(
            "uq_policies_revision_digest",
            "contract_revision_id",
            "policy_digest",
            unique=True,
            postgresql_where=text("policy_digest IS NOT NULL"),
            sqlite_where=text("policy_digest IS NOT NULL"),
        ),
        Index(
            "ix_policies_subject_action", "subject_contract_party_id", "action_code"
        ),
        Index("ix_policies_object_action", "contract_object_id", "action_code"),
        Index(
            "ix_policies_revision_priority",
            "contract_revision_id",
            text("priority DESC"),
        ),
        Index("ix_policies_created_by", "created_by"),
    )


class PolicyConstraint(Base):
    __tablename__ = "policy_constraints"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.policies.id",
            name="fk_policy_constraints_policy",
            ondelete="RESTRICT",
        )
    )
    constraint_name: Mapped[str] = mapped_column(String(32))
    operator: Mapped[str] = mapped_column(String(8))
    value: Mapped[Any] = mapped_column(JSON_DOCUMENT)
    unit: Mapped[str | None] = mapped_column(String(16))
    position_no: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )

    policy: Mapped[Policy] = relationship(back_populates="constraints")

    __table_args__ = (
        CheckConstraint(
            f"constraint_name IN ({sql_values(POLICY_CONSTRAINT_NAMES)})",
            name="constraint_name",
        ),
        CheckConstraint(
            f"operator IN ({sql_values(POLICY_CONSTRAINT_OPERATORS)})",
            name="operator",
        ),
        CheckConstraint("position_no > 0", name="position_no_positive"),
        UniqueConstraint(
            "policy_id", "position_no", name="uq_policy_constraints_policy_pos"
        ),
        Index("ix_policy_constraints_policy_name", "policy_id", "constraint_name"),
    )


class PolicyExecutionBinding(Base):
    __tablename__ = "policy_execution_bindings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey(
            f"{SCHEMA}.policies.id",
            name="fk_policy_execution_bindings_policy",
            ondelete="RESTRICT",
        )
    )
    connector_id: Mapped[UUID] = mapped_column()
    execution_role: Mapped[str] = mapped_column(String(32))
    required_capability_code: Mapped[str] = mapped_column(String(48))
    required_capability_version: Mapped[str] = mapped_column(
        String(16), default="1.0", server_default="1.0"
    )
    is_required: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true")
    )
    deployment_status: Mapped[str] = mapped_column(
        String(12), default="pending", server_default="pending"
    )
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_digest: Mapped[str | None] = mapped_column(Text)
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_receipt_digest: Mapped[str | None] = mapped_column(Text)
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        server_default=func.now(),
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    policy: Mapped[Policy] = relationship(back_populates="execution_bindings")

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "connector_id",
                "required_capability_code",
                "required_capability_version",
            ],
            [
                f"{SCHEMA}.connector_capabilities.connector_id",
                f"{SCHEMA}.connector_capabilities.capability_code",
                f"{SCHEMA}.connector_capabilities.capability_version",
            ],
            name="fk_policy_bindings_connector_capability",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(execution_role = 'compute_executor' AND "
            "required_capability_code = 'controlled_compute_execution' AND "
            "required_capability_version = '1.0') OR "
            "(execution_role = 'egress_controller' AND "
            "required_capability_code = 'egress_policy_enforcement' AND "
            "required_capability_version = '1.0') OR "
            "(execution_role = 'audit_evidence_emitter' AND "
            "required_capability_code = 'audit_evidence_emit' AND "
            "required_capability_version = '1.0')",
            name="role_capability_pair",
        ),
        CheckConstraint(
            f"deployment_status IN ({sql_values(POLICY_BINDING_STATUSES)})",
            name="deployment_status",
        ),
        CheckConstraint(
            "(deployment_status = 'pending' AND acknowledged_at IS NULL AND "
            "receipt_digest IS NULL AND rejection_reason IS NULL AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'accepted' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NOT NULL AND rejection_reason IS NULL AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'rejected' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NULL AND length(rejection_reason) > 0 AND revoked_at IS NULL "
            "AND revocation_receipt_digest IS NULL AND revocation_reason IS NULL) OR "
            "(deployment_status = 'revoked' AND acknowledged_at IS NOT NULL AND "
            "receipt_digest IS NOT NULL AND rejection_reason IS NULL AND revoked_at IS NOT NULL "
            "AND revocation_receipt_digest IS NOT NULL AND length(revocation_reason) > 0)",
            name="deployment_shape",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint(
            "policy_id",
            "connector_id",
            "execution_role",
            "required_capability_code",
            "required_capability_version",
            name="uq_policy_bindings_spec",
        ),
        Index(
            "ix_policy_bindings_connector_status_deployed",
            "connector_id",
            "deployment_status",
            text("deployed_at DESC"),
        ),
        Index("ix_policy_bindings_policy_status", "policy_id", "deployment_status"),
        Index(
            "ix_policy_bindings_capability_status",
            "required_capability_code",
            "required_capability_version",
            "deployment_status",
        ),
        Index(
            "ix_policy_bindings_pending",
            "policy_id",
            "connector_id",
            postgresql_where=text("deployment_status = 'pending'"),
            sqlite_where=text("deployment_status = 'pending'"),
        ),
    )


class ContractSignature(Base):
    __tablename__ = "contract_signatures"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    contract_revision_id: Mapped[UUID] = mapped_column()
    contract_party_id: Mapped[UUID] = mapped_column()
    signer_organization_id: Mapped[UUID] = mapped_column()
    signer_user_id: Mapped[UUID] = mapped_column()
    signature_type: Mapped[str] = mapped_column(
        String(16), default="demo", server_default="demo"
    )
    signature_value_ref: Mapped[str] = mapped_column(Text)
    signed_content_digest: Mapped[str] = mapped_column(Text)
    authority_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    verification_status: Mapped[str] = mapped_column(
        String(16), default="verified", server_default="verified"
    )
    signature_digest: Mapped[str] = mapped_column(Text)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    revision: Mapped[ContractRevision] = relationship(
        back_populates="signatures",
        primaryjoin="ContractSignature.contract_revision_id == ContractRevision.id",
        foreign_keys=[contract_revision_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            [
                "contract_revision_id",
                "contract_party_id",
                "signer_organization_id",
            ],
            [
                f"{SCHEMA}.contract_parties.contract_revision_id",
                f"{SCHEMA}.contract_parties.id",
                f"{SCHEMA}.contract_parties.organization_id",
            ],
            name="fk_contract_signatures_party_org",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_revision_id", "signed_content_digest"],
            [
                f"{SCHEMA}.contract_revisions.id",
                f"{SCHEMA}.contract_revisions.content_digest",
            ],
            name="fk_contract_signatures_revision_digest",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["signer_organization_id", "signer_user_id"],
            [
                f"{SCHEMA}.organization_members.organization_id",
                f"{SCHEMA}.organization_members.user_id",
            ],
            name="fk_contract_signatures_signer_member",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"signature_type IN ({sql_values(CONTRACT_SIGNATURE_TYPES)})",
            name="signature_type",
        ),
        CheckConstraint(
            f"verification_status IN "
            f"({sql_values(CONTRACT_SIGNATURE_VERIFICATION_STATUSES)})",
            name="verification_status",
        ),
        CheckConstraint("length(signature_value_ref) > 0", name="value_ref_nonempty"),
        UniqueConstraint("signature_digest", name="uq_contract_signatures_digest"),
        UniqueConstraint(
            "contract_party_id",
            "signed_content_digest",
            name="uq_contract_signatures_party_content",
        ),
        Index(
            "ix_contract_signatures_signer_signed",
            "signer_user_id",
            text("signed_at DESC"),
        ),
        Index(
            "ix_contract_signatures_revision_content",
            "contract_revision_id",
            "signed_content_digest",
        ),
    )

"""Add Web3 identity, contract-anchor and escrow mirrors for Phase 5.4-5.6.

Revision ID: 20260912_0062
Revises: 20260829_0061
Create Date: 2026-09-12

Only identifiers, addresses, digests and chain receipt metadata are stored.
Medical data, contract prose, DID documents and result payloads remain off chain.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260912_0062"
down_revision: str | None = "20260829_0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "medtrust"


def upgrade() -> None:
    _create_wallet_identity_bindings()
    _create_wallet_auth_challenges()
    _create_contract_chain_anchors()
    _create_chain_event_receipts()
    _create_web3_escrow_bindings()
    _create_settlement_proof_records()
    _extend_local_sessions()
    _enable_evm_contract_signatures()


def downgrade() -> None:
    bind = op.get_bind()
    evm_count = bind.execute(
        sa.text(
            "SELECT count(*) FROM medtrust.contract_signatures "
            "WHERE signature_type='evm_receipt'"
        )
    ).scalar_one()
    if evm_count:
        raise RuntimeError(
            "cannot downgrade while verified EVM contract signatures exist"
        )

    _restore_demo_contract_signature_guard()
    op.drop_constraint(
        "ck_contract_signatures_signature_type",
        "contract_signatures",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_contract_signatures_signature_type",
        "contract_signatures",
        "signature_type='demo'",
        schema=SCHEMA,
    )

    op.drop_constraint(
        "ck_local_demo_sessions_auth_method_shape",
        "local_demo_sessions",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_local_demo_sessions_auth_method",
        "local_demo_sessions",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "fk_local_sessions_wallet_binding",
        "local_demo_sessions",
        schema=SCHEMA,
        type_="foreignkey",
    )
    op.drop_column("local_demo_sessions", "wallet_binding_id", schema=SCHEMA)
    op.drop_column("local_demo_sessions", "auth_method", schema=SCHEMA)

    op.drop_table("settlement_proof_records", schema=SCHEMA)
    op.drop_table("web3_escrow_bindings", schema=SCHEMA)
    op.drop_table("chain_event_receipts", schema=SCHEMA)
    op.drop_table("contract_chain_anchors", schema=SCHEMA)
    op.drop_table("wallet_auth_challenges", schema=SCHEMA)
    op.drop_table("wallet_identity_bindings", schema=SCHEMA)


def _create_wallet_identity_bindings() -> None:
    op.create_table(
        "wallet_identity_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("role_code", sa.String(32), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("did_uri", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("identity_evidence_digest", sa.String(71), nullable=False),
        sa.Column("credential_contract_address", sa.String(42)),
        sa.Column("credential_issuance_tx_hash", sa.String(66)),
        sa.Column("credential_token_id", sa.String(78)),
        sa.Column("credential_scope_digest", sa.String(71)),
        sa.Column("credential_expires_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("verified_by", sa.Uuid()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revocation_reason", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "role_code IN ('data_provider','model_provider','data_requester','space_operator')",
            name="ck_wallet_identity_bindings_role_code",
        ),
        sa.CheckConstraint(
            "status IN ('pending','active','revoked')",
            name="ck_wallet_identity_bindings_status",
        ),
        sa.CheckConstraint("chain_id>0", name="ck_wallet_identity_bindings_chain_id_positive"),
        sa.CheckConstraint(
            "length(wallet_address)=42 AND substr(wallet_address,1,2)='0x' "
            "AND wallet_address=lower(wallet_address)",
            name="ck_wallet_identity_bindings_wallet_address_format",
        ),
        sa.CheckConstraint(
            "substr(did_uri,1,4)='did:'",
            name="ck_wallet_identity_bindings_did_uri_format",
        ),
        sa.CheckConstraint(
            "identity_evidence_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "(credential_scope_digest IS NULL OR "
            "credential_scope_digest ~ '^sha256:[0-9a-f]{64}$')",
            name="ck_wallet_identity_bindings_digest_formats",
        ),
        sa.CheckConstraint(
            "credential_contract_address IS NULL OR "
            "(length(credential_contract_address)=42 AND "
            "substr(credential_contract_address,1,2)='0x' AND "
            "credential_contract_address=lower(credential_contract_address))",
            name="ck_wallet_identity_bindings_credential_address_format",
        ),
        sa.CheckConstraint(
            "credential_issuance_tx_hash IS NULL OR "
            "credential_issuance_tx_hash ~ '^0x[0-9a-f]{64}$'",
            name="ck_wallet_identity_bindings_credential_issuance_tx_hash_format",
        ),
        sa.CheckConstraint(
            "(status='pending' AND verified_at IS NULL AND verified_by IS NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status='active' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL "
            "AND credential_contract_address IS NOT NULL "
            "AND credential_token_id IS NOT NULL AND credential_scope_digest IS NOT NULL "
            "AND credential_expires_at IS NOT NULL) OR "
            "(status='revoked' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NOT NULL AND length(revocation_reason)>0)",
            name="ck_wallet_identity_bindings_lifecycle_shape",
        ),
        sa.CheckConstraint(
            "row_version>=1", name="ck_wallet_identity_bindings_row_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["medtrust.organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_identity_bindings"),
        sa.UniqueConstraint(
            "chain_id", "wallet_address", name="uq_wallet_binding_chain_wallet"
        ),
        sa.UniqueConstraint(
            "space_id", "user_id", "role_code", name="uq_wallet_binding_user_role"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wallet_bindings_org_status",
        "wallet_identity_bindings",
        ["space_id", "organization_id", "status"],
        schema=SCHEMA,
    )


def _create_wallet_auth_challenges() -> None:
    op.create_table(
        "wallet_auth_challenges",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("wallet_binding_id", sa.Uuid()),
        sa.Column("subject_user_id", sa.Uuid()),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("wallet_address", sa.String(42), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("nonce_digest", sa.String(71), nullable=False),
        sa.Column("message_digest", sa.String(71), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "purpose IN ('login','bind')", name="ck_wallet_auth_challenges_purpose"
        ),
        sa.CheckConstraint("chain_id>0", name="ck_wallet_auth_challenges_chain_id_positive"),
        sa.CheckConstraint(
            "length(wallet_address)=42 AND substr(wallet_address,1,2)='0x' "
            "AND wallet_address=lower(wallet_address)",
            name="ck_wallet_auth_challenges_wallet_address_format",
        ),
        sa.CheckConstraint(
            "nonce_digest ~ '^sha256:[0-9a-f]{64}$' AND "
            "message_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_wallet_auth_challenges_digest_formats",
        ),
        sa.CheckConstraint(
            "expires_at>issued_at", name="ck_wallet_auth_challenges_expiry_after_issue"
        ),
        sa.CheckConstraint(
            "(purpose='login' AND subject_user_id IS NULL) OR "
            "(purpose='bind' AND subject_user_id IS NOT NULL)",
            name="ck_wallet_auth_challenges_purpose_shape",
        ),
        sa.CheckConstraint(
            "consumed_at IS NULL OR consumed_at>=issued_at",
            name="ck_wallet_auth_challenges_consumed_after_issue",
        ),
        sa.ForeignKeyConstraint(
            ["wallet_binding_id"],
            ["medtrust.wallet_identity_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_wallet_auth_challenges"),
        sa.UniqueConstraint("nonce_digest", name="uq_wallet_challenge_nonce"),
        sa.UniqueConstraint("message_digest", name="uq_wallet_challenge_message"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_wallet_challenges_expiry",
        "wallet_auth_challenges",
        ["wallet_address", "expires_at"],
        schema=SCHEMA,
    )


def _create_contract_chain_anchors() -> None:
    op.create_table(
        "contract_chain_anchors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("contract_id", sa.Uuid(), nullable=False),
        sa.Column("contract_revision_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("registry_address", sa.String(42), nullable=False),
        sa.Column("agreement_key", sa.String(66), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column(
            "required_confirmation_bitmap", sa.Integer(), nullable=False, server_default="15"
        ),
        sa.Column("confirmation_bitmap", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("registration_tx_hash", sa.String(66)),
        sa.Column("registration_block_number", sa.BigInteger()),
        sa.Column("registration_block_hash", sa.String(66)),
        sa.Column("activation_tx_hash", sa.String(66)),
        sa.Column("activation_block_number", sa.BigInteger()),
        sa.Column("activation_block_hash", sa.String(66)),
        sa.Column("prepared_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('prepared','registered','confirming','active','suspended','ended','orphaned')",
            name="ck_contract_chain_anchors_status",
        ),
        sa.CheckConstraint("chain_id>0", name="ck_contract_chain_anchors_chain_id_positive"),
        sa.CheckConstraint(
            "length(registry_address)=42 AND substr(registry_address,1,2)='0x' "
            "AND registry_address=lower(registry_address)",
            name="ck_contract_chain_anchors_registry_address_format",
        ),
        sa.CheckConstraint(
            "agreement_key ~ '^0x[0-9a-f]{64}$'",
            name="ck_contract_chain_anchors_agreement_key_format",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_contract_chain_anchors_content_digest_format",
        ),
        sa.CheckConstraint(
            "required_confirmation_bitmap=15 AND confirmation_bitmap BETWEEN 0 AND 15",
            name="ck_contract_chain_anchors_confirmation_bitmap_range",
        ),
        sa.CheckConstraint(
            "row_version>=1", name="ck_contract_chain_anchors_row_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["contract_id"], ["medtrust.contracts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["contract_revision_id"],
            ["medtrust.contract_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prepared_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_contract_chain_anchors"),
        sa.UniqueConstraint(
            "contract_revision_id", name="uq_contract_chain_anchor_revision"
        ),
        sa.UniqueConstraint(
            "chain_id", "registry_address", "agreement_key", name="uq_chain_agreement"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_contract_chain_anchors_status",
        "contract_chain_anchors",
        ["space_id", "status"],
        schema=SCHEMA,
    )


def _create_chain_event_receipts() -> None:
    op.create_table(
        "chain_event_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("contract_address", sa.String(42), nullable=False),
        sa.Column("transaction_hash", sa.String(66), nullable=False),
        sa.Column("log_index", sa.Integer(), nullable=False),
        sa.Column("block_number", sa.BigInteger(), nullable=False),
        sa.Column("block_hash", sa.String(66), nullable=False),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("subject_type", sa.String(48), nullable=False),
        sa.Column("subject_key", sa.String(128), nullable=False),
        sa.Column("actor_wallet", sa.String(42)),
        sa.Column("payload_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_digest", sa.String(71), nullable=False),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="observed"),
        sa.Column(
            "first_seen_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('observed','finalized','applied','orphaned')",
            name="ck_chain_event_receipts_status",
        ),
        sa.CheckConstraint("chain_id>0", name="ck_chain_event_receipts_chain_id_positive"),
        sa.CheckConstraint(
            "log_index>=0 AND confirmations>=0",
            name="ck_chain_event_receipts_position_values",
        ),
        sa.CheckConstraint(
            "length(contract_address)=42 AND substr(contract_address,1,2)='0x' "
            "AND contract_address=lower(contract_address)",
            name="ck_chain_event_receipts_contract_address_format",
        ),
        sa.CheckConstraint(
            "transaction_hash ~ '^0x[0-9a-f]{64}$' AND "
            "block_hash ~ '^0x[0-9a-f]{64}$'",
            name="ck_chain_event_receipts_chain_hash_formats",
        ),
        sa.CheckConstraint(
            "payload_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_chain_event_receipts_payload_digest_format",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload_snapshot)='object'",
            name="ck_chain_event_receipts_payload_object",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chain_event_receipts"),
        sa.UniqueConstraint(
            "chain_id", "transaction_hash", "log_index", name="uq_chain_event_position"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chain_events_subject",
        "chain_event_receipts",
        ["space_id", "subject_type", "subject_key"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_chain_events_status_block",
        "chain_event_receipts",
        ["status", "block_number"],
        schema=SCHEMA,
    )


def _create_web3_escrow_bindings() -> None:
    op.create_table(
        "web3_escrow_bindings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("commercial_order_id", sa.Uuid(), nullable=False),
        sa.Column("contract_chain_anchor_id", sa.Uuid(), nullable=False),
        sa.Column("chain_id", sa.BigInteger(), nullable=False),
        sa.Column("escrow_contract_address", sa.String(42), nullable=False),
        sa.Column("settlement_token_address", sa.String(42), nullable=False),
        sa.Column("escrow_order_key", sa.String(66), nullable=False),
        sa.Column("payer_wallet", sa.String(42), nullable=False),
        sa.Column("amount_token_units", sa.Numeric(78, 0), nullable=False),
        sa.Column("token_decimals", sa.Integer(), nullable=False),
        sa.Column("distribution_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("distribution_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("refund_eligible_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("funding_tx_hash", sa.String(66)),
        sa.Column("settlement_tx_hash", sa.String(66)),
        sa.Column("refund_tx_hash", sa.String(66)),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("funded_at", sa.DateTime(timezone=True)),
        sa.Column("settled_at", sa.DateTime(timezone=True)),
        sa.Column("refunded_at", sa.DateTime(timezone=True)),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('prepared','funding','funded','proving','claimable','refunded','disputed','orphaned')",
            name="ck_web3_escrow_bindings_status",
        ),
        sa.CheckConstraint("chain_id>0", name="ck_web3_escrow_bindings_chain_id_positive"),
        sa.CheckConstraint(
            "amount_token_units>=0", name="ck_web3_escrow_bindings_amount_nonnegative"
        ),
        sa.CheckConstraint(
            "token_decimals BETWEEN 0 AND 36",
            name="ck_web3_escrow_bindings_token_decimals",
        ),
        sa.CheckConstraint(
            "length(escrow_contract_address)=42 AND "
            "substr(escrow_contract_address,1,2)='0x' AND "
            "escrow_contract_address=lower(escrow_contract_address) AND "
            "length(settlement_token_address)=42 AND "
            "substr(settlement_token_address,1,2)='0x' AND "
            "settlement_token_address=lower(settlement_token_address) AND "
            "length(payer_wallet)=42 AND substr(payer_wallet,1,2)='0x' AND "
            "payer_wallet=lower(payer_wallet)",
            name="ck_web3_escrow_bindings_address_formats",
        ),
        sa.CheckConstraint(
            "escrow_order_key ~ '^0x[0-9a-f]{64}$'",
            name="ck_web3_escrow_bindings_order_key_format",
        ),
        sa.CheckConstraint(
            "distribution_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_web3_escrow_bindings_distribution_digest_format",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(distribution_snapshot)='object'",
            name="ck_web3_escrow_bindings_distribution_object",
        ),
        sa.CheckConstraint(
            "row_version>=1", name="ck_web3_escrow_bindings_row_version_positive"
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["commercial_order_id"],
            ["medtrust.commercial_orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_chain_anchor_id"],
            ["medtrust.contract_chain_anchors.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_web3_escrow_bindings"),
        sa.UniqueConstraint("commercial_order_id", name="uq_web3_escrow_order"),
        sa.UniqueConstraint(
            "chain_id", "escrow_contract_address", "escrow_order_key", name="uq_chain_escrow"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_web3_escrows_status",
        "web3_escrow_bindings",
        ["space_id", "status"],
        schema=SCHEMA,
    )


def _create_settlement_proof_records() -> None:
    op.create_table(
        "settlement_proof_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("space_id", sa.Uuid(), nullable=False),
        sa.Column("escrow_binding_id", sa.Uuid(), nullable=False),
        sa.Column("proof_type", sa.String(16), nullable=False),
        sa.Column("proof_digest", sa.String(71), nullable=False),
        sa.Column("compute_run_id", sa.Uuid()),
        sa.Column("artifact_id", sa.Uuid()),
        sa.Column("result_package_id", sa.Uuid()),
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="prepared"),
        sa.Column("transaction_hash", sa.String(66)),
        sa.Column("submitted_by", sa.Uuid(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "proof_type IN ('execution','delivery')",
            name="ck_settlement_proof_records_proof_type",
        ),
        sa.CheckConstraint(
            "status IN ('prepared','submitted','finalized','orphaned')",
            name="ck_settlement_proof_records_status",
        ),
        sa.CheckConstraint(
            "proof_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_settlement_proof_records_proof_digest_format",
        ),
        sa.CheckConstraint(
            "(proof_type='execution' AND compute_run_id IS NOT NULL "
            "AND result_package_id IS NULL) OR "
            "(proof_type='delivery' AND artifact_id IS NOT NULL "
            "AND result_package_id IS NOT NULL)",
            name="ck_settlement_proof_records_proof_source_shape",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(source_snapshot)='object'",
            name="ck_settlement_proof_records_source_object",
        ),
        sa.ForeignKeyConstraint(
            ["space_id"], ["medtrust.spaces.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["escrow_binding_id"],
            ["medtrust.web3_escrow_bindings.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compute_run_id"], ["medtrust.compute_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], ["medtrust.artifacts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["result_package_id"],
            ["medtrust.approved_result_packages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by"], ["medtrust.users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_settlement_proof_records"),
        sa.UniqueConstraint(
            "escrow_binding_id", "proof_type", name="uq_settlement_proof_type"
        ),
        sa.UniqueConstraint("proof_digest", name="uq_settlement_proof_digest"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_settlement_proofs_status",
        "settlement_proof_records",
        ["space_id", "status"],
        schema=SCHEMA,
    )


def _extend_local_sessions() -> None:
    op.add_column(
        "local_demo_sessions",
        sa.Column("auth_method", sa.String(16), nullable=False, server_default="password"),
        schema=SCHEMA,
    )
    op.add_column(
        "local_demo_sessions",
        sa.Column("wallet_binding_id", sa.Uuid()),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_local_sessions_wallet_binding",
        "local_demo_sessions",
        "wallet_identity_bindings",
        ["wallet_binding_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_local_demo_sessions_auth_method",
        "local_demo_sessions",
        "auth_method IN ('password','siwe')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_local_demo_sessions_auth_method_shape",
        "local_demo_sessions",
        "(auth_method='password' AND wallet_binding_id IS NULL) OR "
        "(auth_method='siwe' AND wallet_binding_id IS NOT NULL)",
        schema=SCHEMA,
    )


def _enable_evm_contract_signatures() -> None:
    op.drop_constraint(
        "ck_contract_signatures_signature_type",
        "contract_signatures",
        schema=SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "ck_contract_signatures_signature_type",
        "contract_signatures",
        "signature_type IN ('demo','evm_receipt')",
        schema=SCHEMA,
    )
    op.execute(_signature_guard_sql(allow_evm=True))


def _restore_demo_contract_signature_guard() -> None:
    op.execute(_signature_guard_sql(allow_evm=False))


def _signature_guard_sql(*, allow_evm: bool) -> str:
    evm_branch = ""
    accepted_types = "NEW.signature_type <> 'demo'"
    if allow_evm:
        accepted_types = "NEW.signature_type NOT IN ('demo','evm_receipt')"
        evm_branch = """
            ELSIF NEW.signature_type = 'evm_receipt' THEN
                IF NEW.authority_snapshot->>'schema_version' <>
                       'medtrust.evm-receipt-signature/v1' OR
                   NEW.authority_snapshot->>'is_demo' <> 'false' OR
                   NEW.authority_snapshot->>'organization_id' <>
                       NEW.signer_organization_id::text OR
                   NEW.authority_snapshot->>'user_id' <> NEW.signer_user_id::text OR
                   NEW.authority_snapshot->>'membership_status' <> 'active' OR
                   NEW.authority_snapshot->>'authority_code' <> 'evm_contract_signer' OR
                   NEW.authority_snapshot#>>'{scope,contract_revision_id}' <>
                       NEW.contract_revision_id::text OR
                   NEW.authority_snapshot#>>'{scope,contract_party_id}' <>
                       NEW.contract_party_id::text OR
                   NEW.signature_value_ref !~
                       '^eip155:[1-9][0-9]*:tx:0x[0-9a-f]{64}:log:[0-9]+$' THEN
                    RAISE EXCEPTION 'EVM authority snapshot does not match signature scope';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.wallet_identity_bindings w
                      JOIN medtrust.contract_parties p
                        ON p.id=NEW.contract_party_id
                       AND p.contract_revision_id=NEW.contract_revision_id
                     WHERE w.id=(NEW.authority_snapshot->>'wallet_binding_id')::uuid
                       AND w.user_id=NEW.signer_user_id
                       AND w.organization_id=NEW.signer_organization_id
                       AND w.status='active'
                       AND w.wallet_address=NEW.authority_snapshot->>'wallet_address'
                       AND w.chain_id=(NEW.authority_snapshot->>'chain_id')::bigint
                       AND w.credential_contract_address=
                           NEW.authority_snapshot->>'credential_contract_address'
                       AND w.credential_token_id=
                           NEW.authority_snapshot->>'credential_token_id'
                       AND w.credential_expires_at>NEW.signed_at
                       AND w.role_code=CASE p.party_role
                           WHEN 'operator_witness' THEN 'space_operator'
                           ELSE p.party_role
                       END
                ) THEN
                    RAISE EXCEPTION 'wallet role credential is not active for this party';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                      FROM medtrust.chain_event_receipts r
                     WHERE r.id=(NEW.authority_snapshot->>'chain_event_receipt_id')::uuid
                       AND r.chain_id=(NEW.authority_snapshot->>'chain_id')::bigint
                       AND r.transaction_hash=
                           NEW.authority_snapshot->>'transaction_hash'
                       AND r.log_index=(NEW.authority_snapshot->>'log_index')::integer
                       AND r.actor_wallet=NEW.authority_snapshot->>'wallet_address'
                       AND r.subject_type='contract_revision'
                       AND r.subject_key=NEW.contract_revision_id::text
                       AND r.status IN ('finalized','applied')
                ) THEN
                    RAISE EXCEPTION 'finalized chain receipt is missing';
                END IF;
        """

    return f"""
        CREATE OR REPLACE FUNCTION medtrust.guard_contract_signature_append_only_v6()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE parent_status text;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'contract signature is append-only';
            END IF;
            SELECT status INTO parent_status
              FROM medtrust.contract_revisions
             WHERE id=NEW.contract_revision_id;
            IF parent_status IS DISTINCT FROM 'proposed' THEN
                RAISE EXCEPTION 'contract signatures can only be appended to proposed revisions';
            END IF;
            IF {accepted_types} OR NEW.verification_status <> 'verified' OR
               NEW.verified_at IS NULL OR jsonb_typeof(NEW.authority_snapshot) <> 'object' THEN
                RAISE EXCEPTION 'invalid contract signature shape';
            END IF;
            IF NEW.signature_type = 'demo' THEN
                IF NEW.authority_snapshot->>'schema_version' <> '1.0' OR
                   NEW.authority_snapshot->>'is_demo' <> 'true' OR
                   NEW.authority_snapshot->>'organization_id' <>
                       NEW.signer_organization_id::text OR
                   NEW.authority_snapshot->>'user_id' <> NEW.signer_user_id::text OR
                   NEW.authority_snapshot->>'membership_status' <> 'active' OR
                   NEW.authority_snapshot->>'authority_code' <> 'demo_contract_signer' OR
                   NEW.authority_snapshot#>>'{{scope,contract_revision_id}}' <>
                       NEW.contract_revision_id::text OR
                   NEW.authority_snapshot#>>'{{scope,contract_party_id}}' <>
                       NEW.contract_party_id::text THEN
                    RAISE EXCEPTION 'authority snapshot does not match signature scope';
                END IF;
            {evm_branch}
            END IF;
            IF NOT EXISTS (
                SELECT 1
                  FROM medtrust.organization_members om
                  JOIN medtrust.organization_member_roles r
                    ON r.organization_member_id=om.id
                  JOIN medtrust.organizations o ON o.id=om.organization_id
                  JOIN medtrust.users u ON u.id=om.user_id
                 WHERE om.organization_id=NEW.signer_organization_id
                   AND om.user_id=NEW.signer_user_id
                   AND om.id::text=NEW.authority_snapshot->>'organization_member_id'
                   AND om.status='active'
                   AND (om.valid_from IS NULL OR om.valid_from<=NEW.signed_at)
                   AND (om.valid_until IS NULL OR om.valid_until>NEW.signed_at)
                   AND r.role_code='contract_signer'
                   AND o.status='active'
                   AND u.status='active'
            ) THEN
                RAISE EXCEPTION 'signer authority is not active';
            END IF;
            RETURN NEW;
        END;
        $$;
    """

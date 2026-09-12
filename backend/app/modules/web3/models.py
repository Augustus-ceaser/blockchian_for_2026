from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.identity.models import sql_values, utc_now


SCHEMA = "medtrust"
JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")

WALLET_ROLES = ("data_provider", "model_provider", "data_requester", "space_operator")
WALLET_BINDING_STATUSES = ("pending", "active", "revoked")
WALLET_CHALLENGE_PURPOSES = ("login", "bind")
CONTRACT_ANCHOR_STATUSES = (
    "prepared",
    "registered",
    "confirming",
    "active",
    "suspended",
    "ended",
    "orphaned",
)
CHAIN_EVENT_STATUSES = ("observed", "finalized", "applied", "orphaned")
ESCROW_STATUSES = (
    "prepared",
    "funding",
    "funded",
    "proving",
    "claimable",
    "refunded",
    "disputed",
    "orphaned",
)
SETTLEMENT_PROOF_TYPES = ("execution", "delivery")
SETTLEMENT_PROOF_STATUSES = ("prepared", "submitted", "finalized", "orphaned")


class WalletIdentityBinding(Base):
    """Server-authorized wallet-to-user binding and credential mirror.

    The on-chain credential proves a previously reviewed role. It never grants a
    new platform role by itself and contains no identity or medical plaintext.
    """

    __tablename__ = "wallet_identity_bindings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="RESTRICT")
    )
    role_code: Mapped[str] = mapped_column(String(32))
    chain_id: Mapped[int] = mapped_column(BigInteger)
    wallet_address: Mapped[str] = mapped_column(String(42))
    did_uri: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="pending", server_default="pending"
    )
    identity_evidence_digest: Mapped[str] = mapped_column(String(71))
    credential_contract_address: Mapped[str | None] = mapped_column(String(42))
    credential_issuance_tx_hash: Mapped[str | None] = mapped_column(String(66))
    credential_token_id: Mapped[str | None] = mapped_column(String(78))
    credential_scope_digest: Mapped[str | None] = mapped_column(String(71))
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(f"role_code IN ({sql_values(WALLET_ROLES)})", name="role_code"),
        CheckConstraint(
            f"status IN ({sql_values(WALLET_BINDING_STATUSES)})", name="status"
        ),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint(
            "length(wallet_address)=42 AND substr(wallet_address,1,2)='0x' "
            "AND wallet_address=lower(wallet_address)",
            name="wallet_address_format",
        ),
        CheckConstraint("substr(did_uri,1,4)='did:'", name="did_uri_format"),
        CheckConstraint(
            "length(identity_evidence_digest)=71 AND "
            "substr(identity_evidence_digest,1,7)='sha256:' AND "
            "(credential_scope_digest IS NULL OR "
            "(length(credential_scope_digest)=71 AND "
            "substr(credential_scope_digest,1,7)='sha256:'))",
            name="digest_formats",
        ),
        CheckConstraint(
            "credential_contract_address IS NULL OR "
            "(length(credential_contract_address)=42 AND "
            "substr(credential_contract_address,1,2)='0x' AND "
            "credential_contract_address=lower(credential_contract_address))",
            name="credential_address_format",
        ),
        CheckConstraint(
            "credential_issuance_tx_hash IS NULL OR "
            "(length(credential_issuance_tx_hash)=66 AND "
            "substr(credential_issuance_tx_hash,1,2)='0x' AND "
            "credential_issuance_tx_hash=lower(credential_issuance_tx_hash))",
            name="credential_issuance_tx_hash_format",
        ),
        CheckConstraint(
            "(status='pending' AND verified_at IS NULL AND verified_by IS NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status='active' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NULL AND revocation_reason IS NULL "
            "AND credential_contract_address IS NOT NULL "
            "AND credential_token_id IS NOT NULL AND credential_scope_digest IS NOT NULL "
            "AND credential_expires_at IS NOT NULL) OR "
            "(status='revoked' AND verified_at IS NOT NULL AND verified_by IS NOT NULL "
            "AND revoked_at IS NOT NULL AND length(revocation_reason)>0)",
            name="lifecycle_shape",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("chain_id", "wallet_address", name="uq_wallet_binding_chain_wallet"),
        UniqueConstraint(
            "space_id", "user_id", "role_code", name="uq_wallet_binding_user_role"
        ),
        Index(
            "ix_wallet_bindings_org_status",
            "space_id",
            "organization_id",
            "status",
        ),
    )


class WalletAuthChallenge(Base):
    """One-use SIWE challenge. Only digests are persisted, never the signature."""

    __tablename__ = "wallet_auth_challenges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    purpose: Mapped[str] = mapped_column(String(16))
    wallet_binding_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.wallet_identity_bindings.id", ondelete="RESTRICT")
    )
    subject_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    chain_id: Mapped[int] = mapped_column(BigInteger)
    wallet_address: Mapped[str] = mapped_column(String(42))
    domain: Mapped[str] = mapped_column(String(255))
    uri: Mapped[str] = mapped_column(Text)
    nonce_digest: Mapped[str] = mapped_column(String(71))
    message_digest: Mapped[str] = mapped_column(String(71))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            f"purpose IN ({sql_values(WALLET_CHALLENGE_PURPOSES)})", name="purpose"
        ),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint(
            "length(wallet_address)=42 AND substr(wallet_address,1,2)='0x' "
            "AND wallet_address=lower(wallet_address)",
            name="wallet_address_format",
        ),
        CheckConstraint(
            "length(nonce_digest)=71 AND substr(nonce_digest,1,7)='sha256:' AND "
            "length(message_digest)=71 AND substr(message_digest,1,7)='sha256:'",
            name="digest_formats",
        ),
        CheckConstraint("expires_at > issued_at", name="expiry_after_issue"),
        CheckConstraint(
            "(purpose='login' AND subject_user_id IS NULL) OR "
            "(purpose='bind' AND subject_user_id IS NOT NULL)",
            name="purpose_shape",
        ),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at", name="consumed_after_issue"
        ),
        UniqueConstraint("nonce_digest", name="uq_wallet_challenge_nonce"),
        UniqueConstraint("message_digest", name="uq_wallet_challenge_message"),
        Index("ix_wallet_challenges_expiry", "wallet_address", "expires_at"),
    )


class ContractChainAnchor(Base):
    """Canonical chain mirror for one immutable digital-contract revision."""

    __tablename__ = "contract_chain_anchors"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.contracts.id", ondelete="RESTRICT")
    )
    contract_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.contract_revisions.id", ondelete="RESTRICT")
    )
    chain_id: Mapped[int] = mapped_column(BigInteger)
    registry_address: Mapped[str] = mapped_column(String(42))
    agreement_key: Mapped[str] = mapped_column(String(66))
    content_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(
        String(16), default="prepared", server_default="prepared"
    )
    required_confirmation_bitmap: Mapped[int] = mapped_column(
        Integer, default=15, server_default="15"
    )
    confirmation_bitmap: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    registration_tx_hash: Mapped[str | None] = mapped_column(String(66))
    registration_block_number: Mapped[int | None] = mapped_column(BigInteger)
    registration_block_hash: Mapped[str | None] = mapped_column(String(66))
    activation_tx_hash: Mapped[str | None] = mapped_column(String(66))
    activation_block_number: Mapped[int | None] = mapped_column(BigInteger)
    activation_block_hash: Mapped[str | None] = mapped_column(String(66))
    prepared_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(CONTRACT_ANCHOR_STATUSES)})", name="status"
        ),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint(
            "length(registry_address)=42 AND substr(registry_address,1,2)='0x' "
            "AND registry_address=lower(registry_address)",
            name="registry_address_format",
        ),
        CheckConstraint(
            "length(agreement_key)=66 AND substr(agreement_key,1,2)='0x'",
            name="agreement_key_format",
        ),
        CheckConstraint(
            "length(content_digest)=71 AND substr(content_digest,1,7)='sha256:'",
            name="content_digest_format",
        ),
        CheckConstraint(
            "required_confirmation_bitmap=15 AND confirmation_bitmap>=0 "
            "AND confirmation_bitmap<=15",
            name="confirmation_bitmap_range",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("contract_revision_id", name="uq_contract_chain_anchor_revision"),
        UniqueConstraint(
            "chain_id", "registry_address", "agreement_key", name="uq_chain_agreement"
        ),
        Index("ix_contract_chain_anchors_status", "space_id", "status"),
    )


class ChainEventReceipt(Base):
    """Finality-aware, idempotent record of an observed EVM log."""

    __tablename__ = "chain_event_receipts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    chain_id: Mapped[int] = mapped_column(BigInteger)
    contract_address: Mapped[str] = mapped_column(String(42))
    transaction_hash: Mapped[str] = mapped_column(String(66))
    log_index: Mapped[int] = mapped_column(Integer)
    block_number: Mapped[int] = mapped_column(BigInteger)
    block_hash: Mapped[str] = mapped_column(String(66))
    event_name: Mapped[str] = mapped_column(String(64))
    subject_type: Mapped[str] = mapped_column(String(48))
    subject_key: Mapped[str] = mapped_column(String(128))
    actor_wallet: Mapped[str | None] = mapped_column(String(42))
    payload_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    payload_digest: Mapped[str] = mapped_column(String(71))
    confirmations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    status: Mapped[str] = mapped_column(
        String(16), default="observed", server_default="observed"
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            f"status IN ({sql_values(CHAIN_EVENT_STATUSES)})", name="status"
        ),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint("log_index >= 0 AND confirmations >= 0", name="position_values"),
        CheckConstraint(
            "length(contract_address)=42 AND substr(contract_address,1,2)='0x' "
            "AND contract_address=lower(contract_address)",
            name="contract_address_format",
        ),
        CheckConstraint(
            "length(transaction_hash)=66 AND substr(transaction_hash,1,2)='0x' AND "
            "length(block_hash)=66 AND substr(block_hash,1,2)='0x'",
            name="chain_hash_formats",
        ),
        CheckConstraint(
            "length(payload_digest)=71 AND substr(payload_digest,1,7)='sha256:'",
            name="payload_digest_format",
        ),
        UniqueConstraint(
            "chain_id", "transaction_hash", "log_index", name="uq_chain_event_position"
        ),
        Index("ix_chain_events_subject", "space_id", "subject_type", "subject_key"),
        Index("ix_chain_events_status_block", "status", "block_number"),
    )


class Web3EscrowBinding(Base):
    """Orthogonal on-chain escrow state for a legacy commercial order."""

    __tablename__ = "web3_escrow_bindings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    commercial_order_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.commercial_orders.id", ondelete="RESTRICT")
    )
    contract_chain_anchor_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.contract_chain_anchors.id", ondelete="RESTRICT")
    )
    chain_id: Mapped[int] = mapped_column(BigInteger)
    escrow_contract_address: Mapped[str] = mapped_column(String(42))
    settlement_token_address: Mapped[str] = mapped_column(String(42))
    escrow_order_key: Mapped[str] = mapped_column(String(66))
    payer_wallet: Mapped[str] = mapped_column(String(42))
    amount_token_units: Mapped[Decimal] = mapped_column(Numeric(78, 0))
    token_decimals: Mapped[int] = mapped_column(Integer)
    distribution_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    distribution_digest: Mapped[str] = mapped_column(String(71))
    status: Mapped[str] = mapped_column(
        String(16), default="prepared", server_default="prepared"
    )
    refund_eligible_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    funding_tx_hash: Mapped[str | None] = mapped_column(String(66))
    settlement_tx_hash: Mapped[str | None] = mapped_column(String(66))
    refund_tx_hash: Mapped[str | None] = mapped_column(String(66))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    funded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    __table_args__ = (
        CheckConstraint(f"status IN ({sql_values(ESCROW_STATUSES)})", name="status"),
        CheckConstraint("chain_id > 0", name="chain_id_positive"),
        CheckConstraint("amount_token_units >= 0", name="amount_nonnegative"),
        CheckConstraint("token_decimals >= 0 AND token_decimals <= 36", name="token_decimals"),
        CheckConstraint(
            "length(escrow_contract_address)=42 AND "
            "substr(escrow_contract_address,1,2)='0x' AND "
            "escrow_contract_address=lower(escrow_contract_address) AND "
            "length(settlement_token_address)=42 AND "
            "substr(settlement_token_address,1,2)='0x' AND "
            "settlement_token_address=lower(settlement_token_address) AND "
            "length(payer_wallet)=42 AND substr(payer_wallet,1,2)='0x' AND "
            "payer_wallet=lower(payer_wallet)",
            name="address_formats",
        ),
        CheckConstraint(
            "length(escrow_order_key)=66 AND substr(escrow_order_key,1,2)='0x'",
            name="order_key_format",
        ),
        CheckConstraint(
            "length(distribution_digest)=71 AND "
            "substr(distribution_digest,1,7)='sha256:'",
            name="distribution_digest_format",
        ),
        CheckConstraint("row_version >= 1", name="row_version_positive"),
        UniqueConstraint("commercial_order_id", name="uq_web3_escrow_order"),
        UniqueConstraint(
            "chain_id", "escrow_contract_address", "escrow_order_key", name="uq_chain_escrow"
        ),
        Index("ix_web3_escrows_status", "space_id", "status"),
    )


class SettlementProofRecord(Base):
    """One of the two independent proofs required before escrow settlement."""

    __tablename__ = "settlement_proof_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="RESTRICT")
    )
    escrow_binding_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.web3_escrow_bindings.id", ondelete="RESTRICT")
    )
    proof_type: Mapped[str] = mapped_column(String(16))
    proof_digest: Mapped[str] = mapped_column(String(71))
    compute_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.compute_runs.id", ondelete="RESTRICT")
    )
    artifact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.artifacts.id", ondelete="RESTRICT")
    )
    result_package_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.approved_result_packages.id", ondelete="RESTRICT")
    )
    source_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    status: Mapped[str] = mapped_column(
        String(16), default="prepared", server_default="prepared"
    )
    transaction_hash: Mapped[str | None] = mapped_column(String(66))
    submitted_by: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="RESTRICT")
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            f"proof_type IN ({sql_values(SETTLEMENT_PROOF_TYPES)})", name="proof_type"
        ),
        CheckConstraint(
            f"status IN ({sql_values(SETTLEMENT_PROOF_STATUSES)})", name="status"
        ),
        CheckConstraint(
            "length(proof_digest)=71 AND substr(proof_digest,1,7)='sha256:'",
            name="proof_digest_format",
        ),
        CheckConstraint(
            "(proof_type='execution' AND compute_run_id IS NOT NULL "
            "AND result_package_id IS NULL) OR "
            "(proof_type='delivery' AND artifact_id IS NOT NULL "
            "AND result_package_id IS NOT NULL)",
            name="proof_source_shape",
        ),
        UniqueConstraint(
            "escrow_binding_id", "proof_type", name="uq_settlement_proof_type"
        ),
        UniqueConstraint("proof_digest", name="uq_settlement_proof_digest"),
        Index("ix_settlement_proofs_status", "space_id", "status"),
    )

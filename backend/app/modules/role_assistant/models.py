from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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

AGENT_ROLE_CODES = (
    "space_operator",
    "data_provider",
    "model_provider",
    "data_requester",
)
AGENT_CONVERSATION_STATUSES = ("active", "closed")
AGENT_TURN_STATUSES = ("running", "completed", "failed")
AGENT_STEP_TYPES = ("authorization", "tool", "response")
AGENT_STEP_STATUSES = ("success", "empty", "error", "denied")
AGENT_TOOL_RISKS = ("read", "propose", "commit")


class AgentConversation(Base):
    __tablename__ = "agent_conversations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    space_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.spaces.id", ondelete="CASCADE")
    )
    actor_organization_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.organizations.id", ondelete="CASCADE")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.users.id", ondelete="CASCADE")
    )
    role_code: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    entity_context: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT, default=dict, server_default=text("'{}'")
    )
    turn_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    turns: Mapped[list[AgentTurn]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentTurn.sequence_no",
    )

    __table_args__ = (
        CheckConstraint(
            f"role_code IN ({sql_values(AGENT_ROLE_CODES)})",
            name="role_code",
        ),
        CheckConstraint(
            f"status IN ({sql_values(AGENT_CONVERSATION_STATUSES)})",
            name="status",
        ),
        CheckConstraint("turn_count >= 0", name="turn_count_nonnegative"),
        UniqueConstraint("id", "space_id", name="uq_agent_conversation_space"),
        Index(
            "ix_agent_conversations_actor_recent",
            "space_id",
            "actor_user_id",
            "role_code",
            "last_seen_at",
        ),
    )


class AgentTurn(Base):
    __tablename__ = "agent_turns"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.agent_conversations.id", ondelete="CASCADE")
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(
        String(48),
        default="medtrust.agent-turn/v1",
        server_default="medtrust.agent-turn/v1",
    )
    input_length: Mapped[int] = mapped_column(Integer)
    context_applied: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    context_refs: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    intent: Mapped[str | None] = mapped_column(String(48))
    plan_source: Mapped[str | None] = mapped_column(String(16))
    provider: Mapped[str | None] = mapped_column(String(24))
    model_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(
        String(16), default="running", server_default="running"
    )
    route_hint: Mapped[str | None] = mapped_column(String(256))
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    answer_length: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    conversation: Mapped[AgentConversation] = relationship(back_populates="turns")
    steps: Mapped[list[AgentRunStep]] = relationship(
        back_populates="turn",
        cascade="all, delete-orphan",
        order_by="AgentRunStep.sequence_no",
    )

    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint("input_length >= 0 AND input_length <= 2000", name="input_length"),
        CheckConstraint("result_count >= 0", name="result_count_nonnegative"),
        CheckConstraint("answer_length >= 0", name="answer_length_nonnegative"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        CheckConstraint(
            f"status IN ({sql_values(AGENT_TURN_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            "schema_version = 'medtrust.agent-turn/v1'",
            name="schema_version",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_agent_turn_conversation_sequence",
        ),
        Index("ix_agent_turns_conversation_started", "conversation_id", "started_at"),
        Index("ix_agent_turns_status_started", "status", "started_at"),
    )


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    turn_id: Mapped[UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.agent_turns.id", ondelete="CASCADE")
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(16))
    tool_name: Mapped[str | None] = mapped_column(String(96))
    tool_label: Mapped[str | None] = mapped_column(String(96))
    risk_class: Mapped[str | None] = mapped_column(String(16))
    authorization_result: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    result_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    resource_refs: Mapped[list[str]] = mapped_column(
        JSON_DOCUMENT, default=list, server_default=text("'[]'")
    )
    source: Mapped[str | None] = mapped_column(String(128))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, server_default=func.now()
    )

    turn: Mapped[AgentTurn] = relationship(back_populates="steps")

    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint(
            f"step_type IN ({sql_values(AGENT_STEP_TYPES)})",
            name="step_type",
        ),
        CheckConstraint(
            f"status IN ({sql_values(AGENT_STEP_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            "risk_class IS NULL OR "
            f"risk_class IN ({sql_values(AGENT_TOOL_RISKS)})",
            name="risk_class",
        ),
        CheckConstraint("result_count >= 0", name="result_count_nonnegative"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        UniqueConstraint("turn_id", "sequence_no", name="uq_agent_step_turn_sequence"),
        Index("ix_agent_run_steps_turn_type", "turn_id", "step_type"),
    )

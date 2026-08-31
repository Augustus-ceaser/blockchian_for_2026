"""Agent conversations and redacted run steps.

Revision ID: 20260828_0059
Revises: 20260730_0058
"""

from alembic import op
import sqlalchemy as sa


revision = "20260828_0059"
down_revision = "20260730_0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_conversations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "space_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_organization_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role_code", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("entity_context", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "role_code IN ('space_operator','data_provider','model_provider','data_requester')",
            name="role_code",
        ),
        sa.CheckConstraint(
            "status IN ('active','closed')",
            name="status",
        ),
        sa.CheckConstraint(
            "turn_count >= 0",
            name="turn_count_nonnegative",
        ),
        sa.UniqueConstraint(
            "id", "space_id", name="uq_agent_conversation_space"
        ),
        schema="medtrust",
    )
    op.create_index(
        "ix_agent_conversations_actor_recent",
        "agent_conversations",
        ["space_id", "actor_user_id", "role_code", "last_seen_at"],
        schema="medtrust",
    )

    op.create_table(
        "agent_turns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column(
            "schema_version",
            sa.String(48),
            nullable=False,
            server_default="medtrust.agent-turn/v1",
        ),
        sa.Column("input_length", sa.Integer(), nullable=False),
        sa.Column(
            "context_applied", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("context_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("intent", sa.String(48), nullable=True),
        sa.Column("plan_source", sa.String(16), nullable=True),
        sa.Column("provider", sa.String(24), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="running"),
        sa.Column("route_hint", sa.String(256), nullable=True),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answer_length", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint("sequence_no > 0", name="sequence_positive"),
        sa.CheckConstraint(
            "input_length >= 0 AND input_length <= 2000",
            name="input_length",
        ),
        sa.CheckConstraint(
            "result_count >= 0", name="result_count_nonnegative"
        ),
        sa.CheckConstraint(
            "answer_length >= 0", name="answer_length_nonnegative"
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="duration_nonnegative",
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')",
            name="status",
        ),
        sa.CheckConstraint(
            "schema_version = 'medtrust.agent-turn/v1'",
            name="schema_version",
        ),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_agent_turn_conversation_sequence",
        ),
        schema="medtrust",
    )
    op.create_index(
        "ix_agent_turns_conversation_started",
        "agent_turns",
        ["conversation_id", "started_at"],
        schema="medtrust",
    )
    op.create_index(
        "ix_agent_turns_status_started",
        "agent_turns",
        ["status", "started_at"],
        schema="medtrust",
    )

    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "turn_id",
            sa.Uuid(),
            sa.ForeignKey("medtrust.agent_turns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(16), nullable=False),
        sa.Column("tool_name", sa.String(96), nullable=True),
        sa.Column("tool_label", sa.String(96), nullable=True),
        sa.Column("risk_class", sa.String(16), nullable=True),
        sa.Column("authorization_result", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resource_refs", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("source", sa.String(128), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "sequence_no > 0", name="sequence_positive"
        ),
        sa.CheckConstraint(
            "step_type IN ('authorization','tool','response')",
            name="step_type",
        ),
        sa.CheckConstraint(
            "status IN ('success','empty','error','denied')",
            name="status",
        ),
        sa.CheckConstraint(
            "risk_class IS NULL OR risk_class IN ('read','propose','commit')",
            name="risk_class",
        ),
        sa.CheckConstraint(
            "result_count >= 0",
            name="result_count_nonnegative",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="duration_nonnegative",
        ),
        sa.UniqueConstraint(
            "turn_id", "sequence_no", name="uq_agent_step_turn_sequence"
        ),
        schema="medtrust",
    )
    op.create_index(
        "ix_agent_run_steps_turn_type",
        "agent_run_steps",
        ["turn_id", "step_type"],
        schema="medtrust",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_run_steps_turn_type",
        table_name="agent_run_steps",
        schema="medtrust",
    )
    op.drop_table("agent_run_steps", schema="medtrust")
    op.drop_index(
        "ix_agent_turns_status_started",
        table_name="agent_turns",
        schema="medtrust",
    )
    op.drop_index(
        "ix_agent_turns_conversation_started",
        table_name="agent_turns",
        schema="medtrust",
    )
    op.drop_table("agent_turns", schema="medtrust")
    op.drop_index(
        "ix_agent_conversations_actor_recent",
        table_name="agent_conversations",
        schema="medtrust",
    )
    op.drop_table("agent_conversations", schema="medtrust")

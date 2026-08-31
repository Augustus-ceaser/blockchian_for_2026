"""Phase 5.13E-2C-R1 fixed reference execution authorization.

Revision ID: 20260729_0055
Revises: 20260729_0054
"""

from alembic import op
import sqlalchemy as sa

revision = "20260729_0055"
down_revision = "20260729_0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    def columns(table: str) -> set[str]:
        return {
            item["name"]
            for item in sa.inspect(bind).get_columns(table, schema="medtrust")
        }

    def checks(table: str) -> set[str]:
        return {
            item["name"]
            for item in sa.inspect(bind).get_check_constraints(
                table, schema="medtrust"
            )
        }

    def foreign_keys(table: str) -> set[str]:
        return {
            item["name"]
            for item in sa.inspect(bind).get_foreign_keys(
                table, schema="medtrust"
            )
        }

    if "local_executor_id" not in columns("hospital_executor_mirrors"):
        op.add_column(
            "hospital_executor_mirrors",
            sa.Column("local_executor_id", sa.String(36), nullable=True),
            schema="medtrust",
        )
    for name, column in (
        ("latest_status_event_id", sa.Column("latest_status_event_id", sa.Uuid(), nullable=True)),
        ("latest_status_event_sequence", sa.Column("latest_status_event_sequence", sa.Integer(), nullable=True)),
        ("latest_status_event_digest", sa.Column("latest_status_event_digest", sa.String(71), nullable=True)),
        ("latest_status_schema_version", sa.Column("latest_status_schema_version", sa.String(64), nullable=True)),
        ("latest_verified_readiness_event_id", sa.Column("latest_verified_readiness_event_id", sa.Uuid(), nullable=True)),
        ("latest_verified_readiness_digest", sa.Column("latest_verified_readiness_digest", sa.String(71), nullable=True)),
        ("latest_verified_readiness_at", sa.Column("latest_verified_readiness_at", sa.DateTime(timezone=True), nullable=True)),
        ("readiness_valid_until", sa.Column("readiness_valid_until", sa.DateTime(timezone=True), nullable=True)),
        ("fixed_reference_readiness_status", sa.Column("fixed_reference_readiness_status", sa.String(24), nullable=True)),
        ("fixed_reference_readiness_reason", sa.Column("fixed_reference_readiness_reason", sa.String(80), nullable=True)),
        ("attested_image_digest", sa.Column("attested_image_digest", sa.String(71), nullable=True)),
        ("attested_security_profile_digest", sa.Column("attested_security_profile_digest", sa.String(71), nullable=True)),
        ("attested_resource_policy_digest", sa.Column("attested_resource_policy_digest", sa.String(71), nullable=True)),
        ("attested_admission_digest", sa.Column("attested_admission_digest", sa.String(71), nullable=True)),
        ("attested_capability_digest", sa.Column("attested_capability_digest", sa.String(71), nullable=True)),
    ):
        if name not in columns("hospital_executor_mirrors"):
            op.add_column(
                "hospital_executor_mirrors", column, schema="medtrust"
            )
    if (
        "fk_executor_mirror_latest_status_event"
        not in foreign_keys("hospital_executor_mirrors")
    ):
        op.create_foreign_key(
            "fk_executor_mirror_latest_status_event",
            "hospital_executor_mirrors", "hospital_executor_status_events",
            ["latest_status_event_id"], ["id"], source_schema="medtrust",
            referent_schema="medtrust", ondelete="RESTRICT")
    if (
        "fk_executor_mirror_latest_readiness_event"
        not in foreign_keys("hospital_executor_mirrors")
    ):
        op.create_foreign_key(
            "fk_executor_mirror_latest_readiness_event",
            "hospital_executor_mirrors", "hospital_executor_status_events",
            ["latest_verified_readiness_event_id"], ["id"],
            source_schema="medtrust", referent_schema="medtrust",
            ondelete="RESTRICT")
    if (
        "ck_hospital_executor_mirrors_fixed_readiness_status"
        not in checks("hospital_executor_mirrors")
    ):
        op.create_check_constraint(
            "ck_hospital_executor_mirrors_fixed_readiness_status",
            "hospital_executor_mirrors",
            "fixed_reference_readiness_status IS NULL OR "
            "fixed_reference_readiness_status IN ('ready','not_ready')",
            schema="medtrust")
    if "schema_version" not in columns("hospital_executor_status_events"):
        op.add_column(
            "hospital_executor_status_events",
            sa.Column("schema_version", sa.String(64), nullable=True),
            schema="medtrust",
        )
    event_type = next(
        item["type"]
        for item in sa.inspect(bind).get_columns(
            "hospital_executor_status_events", schema="medtrust"
        )
        if item["name"] == "event_type"
    )
    if getattr(event_type, "length", 0) < 64:
        op.alter_column(
            "hospital_executor_status_events", "event_type",
            type_=sa.String(64), existing_type=sa.String(32),
            schema="medtrust",
        )
    if "nonce" not in columns("hospital_executor_status_events"):
        op.add_column(
            "hospital_executor_status_events",
            sa.Column("nonce", sa.String(96), nullable=True),
            schema="medtrust",
        )
    for name, column in (
        ("signing_key_id", sa.Column("signing_key_id", sa.String(80), nullable=True)),
        ("signature", sa.Column("signature", sa.Text(), nullable=True)),
        (
            "verification_status",
            sa.Column(
                "verification_status", sa.String(16), nullable=False,
                server_default="verified",
            ),
        ),
        (
            "verification_reason",
            sa.Column("verification_reason", sa.String(80), nullable=True),
        ),
        (
            "verified_at",
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        ),
    ):
        if name not in columns("hospital_executor_status_events"):
            op.add_column(
                "hospital_executor_status_events", column, schema="medtrust"
            )
    nonce_is_unique = any(
        item.get("column_names") == ["nonce"]
        for item in sa.inspect(bind).get_unique_constraints(
            "hospital_executor_status_events", schema="medtrust"
        )
    )
    if not nonce_is_unique:
        op.create_unique_constraint(
            "uq_executor_status_event_nonce",
            "hospital_executor_status_events", ["nonce"], schema="medtrust",
        )
    event_checks = checks("hospital_executor_status_events")
    if "ck_hospital_executor_status_events_event_type" in event_checks:
        op.drop_constraint(
            op.f("ck_hospital_executor_status_events_event_type"),
            "hospital_executor_status_events", schema="medtrust",
            type_="check",
        )
    op.create_check_constraint(
        "ck_hospital_executor_status_events_event_type",
        "hospital_executor_status_events",
        "event_type IN ('registered','heartbeat','paused','resumed','revoked',"
        "'EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION')",
        schema="medtrust",
    )
    if (
        "ck_hospital_executor_status_events_verification_status"
        not in checks("hospital_executor_status_events")
    ):
        op.create_check_constraint(
            "ck_hospital_executor_status_events_verification_status",
            "hospital_executor_status_events",
            "verification_status IN ('verified','rejected')",
            schema="medtrust",
        )
    op.execute("""
        CREATE FUNCTION medtrust.guard_hospital_executor_status_event_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'hospital executor status events are append-only';
        END;
        $$;
    """)
    op.execute("""
        DROP TRIGGER IF EXISTS trg_hospital_executor_status_event_append_only
        ON medtrust.hospital_executor_status_events
    """)
    op.execute("""
        CREATE TRIGGER trg_hospital_executor_status_event_append_only
        BEFORE UPDATE OR DELETE ON medtrust.hospital_executor_status_events
        FOR EACH ROW EXECUTE FUNCTION
          medtrust.guard_hospital_executor_status_event_append_only()
    """)
    for table, additions in {
        "control_readiness_snapshots": (
            (
                "requested_action",
                sa.Column(
                    "requested_action", sa.String(48), nullable=False,
                    server_default="VALIDATE_POLICY_ONLY",
                ),
            ),
            ("task_type", sa.Column("task_type", sa.String(48), nullable=True)),
        ),
        "policy_bundle_versions": (
            (
                "execution_scope",
                sa.Column("execution_scope", sa.String(40), nullable=True),
            ),
            ("task_type", sa.Column("task_type", sa.String(48), nullable=True)),
            (
                "max_execution_count",
                sa.Column(
                    "max_execution_count", sa.Integer(), nullable=False,
                    server_default="0",
                ),
            ),
        ),
        "execution_orders": (
            (
                "execution_authorized",
                sa.Column(
                    "execution_authorized", sa.Boolean(), nullable=False,
                    server_default=sa.false(),
                ),
            ),
            (
                "execution_scope",
                sa.Column("execution_scope", sa.String(40), nullable=True),
            ),
            ("task_type", sa.Column("task_type", sa.String(48), nullable=True)),
            (
                "max_execution_count",
                sa.Column(
                    "max_execution_count", sa.Integer(), nullable=False,
                    server_default="0",
                ),
            ),
            (
                "consumed_count",
                sa.Column(
                    "consumed_count", sa.Integer(), nullable=False,
                    server_default="0",
                ),
            ),
            ("executor_id", sa.Column("executor_id", sa.Uuid(), nullable=True)),
        ),
    }.items():
        for name, column in additions:
            if name not in columns(table):
                op.add_column(table, column, schema="medtrust")
    if (
        "fk_execution_orders_executor_id_hospital_executor_mirrors"
        not in foreign_keys("execution_orders")
    ):
        op.create_foreign_key(
            "fk_execution_orders_executor_id_hospital_executor_mirrors",
            "execution_orders", "hospital_executor_mirrors",
            ["executor_id"], ["id"], source_schema="medtrust",
            referent_schema="medtrust", ondelete="RESTRICT")
    for table, constraints in {
        "control_readiness_snapshots": ("mode", "control_only"),
        "policy_bundle_versions": ("action", "execution_disabled"),
        "execution_orders": ("mode", "action"),
    }.items():
        existing_checks = checks(table)
        for name in constraints:
            constraint_name = f"ck_{table}_{name}"
            if constraint_name in existing_checks:
                op.drop_constraint(
                    op.f(constraint_name), table, schema="medtrust",
                    type_="check")
    if (
        "ck_control_readiness_snapshots_authorization_mode"
        not in checks("control_readiness_snapshots")
    ):
        op.create_check_constraint(
        "ck_control_readiness_snapshots_authorization_mode",
        "control_readiness_snapshots",
        "(readiness_mode='CONTROL_POLICY_VALIDATION' AND NOT execution_authorized "
        "AND requested_action='VALIDATE_POLICY_ONLY' AND task_type IS NULL) OR "
        "(readiness_mode='FIXED_REFERENCE_EXECUTION' AND execution_authorized "
        "AND requested_action='EXECUTE_FIXED_REFERENCE_TASK' "
        "AND task_type='PATHMNIST_REFERENCE_V1')", schema="medtrust")
    if (
        "ck_control_readiness_snapshots_hard_isolation_false"
        not in checks("control_readiness_snapshots")
    ):
        op.create_check_constraint(
            "ck_control_readiness_snapshots_hard_isolation_false",
            "control_readiness_snapshots", "NOT hard_isolation",
            schema="medtrust")
    if (
        "ck_policy_bundle_versions_authorization_mode"
        not in checks("policy_bundle_versions")
    ):
        op.create_check_constraint(
        "ck_policy_bundle_versions_authorization_mode",
        "policy_bundle_versions",
        "(requested_action='VALIDATE_POLICY_ONLY' AND NOT execution_authorized "
        "AND execution_scope IS NULL AND task_type IS NULL AND max_execution_count=0) OR "
        "(requested_action='EXECUTE_FIXED_REFERENCE_TASK' AND execution_authorized "
        "AND execution_scope='FIXED_REFERENCE_ONLY' "
        "AND task_type='PATHMNIST_REFERENCE_V1' AND max_execution_count=1)",
        schema="medtrust")
    if (
        "ck_execution_orders_authorization_mode"
        not in checks("execution_orders")
    ):
        op.create_check_constraint(
        "ck_execution_orders_authorization_mode", "execution_orders",
        "(order_mode='CONTROL_VALIDATION_ONLY' AND requested_action='VALIDATE_POLICY_ONLY' "
        "AND NOT execution_authorized AND execution_scope IS NULL "
        "AND task_type IS NULL AND max_execution_count=0) OR "
        "(order_mode='FIXED_REFERENCE_EXECUTION' "
        "AND requested_action='EXECUTE_FIXED_REFERENCE_TASK' "
        "AND execution_authorized AND execution_scope='FIXED_REFERENCE_ONLY' "
        "AND task_type='PATHMNIST_REFERENCE_V1' AND max_execution_count=1 "
        "AND executor_id IS NOT NULL)", schema="medtrust")
    if (
        "ck_execution_orders_consumption_limit"
        not in checks("execution_orders")
    ):
        op.create_check_constraint(
            "ck_execution_orders_consumption_limit", "execution_orders",
            "consumed_count >= 0 AND consumed_count <= max_execution_count",
            schema="medtrust")


def downgrade() -> None:
    raise RuntimeError("Phase 5.13E-2C-R1 authorization records are append-only")

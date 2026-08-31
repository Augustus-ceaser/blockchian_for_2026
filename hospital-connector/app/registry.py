from __future__ import annotations

import json
import hashlib
import csv
import os
import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fixed_reference_safety_margin_seconds() -> int:
    value = int(
        os.getenv(
            "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS",
            "300",
        )
    )
    if value < 0:
        raise ValueError("FIXED_REFERENCE_SAFETY_MARGIN_INVALID")
    return value


def migrate(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_schema_migrations (
      version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS local_asset_descriptors (
      id TEXT PRIMARY KEY, connector_id TEXT NOT NULL, local_asset_key TEXT NOT NULL,
      display_name TEXT NOT NULL, description TEXT NOT NULL, asset_kind TEXT NOT NULL,
      modality TEXT NOT NULL, source_category TEXT NOT NULL,
      sensitivity_classification TEXT NOT NULL, status TEXT NOT NULL,
      current_version_id TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, UNIQUE(connector_id, local_asset_key)
    );
    CREATE TABLE IF NOT EXISTS local_asset_versions (
      id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, version_label TEXT NOT NULL,
      schema_version TEXT NOT NULL, metadata_payload TEXT NOT NULL,
      metadata_digest TEXT NOT NULL, schema_digest TEXT NOT NULL,
      created_by TEXT NOT NULL, created_at TEXT NOT NULL,
      supersedes_version_id TEXT, is_current INTEGER NOT NULL,
      UNIQUE(asset_id, version_label)
    );
    CREATE TABLE IF NOT EXISTS local_asset_location_refs (
      id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, storage_backend TEXT NOT NULL,
      location_alias TEXT NOT NULL, encrypted_location_reference TEXT,
      location_digest TEXT NOT NULL, access_mode TEXT NOT NULL,
      available INTEGER NOT NULL, last_checked_at TEXT NOT NULL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS local_data_quality_profiles (
      id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, profile_version TEXT NOT NULL,
      assessment_scope TEXT NOT NULL, assessed_at TEXT NOT NULL, assessed_by TEXT NOT NULL,
      method_version TEXT NOT NULL, disclosure_summary TEXT NOT NULL,
      quality_summary TEXT NOT NULL, known_limitations TEXT NOT NULL,
      warning_flags TEXT NOT NULL, fitness_for_use_status TEXT NOT NULL,
      quality_digest TEXT NOT NULL, status TEXT NOT NULL,
      supersedes_profile_id TEXT, created_at TEXT NOT NULL,
      UNIQUE(asset_version_id, profile_version)
    );
    CREATE TABLE IF NOT EXISTS local_asset_reviews (
      id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, quality_profile_id TEXT NOT NULL,
      reviewer TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL,
      reviewed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS local_asset_metadata_bundles (
      id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL, bundle_sequence INTEGER NOT NULL UNIQUE,
      payload_json TEXT NOT NULL, bundle_digest TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL, created_at TEXT NOT NULL, synced_at TEXT,
      central_mirror_id TEXT, central_version_id TEXT
    );
    CREATE TRIGGER IF NOT EXISTS trg_local_asset_versions_append_only_update
      BEFORE UPDATE ON local_asset_versions BEGIN
      SELECT RAISE(ABORT, 'local asset versions are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_local_asset_versions_append_only_delete
      BEFORE DELETE ON local_asset_versions BEGIN
      SELECT RAISE(ABORT, 'local asset versions are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_quality_profiles_append_only_update
      BEFORE UPDATE ON local_data_quality_profiles BEGIN
      SELECT RAISE(ABORT, 'quality profiles are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_quality_profiles_append_only_delete
      BEFORE DELETE ON local_data_quality_profiles BEGIN
      SELECT RAISE(ABORT, 'quality profiles are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_reviews_append_only_update
      BEFORE UPDATE ON local_asset_reviews BEGIN
      SELECT RAISE(ABORT, 'local reviews are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_reviews_append_only_delete
      BEFORE DELETE ON local_asset_reviews BEGIN
      SELECT RAISE(ABORT, 'local reviews are append-only'); END;
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13C_0001", _now()),
    )
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_users (
      id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
      password_hash TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
      failed_login_count INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
      last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      CHECK(role IN ('local_asset_curator','local_asset_reviewer','local_policy_reviewer','connector_local_admin'))
    );
    CREATE TABLE IF NOT EXISTS local_sessions (
      id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_digest TEXT NOT NULL UNIQUE,
      issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
      last_seen_at TEXT NOT NULL, user_agent_digest TEXT NOT NULL, created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES local_users(id)
    );
    CREATE TABLE IF NOT EXISTS local_asset_submissions (
      id TEXT PRIMARY KEY, asset_version_id TEXT NOT NULL UNIQUE,
      quality_profile_id TEXT NOT NULL, submitted_by TEXT NOT NULL,
      status TEXT NOT NULL, submitted_at TEXT NOT NULL,
      CHECK(status IN ('pending','approved','rejected'))
    );
    CREATE TABLE IF NOT EXISTS local_sync_history (
      id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, actor_id TEXT NOT NULL,
      status TEXT NOT NULL, response_code INTEGER, detail TEXT NOT NULL,
      attempted_at TEXT NOT NULL
    );
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13C_0002", _now()),
    )
    user_sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='local_users'"
    ).fetchone()[0]
    if "local_policy_reviewer" not in user_sql:
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
        ALTER TABLE local_sessions RENAME TO local_sessions_before_513d;
        ALTER TABLE local_users RENAME TO local_users_before_513d;
        CREATE TABLE local_users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
          password_hash TEXT NOT NULL, role TEXT NOT NULL, status TEXT NOT NULL,
          failed_login_count INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
          last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          CHECK(role IN ('local_asset_curator','local_asset_reviewer','local_policy_reviewer','connector_local_admin'))
        );
        CREATE TABLE local_sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_digest TEXT NOT NULL UNIQUE,
          issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, revoked_at TEXT,
          last_seen_at TEXT NOT NULL, user_agent_digest TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES local_users(id)
        );
        INSERT INTO local_users SELECT * FROM local_users_before_513d;
        INSERT INTO local_sessions SELECT * FROM local_sessions_before_513d;
        DROP TABLE local_sessions_before_513d;
        DROP TABLE local_users_before_513d;
        """)
        db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_control_orders (
      id TEXT PRIMARY KEY, central_order_id TEXT NOT NULL UNIQUE,
      connector_sequence INTEGER NOT NULL UNIQUE, order_payload TEXT NOT NULL,
      order_digest TEXT NOT NULL UNIQUE, order_signature TEXT NOT NULL,
      policy_payload TEXT NOT NULL, policy_digest TEXT NOT NULL,
      policy_signature TEXT NOT NULL, signing_key_id TEXT NOT NULL,
      signing_public_key TEXT NOT NULL, signing_key_fingerprint TEXT NOT NULL,
      central_status TEXT NOT NULL, local_status TEXT NOT NULL,
      received_at TEXT NOT NULL, expires_at TEXT NOT NULL,
      CHECK(local_status IN ('validation_failed','awaiting_local_review','accepted','rejected','revoked_after_acceptance'))
    );
    CREATE TABLE IF NOT EXISTS local_policy_validations (
      id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
      validation_status TEXT NOT NULL, checks_json TEXT NOT NULL,
      failure_code TEXT, validated_at TEXT NOT NULL,
      CHECK(validation_status IN ('passed','failed'))
    );
    CREATE TABLE IF NOT EXISTS local_policy_reviews (
      id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
      reviewer_id TEXT NOT NULL, decision TEXT NOT NULL,
      reason_code TEXT NOT NULL, reason_text TEXT NOT NULL,
      decided_at TEXT NOT NULL,
      CHECK(decision IN ('accepted','rejected','revoked_after_acceptance'))
    );
    CREATE TABLE IF NOT EXISTS local_order_receipts (
      id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
      payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
      signature TEXT NOT NULL, delivery_status TEXT NOT NULL,
      created_at TEXT NOT NULL, delivered_at TEXT
    );
    CREATE TABLE IF NOT EXISTS local_order_decisions (
      id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
      payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
      signature TEXT NOT NULL, delivery_status TEXT NOT NULL,
      created_at TEXT NOT NULL, delivered_at TEXT
    );
    CREATE TABLE IF NOT EXISTS local_order_replay_cache (
      nonce TEXT PRIMARY KEY, central_order_id TEXT NOT NULL,
      connector_sequence INTEGER NOT NULL, first_seen_at TEXT NOT NULL
    );
    CREATE TRIGGER IF NOT EXISTS trg_local_control_orders_no_delete
      BEFORE DELETE ON local_control_orders BEGIN
      SELECT RAISE(ABORT, 'local control orders are append-only'); END;
    CREATE TRIGGER IF NOT EXISTS trg_local_policy_validations_immutable
      BEFORE UPDATE ON local_policy_validations BEGIN
      SELECT RAISE(ABORT, 'local policy validations are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_local_policy_reviews_immutable
      BEFORE UPDATE ON local_policy_reviews BEGIN
      SELECT RAISE(ABORT, 'local policy reviews are immutable'); END;
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13D_0001", _now()),
    )
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_executor_registrations (
      id TEXT PRIMARY KEY, executor_instance_id TEXT NOT NULL UNIQUE,
      executor_version TEXT NOT NULL, architecture TEXT NOT NULL,
      csr_pem TEXT NOT NULL, csr_fingerprint TEXT NOT NULL UNIQUE,
      installation_digest TEXT NOT NULL UNIQUE, capability_payload TEXT NOT NULL,
      capability_digest TEXT NOT NULL, runtime_digest TEXT NOT NULL,
      image_digest TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE,
      request_timestamp TEXT NOT NULL, status TEXT NOT NULL,
      reviewed_by TEXT, reviewed_at TEXT, rejection_reason TEXT,
      executor_id TEXT, created_at TEXT NOT NULL,
      CHECK(status IN ('pending','approved','rejected','certificate_issued'))
    );
    CREATE TABLE IF NOT EXISTS local_executors (
      id TEXT PRIMARY KEY, connector_id TEXT NOT NULL,
      executor_instance_id TEXT NOT NULL UNIQUE, executor_version TEXT NOT NULL,
      architecture TEXT NOT NULL, status TEXT NOT NULL,
      current_certificate_id TEXT, current_capability_manifest_id TEXT,
      last_heartbeat_at TEXT, last_heartbeat_sequence INTEGER NOT NULL DEFAULT 0,
      status_sequence INTEGER NOT NULL DEFAULT 0,
      security_status TEXT NOT NULL, activated_at TEXT,
      paused_at TEXT, revoked_at TEXT, revocation_reason TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      CHECK(status IN ('created','pending','approved','active','paused','revoked','offline')),
      CHECK(security_status IN ('pending','passed','failed','revoked')),
      CHECK(last_heartbeat_sequence >= 0 AND status_sequence >= 0)
    );
    CREATE TABLE IF NOT EXISTS local_executor_certificates (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL, serial_number TEXT NOT NULL UNIQUE,
      subject TEXT NOT NULL, issuer TEXT NOT NULL,
      fingerprint_sha256 TEXT NOT NULL UNIQUE, certificate_pem TEXT NOT NULL,
      valid_from TEXT NOT NULL, valid_to TEXT NOT NULL, status TEXT NOT NULL,
      issued_at TEXT NOT NULL, revoked_at TEXT, revocation_reason TEXT,
      CHECK(status IN ('active','revoked','expired'))
    );
    CREATE TABLE IF NOT EXISTS local_executor_capability_manifests (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL, schema_version TEXT NOT NULL,
      manifest_version TEXT NOT NULL, executor_version TEXT NOT NULL,
      runtime TEXT NOT NULL, image_digest TEXT NOT NULL, architecture TEXT NOT NULL,
      network_mode TEXT NOT NULL, filesystem_mode TEXT NOT NULL,
      rootless INTEGER NOT NULL, gpu INTEGER NOT NULL,
      supported_task_types TEXT NOT NULL, resource_limits TEXT NOT NULL,
      security_features TEXT NOT NULL, execution_enabled INTEGER NOT NULL,
      hard_isolation INTEGER NOT NULL, manifest_digest TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL,
      CHECK(network_mode='none'),
      CHECK(filesystem_mode='readonly_input'),
      CHECK(rootless=1 AND gpu=0),
      CHECK(execution_enabled=0 AND hard_isolation=0)
    );
    CREATE TABLE IF NOT EXISTS local_executor_heartbeats (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
      sequence INTEGER NOT NULL, sent_at TEXT NOT NULL, status TEXT NOT NULL,
      capability_digest TEXT NOT NULL, runtime_digest TEXT NOT NULL,
      certificate_fingerprint TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE,
      message_digest TEXT NOT NULL UNIQUE, received_at TEXT NOT NULL,
      CHECK(sequence > 0), CHECK(status IN ('healthy','degraded')),
      UNIQUE(executor_id, sequence)
    );
    CREATE TABLE IF NOT EXISTS local_executor_status_sync_history (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL, status_sequence INTEGER NOT NULL,
      event_type TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
      delivery_status TEXT NOT NULL, response_code INTEGER,
      detail TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
      UNIQUE(executor_id, status_sequence)
    );
    CREATE TRIGGER IF NOT EXISTS trg_executor_registrations_no_delete
      BEFORE DELETE ON local_executor_registrations BEGIN
      SELECT RAISE(ABORT, 'executor registrations are retained'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_capabilities_immutable
      BEFORE UPDATE ON local_executor_capability_manifests BEGIN
      SELECT RAISE(ABORT, 'executor capability manifests are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_capabilities_no_delete
      BEFORE DELETE ON local_executor_capability_manifests BEGIN
      SELECT RAISE(ABORT, 'executor capability manifests are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_heartbeats_immutable
      BEFORE UPDATE ON local_executor_heartbeats BEGIN
      SELECT RAISE(ABORT, 'executor heartbeats are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_heartbeats_no_delete
      BEFORE DELETE ON local_executor_heartbeats BEGIN
      SELECT RAISE(ABORT, 'executor heartbeats are immutable'); END;
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13E_0001", _now()),
    )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0002",),
    ).fetchone()
    if migration_applied is None:
        db.executescript("""
        DROP TRIGGER IF EXISTS trg_executor_capabilities_immutable;
        DROP TRIGGER IF EXISTS trg_executor_capabilities_no_delete;
        ALTER TABLE local_executor_capability_manifests
          RENAME TO local_executor_capability_manifests_before_513e_0002;
        CREATE TABLE local_executor_capability_manifests (
          id TEXT PRIMARY KEY, executor_id TEXT NOT NULL, schema_version TEXT NOT NULL,
          manifest_version TEXT NOT NULL, executor_version TEXT NOT NULL,
          runtime TEXT NOT NULL, image_digest TEXT NOT NULL, architecture TEXT NOT NULL,
          network_mode TEXT NOT NULL, filesystem_mode TEXT NOT NULL,
          rootless INTEGER NOT NULL, gpu INTEGER NOT NULL,
          supported_task_types TEXT NOT NULL, resource_limits TEXT NOT NULL,
          security_features TEXT NOT NULL, execution_enabled INTEGER NOT NULL,
          hard_isolation INTEGER NOT NULL, manifest_digest TEXT NOT NULL,
          created_at TEXT NOT NULL,
          CHECK(network_mode='none'),
          CHECK(filesystem_mode='readonly_input'),
          CHECK(rootless=1 AND gpu=0),
          CHECK(execution_enabled=0 AND hard_isolation=0),
          UNIQUE(executor_id, manifest_digest)
        );
        INSERT INTO local_executor_capability_manifests
          SELECT * FROM local_executor_capability_manifests_before_513e_0002;
        DROP TABLE local_executor_capability_manifests_before_513e_0002;
        CREATE TRIGGER trg_executor_capabilities_immutable
          BEFORE UPDATE ON local_executor_capability_manifests BEGIN
          SELECT RAISE(ABORT, 'executor capability manifests are immutable'); END;
        CREATE TRIGGER trg_executor_capabilities_no_delete
          BEFORE DELETE ON local_executor_capability_manifests BEGIN
          SELECT RAISE(ABORT, 'executor capability manifests are immutable'); END;

        ALTER TABLE local_executor_status_sync_history
          RENAME TO local_executor_status_sync_history_before_513e_0002;
        CREATE TABLE local_executor_status_sync_history (
          id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
          status_sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
          payload_digest TEXT NOT NULL UNIQUE, delivery_status TEXT NOT NULL,
          response_code INTEGER, detail TEXT NOT NULL, created_at TEXT NOT NULL,
          delivered_at TEXT
        );
        INSERT INTO local_executor_status_sync_history
          SELECT * FROM local_executor_status_sync_history_before_513e_0002;
        DROP TABLE local_executor_status_sync_history_before_513e_0002;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0002", _now()),
        )
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_executor_security_profiles (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
      security_version TEXT NOT NULL, network_mode TEXT NOT NULL,
      filesystem_mode TEXT NOT NULL, rootless INTEGER NOT NULL,
      privileged INTEGER NOT NULL, docker_socket_access INTEGER NOT NULL,
      runtime_download INTEGER NOT NULL, resource_policy TEXT NOT NULL,
      profile_digest TEXT NOT NULL UNIQUE, status TEXT NOT NULL,
      created_at TEXT NOT NULL,
      CHECK(status IN ('valid','invalid','revoked'))
    );
    CREATE TABLE IF NOT EXISTS local_execution_image_manifests (
      id TEXT PRIMARY KEY, image_id TEXT NOT NULL UNIQUE,
      image_digest TEXT NOT NULL UNIQUE, signature TEXT,
      signature_verified INTEGER NOT NULL, builder TEXT NOT NULL,
      build_time TEXT NOT NULL, dependency_hash TEXT NOT NULL,
      runtime_version TEXT NOT NULL, security_scan_status TEXT NOT NULL,
      status TEXT NOT NULL, manifest_digest TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      CHECK(status IN ('candidate','approved','deprecated','revoked')),
      CHECK(security_scan_status IN ('passed','failed','unknown'))
    );
    CREATE TABLE IF NOT EXISTS local_executor_admission_checks (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
      security_profile_id TEXT, image_manifest_id TEXT,
      decision TEXT NOT NULL, rejection_reasons TEXT NOT NULL,
      policy_snapshot TEXT NOT NULL, admission_digest TEXT NOT NULL UNIQUE,
      execution_enabled INTEGER NOT NULL, checked_by TEXT NOT NULL,
      checked_at TEXT NOT NULL,
      CHECK(decision IN ('approved','rejected')),
      CHECK(execution_enabled=0)
    );
    CREATE TRIGGER IF NOT EXISTS trg_executor_security_profiles_immutable
      BEFORE UPDATE ON local_executor_security_profiles BEGIN
      SELECT RAISE(ABORT, 'executor security profiles are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_security_profiles_no_delete
      BEFORE DELETE ON local_executor_security_profiles BEGIN
      SELECT RAISE(ABORT, 'executor security profiles are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_admissions_immutable
      BEFORE UPDATE ON local_executor_admission_checks BEGIN
      SELECT RAISE(ABORT, 'executor admission checks are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_admissions_no_delete
      BEFORE DELETE ON local_executor_admission_checks BEGIN
      SELECT RAISE(ABORT, 'executor admission checks are immutable'); END;
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13E_0003", _now()),
    )
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_executor_runtime_sessions (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
      admission_check_id TEXT NOT NULL UNIQUE,
      runtime_version TEXT NOT NULL, image_manifest_id TEXT NOT NULL,
      security_profile_id TEXT NOT NULL, sandbox_id TEXT NOT NULL UNIQUE,
      status TEXT NOT NULL, runtime_policy TEXT NOT NULL,
      policy_digest TEXT NOT NULL, idempotency_digest TEXT NOT NULL UNIQUE,
      created_at TEXT NOT NULL, prepared_at TEXT, destroyed_at TEXT,
      CHECK(status IN ('created','admitted','prepared','started','stopped','destroyed'))
    );
    CREATE TABLE IF NOT EXISTS local_sandbox_workspaces (
      id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
      sandbox_id TEXT NOT NULL UNIQUE, relative_reference TEXT NOT NULL UNIQUE,
      directory_manifest TEXT NOT NULL, status TEXT NOT NULL,
      created_at TEXT NOT NULL, destroyed_at TEXT,
      CHECK(status IN ('prepared','destroyed'))
    );
    CREATE TABLE IF NOT EXISTS local_runtime_lifecycle_events (
      id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL,
      sequence INTEGER NOT NULL, event_type TEXT NOT NULL,
      status TEXT NOT NULL, detail_json TEXT NOT NULL,
      event_digest TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
      UNIQUE(runtime_session_id, sequence)
    );
    CREATE TRIGGER IF NOT EXISTS trg_runtime_started_forbidden
      BEFORE UPDATE OF status ON local_executor_runtime_sessions
      WHEN NEW.status='started' BEGIN
      SELECT RAISE(ABORT, 'runtime start is forbidden in phase 5.13E-2A'); END;
    CREATE TRIGGER IF NOT EXISTS trg_runtime_destroyed_terminal
      BEFORE UPDATE OF status ON local_executor_runtime_sessions
      WHEN OLD.status='destroyed' AND NEW.status!='destroyed' BEGIN
      SELECT RAISE(ABORT, 'destroyed runtime is terminal'); END;
    CREATE TRIGGER IF NOT EXISTS trg_runtime_events_immutable
      BEFORE UPDATE ON local_runtime_lifecycle_events BEGIN
      SELECT RAISE(ABORT, 'runtime lifecycle events are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_runtime_events_no_delete
      BEFORE DELETE ON local_runtime_lifecycle_events BEGIN
      SELECT RAISE(ABORT, 'runtime lifecycle events are immutable'); END;
    """)
    db.execute(
        "INSERT OR IGNORE INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
        ("phase5.13E_0004", _now()),
    )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0005",),
    ).fetchone()
    if migration_applied is None:
        db.executescript("""
        ALTER TABLE local_execution_image_manifests
          RENAME TO local_execution_image_manifests_before_513e_0005;
        CREATE TABLE local_execution_image_manifests (
          id TEXT PRIMARY KEY, image_id TEXT NOT NULL UNIQUE,
          image_digest TEXT NOT NULL, signature TEXT,
          signature_verified INTEGER NOT NULL, builder TEXT NOT NULL,
          build_time TEXT NOT NULL, dependency_hash TEXT NOT NULL,
          runtime_version TEXT NOT NULL, security_scan_status TEXT NOT NULL,
          status TEXT NOT NULL, manifest_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          CHECK(status IN ('candidate','approved','deprecated','revoked')),
          CHECK(security_scan_status IN ('passed','failed','unknown'))
        );
        INSERT INTO local_execution_image_manifests
          SELECT * FROM local_execution_image_manifests_before_513e_0005;
        DROP TABLE local_execution_image_manifests_before_513e_0005;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0005", _now()),
        )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0006",),
    ).fetchone()
    if migration_applied is None:
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
        DROP TRIGGER IF EXISTS trg_runtime_started_forbidden;
        DROP TRIGGER IF EXISTS trg_runtime_destroyed_terminal;
        ALTER TABLE local_executor_runtime_sessions
          RENAME TO local_executor_runtime_sessions_before_513e_0006;
        CREATE TABLE local_executor_runtime_sessions (
          id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
          admission_check_id TEXT NOT NULL UNIQUE,
          runtime_version TEXT NOT NULL, image_manifest_id TEXT NOT NULL,
          security_profile_id TEXT NOT NULL, sandbox_id TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, runtime_policy TEXT NOT NULL,
          policy_digest TEXT NOT NULL, idempotency_digest TEXT NOT NULL UNIQUE,
          task_digest TEXT, runtime_digest TEXT,
          created_at TEXT NOT NULL, prepared_at TEXT, started_at TEXT,
          completed_at TEXT, failed_at TEXT, destroyed_at TEXT,
          CHECK(status IN (
            'created','admitted','prepared','running','completed','failed',
            'stopped','destroyed'
          ))
        );
        INSERT INTO local_executor_runtime_sessions
          (id,executor_id,admission_check_id,runtime_version,image_manifest_id,
           security_profile_id,sandbox_id,status,runtime_policy,policy_digest,
           idempotency_digest,created_at,prepared_at,destroyed_at)
          SELECT id,executor_id,admission_check_id,runtime_version,
                 image_manifest_id,security_profile_id,sandbox_id,status,
                 runtime_policy,policy_digest,idempotency_digest,created_at,
                 prepared_at,destroyed_at
            FROM local_executor_runtime_sessions_before_513e_0006;
        DROP TABLE local_executor_runtime_sessions_before_513e_0006;
        CREATE TRIGGER trg_runtime_destroyed_terminal
          BEFORE UPDATE OF status ON local_executor_runtime_sessions
          WHEN OLD.status='destroyed' AND NEW.status!='destroyed' BEGIN
          SELECT RAISE(ABORT, 'destroyed runtime is terminal'); END;
        CREATE TRIGGER trg_runtime_completed_terminal
          BEFORE UPDATE OF status ON local_executor_runtime_sessions
          WHEN OLD.status IN ('completed','failed')
               AND NEW.status NOT IN (OLD.status,'destroyed') BEGIN
          SELECT RAISE(ABORT, 'finished runtime is terminal'); END;
        CREATE TABLE local_execution_task_manifests (
          id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
          task_type TEXT NOT NULL, task_version TEXT NOT NULL,
          image_digest TEXT NOT NULL, model_reference TEXT NOT NULL,
          dataset_reference TEXT NOT NULL, input_schema TEXT NOT NULL,
          output_schema TEXT NOT NULL, resource_policy TEXT NOT NULL,
          output_allowlist TEXT NOT NULL, task_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          CHECK(task_type='PATHMNIST_REFERENCE_V1')
        );
        CREATE TABLE local_execution_input_manifests (
          id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
          asset_version_id TEXT NOT NULL, metadata_digest TEXT NOT NULL,
          sample_count INTEGER NOT NULL, schema_digest TEXT NOT NULL,
          fixed_indices_digest TEXT NOT NULL, input_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL, CHECK(sample_count=20)
        );
        CREATE TABLE local_reference_executions (
          id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
          task_manifest_id TEXT NOT NULL, input_manifest_id TEXT NOT NULL,
          status TEXT NOT NULL, request_digest TEXT NOT NULL UNIQUE,
          result_digest TEXT, failure_code TEXT,
          created_at TEXT NOT NULL, started_at TEXT NOT NULL,
          completed_at TEXT, failed_at TEXT,
          CHECK(status IN ('running','completed','failed'))
        );
        CREATE TABLE local_execution_artifacts (
          id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
          execution_id TEXT NOT NULL UNIQUE, artifact_type TEXT NOT NULL,
          status TEXT NOT NULL, relative_reference TEXT NOT NULL UNIQUE,
          output_manifest TEXT NOT NULL, artifact_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL,
          CHECK(status IN ('created','quarantined')),
          CHECK(artifact_type='PATHMNIST_REFERENCE_AGGREGATE')
        );
        CREATE TRIGGER trg_execution_task_manifests_immutable
          BEFORE UPDATE ON local_execution_task_manifests BEGIN
          SELECT RAISE(ABORT, 'execution task manifests are immutable'); END;
        CREATE TRIGGER trg_execution_task_manifests_no_delete
          BEFORE DELETE ON local_execution_task_manifests BEGIN
          SELECT RAISE(ABORT, 'execution task manifests are immutable'); END;
        CREATE TRIGGER trg_execution_input_manifests_immutable
          BEFORE UPDATE ON local_execution_input_manifests BEGIN
          SELECT RAISE(ABORT, 'execution input manifests are immutable'); END;
        CREATE TRIGGER trg_execution_input_manifests_no_delete
          BEFORE DELETE ON local_execution_input_manifests BEGIN
          SELECT RAISE(ABORT, 'execution input manifests are immutable'); END;
        CREATE TRIGGER trg_execution_artifacts_no_delete
          BEFORE DELETE ON local_execution_artifacts BEGIN
          SELECT RAISE(ABORT, 'execution artifacts require review lifecycle'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0006", _now()),
        )
        db.execute("PRAGMA foreign_keys=ON")
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0007",),
    ).fetchone()
    if migration_applied is None:
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
        ALTER TABLE local_sessions RENAME TO local_sessions_before_513e_0007;
        ALTER TABLE local_users RENAME TO local_users_before_513e_0007;
        CREATE TABLE local_users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL, status TEXT NOT NULL,
          failed_login_count INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
          last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          CHECK(role IN (
            'local_asset_curator','local_asset_reviewer',
            'local_policy_reviewer','local_artifact_reviewer',
            'connector_local_admin'
          ))
        );
        CREATE TABLE local_sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          session_digest TEXT NOT NULL UNIQUE, issued_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, revoked_at TEXT, last_seen_at TEXT NOT NULL,
          user_agent_digest TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES local_users(id)
        );
        INSERT INTO local_users SELECT * FROM local_users_before_513e_0007;
        INSERT INTO local_sessions SELECT * FROM local_sessions_before_513e_0007;
        DROP TABLE local_sessions_before_513e_0007;
        DROP TABLE local_users_before_513e_0007;
        ALTER TABLE local_execution_artifacts
          RENAME TO local_execution_artifacts_before_513e_0007;
        CREATE TABLE local_execution_artifacts (
          id TEXT PRIMARY KEY, runtime_session_id TEXT NOT NULL UNIQUE,
          execution_id TEXT NOT NULL UNIQUE, artifact_type TEXT NOT NULL,
          status TEXT NOT NULL, relative_reference TEXT NOT NULL UNIQUE,
          output_manifest TEXT NOT NULL, artifact_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL, updated_at TEXT,
          CHECK(status IN (
            'created','quarantined','scanning','review_pending',
            'approved','rejected'
          )),
          CHECK(artifact_type='PATHMNIST_REFERENCE_AGGREGATE')
        );
        INSERT INTO local_execution_artifacts
          (id,runtime_session_id,execution_id,artifact_type,status,
           relative_reference,output_manifest,artifact_digest,created_at)
          SELECT id,runtime_session_id,execution_id,artifact_type,status,
                 relative_reference,output_manifest,artifact_digest,created_at
            FROM local_execution_artifacts_before_513e_0007;
        DROP TABLE local_execution_artifacts_before_513e_0007;
        CREATE TABLE local_artifact_scan_reports (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          scanner_version TEXT NOT NULL, decision TEXT NOT NULL,
          findings_json TEXT NOT NULL, scanned_manifest TEXT NOT NULL,
          scan_digest TEXT NOT NULL UNIQUE, scanned_at TEXT NOT NULL,
          CHECK(decision IN ('passed','failed'))
        );
        CREATE TABLE local_artifact_review_decisions (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          scan_report_id TEXT NOT NULL, reviewer_id TEXT NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL,
          review_digest TEXT NOT NULL UNIQUE, reviewed_at TEXT NOT NULL,
          CHECK(decision IN ('approved','rejected'))
        );
        CREATE TRIGGER trg_artifact_scan_reports_immutable
          BEFORE UPDATE ON local_artifact_scan_reports BEGIN
          SELECT RAISE(ABORT, 'artifact scan reports are immutable'); END;
        CREATE TRIGGER trg_artifact_scan_reports_no_delete
          BEFORE DELETE ON local_artifact_scan_reports BEGIN
          SELECT RAISE(ABORT, 'artifact scan reports are immutable'); END;
        CREATE TRIGGER trg_artifact_reviews_immutable
          BEFORE UPDATE ON local_artifact_review_decisions BEGIN
          SELECT RAISE(ABORT, 'artifact reviews are immutable'); END;
        CREATE TRIGGER trg_artifact_reviews_no_delete
          BEFORE DELETE ON local_artifact_review_decisions BEGIN
          SELECT RAISE(ABORT, 'artifact reviews are immutable'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0007", _now()),
        )
        db.execute("PRAGMA foreign_keys=ON")
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0008",),
    ).fetchone()
    if migration_applied is None:
        db.execute("PRAGMA foreign_keys=OFF")
        db.executescript("""
        ALTER TABLE local_sessions RENAME TO local_sessions_before_513e_0008;
        ALTER TABLE local_users RENAME TO local_users_before_513e_0008;
        CREATE TABLE local_users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
          role TEXT NOT NULL, status TEXT NOT NULL,
          failed_login_count INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
          last_login_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          CHECK(role IN (
            'local_asset_curator','local_asset_reviewer',
            'local_policy_reviewer','local_artifact_reviewer',
            'local_execution_operator','connector_local_admin'
          ))
        );
        CREATE TABLE local_sessions (
          id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
          session_digest TEXT NOT NULL UNIQUE, issued_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, revoked_at TEXT, last_seen_at TEXT NOT NULL,
          user_agent_digest TEXT NOT NULL, created_at TEXT NOT NULL,
          FOREIGN KEY(user_id) REFERENCES local_users(id)
        );
        INSERT INTO local_users SELECT * FROM local_users_before_513e_0008;
        INSERT INTO local_sessions SELECT * FROM local_sessions_before_513e_0008;
        DROP TABLE local_sessions_before_513e_0008;
        DROP TABLE local_users_before_513e_0008;

        CREATE TABLE local_execution_evidence_eligibility_assessments (
          id TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE,
          artifact_id TEXT NOT NULL, eligible INTEGER NOT NULL,
          reason_code TEXT NOT NULL, assessment_digest TEXT NOT NULL UNIQUE,
          assessed_at TEXT NOT NULL, CHECK(eligible=0),
          CHECK(reason_code='MISSING_PRE_EXECUTION_AUTHORIZATION_BINDING')
        );
        CREATE TABLE local_executor_readiness_attestations (
          id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
          event_sequence INTEGER NOT NULL, schema_version TEXT NOT NULL,
          event_type TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE,
          payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
          signing_key_id TEXT NOT NULL, signature TEXT NOT NULL,
          readiness_result TEXT NOT NULL, generated_at TEXT NOT NULL,
          expires_at TEXT NOT NULL, delivery_status TEXT NOT NULL,
          response_code INTEGER, delivered_at TEXT,
          UNIQUE(executor_id,event_sequence),
          CHECK(schema_version='hospital_executor_status_v2'),
          CHECK(event_type='EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION'),
          CHECK(readiness_result IN (
            'READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION','NOT_READY'
          )),
          CHECK(delivery_status IN ('pending','delivered','failed'))
        );
        CREATE TRIGGER trg_executor_readiness_attestations_immutable
          BEFORE UPDATE ON local_executor_readiness_attestations
          WHEN OLD.delivery_status <> 'pending'
            OR NEW.id <> OLD.id
            OR NEW.executor_id <> OLD.executor_id
            OR NEW.event_sequence <> OLD.event_sequence
            OR NEW.schema_version <> OLD.schema_version
            OR NEW.event_type <> OLD.event_type
            OR NEW.nonce <> OLD.nonce
            OR NEW.payload_json <> OLD.payload_json
            OR NEW.payload_digest <> OLD.payload_digest
            OR NEW.signing_key_id <> OLD.signing_key_id
            OR NEW.signature <> OLD.signature
            OR NEW.readiness_result <> OLD.readiness_result
            OR NEW.generated_at <> OLD.generated_at
            OR NEW.expires_at <> OLD.expires_at
          BEGIN SELECT RAISE(ABORT, 'executor readiness attestations are immutable'); END;
        CREATE TRIGGER trg_executor_readiness_attestations_no_delete
          BEFORE DELETE ON local_executor_readiness_attestations BEGIN
          SELECT RAISE(ABORT, 'executor readiness attestations are append-only'); END;
        CREATE TABLE local_execution_authorization_snapshots (
          id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
          connector_id TEXT NOT NULL, executor_id TEXT NOT NULL,
          policy_bundle_id TEXT NOT NULL, policy_bundle_version_id TEXT NOT NULL,
          policy_digest TEXT NOT NULL, readiness_digest TEXT NOT NULL,
          execution_order_id TEXT NOT NULL UNIQUE,
          execution_order_digest TEXT NOT NULL,
          connector_receipt_id TEXT NOT NULL, connector_receipt_digest TEXT NOT NULL,
          connector_decision_id TEXT NOT NULL, connector_decision_digest TEXT NOT NULL,
          admission_check_id TEXT NOT NULL, admission_check_digest TEXT NOT NULL,
          asset_version_id TEXT NOT NULL, asset_metadata_digest TEXT NOT NULL,
          quality_digest TEXT NOT NULL, model_reference_digest TEXT NOT NULL,
          image_manifest_id TEXT NOT NULL, image_digest TEXT NOT NULL,
          security_profile_digest TEXT NOT NULL, resource_policy_digest TEXT NOT NULL,
          task_definition_digest TEXT NOT NULL, output_schema_digest TEXT NOT NULL,
          task_type TEXT NOT NULL, max_execution_count INTEGER NOT NULL,
          authorized_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          snapshot_digest TEXT NOT NULL UNIQUE, connector_signature TEXT NOT NULL,
          status TEXT NOT NULL, consumed_at TEXT,
          CHECK(task_type='PATHMNIST_REFERENCE_V1'),
          CHECK(max_execution_count=1),
          CHECK(status IN ('created','validated','consumed','revoked','expired'))
        );
        CREATE TABLE local_authorized_task_manifests (
          id TEXT PRIMARY KEY, authorization_snapshot_id TEXT NOT NULL UNIQUE,
          binding_payload TEXT NOT NULL, task_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        );
        CREATE TABLE local_authorized_runtime_sessions (
          id TEXT PRIMARY KEY, authorization_snapshot_id TEXT NOT NULL UNIQUE,
          task_manifest_id TEXT NOT NULL UNIQUE, executor_id TEXT NOT NULL,
          admission_check_id TEXT NOT NULL, sandbox_id TEXT NOT NULL UNIQUE,
          binding_payload TEXT NOT NULL, runtime_digest TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT,
          completed_at TEXT, failed_at TEXT,
          CHECK(status IN ('prepared','running','completed','failed'))
        );
        CREATE TABLE local_authorized_reference_executions (
          id TEXT PRIMARY KEY, authorization_snapshot_id TEXT NOT NULL UNIQUE,
          task_manifest_id TEXT NOT NULL UNIQUE, runtime_session_id TEXT NOT NULL UNIQUE,
          input_manifest_id TEXT NOT NULL UNIQUE, binding_payload TEXT NOT NULL,
          request_digest TEXT NOT NULL UNIQUE, result_digest TEXT,
          status TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT NOT NULL,
          completed_at TEXT, failed_at TEXT,
          CHECK(status IN ('running','completed','failed','result_mismatch'))
        );
        CREATE TABLE local_authorized_execution_artifacts (
          id TEXT PRIMARY KEY, execution_id TEXT NOT NULL UNIQUE,
          authorization_snapshot_id TEXT NOT NULL UNIQUE,
          binding_payload TEXT NOT NULL, relative_reference TEXT NOT NULL UNIQUE,
          output_manifest TEXT NOT NULL, artifact_digest TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, created_at TEXT NOT NULL,
          CHECK(status IN ('created','quarantined'))
        );
        """)
        for table in (
            "local_execution_evidence_eligibility_assessments",
            "local_authorized_task_manifests",
            "local_authorized_runtime_sessions",
            "local_authorized_reference_executions",
            "local_authorized_execution_artifacts",
        ):
            db.executescript(f"""
            CREATE TRIGGER trg_{table}_immutable
              BEFORE UPDATE ON {table} BEGIN
              SELECT RAISE(ABORT, '{table} is immutable'); END;
            CREATE TRIGGER trg_{table}_no_delete
              BEFORE DELETE ON {table} BEGIN
              SELECT RAISE(ABORT, '{table} is immutable'); END;
            """)
        db.executescript("""
        CREATE TRIGGER trg_authorization_snapshot_core_immutable
          BEFORE UPDATE ON local_execution_authorization_snapshots
          WHEN NEW.id<>OLD.id OR NEW.local_order_id<>OLD.local_order_id
            OR NEW.snapshot_digest<>OLD.snapshot_digest
            OR NEW.execution_order_digest<>OLD.execution_order_digest
            OR NEW.policy_digest<>OLD.policy_digest
          BEGIN SELECT RAISE(ABORT, 'authorization snapshot binding is immutable'); END;
        CREATE TRIGGER trg_authorization_snapshot_terminal
          BEFORE UPDATE OF status ON local_execution_authorization_snapshots
          WHEN OLD.status IN ('consumed','revoked','expired')
            AND NEW.status<>OLD.status
          BEGIN SELECT RAISE(ABORT, 'authorization snapshot is terminal'); END;
        CREATE TRIGGER trg_authorization_snapshot_no_delete
          BEFORE DELETE ON local_execution_authorization_snapshots
          BEGIN SELECT RAISE(ABORT, 'authorization snapshot is append-only'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0008", _now()),
        )
        db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS local_executor_readiness_attestations (
      id TEXT PRIMARY KEY, executor_id TEXT NOT NULL,
      event_sequence INTEGER NOT NULL, schema_version TEXT NOT NULL,
      event_type TEXT NOT NULL, nonce TEXT NOT NULL UNIQUE,
      payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
      signing_key_id TEXT NOT NULL, signature TEXT NOT NULL,
      readiness_result TEXT NOT NULL, generated_at TEXT NOT NULL,
      expires_at TEXT NOT NULL, delivery_status TEXT NOT NULL,
      response_code INTEGER, delivered_at TEXT,
      UNIQUE(executor_id,event_sequence),
      CHECK(schema_version='hospital_executor_status_v2'),
      CHECK(event_type='EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION'),
      CHECK(readiness_result IN (
        'READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION','NOT_READY'
      )),
      CHECK(delivery_status IN ('pending','delivered','failed'))
    );
    CREATE TRIGGER IF NOT EXISTS trg_executor_readiness_attestations_immutable
      BEFORE UPDATE ON local_executor_readiness_attestations
      WHEN OLD.delivery_status <> 'pending'
        OR NEW.id <> OLD.id
        OR NEW.executor_id <> OLD.executor_id
        OR NEW.event_sequence <> OLD.event_sequence
        OR NEW.schema_version <> OLD.schema_version
        OR NEW.event_type <> OLD.event_type
        OR NEW.nonce <> OLD.nonce
        OR NEW.payload_json <> OLD.payload_json
        OR NEW.payload_digest <> OLD.payload_digest
        OR NEW.signing_key_id <> OLD.signing_key_id
        OR NEW.signature <> OLD.signature
        OR NEW.readiness_result <> OLD.readiness_result
        OR NEW.generated_at <> OLD.generated_at
        OR NEW.expires_at <> OLD.expires_at
      BEGIN SELECT RAISE(ABORT, 'executor readiness attestations are immutable'); END;
    CREATE TRIGGER IF NOT EXISTS trg_executor_readiness_attestations_no_delete
      BEFORE DELETE ON local_executor_readiness_attestations BEGIN
      SELECT RAISE(ABORT, 'executor readiness attestations are append-only'); END;
    """)
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0009",),
    ).fetchone()
    if migration_applied is None:
        snapshot_columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(local_execution_authorization_snapshots)"
            ).fetchall()
        }
        additions = {
            "source_executor_status_event_id": "TEXT",
            "source_executor_status_event_digest": "TEXT",
            "capability_digest": "TEXT",
            "input_schema_digest": "TEXT",
        }
        for column, sql_type in additions.items():
            if column not in snapshot_columns:
                db.execute(
                    "ALTER TABLE local_execution_authorization_snapshots "
                    f"ADD COLUMN {column} {sql_type}"
                )
        db.executescript("""
        DROP TRIGGER IF EXISTS trg_authorization_snapshot_core_immutable;
        CREATE TRIGGER trg_authorization_snapshot_core_immutable
          BEFORE UPDATE ON local_execution_authorization_snapshots
          WHEN NEW.id IS NOT OLD.id
            OR NEW.local_order_id IS NOT OLD.local_order_id
            OR NEW.connector_id IS NOT OLD.connector_id
            OR NEW.executor_id IS NOT OLD.executor_id
            OR NEW.policy_bundle_id IS NOT OLD.policy_bundle_id
            OR NEW.policy_bundle_version_id IS NOT OLD.policy_bundle_version_id
            OR NEW.policy_digest IS NOT OLD.policy_digest
            OR NEW.readiness_digest IS NOT OLD.readiness_digest
            OR NEW.execution_order_id IS NOT OLD.execution_order_id
            OR NEW.execution_order_digest IS NOT OLD.execution_order_digest
            OR NEW.connector_receipt_id IS NOT OLD.connector_receipt_id
            OR NEW.connector_receipt_digest IS NOT OLD.connector_receipt_digest
            OR NEW.connector_decision_id IS NOT OLD.connector_decision_id
            OR NEW.connector_decision_digest IS NOT OLD.connector_decision_digest
            OR NEW.admission_check_id IS NOT OLD.admission_check_id
            OR NEW.admission_check_digest IS NOT OLD.admission_check_digest
            OR NEW.asset_version_id IS NOT OLD.asset_version_id
            OR NEW.asset_metadata_digest IS NOT OLD.asset_metadata_digest
            OR NEW.quality_digest IS NOT OLD.quality_digest
            OR NEW.model_reference_digest IS NOT OLD.model_reference_digest
            OR NEW.image_manifest_id IS NOT OLD.image_manifest_id
            OR NEW.image_digest IS NOT OLD.image_digest
            OR NEW.security_profile_digest IS NOT OLD.security_profile_digest
            OR NEW.resource_policy_digest IS NOT OLD.resource_policy_digest
            OR NEW.task_definition_digest IS NOT OLD.task_definition_digest
            OR NEW.output_schema_digest IS NOT OLD.output_schema_digest
            OR NEW.task_type IS NOT OLD.task_type
            OR NEW.max_execution_count IS NOT OLD.max_execution_count
            OR NEW.authorized_at IS NOT OLD.authorized_at
            OR NEW.expires_at IS NOT OLD.expires_at
            OR NEW.snapshot_digest IS NOT OLD.snapshot_digest
            OR NEW.connector_signature IS NOT OLD.connector_signature
            OR NEW.source_executor_status_event_id
               IS NOT OLD.source_executor_status_event_id
            OR NEW.source_executor_status_event_digest
               IS NOT OLD.source_executor_status_event_digest
            OR NEW.capability_digest IS NOT OLD.capability_digest
            OR NEW.input_schema_digest IS NOT OLD.input_schema_digest
          BEGIN
            SELECT RAISE(ABORT, 'authorization snapshot binding is immutable');
          END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0009", _now()),
        )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0010",),
    ).fetchone()
    if migration_applied is None:
        order_columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(local_control_orders)"
            ).fetchall()
        }
        if "consumed_count" not in order_columns:
            db.execute(
                "ALTER TABLE local_control_orders ADD COLUMN "
                "consumed_count INTEGER NOT NULL DEFAULT 0 "
                "CHECK(consumed_count IN (0,1))"
            )
        db.executescript("""
        CREATE TABLE local_authorized_input_manifests (
          id TEXT PRIMARY KEY, authorization_snapshot_id TEXT NOT NULL UNIQUE,
          binding_payload TEXT NOT NULL, input_digest TEXT NOT NULL UNIQUE,
          created_at TEXT NOT NULL
        );
        CREATE TABLE local_execution_consumption_receipts (
          id TEXT PRIMARY KEY, local_order_id TEXT NOT NULL UNIQUE,
          authorization_snapshot_id TEXT NOT NULL UNIQUE,
          task_manifest_id TEXT NOT NULL UNIQUE,
          runtime_session_id TEXT NOT NULL UNIQUE,
          reference_execution_id TEXT NOT NULL UNIQUE,
          payload_json TEXT NOT NULL, payload_digest TEXT NOT NULL UNIQUE,
          signature TEXT NOT NULL, delivery_status TEXT NOT NULL,
          response_code INTEGER, created_at TEXT NOT NULL, delivered_at TEXT,
          CHECK(delivery_status IN ('pending','delivered','failed'))
        );
        CREATE TRIGGER trg_authorized_input_manifests_immutable
          BEFORE UPDATE ON local_authorized_input_manifests BEGIN
          SELECT RAISE(ABORT, 'authorized input manifests are immutable'); END;
        CREATE TRIGGER trg_authorized_input_manifests_no_delete
          BEFORE DELETE ON local_authorized_input_manifests BEGIN
          SELECT RAISE(ABORT, 'authorized input manifests are immutable'); END;
        CREATE TRIGGER trg_execution_consumption_receipts_immutable
          BEFORE UPDATE ON local_execution_consumption_receipts
          WHEN OLD.delivery_status <> 'pending'
            OR NEW.id IS NOT OLD.id
            OR NEW.local_order_id IS NOT OLD.local_order_id
            OR NEW.authorization_snapshot_id
               IS NOT OLD.authorization_snapshot_id
            OR NEW.task_manifest_id IS NOT OLD.task_manifest_id
            OR NEW.runtime_session_id IS NOT OLD.runtime_session_id
            OR NEW.reference_execution_id IS NOT OLD.reference_execution_id
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.payload_digest IS NOT OLD.payload_digest
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
          BEGIN
          SELECT RAISE(ABORT, 'execution consumption receipt is immutable');
          END;
        CREATE TRIGGER trg_execution_consumption_receipts_no_delete
          BEFORE DELETE ON local_execution_consumption_receipts BEGIN
          SELECT RAISE(ABORT, 'execution consumption receipts are append-only');
          END;
        DROP TRIGGER trg_local_authorized_runtime_sessions_immutable;
        CREATE TRIGGER trg_local_authorized_runtime_sessions_immutable
          BEFORE UPDATE ON local_authorized_runtime_sessions
          WHEN NEW.id IS NOT OLD.id
            OR NEW.authorization_snapshot_id
               IS NOT OLD.authorization_snapshot_id
            OR NEW.task_manifest_id IS NOT OLD.task_manifest_id
            OR NEW.executor_id IS NOT OLD.executor_id
            OR NEW.admission_check_id IS NOT OLD.admission_check_id
            OR NEW.sandbox_id IS NOT OLD.sandbox_id
            OR NEW.binding_payload IS NOT OLD.binding_payload
            OR NEW.runtime_digest IS NOT OLD.runtime_digest
            OR NEW.created_at IS NOT OLD.created_at
            OR NEW.started_at IS NOT OLD.started_at
            OR NOT (
              (OLD.status='running' AND NEW.status IN ('completed','failed'))
              OR NEW.status=OLD.status
            )
          BEGIN
          SELECT RAISE(ABORT, 'authorized runtime binding is immutable'); END;
        DROP TRIGGER trg_local_authorized_reference_executions_immutable;
        CREATE TRIGGER trg_local_authorized_reference_executions_immutable
          BEFORE UPDATE ON local_authorized_reference_executions
          WHEN NEW.id IS NOT OLD.id
            OR NEW.authorization_snapshot_id
               IS NOT OLD.authorization_snapshot_id
            OR NEW.task_manifest_id IS NOT OLD.task_manifest_id
            OR NEW.runtime_session_id IS NOT OLD.runtime_session_id
            OR NEW.input_manifest_id IS NOT OLD.input_manifest_id
            OR NEW.binding_payload IS NOT OLD.binding_payload
            OR NEW.request_digest IS NOT OLD.request_digest
            OR NEW.created_at IS NOT OLD.created_at
            OR NEW.started_at IS NOT OLD.started_at
            OR NOT (
              (OLD.status='running' AND NEW.status IN (
                'completed','failed','result_mismatch'
              ))
              OR NEW.status=OLD.status
            )
          BEGIN
          SELECT RAISE(ABORT, 'authorized execution binding is immutable'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0010", _now()),
        )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0011",),
    ).fetchone()
    if migration_applied is None:
        db.executescript("""
        CREATE TABLE local_authorized_artifact_scan_reports (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          scanner_version TEXT NOT NULL, decision TEXT NOT NULL,
          findings_json TEXT NOT NULL, scanned_manifest TEXT NOT NULL,
          scan_digest TEXT NOT NULL UNIQUE, scanned_at TEXT NOT NULL,
          CHECK(decision IN ('passed','failed'))
        );
        CREATE TABLE local_authorized_artifact_review_decisions (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          scan_report_id TEXT NOT NULL UNIQUE, reviewer_id TEXT NOT NULL,
          decision TEXT NOT NULL, reason TEXT NOT NULL,
          review_digest TEXT NOT NULL UNIQUE, reviewed_at TEXT NOT NULL,
          CHECK(decision IN (
            'APPROVE_FOR_EVIDENCE_CANDIDACY','REJECT'
          ))
        );
        CREATE TABLE local_artifact_causal_validations (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          review_id TEXT NOT NULL UNIQUE, validation_version TEXT NOT NULL,
          decision TEXT NOT NULL, checks_json TEXT NOT NULL,
          validation_digest TEXT NOT NULL UNIQUE, validated_at TEXT NOT NULL,
          CHECK(decision IN ('passed','failed'))
        );
        CREATE TABLE local_execution_evidence_bundles (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL UNIQUE,
          review_id TEXT NOT NULL UNIQUE, causal_validation_id TEXT NOT NULL UNIQUE,
          bundle_version INTEGER NOT NULL, schema_version TEXT NOT NULL,
          payload_json TEXT NOT NULL, bundle_digest TEXT NOT NULL UNIQUE,
          signing_key_id TEXT NOT NULL, signature TEXT NOT NULL,
          delivery_status TEXT NOT NULL, response_code INTEGER,
          central_receipt_id TEXT, created_at TEXT NOT NULL, delivered_at TEXT,
          CHECK(bundle_version=1),
          CHECK(delivery_status IN ('pending','delivered','failed'))
        );
        """)
        for table in (
            "local_authorized_artifact_scan_reports",
            "local_authorized_artifact_review_decisions",
            "local_artifact_causal_validations",
        ):
            db.executescript(f"""
            CREATE TRIGGER trg_{table}_immutable
              BEFORE UPDATE ON {table} BEGIN
              SELECT RAISE(ABORT, '{table} is immutable'); END;
            CREATE TRIGGER trg_{table}_no_delete
              BEFORE DELETE ON {table} BEGIN
              SELECT RAISE(ABORT, '{table} is append-only'); END;
            """)
        db.executescript("""
        CREATE TRIGGER trg_local_execution_evidence_bundles_immutable
          BEFORE UPDATE ON local_execution_evidence_bundles
          WHEN OLD.delivery_status <> 'pending'
            OR NEW.id IS NOT OLD.id
            OR NEW.artifact_id IS NOT OLD.artifact_id
            OR NEW.review_id IS NOT OLD.review_id
            OR NEW.causal_validation_id IS NOT OLD.causal_validation_id
            OR NEW.bundle_version IS NOT OLD.bundle_version
            OR NEW.schema_version IS NOT OLD.schema_version
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.bundle_digest IS NOT OLD.bundle_digest
            OR NEW.signing_key_id IS NOT OLD.signing_key_id
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
          BEGIN
          SELECT RAISE(ABORT, 'evidence bundle content is immutable'); END;
        CREATE TRIGGER trg_local_execution_evidence_bundles_no_delete
          BEFORE DELETE ON local_execution_evidence_bundles BEGIN
          SELECT RAISE(ABORT, 'evidence bundles are append-only'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0011", _now()),
        )
    migration_applied = db.execute(
        "SELECT 1 FROM local_schema_migrations WHERE version=?",
        ("phase5.13E_0012",),
    ).fetchone()
    if migration_applied is None:
        db.executescript("""
        DROP TRIGGER IF EXISTS trg_local_control_orders_no_delete;
        ALTER TABLE local_control_orders
          RENAME TO local_control_orders_before_513e_0012;
        CREATE TABLE local_control_orders (
          id TEXT PRIMARY KEY, central_order_id TEXT NOT NULL UNIQUE,
          connector_sequence INTEGER NOT NULL UNIQUE,
          order_payload TEXT NOT NULL, order_digest TEXT NOT NULL UNIQUE,
          order_signature TEXT NOT NULL, policy_payload TEXT NOT NULL,
          policy_digest TEXT NOT NULL, policy_signature TEXT NOT NULL,
          signing_key_id TEXT NOT NULL, signing_public_key TEXT NOT NULL,
          signing_key_fingerprint TEXT NOT NULL,
          central_status TEXT NOT NULL, local_status TEXT NOT NULL,
          received_at TEXT NOT NULL, expires_at TEXT NOT NULL,
          consumed_count INTEGER NOT NULL DEFAULT 0,
          CHECK(consumed_count IN (0,1)),
          CHECK(local_status IN (
            'validation_failed','awaiting_local_review','accepted','rejected',
            'revoked','revoked_after_acceptance'
          ))
        );
        INSERT INTO local_control_orders
          (id,central_order_id,connector_sequence,order_payload,order_digest,
           order_signature,policy_payload,policy_digest,policy_signature,
           signing_key_id,signing_public_key,signing_key_fingerprint,
           central_status,local_status,received_at,expires_at,consumed_count)
          SELECT id,central_order_id,connector_sequence,order_payload,
                 order_digest,order_signature,policy_payload,policy_digest,
                 policy_signature,signing_key_id,signing_public_key,
                 signing_key_fingerprint,central_status,local_status,
                 received_at,expires_at,consumed_count
            FROM local_control_orders_before_513e_0012;
        DROP TABLE local_control_orders_before_513e_0012;
        CREATE TRIGGER trg_local_control_orders_no_delete
          BEFORE DELETE ON local_control_orders BEGIN
          SELECT RAISE(ABORT, 'local control orders are append-only'); END;
        CREATE TRIGGER trg_local_control_orders_immutable
          BEFORE UPDATE ON local_control_orders
          WHEN NEW.id IS NOT OLD.id
            OR NEW.central_order_id IS NOT OLD.central_order_id
            OR NEW.connector_sequence IS NOT OLD.connector_sequence
            OR NEW.order_payload IS NOT OLD.order_payload
            OR NEW.order_digest IS NOT OLD.order_digest
            OR NEW.order_signature IS NOT OLD.order_signature
            OR NEW.policy_payload IS NOT OLD.policy_payload
            OR NEW.policy_digest IS NOT OLD.policy_digest
            OR NEW.policy_signature IS NOT OLD.policy_signature
            OR NEW.signing_key_id IS NOT OLD.signing_key_id
            OR NEW.signing_public_key IS NOT OLD.signing_public_key
            OR NEW.signing_key_fingerprint
               IS NOT OLD.signing_key_fingerprint
            OR NEW.received_at IS NOT OLD.received_at
            OR NEW.expires_at IS NOT OLD.expires_at
            OR NOT (
              (NEW.central_status IS OLD.central_status
               AND NEW.local_status IS OLD.local_status
               AND NEW.consumed_count=OLD.consumed_count)
              OR (NEW.central_status IS OLD.central_status
                  AND NEW.central_status<>'revoked'
                  AND OLD.local_status='awaiting_local_review'
                  AND NEW.local_status IN ('accepted','rejected')
                  AND NEW.consumed_count=OLD.consumed_count)
              OR (OLD.central_status<>'revoked'
                  AND NEW.central_status='revoked'
                  AND NEW.consumed_count=OLD.consumed_count
                  AND (
                    (OLD.local_status='accepted'
                     AND NEW.local_status='revoked_after_acceptance')
                    OR (OLD.local_status IN (
                          'validation_failed','awaiting_local_review','rejected'
                        ) AND NEW.local_status='revoked')
                  ))
              OR (NEW.central_status IS OLD.central_status
                  AND NEW.central_status<>'revoked'
                  AND OLD.local_status='accepted'
                  AND NEW.local_status='accepted'
                  AND OLD.consumed_count=0 AND NEW.consumed_count=1)
            )
          BEGIN
          SELECT RAISE(ABORT, 'local control order binding is immutable');
          END;

        CREATE TRIGGER trg_local_order_receipts_immutable
          BEFORE UPDATE ON local_order_receipts
          WHEN NEW.id IS NOT OLD.id
            OR NEW.local_order_id IS NOT OLD.local_order_id
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.payload_digest IS NOT OLD.payload_digest
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (
              OLD.delivery_status='pending'
              AND NEW.delivery_status IN ('failed','delivered')
            )
            OR (NEW.delivery_status='delivered' AND NEW.delivered_at IS NULL)
            OR (NEW.delivery_status<>'delivered' AND NEW.delivered_at IS NOT NULL)
          BEGIN
          SELECT RAISE(ABORT, 'local order receipt is immutable'); END;
        CREATE TRIGGER trg_local_order_receipts_no_delete
          BEFORE DELETE ON local_order_receipts BEGIN
          SELECT RAISE(ABORT, 'local order receipts are append-only'); END;

        CREATE TRIGGER trg_local_order_decisions_immutable
          BEFORE UPDATE ON local_order_decisions
          WHEN NEW.id IS NOT OLD.id
            OR NEW.local_order_id IS NOT OLD.local_order_id
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.payload_digest IS NOT OLD.payload_digest
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (
              OLD.delivery_status='pending'
              AND NEW.delivery_status IN ('failed','delivered')
            )
            OR (NEW.delivery_status='delivered' AND NEW.delivered_at IS NULL)
            OR (NEW.delivery_status<>'delivered' AND NEW.delivered_at IS NOT NULL)
          BEGIN
          SELECT RAISE(ABORT, 'local order decision is immutable'); END;
        CREATE TRIGGER trg_local_order_decisions_no_delete
          BEFORE DELETE ON local_order_decisions BEGIN
          SELECT RAISE(ABORT, 'local order decisions are append-only'); END;
        CREATE TRIGGER trg_local_policy_validations_no_delete
          BEFORE DELETE ON local_policy_validations BEGIN
          SELECT RAISE(ABORT, 'local policy validations are append-only'); END;
        CREATE TRIGGER trg_local_policy_reviews_no_delete
          BEFORE DELETE ON local_policy_reviews BEGIN
          SELECT RAISE(ABORT, 'local policy reviews are append-only'); END;

        DROP TRIGGER IF EXISTS trg_execution_consumption_receipts_immutable;
        CREATE TRIGGER trg_execution_consumption_receipts_immutable
          BEFORE UPDATE ON local_execution_consumption_receipts
          WHEN NEW.id IS NOT OLD.id
            OR NEW.local_order_id IS NOT OLD.local_order_id
            OR NEW.authorization_snapshot_id
               IS NOT OLD.authorization_snapshot_id
            OR NEW.task_manifest_id IS NOT OLD.task_manifest_id
            OR NEW.runtime_session_id IS NOT OLD.runtime_session_id
            OR NEW.reference_execution_id IS NOT OLD.reference_execution_id
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.payload_digest IS NOT OLD.payload_digest
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (
              (OLD.delivery_status='pending'
               AND NEW.delivery_status IN ('failed','delivered'))
              OR (OLD.delivery_status='failed'
                  AND NEW.delivery_status='delivered')
            )
            OR (OLD.delivery_status='delivered' AND (
              NEW.response_code IS NOT OLD.response_code
              OR NEW.delivered_at IS NOT OLD.delivered_at
            ))
            OR (NEW.delivery_status='delivered'
                AND NEW.delivered_at IS NULL)
            OR (NEW.delivery_status<>'delivered'
                AND NEW.delivered_at IS NOT NULL)
          BEGIN
          SELECT RAISE(ABORT, 'execution consumption receipt is immutable');
          END;

        DROP TRIGGER IF EXISTS trg_local_execution_evidence_bundles_immutable;
        CREATE TRIGGER trg_local_execution_evidence_bundles_immutable
          BEFORE UPDATE ON local_execution_evidence_bundles
          WHEN NEW.id IS NOT OLD.id
            OR NEW.artifact_id IS NOT OLD.artifact_id
            OR NEW.review_id IS NOT OLD.review_id
            OR NEW.causal_validation_id IS NOT OLD.causal_validation_id
            OR NEW.bundle_version IS NOT OLD.bundle_version
            OR NEW.schema_version IS NOT OLD.schema_version
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.bundle_digest IS NOT OLD.bundle_digest
            OR NEW.signing_key_id IS NOT OLD.signing_key_id
            OR NEW.signature IS NOT OLD.signature
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (
              (OLD.delivery_status='pending'
               AND NEW.delivery_status IN ('failed','delivered'))
              OR (OLD.delivery_status='failed'
                  AND NEW.delivery_status='delivered')
            )
            OR (OLD.delivery_status='delivered' AND (
              NEW.response_code IS NOT OLD.response_code
              OR NEW.central_receipt_id IS NOT OLD.central_receipt_id
              OR NEW.delivered_at IS NOT OLD.delivered_at
            ))
            OR (NEW.delivery_status='delivered' AND (
              NEW.central_receipt_id IS NULL OR NEW.delivered_at IS NULL
            ))
            OR (NEW.delivery_status<>'delivered' AND (
              NEW.central_receipt_id IS NOT NULL
              OR NEW.delivered_at IS NOT NULL
            ))
          BEGIN
          SELECT RAISE(ABORT, 'evidence bundle content is immutable'); END;

        CREATE TABLE local_authorized_execution_dispatches (
          reference_execution_id TEXT PRIMARY KEY,
          authorization_snapshot_id TEXT NOT NULL UNIQUE,
          runtime_session_id TEXT NOT NULL UNIQUE,
          payload_json TEXT NOT NULL,
          request_digest TEXT NOT NULL UNIQUE,
          status TEXT NOT NULL, created_at TEXT NOT NULL,
          dispatched_at TEXT,
          CHECK(status IN ('pending','dispatched'))
        );
        CREATE TRIGGER trg_authorized_execution_dispatches_immutable
          BEFORE UPDATE ON local_authorized_execution_dispatches
          WHEN NEW.reference_execution_id IS NOT OLD.reference_execution_id
            OR NEW.authorization_snapshot_id
               IS NOT OLD.authorization_snapshot_id
            OR NEW.runtime_session_id IS NOT OLD.runtime_session_id
            OR NEW.payload_json IS NOT OLD.payload_json
            OR NEW.request_digest IS NOT OLD.request_digest
            OR NEW.created_at IS NOT OLD.created_at
            OR NOT (OLD.status='pending' AND NEW.status='dispatched')
            OR (OLD.status='dispatched'
                AND NEW.dispatched_at IS NOT OLD.dispatched_at)
            OR (NEW.status='pending' AND NEW.dispatched_at IS NOT NULL)
            OR (NEW.status='dispatched' AND NEW.dispatched_at IS NULL)
          BEGIN
          SELECT RAISE(ABORT, 'authorized dispatch is immutable'); END;
        CREATE TRIGGER trg_authorized_execution_dispatches_no_delete
          BEFORE DELETE ON local_authorized_execution_dispatches BEGIN
          SELECT RAISE(ABORT, 'authorized dispatches are append-only'); END;
        """)
        db.execute(
            "INSERT INTO local_schema_migrations(version,applied_at) VALUES(?,?)",
            ("phase5.13E_0012", _now()),
        )
    db.commit()


def password_hash(password: str, *, salt: bytes | None = None) -> str:
    salt = salt or __import__("secrets").token_bytes(16)
    value = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${value.hex()}"


def password_matches(password: str, encoded: str) -> bool:
    import hmac
    algorithm, rounds, salt_hex, expected = encoded.split("$", 3)
    if algorithm != "pbkdf2_sha256":
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), int(rounds)
    ).hex()
    return hmac.compare_digest(actual, expected)


def bootstrap_users(
    db: sqlite3.Connection, curator_password: str, reviewer_password: str,
    policy_reviewer_password: str = "", admin_password: str = "",
    artifact_reviewer_password: str = "", execution_operator_password: str = "",
) -> None:
    stamp = _now()
    users = (
        ("local.curator", "Local Asset Curator", curator_password, "local_asset_curator"),
        ("local.reviewer", "Local Asset Reviewer", reviewer_password, "local_asset_reviewer"),
        ("local.policy-reviewer", "Local Policy Reviewer", policy_reviewer_password, "local_policy_reviewer"),
        ("local.connector-admin", "Connector Local Administrator", admin_password, "connector_local_admin"),
        ("local.artifact-reviewer", "Hospital Artifact Reviewer",
         artifact_reviewer_password, "local_artifact_reviewer"),
        ("local.execution-operator", "Local Execution Operator",
         execution_operator_password, "local_execution_operator"),
    )
    for username, display_name, password, role in users:
        if not password:
            continue
        db.execute(
            """INSERT OR IGNORE INTO local_users
               (id,username,display_name,password_hash,role,status,created_at,updated_at)
               VALUES(?,?,?,?,?,'active',?,?)""",
            (str(uuid4()), username, display_name, password_hash(password), role, stamp, stamp),
        )
    db.commit()


EXECUTOR_CAPABILITY_FIELDS = {
    "schema_version", "manifest_version", "executor_version", "runtime",
    "image_digest", "architecture", "network_mode", "filesystem_mode",
    "rootless", "gpu", "supported_task_types", "resource_limits",
    "security_features", "execution_enabled", "hard_isolation",
}

EXECUTOR_RESOURCE_FIELDS = {
    "cpu_cores", "memory_mb", "disk_mb", "processes", "timeout_seconds",
}

REFERENCE_TASK_TYPE = "PATHMNIST_REFERENCE_V1"
REFERENCE_TASK_VERSION = "1"
REFERENCE_MODEL = "registered://models/pathmnist-resnet18/v1"
REFERENCE_DATASET = "registered://datasets/pathmnist/v1"
REFERENCE_DATASET_DIGEST = (
    "sha256:81823f52dc622e69db2db4c72f8e8e617938dd6864d3c1f23d4e49724a28ea72"
)
REFERENCE_MODEL_DIGEST = (
    "sha256:64774e5fdf8786c7f0182eb6a7300d162b12a7a93455805cb2987eb0c12258e0"
)
REFERENCE_INPUT_SCHEMA = "pathmnist-rgb-28x28/v1"
REFERENCE_OUTPUT_SCHEMA = "pathmnist-aggregate-inference/v1"
REFERENCE_OUTPUT_FILES = (
    "aggregate_metrics.json", "confusion_matrix.csv", "execution_summary.json",
)
REFERENCE_INDICES = (
    126, 345, 449, 561, 670, 1296, 2416, 2920, 3085, 3500,
    3513, 4188, 4444, 5047, 5090, 5278, 5439, 5642, 5770, 6108,
)

AUTHORIZATION_BINDING_FIELDS = {
    "connector_id", "executor_id", "policy_bundle_id",
    "policy_bundle_version_id", "policy_digest", "readiness_digest",
    "execution_order_id", "execution_order_digest", "connector_receipt_id",
    "connector_receipt_digest", "connector_decision_id",
    "connector_decision_digest", "admission_check_id",
    "admission_check_digest", "asset_version_id", "asset_metadata_digest",
    "quality_digest", "model_reference_digest", "image_manifest_id",
    "image_digest", "security_profile_digest", "resource_policy_digest",
    "task_definition_digest", "output_schema_digest", "task_type",
    "max_execution_count", "authorized_at", "expires_at",
    "source_executor_status_event_id",
    "source_executor_status_event_digest", "capability_digest",
    "input_schema_digest",
}


def create_execution_authorization_snapshot(
    db: sqlite3.Connection, *, local_order_id: str, binding: dict[str, Any],
    connector_signature: str | None = None,
    canonical_digest: Callable[[dict[str, Any]], str],
    signer: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    if set(binding) != AUTHORIZATION_BINDING_FIELDS:
        raise ValueError("AUTHORIZATION_BINDING_SCHEMA_INVALID")
    if (
        binding["task_type"] != REFERENCE_TASK_TYPE
        or binding["max_execution_count"] != 1
    ):
        raise ValueError("FIXED_REFERENCE_AUTHORIZATION_REQUIRED")
    now = datetime.now(timezone.utc)
    try:
        expires_at = datetime.fromisoformat(binding["expires_at"])
        authorized_at = datetime.fromisoformat(binding["authorized_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("AUTHORIZATION_TIME_INVALID") from exc
    if authorized_at > now or expires_at <= now:
        raise ValueError("AUTHORIZATION_TIME_INVALID")
    order = db.execute(
        """SELECT o.*,v.validation_status,r.decision review_decision,
                  rc.id receipt_id,rc.payload_digest receipt_digest,
                  dc.id decision_id,dc.payload_digest decision_digest
             FROM local_control_orders o
             JOIN local_policy_validations v ON v.local_order_id=o.id
             JOIN local_policy_reviews r ON r.local_order_id=o.id
             JOIN local_order_receipts rc ON rc.local_order_id=o.id
             JOIN local_order_decisions dc ON dc.local_order_id=o.id
            WHERE o.id=?""",
        (local_order_id,),
    ).fetchone()
    if order is not None and order["central_status"] == "revoked":
        raise ValueError("CENTRAL_POLICY_REVOKED")
    if (
        order is None
        or order["local_status"] != "accepted"
        or order["validation_status"] != "passed"
        or order["review_decision"] != "accepted"
    ):
        raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
    order_payload = json.loads(order["order_payload"])
    policy_payload = json.loads(order["policy_payload"])
    required = {
        "order_mode": "FIXED_REFERENCE_EXECUTION",
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "task_type": REFERENCE_TASK_TYPE,
        "execution_authorized": True,
        "max_execution_count": 1,
    }
    if any(order_payload.get(key) != value for key, value in required.items()):
        raise ValueError("FIXED_REFERENCE_ORDER_REQUIRED")
    if (
        policy_payload.get("execution_scope") != "FIXED_REFERENCE_ONLY"
        or policy_payload.get("execution_authorized") is not True
    ):
        raise ValueError("FIXED_REFERENCE_POLICY_REQUIRED")
    digest_bindings = {
        "policy_digest": order["policy_digest"],
        "execution_order_digest": order["order_digest"],
        "connector_receipt_id": order["receipt_id"],
        "connector_receipt_digest": order["receipt_digest"],
        "connector_decision_id": order["decision_id"],
        "connector_decision_digest": order["decision_digest"],
    }
    if any(binding[key] != value for key, value in digest_bindings.items()):
        raise ValueError("AUTHORIZATION_DIGEST_MISMATCH")
    snapshot_id = str(uuid4())
    snapshot_payload = {
        "schema_version": "phase5.13E-2C-R1/authorization-snapshot/v1",
        "snapshot_id": snapshot_id,
        "local_order_id": local_order_id,
        **binding,
    }
    snapshot_digest = canonical_digest(snapshot_payload)
    snapshot_signature = (
        signer(snapshot_payload) if signer is not None else connector_signature
    )
    if not snapshot_signature:
        raise ValueError("AUTHORIZATION_SNAPSHOT_SIGNATURE_REQUIRED")
    columns = [
        "id", "local_order_id", *AUTHORIZATION_BINDING_FIELDS,
        "snapshot_digest", "connector_signature", "status",
    ]
    values = [
        snapshot_id, local_order_id,
        *(binding[name] for name in AUTHORIZATION_BINDING_FIELDS),
        snapshot_digest, snapshot_signature, "validated",
    ]
    db.execute(
        f"INSERT INTO local_execution_authorization_snapshots "
        f"({','.join(columns)}) VALUES({','.join('?' for _ in values)})",
        values,
    )
    db.commit()
    return {"id": snapshot_id, "status": "validated",
            "snapshot_digest": snapshot_digest,
            "connector_signature": snapshot_signature,
            "payload": snapshot_payload}


def create_execution_authorization_snapshot_from_order(
    db: sqlite3.Connection, *, local_order_id: str,
    canonical_digest: Callable[[dict[str, Any]], str],
    signer: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    order = db.execute(
        """SELECT o.*,v.validation_status,v.checks_json,v.failure_code,
                  r.reviewer_id,r.decision review_decision,r.decided_at,
                  rc.id receipt_id,rc.payload_json receipt_payload,
                  rc.payload_digest receipt_digest,
                  dc.id decision_id,dc.payload_json decision_payload,
                  dc.payload_digest decision_digest
             FROM local_control_orders o
             JOIN local_policy_validations v ON v.local_order_id=o.id
             JOIN local_policy_reviews r ON r.local_order_id=o.id
             JOIN local_order_receipts rc ON rc.local_order_id=o.id
             JOIN local_order_decisions dc ON dc.local_order_id=o.id
            WHERE o.id=?""",
        (local_order_id,),
    ).fetchone()
    if order is None:
        raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
    if order["central_status"] == "revoked":
        raise ValueError("CENTRAL_POLICY_REVOKED")
    policy = json.loads(order["policy_payload"])
    order_payload = json.loads(order["order_payload"])
    receipt = json.loads(order["receipt_payload"])
    decision = json.loads(order["decision_payload"])
    if (
        order["local_status"] != "accepted"
        or order["validation_status"] != "passed"
        or order["review_decision"] != "accepted"
        or receipt.get("validation_status") != "passed"
        or decision.get("decision") != "accepted"
        or order_payload.get("consumed_count") != 0
        or order_payload.get("max_execution_count") != 1
        or policy.get("execution_authorized") is not True
        or policy.get("execution_scope") != "FIXED_REFERENCE_ONLY"
    ):
        raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
    now = datetime.now(timezone.utc)
    attestation = db.execute(
        """SELECT * FROM local_executor_readiness_attestations
           WHERE payload_digest=? AND executor_id=?
           ORDER BY event_sequence DESC LIMIT 1""",
        (
            policy["source_executor_status_event_digest"],
            policy["executor_id"],
        ),
    ).fetchone()
    current_attestation = db.execute(
        """SELECT * FROM local_executor_readiness_attestations
           WHERE executor_id=? ORDER BY event_sequence DESC LIMIT 1""",
        (policy["executor_id"],),
    ).fetchone()
    executor = db.execute(
        "SELECT * FROM local_executors WHERE id=?",
        (policy["executor_id"],),
    ).fetchone()
    admission = db.execute(
        "SELECT * FROM local_executor_admission_checks WHERE id=?",
        (policy["attested_admission_check_id"],),
    ).fetchone()
    image = db.execute(
        "SELECT * FROM local_execution_image_manifests WHERE id=?",
        (policy["attested_image_manifest_id"],),
    ).fetchone()
    profile = db.execute(
        "SELECT * FROM local_executor_security_profiles WHERE id=?",
        (policy["attested_security_profile_id"],),
    ).fetchone()
    asset = db.execute(
        """SELECT v.id,v.metadata_digest,q.quality_digest,d.status
             FROM local_asset_versions v
             JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
             JOIN local_asset_reviews r ON r.asset_version_id=v.id
               AND r.quality_profile_id=q.id AND r.decision='approved'
             JOIN local_asset_descriptors d ON d.id=v.asset_id
            WHERE d.local_asset_key=? AND v.version_label=?
            ORDER BY q.created_at DESC LIMIT 1""",
        (policy["local_asset_key"], policy["local_asset_version_ref"]),
    ).fetchone()
    if not all((attestation, current_attestation, executor, admission, image,
                profile, asset)):
        raise ValueError("AUTHORIZATION_SOURCE_OBJECT_MISSING")
    admission_snapshot = json.loads(admission["policy_snapshot"])
    expiry_values = [
        datetime.fromisoformat(order_payload["expires_at"]),
        datetime.fromisoformat(policy["expires_at"]),
        datetime.fromisoformat(attestation["expires_at"]),
        datetime.fromisoformat(admission_snapshot["valid_until"]),
    ]
    resource_timeout = int(
        json.loads(profile["resource_policy"])["timeout_seconds"]
    )
    minimum_validity = (
        resource_timeout + fixed_reference_safety_margin_seconds()
    )
    if (
        current_attestation["id"] != attestation["id"]
        or attestation["readiness_result"]
        != "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
        or min(expiry_values) <= now + timedelta(seconds=minimum_validity)
        or executor["status"] != "active"
        or admission["decision"] != "approved"
        or image["status"] != "approved"
        or profile["status"] != "valid"
        or asset["status"] in {"unavailable", "archived", "deleted"}
    ):
        raise ValueError("AUTHORIZATION_SOURCE_NOT_CURRENT")
    proof_bindings = {
        "admission_check_digest": admission["admission_digest"],
        "asset_metadata_digest": asset["metadata_digest"],
        "quality_digest": asset["quality_digest"],
        "image_digest": image["image_digest"],
        "security_profile_digest": profile["profile_digest"],
        "resource_policy_digest":
            admission_snapshot["resource_policy_digest"],
        "capability_digest": admission_snapshot["capability_digest"],
    }
    policy_bindings = {
        "admission_check_digest": policy["admission_digest"],
        "asset_metadata_digest": policy["local_asset_metadata_digest"],
        "quality_digest": policy["quality_digest"],
        "image_digest": policy["image_digest"],
        "security_profile_digest": policy["security_profile_digest"],
        "resource_policy_digest": policy["resource_policy_digest"],
        "capability_digest": policy["capability_digest"],
    }
    if proof_bindings != policy_bindings:
        raise ValueError("AUTHORIZATION_DIGEST_MISMATCH")
    if (
        receipt.get("receipt_id") != order["receipt_id"]
        or decision.get("decision_id") != order["decision_id"]
        or decision.get("receipt_digest") != order["receipt_digest"]
        or decision.get("order_digest") != order["order_digest"]
        or decision.get("policy_digest") != order["policy_digest"]
    ):
        raise ValueError("AUTHORIZATION_MESSAGE_BINDING_INVALID")
    binding = {
        "connector_id": policy["connector_id"],
        "executor_id": policy["executor_id"],
        "policy_bundle_id": order_payload["policy_bundle_id"],
        "policy_bundle_version_id": order_payload["policy_bundle_version_id"],
        "policy_digest": order["policy_digest"],
        "readiness_digest": policy["readiness_digest"],
        "execution_order_id": order_payload["execution_order_id"],
        "execution_order_digest": order["order_digest"],
        "connector_receipt_id": order["receipt_id"],
        "connector_receipt_digest": order["receipt_digest"],
        "connector_decision_id": order["decision_id"],
        "connector_decision_digest": order["decision_digest"],
        "admission_check_id": admission["id"],
        "admission_check_digest": admission["admission_digest"],
        "asset_version_id": asset["id"],
        "asset_metadata_digest": asset["metadata_digest"],
        "quality_digest": asset["quality_digest"],
        "model_reference_digest": policy["model_reference_digest"],
        "image_manifest_id": image["id"],
        "image_digest": image["image_digest"],
        "security_profile_digest": profile["profile_digest"],
        "resource_policy_digest": admission_snapshot["resource_policy_digest"],
        "task_definition_digest": policy["task_definition_digest"],
        "input_schema_digest": policy["input_schema_digest"],
        "output_schema_digest": policy["output_schema_digest"],
        "task_type": policy["task_type"],
        "max_execution_count": 1,
        "authorized_at": order["decided_at"],
        "expires_at": min(expiry_values).isoformat(),
        "source_executor_status_event_id":
            policy["source_executor_status_event_id"],
        "source_executor_status_event_digest":
            policy["source_executor_status_event_digest"],
        "capability_digest": policy["capability_digest"],
    }
    return create_execution_authorization_snapshot(
        db,
        local_order_id=local_order_id,
        binding=binding,
        canonical_digest=canonical_digest,
        signer=signer,
    )


def consume_execution_authorization(
    db: sqlite3.Connection, *, snapshot_id: str,
    binding_payload: dict[str, Any],
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    db.execute("BEGIN IMMEDIATE")
    try:
        snapshot = db.execute(
            "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError("EXECUTION_AUTHORIZATION_UNKNOWN")
        if snapshot["status"] != "validated":
            raise ValueError("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED")
        if datetime.fromisoformat(snapshot["expires_at"]) <= datetime.now(timezone.utc):
            raise ValueError("EXECUTION_AUTHORIZATION_EXPIRED")
        required = {
            "authorization_snapshot_id": snapshot_id,
            "authorization_snapshot_digest": snapshot["snapshot_digest"],
            "policy_digest": snapshot["policy_digest"],
            "execution_order_digest": snapshot["execution_order_digest"],
            "connector_decision_digest": snapshot["connector_decision_digest"],
            "admission_check_digest": snapshot["admission_check_digest"],
            "image_digest": snapshot["image_digest"],
            "task_definition_digest": snapshot["task_definition_digest"],
            "output_schema_digest": snapshot["output_schema_digest"],
        }
        if any(binding_payload.get(key) != value for key, value in required.items()):
            raise ValueError("PRE_EXECUTION_BINDING_MISMATCH")
        task_id, stamp = str(uuid4()), _now()
        task_digest = canonical_digest(binding_payload)
        db.execute(
            """INSERT INTO local_authorized_task_manifests
               (id,authorization_snapshot_id,binding_payload,task_digest,created_at)
               VALUES(?,?,?,?,?)""",
            (task_id, snapshot_id, json.dumps(binding_payload, sort_keys=True),
             task_digest, stamp),
        )
        updated = db.execute(
            """UPDATE local_execution_authorization_snapshots
               SET status='consumed',consumed_at=?
               WHERE id=? AND status='validated'""",
            (stamp, snapshot_id),
        )
        if updated.rowcount != 1:
            raise ValueError("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED")
        db.commit()
        return {"snapshot_id": snapshot_id, "task_manifest_id": task_id,
                "task_digest": task_digest, "status": "consumed"}
    except Exception:
        db.rollback()
        raise


def _recover_authorized_execution_reservation(
    db: sqlite3.Connection, *, snapshot_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    snapshot = db.execute(
        "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    if snapshot is None:
        raise ValueError("EXECUTION_AUTHORIZATION_UNKNOWN")
    order = db.execute(
        "SELECT * FROM local_control_orders WHERE id=?",
        (snapshot["local_order_id"],),
    ).fetchone()
    if order is None:
        raise ValueError("AUTHORIZATION_SOURCE_OBJECT_MISSING")
    if order["central_status"] == "revoked":
        raise ValueError("CENTRAL_POLICY_REVOKED")
    if order["local_status"] != "accepted":
        raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
    if snapshot["status"] != "consumed":
        raise ValueError("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED")
    task = db.execute(
        "SELECT * FROM local_authorized_task_manifests "
        "WHERE authorization_snapshot_id=?", (snapshot_id,),
    ).fetchone()
    input_manifest = db.execute(
        "SELECT * FROM local_authorized_input_manifests "
        "WHERE authorization_snapshot_id=?", (snapshot_id,),
    ).fetchone()
    runtime = db.execute(
        "SELECT * FROM local_authorized_runtime_sessions "
        "WHERE authorization_snapshot_id=?", (snapshot_id,),
    ).fetchone()
    execution = db.execute(
        "SELECT * FROM local_authorized_reference_executions "
        "WHERE authorization_snapshot_id=?", (snapshot_id,),
    ).fetchone()
    consumption = db.execute(
        "SELECT * FROM local_execution_consumption_receipts "
        "WHERE authorization_snapshot_id=?", (snapshot_id,),
    ).fetchone()
    if not all((task, input_manifest, runtime, execution, consumption)):
        raise ValueError("EXECUTION_RESERVATION_INCOMPLETE")
    dispatch = db.execute(
        "SELECT * FROM local_authorized_execution_dispatches "
        "WHERE reference_execution_id=?", (execution["id"],),
    ).fetchone()
    if dispatch is None:
        profile = db.execute(
            "SELECT * FROM local_executor_security_profiles "
            "WHERE executor_id=? AND profile_digest=?",
            (snapshot["executor_id"], snapshot["security_profile_digest"]),
        ).fetchone()
        if profile is None:
            raise ValueError("AUTHORIZATION_SOURCE_OBJECT_MISSING")
        task_manifest = {
            "schema_version": "phase5.13E-2B-1/task-manifest/v1",
            "task_type": REFERENCE_TASK_TYPE,
            "task_version": REFERENCE_TASK_VERSION,
            "image_digest": snapshot["image_digest"],
            "model_reference": REFERENCE_MODEL,
            "model_digest": REFERENCE_MODEL_DIGEST,
            "dataset_reference": REFERENCE_DATASET,
            "dataset_digest": REFERENCE_DATASET_DIGEST,
            "input_schema": REFERENCE_INPUT_SCHEMA,
            "output_schema": REFERENCE_OUTPUT_SCHEMA,
            "resource_policy": json.loads(profile["resource_policy"]),
            "output_allowlist": list(REFERENCE_OUTPUT_FILES),
            "network_mode": "none", "rootless": True, "non_clinical": True,
        }
        input_payload = json.loads(input_manifest["binding_payload"])[
            "input_manifest"
        ]
        request_payload = {
            "schema_version": "phase5.13E-2B-1/worker-request/v1",
            "runtime_session_id": runtime["id"],
            "sandbox_id": runtime["sandbox_id"],
            "task_manifest": task_manifest,
            "task_digest": canonical_digest(task_manifest),
            "input_manifest": input_payload,
            "input_digest": canonical_digest(input_payload),
        }
        request_digest = canonical_digest(request_payload)
        if request_digest != execution["request_digest"]:
            raise ValueError("AUTHORIZED_DISPATCH_REQUEST_MISMATCH")
        request_payload["request_digest"] = request_digest
        runtime_dir = (
            sandbox_root.resolve() / runtime["sandbox_id"] / "runtime"
        ).resolve()
        if runtime_dir.parent.parent != sandbox_root.resolve():
            raise ValueError("SANDBOX_PATH_INVALID")
        already_dispatched = any(
            (runtime_dir / name).exists()
            for name in ("request.json", "request.claimed.json", "result.json")
        )
        dispatch_status = "dispatched" if already_dispatched else "pending"
        stamp = _now()
        db.execute(
            """INSERT INTO local_authorized_execution_dispatches
               (reference_execution_id,authorization_snapshot_id,
                runtime_session_id,payload_json,request_digest,status,
                created_at,dispatched_at) VALUES(?,?,?,?,?,?,?,?)""",
            (
                execution["id"], snapshot_id, runtime["id"],
                json.dumps(request_payload, sort_keys=True), request_digest,
                dispatch_status, stamp, stamp if already_dispatched else None,
            ),
        )
        db.commit()
        dispatch = db.execute(
            "SELECT * FROM local_authorized_execution_dispatches "
            "WHERE reference_execution_id=?", (execution["id"],),
        ).fetchone()
    if (
        consumption["delivery_status"] != "delivered"
        and (
            dispatch["status"] != "pending"
            or execution["status"] != "running"
            or runtime["status"] != "running"
        )
    ):
        raise ValueError("EXECUTION_CONSUMPTION_RETRY_NOT_CURRENT")
    request_payload = json.loads(dispatch["payload_json"])
    unsigned_request = dict(request_payload)
    embedded_digest = unsigned_request.pop("request_digest", None)
    if (
        embedded_digest != dispatch["request_digest"]
        or dispatch["request_digest"] != execution["request_digest"]
        or canonical_digest(unsigned_request) != dispatch["request_digest"]
    ):
        raise ValueError("AUTHORIZED_DISPATCH_REQUEST_MISMATCH")
    consumption_payload = json.loads(consumption["payload_json"])
    if (
        consumption_payload.get("consumption_receipt_id") != consumption["id"]
        or consumption_payload.get("authorization_snapshot_id") != snapshot_id
        or consumption_payload.get("reference_execution_id") != execution["id"]
        or consumption_payload.get("request_digest") != execution["request_digest"]
        or canonical_digest(consumption_payload) != consumption["payload_digest"]
    ):
        raise ValueError("EXECUTION_CONSUMPTION_RECEIPT_MISMATCH")
    return {
        "snapshot_id": snapshot_id,
        "task_manifest_id": task["id"],
        "task_digest": task["task_digest"],
        "runtime_session_id": runtime["id"],
        "runtime_digest": runtime["runtime_digest"],
        "reference_execution_id": execution["id"],
        "request_payload": request_payload,
        "consumption_receipt_id": consumption["id"],
        "consumption_payload": consumption_payload,
        "consumption_digest": consumption["payload_digest"],
        "consumption_delivery_status": consumption["delivery_status"],
        "dispatch_status": dispatch["status"],
        "remaining_validity_seconds": consumption_payload[
            "remaining_validity_seconds"
        ],
        "status": "reserved", "recovered": True,
    }


def start_authorized_fixed_reference_execution(
    db: sqlite3.Connection, *, snapshot_id: str, sandbox_root: Path,
    approved_execution_image_digest: str, checked_by: str,
    safety_margin_seconds: int,
    canonical_digest: Callable[[dict[str, Any]], str],
    signer: Callable[[dict[str, Any]], str],
    local_audit_head: str | None,
) -> dict[str, Any]:
    root = sandbox_root.resolve()
    if root.drive.upper() == "C:":
        raise ValueError("SANDBOX_ROOT_FORBIDDEN")
    existing_snapshot = db.execute(
        "SELECT status FROM local_execution_authorization_snapshots WHERE id=?",
        (snapshot_id,),
    ).fetchone()
    if existing_snapshot and existing_snapshot["status"] == "consumed":
        return _recover_authorized_execution_reservation(
            db, snapshot_id=snapshot_id, sandbox_root=sandbox_root,
            canonical_digest=canonical_digest,
        )
    root.mkdir(parents=True, exist_ok=True)
    sandbox_id = f"sbx-{uuid4()}"
    workspace = (root / sandbox_id).resolve()
    if workspace.parent != root:
        raise ValueError("SANDBOX_PATH_INVALID")
    directories = ("input", "runtime", "output", "logs")
    workspace.mkdir(mode=0o700)
    for name in directories:
        (workspace / name).mkdir(mode=0o700)
    db.execute("BEGIN IMMEDIATE")
    try:
        snapshot = db.execute(
            "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
            (snapshot_id,),
        ).fetchone()
        if snapshot is None:
            raise ValueError("EXECUTION_AUTHORIZATION_UNKNOWN")
        if snapshot["status"] != "validated":
            raise ValueError("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED")
        order = db.execute(
            """SELECT o.*,v.validation_status,r.decision review_decision,
                      rc.payload_digest receipt_digest,
                      dc.payload_digest decision_digest
                 FROM local_control_orders o
                 JOIN local_policy_validations v ON v.local_order_id=o.id
                 JOIN local_policy_reviews r ON r.local_order_id=o.id
                 JOIN local_order_receipts rc ON rc.local_order_id=o.id
                 JOIN local_order_decisions dc ON dc.local_order_id=o.id
                WHERE o.id=?""",
            (snapshot["local_order_id"],),
        ).fetchone()
        if order is not None and order["central_status"] == "revoked":
            raise ValueError("CENTRAL_POLICY_REVOKED")
        if (
            order is None
            or order["local_status"] != "accepted"
            or order["validation_status"] != "passed"
            or order["review_decision"] != "accepted"
            or order["consumed_count"] != 0
        ):
            raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
        policy = json.loads(order["policy_payload"])
        order_payload = json.loads(order["order_payload"])
        now_at = datetime.now(timezone.utc)
        expiry_values = {
            "POLICY_EXPIRED": datetime.fromisoformat(policy["expires_at"]),
            "ORDER_EXPIRED": datetime.fromisoformat(order_payload["expires_at"]),
            "EXECUTION_AUTHORIZATION_EXPIRED": datetime.fromisoformat(
                snapshot["expires_at"]
            ),
        }
        expired = next(
            (code for code, value in expiry_values.items() if value <= now_at),
            None,
        )
        if expired:
            raise ValueError(expired)
        attestation = db.execute(
            """SELECT * FROM local_executor_readiness_attestations
               WHERE executor_id=? AND payload_digest=?""",
            (
                snapshot["executor_id"],
                snapshot["source_executor_status_event_digest"],
            ),
        ).fetchone()
        current_attestation = db.execute(
            """SELECT * FROM local_executor_readiness_attestations
               WHERE executor_id=? ORDER BY event_sequence DESC LIMIT 1""",
            (snapshot["executor_id"],),
        ).fetchone()
        admission = db.execute(
            "SELECT * FROM local_executor_admission_checks WHERE id=?",
            (snapshot["admission_check_id"],),
        ).fetchone()
        image = db.execute(
            "SELECT * FROM local_execution_image_manifests WHERE id=?",
            (snapshot["image_manifest_id"],),
        ).fetchone()
        profile = db.execute(
            "SELECT * FROM local_executor_security_profiles "
            "WHERE executor_id=? AND profile_digest=?",
            (snapshot["executor_id"], snapshot["security_profile_digest"]),
        ).fetchone()
        executor = db.execute(
            "SELECT * FROM local_executors WHERE id=?",
            (snapshot["executor_id"],),
        ).fetchone()
        if not all(
            (attestation, current_attestation, admission, image, profile, executor)
        ):
            raise ValueError("AUTHORIZATION_SOURCE_OBJECT_MISSING")
        expiry_values["EXECUTOR_ATTESTATION_EXPIRED"] = datetime.fromisoformat(
            attestation["expires_at"]
        )
        admission_policy = json.loads(admission["policy_snapshot"])
        admission_expiry = datetime.fromisoformat(
            admission_policy["valid_until"]
        )
        resource_policy = json.loads(profile["resource_policy"])
        timeout_seconds = int(resource_policy["timeout_seconds"])
        minimum_validity = timeout_seconds + safety_margin_seconds
        remaining = int(
            (
                min(*expiry_values.values(), admission_expiry) - now_at
            ).total_seconds()
        )
        if remaining < minimum_validity:
            raise ValueError("EXECUTION_AUTHORIZATION_VALIDITY_TOO_SHORT")
        if (
            current_attestation["id"] != attestation["id"]
            or attestation["readiness_result"]
            != "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
            or executor["status"] != "active"
            or admission["decision"] != "approved"
            or image["status"] != "approved"
            or profile["status"] != "valid"
            or image["image_digest"] != approved_execution_image_digest
            or snapshot["image_digest"] != approved_execution_image_digest
        ):
            raise ValueError("AUTHORIZATION_SOURCE_NOT_CURRENT")
        security_expected = {
            "network_mode": "none", "filesystem_mode": "readonly_input",
            "rootless": True, "privileged": False,
            "docker_socket_access": False, "runtime_download": False,
        }
        if any(profile[key] != value for key, value in security_expected.items()):
            raise ValueError("RUNTIME_POLICY_INVALID")
        if (
            timeout_seconds > 900
            or resource_policy["cpu_cores"] > 2
            or resource_policy["memory_mb"] > 2048
            or resource_policy["disk_mb"] > 1024
            or resource_policy["processes"] > 64
        ):
            raise ValueError("RESOURCE_POLICY_EXCEEDS_REFERENCE_LIMIT")

        task_manifest = {
            "schema_version": "phase5.13E-2B-1/task-manifest/v1",
            "task_type": REFERENCE_TASK_TYPE,
            "task_version": REFERENCE_TASK_VERSION,
            "image_digest": approved_execution_image_digest,
            "model_reference": REFERENCE_MODEL,
            "model_digest": REFERENCE_MODEL_DIGEST,
            "dataset_reference": REFERENCE_DATASET,
            "dataset_digest": REFERENCE_DATASET_DIGEST,
            "input_schema": REFERENCE_INPUT_SCHEMA,
            "output_schema": REFERENCE_OUTPUT_SCHEMA,
            "resource_policy": resource_policy,
            "output_allowlist": list(REFERENCE_OUTPUT_FILES),
            "network_mode": "none",
            "rootless": True,
            "non_clinical": True,
        }
        input_manifest = {
            "schema_version": "phase5.13E-2B-1/input-manifest/v1",
            "asset_version_id": REFERENCE_DATASET,
            "metadata_digest": REFERENCE_DATASET_DIGEST,
            "sample_count": 20,
            "schema_digest": canonical_digest({
                "input_schema": REFERENCE_INPUT_SCHEMA,
                "shape": [20, 28, 28, 3], "dtype": "uint8",
            }),
            "fixed_indices": list(REFERENCE_INDICES),
            "fixed_indices_digest": canonical_digest(
                {"fixed_indices": list(REFERENCE_INDICES)}
            ),
        }
        task_manifest_digest = canonical_digest(task_manifest)
        input_manifest_digest = canonical_digest(input_manifest)
        stamp = _now()
        task_id, input_id, runtime_id, execution_id = (
            str(uuid4()), str(uuid4()), str(uuid4()), str(uuid4())
        )
        task_binding = {
            "schema_version":
                "phase5.13E-2C-R1/authorized-task-binding/v1",
            "authorization_snapshot_id": snapshot_id,
            "authorization_snapshot_digest": snapshot["snapshot_digest"],
            "policy_bundle_id": snapshot["policy_bundle_id"],
            "policy_bundle_version_id": snapshot["policy_bundle_version_id"],
            "policy_digest": snapshot["policy_digest"],
            "readiness_digest": snapshot["readiness_digest"],
            "execution_order_id": snapshot["execution_order_id"],
            "execution_order_digest": snapshot["execution_order_digest"],
            "source_executor_status_event_id":
                snapshot["source_executor_status_event_id"],
            "source_executor_status_event_digest":
                snapshot["source_executor_status_event_digest"],
            "connector_receipt_id": snapshot["connector_receipt_id"],
            "connector_receipt_digest": snapshot["connector_receipt_digest"],
            "connector_decision_id": snapshot["connector_decision_id"],
            "connector_decision_digest": snapshot["connector_decision_digest"],
            "admission_check_id": snapshot["admission_check_id"],
            "admission_check_digest": snapshot["admission_check_digest"],
            "connector_id": snapshot["connector_id"],
            "executor_id": snapshot["executor_id"],
            "asset_version_id": snapshot["asset_version_id"],
            "asset_metadata_digest": snapshot["asset_metadata_digest"],
            "quality_digest": snapshot["quality_digest"],
            "model_reference_digest": snapshot["model_reference_digest"],
            "image_digest": snapshot["image_digest"],
            "security_profile_digest": snapshot["security_profile_digest"],
            "resource_policy_digest": snapshot["resource_policy_digest"],
            "task_definition_digest": snapshot["task_definition_digest"],
            "input_schema_digest": snapshot["input_schema_digest"],
            "input_manifest_digest": input_manifest_digest,
            "output_schema_digest": snapshot["output_schema_digest"],
            "task_manifest_digest": task_manifest_digest,
        }
        task_digest = canonical_digest(task_binding)
        input_binding = {
            "schema_version":
                "phase5.13E-2C-R1/authorized-input-binding/v1",
            "authorization_snapshot_id": snapshot_id,
            "task_manifest_id": task_id,
            "task_manifest_digest": task_digest,
            "input_manifest": input_manifest,
        }
        input_digest = canonical_digest(input_binding)
        runtime_binding = {
            "schema_version":
                "phase5.13E-2C-R1/authorized-runtime-binding/v1",
            "authorization_snapshot_id": snapshot_id,
            "authorization_snapshot_digest": snapshot["snapshot_digest"],
            "task_manifest_id": task_id,
            "task_manifest_digest": task_digest,
            "policy_digest": snapshot["policy_digest"],
            "execution_order_digest": snapshot["execution_order_digest"],
            "connector_decision_digest":
                snapshot["connector_decision_digest"],
            "admission_check_digest": snapshot["admission_check_digest"],
            "image_digest": snapshot["image_digest"],
            "sandbox_id": sandbox_id,
        }
        runtime_digest = canonical_digest(runtime_binding)
        request_payload = {
            "schema_version": "phase5.13E-2B-1/worker-request/v1",
            "runtime_session_id": runtime_id,
            "sandbox_id": sandbox_id,
            "task_manifest": task_manifest,
            "task_digest": task_manifest_digest,
            "input_manifest": input_manifest,
            "input_digest": input_manifest_digest,
        }
        request_digest = canonical_digest(request_payload)
        request_payload["request_digest"] = request_digest
        execution_binding = {
            **task_binding,
            "schema_version":
                "phase5.13E-2C-R1/authorized-reference-execution/v1",
            "task_manifest_id": task_id,
            "task_binding_digest": task_digest,
            "runtime_session_id": runtime_id,
            "runtime_digest": runtime_digest,
            "input_manifest_id": input_id,
            "authorized_input_digest": input_digest,
            "request_digest": request_digest,
        }
        db.execute(
            """INSERT INTO local_authorized_task_manifests
               (id,authorization_snapshot_id,binding_payload,task_digest,
                created_at) VALUES(?,?,?,?,?)""",
            (
                task_id, snapshot_id,
                json.dumps(task_binding, sort_keys=True), task_digest, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_authorized_input_manifests
               (id,authorization_snapshot_id,binding_payload,input_digest,
                created_at) VALUES(?,?,?,?,?)""",
            (
                input_id, snapshot_id,
                json.dumps(input_binding, sort_keys=True), input_digest, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_authorized_runtime_sessions
               (id,authorization_snapshot_id,task_manifest_id,executor_id,
                admission_check_id,sandbox_id,binding_payload,runtime_digest,
                status,created_at,started_at)
               VALUES(?,?,?,?,?,?,?,?,'running',?,?)""",
            (
                runtime_id, snapshot_id, task_id, snapshot["executor_id"],
                snapshot["admission_check_id"], sandbox_id,
                json.dumps(runtime_binding, sort_keys=True), runtime_digest,
                stamp, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_authorized_reference_executions
               (id,authorization_snapshot_id,task_manifest_id,
                runtime_session_id,input_manifest_id,binding_payload,
                request_digest,status,created_at,started_at)
               VALUES(?,?,?,?,?,?,?,'running',?,?)""",
            (
                execution_id, snapshot_id, task_id, runtime_id, input_id,
                json.dumps(execution_binding, sort_keys=True),
                request_digest, stamp, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_authorized_execution_dispatches
               (reference_execution_id,authorization_snapshot_id,
                runtime_session_id,payload_json,request_digest,status,
                created_at) VALUES(?,?,?,?,?,'pending',?)""",
            (
                execution_id, snapshot_id, runtime_id,
                json.dumps(request_payload, sort_keys=True), request_digest,
                stamp,
            ),
        )
        updated = db.execute(
            """UPDATE local_execution_authorization_snapshots
               SET status='consumed',consumed_at=?
               WHERE id=? AND status='validated'""",
            (stamp, snapshot_id),
        )
        if updated.rowcount != 1:
            raise ValueError("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED")
        order_updated = db.execute(
            """UPDATE local_control_orders SET consumed_count=1
               WHERE id=? AND consumed_count=0""",
            (snapshot["local_order_id"],),
        )
        if order_updated.rowcount != 1:
            raise ValueError("EXECUTION_ORDER_ALREADY_CONSUMED")
        consumption_id = str(uuid4())
        consumption_payload = {
            "schema_version":
                "phase5.13E-2C-R1/execution-consumption/v1",
            "consumption_receipt_id": consumption_id,
            "execution_order_id": snapshot["execution_order_id"],
            "execution_order_digest": snapshot["execution_order_digest"],
            "authorization_snapshot_id": snapshot_id,
            "authorization_snapshot_digest": snapshot["snapshot_digest"],
            "task_manifest_id": task_id,
            "task_manifest_digest": task_digest,
            "runtime_session_id": runtime_id,
            "runtime_digest": runtime_digest,
            "reference_execution_id": execution_id,
            "request_digest": request_digest,
            "consumed_at": stamp,
            "remaining_validity_seconds": remaining,
            "local_audit_head": local_audit_head,
            "execution_started": False,
            "hard_isolation": False,
        }
        consumption_digest = canonical_digest(consumption_payload)
        db.execute(
            """INSERT INTO local_execution_consumption_receipts
               (id,local_order_id,authorization_snapshot_id,task_manifest_id,
                runtime_session_id,reference_execution_id,payload_json,
                payload_digest,signature,delivery_status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,'pending',?)""",
            (
                consumption_id, snapshot["local_order_id"], snapshot_id,
                task_id, runtime_id, execution_id,
                json.dumps(consumption_payload, sort_keys=True),
                consumption_digest, signer(consumption_payload), stamp,
            ),
        )
        db.commit()
        return {
            "snapshot_id": snapshot_id,
            "task_manifest_id": task_id,
            "task_digest": task_digest,
            "runtime_session_id": runtime_id,
            "runtime_digest": runtime_digest,
            "reference_execution_id": execution_id,
            "request_payload": request_payload,
            "consumption_receipt_id": consumption_id,
            "consumption_payload": consumption_payload,
            "consumption_digest": consumption_digest,
            "remaining_validity_seconds": remaining,
            "consumption_delivery_status": "pending",
            "dispatch_status": "pending",
            "status": "reserved", "recovered": False,
        }
    except Exception:
        db.rollback()
        if workspace.exists():
            shutil.rmtree(workspace)
        raise


def dispatch_authorized_fixed_reference_execution(
    db: sqlite3.Connection, *, reference_execution_id: str,
    sandbox_root: Path, request_payload: dict[str, Any],
) -> bool:
    db.execute("BEGIN IMMEDIATE")
    temporary: Path | None = None
    try:
        row = db.execute(
        """SELECT x.status execution_status,s.status runtime_status,
                  s.sandbox_id,c.delivery_status,d.status dispatch_status,
                  d.payload_json dispatch_payload,d.request_digest,
                  a.status snapshot_status,a.expires_at snapshot_expires_at,
                  o.central_status,o.local_status
             FROM local_authorized_reference_executions x
             JOIN local_authorized_runtime_sessions s
               ON s.id=x.runtime_session_id
             JOIN local_execution_consumption_receipts c
               ON c.reference_execution_id=x.id
             JOIN local_authorized_execution_dispatches d
               ON d.reference_execution_id=x.id
             JOIN local_execution_authorization_snapshots a
               ON a.id=x.authorization_snapshot_id
             JOIN local_control_orders o ON o.id=a.local_order_id
            WHERE x.id=?""",
        (reference_execution_id,),
        ).fetchone()
        if row is None:
            raise ValueError("AUTHORIZED_REFERENCE_EXECUTION_UNKNOWN")
        if row["delivery_status"] != "delivered":
            raise ValueError("CENTRAL_CONSUMPTION_NOT_CONFIRMED")
        stored_payload = json.loads(row["dispatch_payload"])
        if stored_payload != request_payload:
            raise ValueError("AUTHORIZED_DISPATCH_REQUEST_MISMATCH")
        if row["dispatch_status"] == "dispatched":
            db.rollback()
            return False
        runtime_dir = (
            sandbox_root.resolve() / row["sandbox_id"] / "runtime"
        ).resolve()
        if runtime_dir.parent.parent != sandbox_root.resolve():
            raise ValueError("SANDBOX_PATH_INVALID")
        request_path = runtime_dir / "request.json"
        claimed_path = runtime_dir / "request.claimed.json"
        result_path = runtime_dir / "result.json"
        already_dispatched = (
            request_path.exists() or claimed_path.exists() or result_path.exists()
        )
        if already_dispatched:
            updated = db.execute(
                """UPDATE local_authorized_execution_dispatches
                   SET status='dispatched',dispatched_at=?
                   WHERE reference_execution_id=? AND status='pending'""",
                (_now(), reference_execution_id),
            )
            if updated.rowcount != 1:
                raise ValueError("AUTHORIZED_DISPATCH_STATE_RACE")
            db.commit()
            return False
        if row["central_status"] == "revoked":
            raise ValueError("CENTRAL_POLICY_REVOKED")
        if row["local_status"] != "accepted":
            raise ValueError("LOCAL_EXECUTION_AUTHORIZATION_NOT_ACCEPTED")
        if row["snapshot_status"] != "consumed":
            raise ValueError("EXECUTION_AUTHORIZATION_NOT_CONSUMED")
        try:
            snapshot_expires_at = datetime.fromisoformat(
                row["snapshot_expires_at"]
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("EXECUTION_AUTHORIZATION_TIME_INVALID") from exc
        if snapshot_expires_at <= datetime.now(timezone.utc):
            raise ValueError("EXECUTION_AUTHORIZATION_EXPIRED")
        if row["execution_status"] in {
            "completed", "failed", "result_mismatch"
        } or row["runtime_status"] in {"completed", "failed"}:
            db.rollback()
            return False
        if (
            row["execution_status"] != "running"
            or row["runtime_status"] != "running"
        ):
            raise ValueError("AUTHORIZED_EXECUTION_NOT_DISPATCHABLE")
        temporary = runtime_dir / f"request.{uuid4().hex}.tmp"
        temporary.write_text(
            json.dumps(
                request_payload, sort_keys=True, separators=(",", ":")
            ),
            encoding="utf-8",
        )
        temporary.replace(request_path)
        updated = db.execute(
            """UPDATE local_authorized_execution_dispatches
               SET status='dispatched',dispatched_at=?
               WHERE reference_execution_id=? AND status='pending'""",
            (_now(), reference_execution_id),
        )
        if updated.rowcount != 1:
            raise ValueError("AUTHORIZED_DISPATCH_STATE_RACE")
        db.commit()
        return True
    except Exception:
        db.rollback()
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_execution_consumption_delivery(
    db: sqlite3.Connection, *, receipt_id: str, delivered: bool,
    response_code: int,
) -> str:
    target = "delivered" if delivered else "failed"
    source_states = "('pending','failed')" if delivered else "('pending')"
    db.execute(
        f"""UPDATE local_execution_consumption_receipts
           SET delivery_status=?,response_code=?,delivered_at=?
           WHERE id=? AND delivery_status IN {source_states}""",
        (
            target, response_code, _now() if delivered else None, receipt_id,
        ),
    )
    db.commit()
    row = db.execute(
        "SELECT delivery_status FROM local_execution_consumption_receipts "
        "WHERE id=?", (receipt_id,),
    ).fetchone()
    if row is None:
        raise ValueError("EXECUTION_CONSUMPTION_RECEIPT_UNKNOWN")
    return str(row["delivery_status"])


def _valid_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(char in "0123456789abcdef" for char in value[7:])
    )


def validate_executor_capability(payload: dict[str, Any]) -> None:
    if set(payload) != EXECUTOR_CAPABILITY_FIELDS:
        raise ValueError("EXECUTOR_CAPABILITY_SCHEMA_INVALID")
    for field in ("image_digest",):
        if not _valid_sha256(payload[field]):
            raise ValueError("EXECUTOR_CAPABILITY_DIGEST_INVALID")
    if payload["schema_version"] != "phase5.13E-1A/executor-capability/v1":
        raise ValueError("EXECUTOR_CAPABILITY_SCHEMA_UNSUPPORTED")
    if payload["runtime"] != "container":
        raise ValueError("EXECUTOR_RUNTIME_UNSUPPORTED")
    if payload["network_mode"] != "none":
        raise ValueError("EXECUTOR_NETWORK_POLICY_INVALID")
    if payload["filesystem_mode"] != "readonly_input":
        raise ValueError("EXECUTOR_FILESYSTEM_POLICY_INVALID")
    if payload["rootless"] is not True or payload["gpu"] is not False:
        raise ValueError("EXECUTOR_PRIVILEGE_POLICY_INVALID")
    if payload["execution_enabled"] is not False or payload["hard_isolation"] is not False:
        raise ValueError("EXECUTOR_CAPABILITY_FORBIDDEN")
    if payload["supported_task_types"] != [REFERENCE_TASK_TYPE]:
        raise ValueError("EXECUTOR_TASK_TYPE_UNKNOWN")
    resources = payload["resource_limits"]
    if not isinstance(resources, dict) or set(resources) != EXECUTOR_RESOURCE_FIELDS:
        raise ValueError("EXECUTOR_RESOURCE_POLICY_INVALID")
    limits = {
        "cpu_cores": (1, 4),
        "memory_mb": (256, 8192),
        "disk_mb": (128, 4096),
        "processes": (1, 128),
        "timeout_seconds": (10, 3600),
    }
    for field, (minimum, maximum) in limits.items():
        value = resources[field]
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("EXECUTOR_RESOURCE_POLICY_INVALID")
        if not minimum <= value <= maximum:
            raise ValueError("EXECUTOR_RESOURCE_POLICY_INVALID")
    features = payload["security_features"]
    if features != [
        "no_new_privileges", "drop_all_capabilities", "read_only_root",
        "no_runtime_install", "no_runtime_download",
    ]:
        raise ValueError("EXECUTOR_SECURITY_FEATURE_UNKNOWN")


def create_executor_registration(
    db: sqlite3.Connection,
    *,
    executor_instance_id: str,
    executor_version: str,
    architecture: str,
    csr_pem: str,
    installation_digest: str,
    capability_payload: dict[str, Any],
    runtime_digest: str,
    nonce: str,
    request_timestamp: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> str:
    validate_executor_capability(capability_payload)
    if not _valid_sha256(installation_digest) or not _valid_sha256(runtime_digest):
        raise ValueError("EXECUTOR_REGISTRATION_DIGEST_INVALID")
    try:
        requested_at = datetime.fromisoformat(request_timestamp)
    except ValueError as exc:
        raise ValueError("EXECUTOR_REGISTRATION_TIMESTAMP_INVALID") from exc
    if requested_at.tzinfo is None:
        raise ValueError("EXECUTOR_REGISTRATION_TIMESTAMP_INVALID")
    if abs((datetime.now(timezone.utc) - requested_at).total_seconds()) > 300:
        raise ValueError("EXECUTOR_REGISTRATION_TIMESTAMP_OUT_OF_WINDOW")
    if (
        len(executor_instance_id) < 12
        or len(nonce) < 16
        or "BEGIN CERTIFICATE REQUEST" not in csr_pem
    ):
        raise ValueError("EXECUTOR_REGISTRATION_INVALID")
    capability_digest = canonical_digest(capability_payload)
    registration_id, stamp = str(uuid4()), _now()
    try:
        db.execute(
            """INSERT INTO local_executor_registrations
               (id,executor_instance_id,executor_version,architecture,csr_pem,
                csr_fingerprint,installation_digest,capability_payload,
                capability_digest,runtime_digest,image_digest,nonce,
                request_timestamp,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)""",
            (
                registration_id, executor_instance_id, executor_version,
                architecture, csr_pem,
                "sha256:" + hashlib.sha256(csr_pem.encode()).hexdigest(),
                installation_digest,
                json.dumps(capability_payload, sort_keys=True),
                capability_digest, runtime_digest,
                capability_payload["image_digest"], nonce,
                request_timestamp, stamp,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("EXECUTOR_REGISTRATION_DUPLICATE") from exc
    return registration_id


def approve_executor_registration(
    db: sqlite3.Connection,
    *,
    registration_id: str,
    connector_id: str,
    reviewer_id: str,
    certificate: dict[str, str],
) -> str:
    registration = db.execute(
        "SELECT * FROM local_executor_registrations WHERE id=?",
        (registration_id,),
    ).fetchone()
    if registration is None:
        raise ValueError("EXECUTOR_REGISTRATION_NOT_FOUND")
    if registration["status"] != "pending":
        raise ValueError("EXECUTOR_REGISTRATION_NOT_PENDING")
    payload = json.loads(registration["capability_payload"])
    validate_executor_capability(payload)
    stamp, executor_id = _now(), str(uuid4())
    certificate_id, manifest_id = str(uuid4()), str(uuid4())
    db.execute(
        """INSERT INTO local_executors
           (id,connector_id,executor_instance_id,executor_version,architecture,
            status,current_certificate_id,current_capability_manifest_id,
            last_heartbeat_sequence,status_sequence,security_status,activated_at,
            created_at,updated_at)
           VALUES(?,?,?,?,?,'active',?,?,0,1,'passed',?,?,?)""",
        (
            executor_id, connector_id, registration["executor_instance_id"],
            registration["executor_version"], registration["architecture"],
            certificate_id, manifest_id, stamp, stamp, stamp,
        ),
    )
    db.execute(
        """INSERT INTO local_executor_certificates
           (id,executor_id,serial_number,subject,issuer,fingerprint_sha256,
            certificate_pem,valid_from,valid_to,status,issued_at)
           VALUES(?,?,?,?,?,?,?,?,?,'active',?)""",
        (
            certificate_id, executor_id, certificate["serial_number"],
            certificate["subject"], certificate["issuer"],
            certificate["fingerprint_sha256"], certificate["certificate_pem"],
            certificate["valid_from"], certificate["valid_to"], stamp,
        ),
    )
    db.execute(
        """INSERT INTO local_executor_capability_manifests
           (id,executor_id,schema_version,manifest_version,executor_version,
            runtime,image_digest,architecture,network_mode,filesystem_mode,
            rootless,gpu,supported_task_types,resource_limits,security_features,
            execution_enabled,hard_isolation,manifest_digest,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            manifest_id, executor_id, payload["schema_version"],
            payload["manifest_version"], payload["executor_version"],
            payload["runtime"], payload["image_digest"], payload["architecture"],
            payload["network_mode"], payload["filesystem_mode"],
            int(payload["rootless"]), int(payload["gpu"]),
            json.dumps(payload["supported_task_types"]),
            json.dumps(payload["resource_limits"], sort_keys=True),
            json.dumps(payload["security_features"]),
            0, 0, registration["capability_digest"], stamp,
        ),
    )
    db.execute(
        """UPDATE local_executor_registrations
           SET status='certificate_issued',reviewed_by=?,reviewed_at=?,
               executor_id=? WHERE id=?""",
        (reviewer_id, stamp, executor_id, registration_id),
    )
    db.commit()
    return executor_id


def reject_executor_registration(
    db: sqlite3.Connection, *, registration_id: str,
    reviewer_id: str, reason: str,
) -> None:
    result = db.execute(
        """UPDATE local_executor_registrations
           SET status='rejected',reviewed_by=?,reviewed_at=?,rejection_reason=?
           WHERE id=? AND status='pending'""",
        (reviewer_id, _now(), reason, registration_id),
    )
    if result.rowcount != 1:
        raise ValueError("EXECUTOR_REGISTRATION_NOT_PENDING")
    db.commit()


def record_executor_heartbeat(
    db: sqlite3.Connection,
    *,
    executor_id: str,
    certificate_fingerprint: str,
    payload: dict[str, Any],
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    required = {
        "executor_id", "sequence", "timestamp", "status",
        "capability_digest", "runtime_digest", "nonce", "message_digest",
    }
    if set(payload) != required:
        raise ValueError("EXECUTOR_HEARTBEAT_SCHEMA_INVALID")
    executor = db.execute(
        """SELECT e.*,c.fingerprint_sha256,c.status certificate_status,
                  m.manifest_digest,m.runtime
           FROM local_executors e
           JOIN local_executor_certificates c ON c.id=e.current_certificate_id
           JOIN local_executor_capability_manifests m
             ON m.id=e.current_capability_manifest_id
           WHERE e.id=?""",
        (executor_id,),
    ).fetchone()
    if executor is None:
        raise ValueError("EXECUTOR_UNKNOWN")
    if executor["status"] == "revoked" or executor["certificate_status"] != "active":
        raise ValueError("EXECUTOR_REVOKED")
    if certificate_fingerprint != executor["fingerprint_sha256"]:
        raise ValueError("EXECUTOR_CERTIFICATE_INVALID")
    if payload["executor_id"] != executor_id:
        raise ValueError("EXECUTOR_IDENTITY_MISMATCH")
    if payload["sequence"] <= executor["last_heartbeat_sequence"]:
        raise ValueError("EXECUTOR_HEARTBEAT_SEQUENCE_NOT_INCREASING")
    try:
        sent_at = datetime.fromisoformat(payload["timestamp"])
    except ValueError as exc:
        raise ValueError("EXECUTOR_HEARTBEAT_TIMESTAMP_INVALID") from exc
    if sent_at.tzinfo is None or abs(
        (datetime.now(timezone.utc) - sent_at).total_seconds()
    ) > 300:
        raise ValueError("EXECUTOR_HEARTBEAT_TIMESTAMP_OUT_OF_WINDOW")
    if payload["capability_digest"] != executor["manifest_digest"]:
        raise ValueError("EXECUTOR_CAPABILITY_DIGEST_MISMATCH")
    if payload["runtime_digest"] != db.execute(
        "SELECT runtime_digest FROM local_executor_registrations WHERE executor_id=?",
        (executor_id,),
    ).fetchone()["runtime_digest"]:
        raise ValueError("EXECUTOR_RUNTIME_DIGEST_MISMATCH")
    if payload["status"] not in {"healthy", "degraded"}:
        raise ValueError("EXECUTOR_HEARTBEAT_STATUS_INVALID")
    expected = canonical_digest(
        {key: value for key, value in payload.items() if key != "message_digest"}
    )
    if payload["message_digest"] != expected:
        raise ValueError("EXECUTOR_HEARTBEAT_DIGEST_MISMATCH")
    stamp = _now()
    try:
        db.execute(
            """INSERT INTO local_executor_heartbeats
               (id,executor_id,sequence,sent_at,status,capability_digest,
                runtime_digest,certificate_fingerprint,nonce,message_digest,
                received_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                str(uuid4()), executor_id, payload["sequence"],
                payload["timestamp"], payload["status"],
                payload["capability_digest"], payload["runtime_digest"],
                certificate_fingerprint, payload["nonce"],
                payload["message_digest"], stamp,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("EXECUTOR_HEARTBEAT_REPLAY") from exc
    next_status = "paused" if executor["status"] == "paused" else "active"
    status_sequence = executor["status_sequence"] + 1
    db.execute(
        """UPDATE local_executors
           SET status=?,last_heartbeat_at=?,last_heartbeat_sequence=?,
               status_sequence=?,updated_at=? WHERE id=?""",
        (
            next_status, stamp, payload["sequence"], status_sequence,
            stamp, executor_id,
        ),
    )
    db.commit()
    return {
        "status": next_status,
        "status_sequence": status_sequence,
        "heartbeat_sequence": payload["sequence"],
        "heartbeat_at": stamp,
    }


def transition_executor(
    db: sqlite3.Connection,
    *,
    executor_id: str,
    action: str,
    reason: str,
) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM local_executors WHERE id=?", (executor_id,)
    ).fetchone()
    if row is None:
        raise ValueError("EXECUTOR_UNKNOWN")
    stamp = _now()
    if action == "pause" and row["status"] == "active":
        status, security_status = "paused", row["security_status"]
        paused_at, revoked_at = stamp, row["revoked_at"]
    elif action == "resume" and row["status"] == "paused":
        cert = db.execute(
            "SELECT status,valid_to FROM local_executor_certificates WHERE id=?",
            (row["current_certificate_id"],),
        ).fetchone()
        if cert is None or cert["status"] != "active":
            raise ValueError("EXECUTOR_CERTIFICATE_INVALID")
        status, security_status = "active", row["security_status"]
        paused_at, revoked_at = None, row["revoked_at"]
    elif action == "revoke" and row["status"] != "revoked":
        status, security_status = "revoked", "revoked"
        paused_at, revoked_at = row["paused_at"], stamp
        db.execute(
            """UPDATE local_executor_certificates
               SET status='revoked',revoked_at=?,revocation_reason=?
               WHERE id=?""",
            (stamp, reason, row["current_certificate_id"]),
        )
    else:
        raise ValueError("EXECUTOR_TRANSITION_INVALID")
    sequence = row["status_sequence"] + 1
    db.execute(
        """UPDATE local_executors
           SET status=?,security_status=?,status_sequence=?,paused_at=?,
               revoked_at=?,revocation_reason=?,updated_at=? WHERE id=?""",
        (
            status, security_status, sequence, paused_at, revoked_at,
            reason if action == "revoke" else row["revocation_reason"],
            stamp, executor_id,
        ),
    )
    db.commit()
    return {"status": status, "status_sequence": sequence}


def list_executors(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute(
        """SELECT e.*,c.fingerprint_sha256,c.valid_to,c.status certificate_status,
                  m.manifest_version,m.manifest_digest,m.runtime,m.image_digest,
                  m.network_mode,m.filesystem_mode,m.rootless,m.gpu,
                  m.supported_task_types,m.resource_limits,m.security_features,
                  r.runtime_digest
           FROM local_executors e
           LEFT JOIN local_executor_certificates c
             ON c.id=e.current_certificate_id
           LEFT JOIN local_executor_capability_manifests m
             ON m.id=e.current_capability_manifest_id
           LEFT JOIN local_executor_registrations r ON r.executor_id=e.id
           ORDER BY e.created_at DESC"""
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for field in (
            "supported_task_types", "resource_limits", "security_features",
        ):
            item[field] = json.loads(item[field]) if item.get(field) else None
        result.append(item)
    return result


def create_executor_fixed_execution_readiness_attestation(
    db: sqlite3.Connection,
    *,
    executor_id: str,
    connector_id: str,
    connector_certificate_fingerprint: str,
    signing_key_id: str,
    ttl_seconds: int,
    canonical_digest: Callable[[dict[str, Any]], str],
    signer: Callable[[dict[str, Any]], str],
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if not 60 <= ttl_seconds <= 3600:
        raise ValueError("EXECUTOR_ATTESTATION_TTL_INVALID")
    stamp = generated_at or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        raise ValueError("EXECUTOR_STATUS_TIMESTAMP_INVALID")
    db.execute("BEGIN IMMEDIATE")
    try:
        executor = db.execute(
            "SELECT * FROM local_executors WHERE id=?", (executor_id,)
        ).fetchone()
        if executor is None:
            raise ValueError("EXECUTOR_UNKNOWN")
        certificate = db.execute(
            "SELECT * FROM local_executor_certificates WHERE id=?",
            (executor["current_certificate_id"],),
        ).fetchone()
        capability = db.execute(
            "SELECT * FROM local_executor_capability_manifests WHERE id=?",
            (executor["current_capability_manifest_id"],),
        ).fetchone()
        profile = db.execute(
            """SELECT * FROM local_executor_security_profiles
               WHERE executor_id=? ORDER BY created_at DESC LIMIT 1""",
            (executor_id,),
        ).fetchone()
        admission = db.execute(
            """SELECT * FROM local_executor_admission_checks
               WHERE executor_id=? ORDER BY checked_at DESC LIMIT 1""",
            (executor_id,),
        ).fetchone()
        image = (
            db.execute(
                "SELECT * FROM local_execution_image_manifests WHERE id=?",
                (admission["image_manifest_id"],),
            ).fetchone()
            if admission is not None else None
        )
        audit_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='audit'"
        ).fetchone()
        audit_head = (
            db.execute(
                "SELECT sequence,event_digest FROM audit ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            if audit_exists else None
        )
        reasons: list[str] = []
        if executor["status"] != "active":
            reasons.append("EXECUTOR_NOT_ACTIVE")
        if certificate is None or certificate["status"] != "active":
            reasons.append("EXECUTOR_CERTIFICATE_INVALID")
        if not executor["last_heartbeat_at"]:
            reasons.append("EXECUTOR_HEARTBEAT_MISSING")
        if capability is None:
            reasons.append("FIXED_REFERENCE_CAPABILITY_MISSING")
        if profile is None or profile["status"] != "valid":
            reasons.append("SECURITY_PROFILE_INVALID")
        if image is None or image["status"] != "approved":
            reasons.append("IMAGE_MANIFEST_NOT_APPROVED")
        elif not image["signature_verified"]:
            reasons.append("IMAGE_SIGNATURE_STATUS_INVALID")
        elif image["security_scan_status"] != "passed":
            reasons.append("IMAGE_SCAN_STATUS_INVALID")
        if admission is None or admission["decision"] != "approved":
            reasons.append("ADMISSION_NOT_APPROVED")

        supported = (
            json.loads(capability["supported_task_types"])
            if capability is not None else []
        )
        features = (
            json.loads(capability["security_features"])
            if capability is not None else []
        )
        try:
            resources = (
                json.loads(profile["resource_policy"])
                if profile is not None else {}
            )
        except (TypeError, json.JSONDecodeError):
            resources = None
        resource_digest = (
            canonical_digest(resources) if isinstance(resources, dict) else None
        )
        safe_resources = resources if isinstance(resources, dict) else {}
        if "PATHMNIST_REFERENCE_V1" not in supported:
            reasons.append("FIXED_REFERENCE_CAPABILITY_MISSING")
        if capability is not None and (
            capability["hard_isolation"] != 0
            or capability["execution_enabled"] != 0
        ):
            reasons.append("HARD_ISOLATION_CLAIM_INVALID")
        if profile is not None:
            checks = (
                (profile["network_mode"] == "none", "NETWORK_POLICY_INVALID"),
                (profile["filesystem_mode"] == "readonly_input", "FILESYSTEM_POLICY_INVALID"),
                (profile["rootless"] == 1, "ROOTLESS_REQUIRED"),
                (profile["privileged"] == 0, "PRIVILEGED_FORBIDDEN"),
                (profile["docker_socket_access"] == 0, "DOCKER_SOCKET_FORBIDDEN"),
                (profile["runtime_download"] == 0, "RUNTIME_DOWNLOAD_FORBIDDEN"),
                ("drop_all_capabilities" in features, "CAPABILITY_DROP_REQUIRED"),
            )
            reasons.extend(code for passed, code in checks if not passed)
        if not isinstance(resources, dict) or set(resources) != set(
            SECURITY_RESOURCE_LIMITS
        ):
            reasons.append("RESOURCE_POLICY_INVALID")
        else:
            for field, (minimum, maximum) in SECURITY_RESOURCE_LIMITS.items():
                value = resources.get(field)
                if (
                    not isinstance(value, int) or isinstance(value, bool)
                    or not minimum <= value <= maximum
                ):
                    reasons.append("RESOURCE_POLICY_INVALID")
                    break

        admission_snapshot: dict[str, Any] = {}
        if admission is not None:
            try:
                admission_snapshot = json.loads(admission["policy_snapshot"])
            except (TypeError, json.JSONDecodeError):
                reasons.append("ADMISSION_BINDING_INVALID")
        admission_valid_until = admission_snapshot.get("valid_until")
        try:
            admission_expiry = datetime.fromisoformat(admission_valid_until)
            if admission_expiry <= stamp:
                reasons.append("ADMISSION_EXPIRED")
        except (TypeError, ValueError):
            admission_expiry = stamp
            reasons.append("ADMISSION_BINDING_INVALID")
        expected_bindings = {
            "image_manifest_digest": image["manifest_digest"] if image else None,
            "image_digest": image["image_digest"] if image else None,
            "security_profile_digest": profile["profile_digest"] if profile else None,
            "resource_policy_digest": resource_digest,
            "capability_digest": capability["manifest_digest"] if capability else None,
        }
        if any(
            admission_snapshot.get(key) != value
            for key, value in expected_bindings.items()
        ):
            reasons.append("ADMISSION_BINDING_INVALID")
        if audit_head is None:
            reasons.append("LOCAL_AUDIT_HEAD_MISSING")

        event_sequence = int(executor["status_sequence"]) + 1
        expires_at = min(stamp + timedelta(seconds=ttl_seconds), admission_expiry)
        result = (
            "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
            if not reasons else "NOT_READY"
        )
        payload = {
            "schema_version": "hospital_executor_status_v2",
            "event_type": "EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION",
            "connector_id": connector_id,
            "executor_id": executor_id,
            "executor_instance_id": executor["executor_instance_id"],
            "connector_certificate_fingerprint": connector_certificate_fingerprint,
            "executor_certificate_fingerprint": (
                certificate["fingerprint_sha256"] if certificate else None
            ),
            "executor_status": executor["status"],
            "executor_version": executor["executor_version"],
            "heartbeat_at": executor["last_heartbeat_at"],
            "capability": {
                "local_object_id": capability["id"] if capability else None,
                "manifest_version": capability["manifest_version"] if capability else None,
                "digest": capability["manifest_digest"] if capability else None,
                "fixed_reference_execution_enabled": (
                    "PATHMNIST_REFERENCE_V1" in supported
                ),
                "supported_task_types": supported,
                "arbitrary_execution_enabled": False,
                "user_code_enabled": False,
                "user_model_enabled": False,
                "data_transfer_enabled": False,
                "model_transfer_enabled": False,
                "artifact_auto_egress_enabled": False,
                "hard_isolation": False,
            },
            "image_manifest": {
                "local_object_id": image["id"] if image else None,
                "image_id": image["image_id"] if image else None,
                "image_digest": image["image_digest"] if image else None,
                "manifest_digest": image["manifest_digest"] if image else None,
                "lifecycle_status": image["status"] if image else "missing",
                "signature_status": (
                    "verified" if image and image["signature_verified"] else "invalid"
                ),
                "security_scan_status": (
                    image["security_scan_status"] if image else "unknown"
                ),
                "build_time": image["build_time"] if image else None,
                "revoked_at": (
                    image["updated_at"]
                    if image and image["status"] == "revoked" else None
                ),
            },
            "security_profile": {
                "local_object_id": profile["id"] if profile else None,
                "security_version": profile["security_version"] if profile else None,
                "profile_digest": profile["profile_digest"] if profile else None,
                "status": profile["status"] if profile else "missing",
                "network_mode": profile["network_mode"] if profile else None,
                "filesystem_mode": profile["filesystem_mode"] if profile else None,
                "rootless": bool(profile["rootless"]) if profile else False,
                "privileged": bool(profile["privileged"]) if profile else True,
                "docker_socket_access": (
                    bool(profile["docker_socket_access"]) if profile else True
                ),
                "runtime_download": (
                    bool(profile["runtime_download"]) if profile else True
                ),
                "input_readonly": bool(
                    profile and profile["filesystem_mode"] == "readonly_input"
                ),
                "capabilities_dropped": "drop_all_capabilities" in features,
                "created_at": profile["created_at"] if profile else None,
            },
            "resource_policy": {
                "local_object_id": (
                    f"{profile['id']}:resource-policy" if profile else None
                ),
                "policy_version": (
                    f"{profile['security_version']}/resource" if profile else None
                ),
                "policy_digest": resource_digest,
                "status": "active" if profile and profile["status"] == "valid" else "invalid",
                **safe_resources,
                "created_at": profile["created_at"] if profile else None,
            },
            "admission": {
                "local_object_id": admission["id"] if admission else None,
                "admission_digest": admission["admission_digest"] if admission else None,
                "result": admission["decision"] if admission else "missing",
                "executor_id": admission["executor_id"] if admission else None,
                **expected_bindings,
                "checked_at": admission["checked_at"] if admission else None,
                "valid_until": admission_valid_until,
            },
            "readiness_result": result,
            "readiness_reason": reasons[0] if reasons else None,
            "local_audit_head": audit_head["event_digest"] if audit_head else None,
            "local_state_revision": (
                f"phase5.13E_0008:audit-{audit_head['sequence']}"
                if audit_head else "phase5.13E_0008:audit-missing"
            ),
            "event_sequence": event_sequence,
            "nonce": __import__("secrets").token_urlsafe(32),
            "generated_at": stamp.isoformat(),
            "not_before": (stamp - timedelta(seconds=30)).isoformat(),
            "expires_at": expires_at.isoformat(),
            "signing_key_id": signing_key_id,
        }
        payload["payload_digest"] = canonical_digest(payload)
        payload["signature"] = signer(payload)
        attestation_id = str(uuid4())
        db.execute(
            """INSERT INTO local_executor_readiness_attestations
               (id,executor_id,event_sequence,schema_version,event_type,nonce,
                payload_json,payload_digest,signing_key_id,signature,
                readiness_result,generated_at,expires_at,delivery_status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')""",
            (
                attestation_id, executor_id, event_sequence,
                payload["schema_version"], payload["event_type"], payload["nonce"],
                json.dumps(payload, sort_keys=True), payload["payload_digest"],
                signing_key_id, payload["signature"], result,
                payload["generated_at"], payload["expires_at"],
            ),
        )
        db.execute(
            "UPDATE local_executors SET status_sequence=?,updated_at=? WHERE id=?",
            (event_sequence, stamp.isoformat(), executor_id),
        )
        db.commit()
        return {"id": attestation_id, "payload": payload}
    except Exception:
        db.rollback()
        raise


SECURITY_RESOURCE_LIMITS = {
    "cpu_cores": (1, 4),
    "memory_mb": (256, 8192),
    "disk_mb": (128, 4096),
    "processes": (1, 128),
    "timeout_seconds": (10, 3600),
}


def create_executor_security_profile(
    db: sqlite3.Connection, *, executor_id: str,
    checked_by: str, canonical_digest: Callable[[dict[str, Any]], str],
) -> str:
    executor = next(
        (item for item in list_executors(db) if item["id"] == executor_id), None
    )
    if executor is None:
        raise ValueError("EXECUTOR_UNKNOWN")
    resources = executor["resource_limits"]
    reasons = []
    if executor["network_mode"] != "none":
        reasons.append("NETWORK_NOT_ALLOWED")
    if executor["filesystem_mode"] != "readonly_input":
        reasons.append("FILESYSTEM_POLICY_INVALID")
    if executor["rootless"] != 1:
        reasons.append("ROOT_PRIVILEGE_FORBIDDEN")
    if "no_runtime_download" not in executor["security_features"]:
        reasons.append("RUNTIME_DOWNLOAD_FORBIDDEN")
    if not isinstance(resources, dict):
        reasons.append("RESOURCE_POLICY_MISSING")
    else:
        for field, (minimum, maximum) in SECURITY_RESOURCE_LIMITS.items():
            value = resources.get(field)
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not minimum <= value <= maximum
            ):
                reasons.append("RESOURCE_POLICY_INVALID")
                break
    profile = {
        "executor_id": executor_id,
        "security_version": "phase5.13E-1B/security-profile/v1",
        "network_mode": executor["network_mode"],
        "filesystem_mode": executor["filesystem_mode"],
        "rootless": bool(executor["rootless"]),
        "privileged": False,
        "docker_socket_access": False,
        "runtime_download": False,
        "resource_policy": resources,
    }
    profile_digest = canonical_digest(profile)
    profile_id = str(uuid4())
    try:
        db.execute(
            """INSERT INTO local_executor_security_profiles
               (id,executor_id,security_version,network_mode,filesystem_mode,
                rootless,privileged,docker_socket_access,runtime_download,
                resource_policy,profile_digest,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,? ,?)""",
            (
                profile_id, executor_id, profile["security_version"],
                profile["network_mode"], profile["filesystem_mode"],
                int(profile["rootless"]), 0, 0, 0,
                json.dumps(resources, sort_keys=True), profile_digest,
                "invalid" if reasons else "valid", _now(),
            ),
        )
        db.commit()
    except sqlite3.IntegrityError as exc:
        raise ValueError("EXECUTOR_SECURITY_PROFILE_DUPLICATE") from exc
    if reasons:
        raise ValueError(reasons[0])
    return profile_id


def create_execution_image_manifest(
    db: sqlite3.Connection, *, payload: dict[str, Any],
    canonical_digest: Callable[[dict[str, Any]], str],
) -> str:
    required = {
        "image_id", "image_digest", "signature", "signature_verified",
        "builder", "build_time", "dependency_hash", "runtime_version",
        "security_scan_status",
    }
    if set(payload) != required:
        raise ValueError("IMAGE_MANIFEST_SCHEMA_INVALID")
    if ":" in payload["image_id"] or payload["image_id"].endswith("latest"):
        raise ValueError("LATEST_IMAGE_FORBIDDEN")
    if not _valid_sha256(payload["image_digest"]) or not _valid_sha256(
        payload["dependency_hash"]
    ):
        raise ValueError("IMAGE_DIGEST_INVALID")
    if payload["security_scan_status"] not in {"passed", "failed", "unknown"}:
        raise ValueError("IMAGE_SCAN_STATUS_INVALID")
    snapshot = {key: payload[key] for key in sorted(payload)}
    manifest_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_execution_image_manifests
           (id,image_id,image_digest,signature,signature_verified,builder,
            build_time,dependency_hash,runtime_version,security_scan_status,
            status,manifest_digest,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,'candidate',?,?,?)""",
        (
            manifest_id, payload["image_id"], payload["image_digest"],
            payload["signature"], int(payload["signature_verified"]),
            payload["builder"], payload["build_time"],
            payload["dependency_hash"], payload["runtime_version"],
            payload["security_scan_status"], canonical_digest(snapshot),
            stamp, stamp,
        ),
    )
    db.commit()
    return manifest_id


def transition_execution_image(
    db: sqlite3.Connection, *, manifest_id: str, action: str,
) -> str:
    row = db.execute(
        "SELECT * FROM local_execution_image_manifests WHERE id=?",
        (manifest_id,),
    ).fetchone()
    if row is None:
        raise ValueError("IMAGE_UNKNOWN")
    if action == "approve":
        if row["status"] != "candidate":
            raise ValueError("IMAGE_TRANSITION_INVALID")
        if not row["signature"] or row["signature_verified"] != 1:
            raise ValueError("UNSIGNED_IMAGE")
        if row["security_scan_status"] != "passed":
            raise ValueError("IMAGE_SCAN_NOT_PASSED")
        status = "approved"
    elif action == "deprecate" and row["status"] == "approved":
        status = "deprecated"
    elif action == "revoke" and row["status"] != "revoked":
        status = "revoked"
    else:
        raise ValueError("IMAGE_TRANSITION_INVALID")
    db.execute(
        "UPDATE local_execution_image_manifests SET status=?,updated_at=? WHERE id=?",
        (status, _now(), manifest_id),
    )
    db.commit()
    return status


def evaluate_executor_admission(
    db: sqlite3.Connection, *, executor_id: str, image_manifest_id: str,
    checked_by: str, canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    executor = next(
        (item for item in list_executors(db) if item["id"] == executor_id), None
    )
    image = db.execute(
        "SELECT * FROM local_execution_image_manifests WHERE id=?",
        (image_manifest_id,),
    ).fetchone()
    profile = db.execute(
        """SELECT * FROM local_executor_security_profiles
           WHERE executor_id=? ORDER BY created_at DESC LIMIT 1""",
        (executor_id,),
    ).fetchone()
    reasons = []
    if executor is None or executor["status"] != "active":
        reasons.append("EXECUTOR_NOT_ACTIVE")
    if profile is None or profile["status"] != "valid":
        reasons.append("SECURITY_PROFILE_INVALID")
    else:
        checks = (
            ("network_mode", "none", "NETWORK_NOT_ALLOWED"),
            ("filesystem_mode", "readonly_input", "FILESYSTEM_POLICY_INVALID"),
            ("rootless", 1, "ROOT_PRIVILEGE_FORBIDDEN"),
            ("privileged", 0, "PRIVILEGED_CONTAINER_FORBIDDEN"),
            ("docker_socket_access", 0, "DOCKER_SOCKET_FORBIDDEN"),
            ("runtime_download", 0, "RUNTIME_DOWNLOAD_FORBIDDEN"),
        )
        for field, expected, reason in checks:
            if profile[field] != expected:
                reasons.append(reason)
        try:
            resources = json.loads(profile["resource_policy"])
        except (TypeError, json.JSONDecodeError):
            resources = None
        if not isinstance(resources, dict):
            reasons.append("RESOURCE_POLICY_MISSING")
        else:
            for field, (minimum, maximum) in SECURITY_RESOURCE_LIMITS.items():
                value = resources.get(field)
                if (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or not minimum <= value <= maximum
                ):
                    reasons.append("RESOURCE_POLICY_INVALID")
                    break
    if image is None:
        reasons.append("UNTRUSTED_IMAGE")
    else:
        if image["status"] != "approved":
            reasons.append("UNTRUSTED_IMAGE")
        if not image["signature"] or image["signature_verified"] != 1:
            reasons.append("UNSIGNED_IMAGE")
        if image["security_scan_status"] != "passed":
            reasons.append("IMAGE_SCAN_NOT_PASSED")
        if executor is not None and image["image_digest"] != executor["image_digest"]:
            reasons.append("IMAGE_DIGEST_MISMATCH")
    try:
        resources = (
            json.loads(profile["resource_policy"])
            if profile is not None and profile["resource_policy"] else None
        )
    except (TypeError, json.JSONDecodeError):
        resources = None
    resource_policy_digest = (
        canonical_digest(resources) if isinstance(resources, dict) else None
    )
    checked_at = _now()
    snapshot = {
        "executor_id": executor_id,
        "security_profile_id": profile["id"] if profile else None,
        "image_manifest_id": image_manifest_id,
        "image_manifest_digest": image["manifest_digest"] if image else None,
        "image_digest": image["image_digest"] if image else None,
        "security_profile_digest": profile["profile_digest"] if profile else None,
        "resource_policy_digest": resource_policy_digest,
        "capability_digest": executor["manifest_digest"] if executor else None,
        "rejection_reasons": reasons,
        "execution_enabled": False,
        "checked_at": checked_at,
        "valid_until": (
            datetime.fromisoformat(checked_at) + timedelta(hours=24)
        ).isoformat(),
    }
    result = {
        "id": str(uuid4()),
        "decision": "rejected" if reasons else "approved",
        "rejection_reasons": reasons,
        "admission_digest": canonical_digest(snapshot),
    }
    db.execute(
        """INSERT INTO local_executor_admission_checks
           (id,executor_id,security_profile_id,image_manifest_id,decision,
            rejection_reasons,policy_snapshot,admission_digest,
            execution_enabled,checked_by,checked_at)
           VALUES(?,?,?,?,?,?,?,?,0,?,?)""",
        (
            result["id"], executor_id,
            profile["id"] if profile else None, image_manifest_id,
            result["decision"], json.dumps(reasons),
            json.dumps(snapshot, sort_keys=True), result["admission_digest"],
            checked_by, checked_at,
        ),
    )
    db.commit()
    return result


def _runtime_binding(
    db: sqlite3.Connection, executor_id: str, admission_check_id: str,
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, sqlite3.Row]:
    executor = db.execute(
        "SELECT * FROM local_executors WHERE id=?", (executor_id,)
    ).fetchone()
    admission = db.execute(
        "SELECT * FROM local_executor_admission_checks WHERE id=?",
        (admission_check_id,),
    ).fetchone()
    if admission is None or admission["decision"] != "approved":
        raise ValueError("ADMISSION_NOT_APPROVED")
    if admission["executor_id"] != executor_id:
        raise ValueError("ADMISSION_EXECUTOR_MISMATCH")
    if executor is None or executor["status"] != "active":
        raise ValueError("EXECUTOR_NOT_ACTIVE")
    profile = db.execute(
        "SELECT * FROM local_executor_security_profiles WHERE id=?",
        (admission["security_profile_id"],),
    ).fetchone()
    image = db.execute(
        "SELECT * FROM local_execution_image_manifests WHERE id=?",
        (admission["image_manifest_id"],),
    ).fetchone()
    if profile is None or profile["status"] != "valid":
        raise ValueError("SECURITY_PROFILE_INVALID")
    if image is None or image["status"] != "approved":
        raise ValueError("IMAGE_NOT_APPROVED")
    capability = db.execute(
        """SELECT * FROM local_executor_capability_manifests
           WHERE id=?""",
        (executor["current_capability_manifest_id"],),
    ).fetchone()
    if capability is None or image["image_digest"] != capability["image_digest"]:
        raise ValueError("IMAGE_DIGEST_MISMATCH")
    return executor, admission, profile, image


def prepare_executor_runtime(
    db: sqlite3.Connection, *, executor_id: str, admission_check_id: str,
    sandbox_root: Path, checked_by: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    executor, admission, profile, image = _runtime_binding(
        db, executor_id, admission_check_id
    )
    try:
        resources = json.loads(profile["resource_policy"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("RESOURCE_POLICY_MISSING") from exc
    if set(resources) != set(SECURITY_RESOURCE_LIMITS):
        raise ValueError("RESOURCE_POLICY_MISSING")
    policy = {
        "network_mode": profile["network_mode"],
        "filesystem_mode": profile["filesystem_mode"],
        "rootless": bool(profile["rootless"]),
        "privileged": bool(profile["privileged"]),
        "docker_socket_access": bool(profile["docker_socket_access"]),
        "runtime_download": bool(profile["runtime_download"]),
        "cpu_limit": resources["cpu_cores"],
        "memory_limit_mb": resources["memory_mb"],
        "disk_limit_mb": resources["disk_mb"],
        "timeout_seconds": resources["timeout_seconds"],
        "process_limit": resources["processes"],
        "execution_enabled": False,
    }
    expected = {
        "network_mode": "none", "filesystem_mode": "readonly_input",
        "rootless": True, "privileged": False,
        "docker_socket_access": False, "runtime_download": False,
    }
    if any(policy[key] != value for key, value in expected.items()):
        raise ValueError("RUNTIME_POLICY_INVALID")
    for field, (minimum, maximum) in SECURITY_RESOURCE_LIMITS.items():
        value = resources[field]
        if not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError("RUNTIME_POLICY_INVALID")
    idempotency_digest = canonical_digest(
        {
            "executor_id": executor_id,
            "admission_check_id": admission_check_id,
            "image_manifest_id": image["id"],
            "security_profile_id": profile["id"],
        }
    )
    existing = db.execute(
        "SELECT * FROM local_executor_runtime_sessions WHERE idempotency_digest=?",
        (idempotency_digest,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "status": existing["status"],
            "sandbox_id": existing["sandbox_id"], "created": False,
        }
    root = sandbox_root.resolve()
    if root.drive.upper() == "C:":
        raise ValueError("SANDBOX_ROOT_FORBIDDEN")
    root.mkdir(parents=True, exist_ok=True)
    session_id, sandbox_id, stamp = str(uuid4()), f"sbx-{uuid4()}", _now()
    workspace = (root / sandbox_id).resolve()
    if workspace.parent != root or not workspace.name.startswith("sbx-"):
        raise ValueError("SANDBOX_PATH_INVALID")
    directory_names = ("input", "runtime", "output", "logs")
    try:
        workspace.mkdir(mode=0o700)
        for name in directory_names:
            (workspace / name).mkdir(mode=0o700)
        if any(any((workspace / name).iterdir()) for name in ("input", "output")):
            raise ValueError("SANDBOX_NOT_EMPTY")
        policy_digest = canonical_digest(policy)
        db.execute(
            """INSERT INTO local_executor_runtime_sessions
               (id,executor_id,admission_check_id,runtime_version,
                image_manifest_id,security_profile_id,sandbox_id,status,
                runtime_policy,policy_digest,idempotency_digest,created_at,
                prepared_at)
               VALUES(?,?,?,?,?,?,?,'prepared',?,?,?,?,?)""",
            (
                session_id, executor_id, admission_check_id,
                image["runtime_version"], image["id"], profile["id"],
                sandbox_id, json.dumps(policy, sort_keys=True), policy_digest,
                idempotency_digest, stamp, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_sandbox_workspaces
               (id,runtime_session_id,sandbox_id,relative_reference,
                directory_manifest,status,created_at)
               VALUES(?,?,?,?,?,'prepared',?)""",
            (
                str(uuid4()), session_id, sandbox_id,
                f"sandbox/{sandbox_id}",
                json.dumps(list(directory_names)), stamp,
            ),
        )
        for sequence, (event_type, status) in enumerate(
            (("runtime.created", "created"), ("runtime.admitted", "admitted"),
             ("runtime.prepared", "prepared")),
            start=1,
        ):
            detail = {
                "executor_id": executor_id, "admission_check_id": admission_check_id,
                "sandbox_id": sandbox_id, "policy_digest": policy_digest,
                "checked_by": checked_by, "execution_enabled": False,
            }
            db.execute(
                """INSERT INTO local_runtime_lifecycle_events
                   (id,runtime_session_id,sequence,event_type,status,detail_json,
                    event_digest,occurred_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()), session_id, sequence, event_type, status,
                    json.dumps(detail, sort_keys=True),
                    canonical_digest({**detail, "sequence": sequence,
                                      "event_type": event_type, "status": status}),
                    stamp,
                ),
            )
        db.commit()
    except Exception:
        db.rollback()
        if workspace.exists():
            shutil.rmtree(workspace)
        raise
    return {
        "id": session_id, "status": "prepared",
        "sandbox_id": sandbox_id, "created": True,
    }


def _append_runtime_event(
    db: sqlite3.Connection, *, runtime_session_id: str, event_type: str,
    status: str, detail: dict[str, Any],
    canonical_digest: Callable[[dict[str, Any]], str],
) -> None:
    sequence = db.execute(
        """SELECT COALESCE(MAX(sequence),0)+1 next_sequence
           FROM local_runtime_lifecycle_events WHERE runtime_session_id=?""",
        (runtime_session_id,),
    ).fetchone()["next_sequence"]
    occurred_at = _now()
    db.execute(
        """INSERT INTO local_runtime_lifecycle_events
           (id,runtime_session_id,sequence,event_type,status,detail_json,
            event_digest,occurred_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            str(uuid4()), runtime_session_id, sequence, event_type, status,
            json.dumps(detail, sort_keys=True),
            canonical_digest({
                **detail, "sequence": sequence, "event_type": event_type,
                "status": status, "occurred_at": occurred_at,
            }),
            occurred_at,
        ),
    )


def start_fixed_reference_execution(
    db: sqlite3.Connection, *, runtime_session_id: str, sandbox_root: Path,
    approved_execution_image_digest: str, checked_by: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    session = db.execute(
        """SELECT s.*,i.image_digest,a.decision admission_decision,
                  p.status profile_status,p.resource_policy
             FROM local_executor_runtime_sessions s
             JOIN local_execution_image_manifests i ON i.id=s.image_manifest_id
             JOIN local_executor_admission_checks a ON a.id=s.admission_check_id
             JOIN local_executor_security_profiles p ON p.id=s.security_profile_id
            WHERE s.id=?""",
        (runtime_session_id,),
    ).fetchone()
    if session is None:
        raise ValueError("RUNTIME_SESSION_UNKNOWN")
    existing = db.execute(
        "SELECT * FROM local_reference_executions WHERE runtime_session_id=?",
        (runtime_session_id,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "status": existing["status"],
            "runtime_session_id": runtime_session_id, "created": False,
        }
    if session["status"] != "prepared":
        raise ValueError("RUNTIME_NOT_PREPARED")
    if session["admission_decision"] != "approved":
        raise ValueError("ADMISSION_NOT_APPROVED")
    if session["profile_status"] != "valid":
        raise ValueError("SECURITY_PROFILE_INVALID")
    if (
        not _valid_sha256(approved_execution_image_digest)
        or session["image_digest"] != approved_execution_image_digest
    ):
        raise ValueError("IMAGE_DIGEST_MISMATCH")
    policy = json.loads(session["runtime_policy"])
    if (
        policy.get("network_mode") != "none"
        or policy.get("filesystem_mode") != "readonly_input"
        or policy.get("rootless") is not True
        or policy.get("privileged") is not False
        or policy.get("docker_socket_access") is not False
        or policy.get("runtime_download") is not False
    ):
        raise ValueError("RUNTIME_POLICY_INVALID")
    resource_policy = json.loads(session["resource_policy"])
    if (
        resource_policy.get("cpu_cores", 0) > 2
        or resource_policy.get("memory_mb", 0) > 2048
        or resource_policy.get("disk_mb", 0) > 1024
        or resource_policy.get("processes", 0) > 64
        or resource_policy.get("timeout_seconds", 0) > 900
    ):
        raise ValueError("RESOURCE_POLICY_EXCEEDS_REFERENCE_LIMIT")
    root = sandbox_root.resolve()
    workspace = (root / session["sandbox_id"]).resolve()
    if workspace.parent != root or not workspace.name.startswith("sbx-"):
        raise ValueError("SANDBOX_PATH_INVALID")
    runtime_dir, output_dir = workspace / "runtime", workspace / "output"
    if not runtime_dir.is_dir() or not output_dir.is_dir():
        raise ValueError("SANDBOX_NOT_PREPARED")
    if any(runtime_dir.iterdir()) or any(output_dir.iterdir()):
        raise ValueError("SANDBOX_NOT_EMPTY")

    task_manifest = {
        "schema_version": "phase5.13E-2B-1/task-manifest/v1",
        "task_type": REFERENCE_TASK_TYPE,
        "task_version": REFERENCE_TASK_VERSION,
        "image_digest": approved_execution_image_digest,
        "model_reference": REFERENCE_MODEL,
        "model_digest": REFERENCE_MODEL_DIGEST,
        "dataset_reference": REFERENCE_DATASET,
        "dataset_digest": REFERENCE_DATASET_DIGEST,
        "input_schema": REFERENCE_INPUT_SCHEMA,
        "output_schema": REFERENCE_OUTPUT_SCHEMA,
        "resource_policy": resource_policy,
        "output_allowlist": list(REFERENCE_OUTPUT_FILES),
        "network_mode": "none",
        "rootless": True,
        "non_clinical": True,
    }
    input_manifest = {
        "schema_version": "phase5.13E-2B-1/input-manifest/v1",
        "asset_version_id": REFERENCE_DATASET,
        "metadata_digest": REFERENCE_DATASET_DIGEST,
        "sample_count": 20,
        "schema_digest": canonical_digest({
            "input_schema": REFERENCE_INPUT_SCHEMA,
            "shape": [20, 28, 28, 3], "dtype": "uint8",
        }),
        "fixed_indices": list(REFERENCE_INDICES),
        "fixed_indices_digest": canonical_digest(
            {"fixed_indices": list(REFERENCE_INDICES)}
        ),
    }
    task_digest = canonical_digest(task_manifest)
    input_digest = canonical_digest(input_manifest)
    request_payload = {
        "schema_version": "phase5.13E-2B-1/worker-request/v1",
        "runtime_session_id": runtime_session_id,
        "sandbox_id": session["sandbox_id"],
        "task_manifest": task_manifest,
        "task_digest": task_digest,
        "input_manifest": input_manifest,
        "input_digest": input_digest,
    }
    request_digest = canonical_digest(request_payload)
    request_payload["request_digest"] = request_digest
    execution_id, task_id, input_id, stamp = (
        str(uuid4()), str(uuid4()), str(uuid4()), _now()
    )
    request_tmp = runtime_dir / "request.json.tmp"
    request_path = runtime_dir / "request.json"
    try:
        db.execute(
            """INSERT INTO local_execution_task_manifests
               (id,runtime_session_id,task_type,task_version,image_digest,
                model_reference,dataset_reference,input_schema,output_schema,
                resource_policy,output_allowlist,task_digest,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                task_id, runtime_session_id, REFERENCE_TASK_TYPE,
                REFERENCE_TASK_VERSION, approved_execution_image_digest,
                REFERENCE_MODEL, REFERENCE_DATASET, REFERENCE_INPUT_SCHEMA,
                REFERENCE_OUTPUT_SCHEMA, json.dumps(resource_policy, sort_keys=True),
                json.dumps(list(REFERENCE_OUTPUT_FILES)), task_digest, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_execution_input_manifests
               (id,runtime_session_id,asset_version_id,metadata_digest,
                sample_count,schema_digest,fixed_indices_digest,input_digest,
                created_at)
               VALUES(?,?,?,?,20,?,?,?,?)""",
            (
                input_id, runtime_session_id, REFERENCE_DATASET,
                REFERENCE_DATASET_DIGEST, input_manifest["schema_digest"],
                input_manifest["fixed_indices_digest"], input_digest, stamp,
            ),
        )
        db.execute(
            """INSERT INTO local_reference_executions
               (id,runtime_session_id,task_manifest_id,input_manifest_id,status,
                request_digest,created_at,started_at)
               VALUES(?,?,?,?,'running',?,?,?)""",
            (
                execution_id, runtime_session_id, task_id, input_id,
                request_digest, stamp, stamp,
            ),
        )
        db.execute(
            """UPDATE local_executor_runtime_sessions
               SET status='running',task_digest=?,runtime_digest=?,started_at=?
             WHERE id=? AND status='prepared'""",
            (
                task_digest,
                canonical_digest({
                    "runtime_session_id": runtime_session_id,
                    "image_digest": approved_execution_image_digest,
                    "policy_digest": session["policy_digest"],
                    "task_digest": task_digest,
                }),
                stamp, runtime_session_id,
            ),
        )
        _append_runtime_event(
            db, runtime_session_id=runtime_session_id,
            event_type="runtime.reference_execution_started", status="running",
            detail={
                "execution_id": execution_id, "task_type": REFERENCE_TASK_TYPE,
                "task_digest": task_digest, "input_digest": input_digest,
                "checked_by": checked_by, "reference_execution_only": True,
            },
            canonical_digest=canonical_digest,
        )
        request_tmp.write_text(
            json.dumps(request_payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        request_tmp.replace(request_path)
        db.commit()
    except Exception:
        db.rollback()
        request_tmp.unlink(missing_ok=True)
        request_path.unlink(missing_ok=True)
        raise
    return {
        "id": execution_id, "status": "running",
        "runtime_session_id": runtime_session_id, "created": True,
    }


def _sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return f"sha256:{value.hexdigest()}"


def reconcile_fixed_reference_execution(
    db: sqlite3.Connection, *, runtime_session_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any] | None:
    session = db.execute(
        "SELECT * FROM local_executor_runtime_sessions WHERE id=?",
        (runtime_session_id,),
    ).fetchone()
    execution = db.execute(
        "SELECT * FROM local_reference_executions WHERE runtime_session_id=?",
        (runtime_session_id,),
    ).fetchone()
    if session is None or execution is None or execution["status"] != "running":
        return None
    root = sandbox_root.resolve()
    workspace = (root / session["sandbox_id"]).resolve()
    if workspace.parent != root:
        raise ValueError("SANDBOX_PATH_INVALID")
    result_path = workspace / "runtime" / "result.json"
    if not result_path.is_file():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("EXECUTION_RESULT_INVALID") from exc
    expected_keys = {
        "schema_version", "runtime_session_id", "request_digest", "status",
        "started_at", "completed_at", "output_manifest", "result_digest",
    }
    if set(result) != expected_keys:
        raise ValueError("EXECUTION_RESULT_SCHEMA_INVALID")
    if (
        result["schema_version"] != "phase5.13E-2B-1/worker-result/v1"
        or result["runtime_session_id"] != runtime_session_id
        or result["request_digest"] != execution["request_digest"]
        or result["status"] not in {"completed", "failed"}
    ):
        raise ValueError("EXECUTION_RESULT_BINDING_INVALID")
    unsigned = {key: value for key, value in result.items() if key != "result_digest"}
    if canonical_digest(unsigned) != result["result_digest"]:
        raise ValueError("EXECUTION_RESULT_DIGEST_INVALID")
    if result["status"] == "failed":
        stamp = _now()
        db.execute(
            """UPDATE local_reference_executions
               SET status='failed',result_digest=?,failure_code=?,
                   failed_at=? WHERE id=?""",
            (result["result_digest"], "FIXED_REFERENCE_EXECUTION_FAILED", stamp,
             execution["id"]),
        )
        db.execute(
            """UPDATE local_executor_runtime_sessions
               SET status='failed',failed_at=? WHERE id=?""",
            (stamp, runtime_session_id),
        )
        _append_runtime_event(
            db, runtime_session_id=runtime_session_id,
            event_type="runtime.reference_execution_failed", status="failed",
            detail={
                "execution_id": execution["id"],
                "result_digest": result["result_digest"],
            },
            canonical_digest=canonical_digest,
        )
        db.commit()
        return {"id": execution["id"], "status": "failed", "artifact_id": None}

    manifest = result["output_manifest"]
    if (
        not isinstance(manifest, list)
        or [item.get("name") for item in manifest] != list(REFERENCE_OUTPUT_FILES)
    ):
        raise ValueError("OUTPUT_ALLOWLIST_INVALID")
    output_dir = workspace / "output"
    actual_names = sorted(path.name for path in output_dir.iterdir() if path.is_file())
    if actual_names != sorted(REFERENCE_OUTPUT_FILES):
        raise ValueError("OUTPUT_ALLOWLIST_INVALID")
    normalized_manifest = []
    for item in manifest:
        if set(item) != {"name", "media_type", "size_bytes", "digest"}:
            raise ValueError("OUTPUT_MANIFEST_INVALID")
        path = (output_dir / item["name"]).resolve()
        if path.parent != output_dir.resolve():
            raise ValueError("OUTPUT_PATH_INVALID")
        if (
            path.stat().st_size != item["size_bytes"]
            or _sha256_file(path) != item["digest"]
        ):
            raise ValueError("OUTPUT_DIGEST_MISMATCH")
        normalized_manifest.append(item)
    artifact_digest = canonical_digest({
        "runtime_session_id": runtime_session_id,
        "execution_id": execution["id"],
        "output_manifest": normalized_manifest,
    })
    artifact_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_execution_artifacts
           (id,runtime_session_id,execution_id,artifact_type,status,
            relative_reference,output_manifest,artifact_digest,created_at)
           VALUES(?,?,?,'PATHMNIST_REFERENCE_AGGREGATE','quarantined',?,?,?,?)""",
        (
            artifact_id, runtime_session_id, execution["id"],
            f"sandbox/{session['sandbox_id']}/output",
            json.dumps(normalized_manifest, sort_keys=True), artifact_digest, stamp,
        ),
    )
    db.execute(
        """UPDATE local_reference_executions
           SET status='completed',result_digest=?,completed_at=?
         WHERE id=?""",
        (result["result_digest"], stamp, execution["id"]),
    )
    db.execute(
        """UPDATE local_executor_runtime_sessions
           SET status='completed',completed_at=? WHERE id=?""",
        (stamp, runtime_session_id),
    )
    _append_runtime_event(
        db, runtime_session_id=runtime_session_id,
        event_type="runtime.reference_execution_completed", status="completed",
        detail={
            "execution_id": execution["id"], "artifact_id": artifact_id,
            "artifact_status": "quarantined",
            "artifact_digest": artifact_digest,
            "raw_data_transfer": False, "model_transfer": False,
        },
        canonical_digest=canonical_digest,
    )
    db.commit()
    return {
        "id": execution["id"], "status": "completed",
        "artifact_id": artifact_id, "artifact_status": "quarantined",
    }


def reconcile_authorized_fixed_reference_execution(
    db: sqlite3.Connection, *, runtime_session_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any] | None:
    runtime = db.execute(
        "SELECT * FROM local_authorized_runtime_sessions WHERE id=?",
        (runtime_session_id,),
    ).fetchone()
    execution = db.execute(
        """SELECT * FROM local_authorized_reference_executions
           WHERE runtime_session_id=?""",
        (runtime_session_id,),
    ).fetchone()
    if (
        runtime is None
        or execution is None
        or execution["status"] != "running"
    ):
        return None
    workspace = (
        sandbox_root.resolve() / runtime["sandbox_id"]
    ).resolve()
    if workspace.parent != sandbox_root.resolve():
        raise ValueError("SANDBOX_PATH_INVALID")
    result_path = workspace / "runtime" / "result.json"
    if not result_path.is_file():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("EXECUTION_RESULT_INVALID") from exc
    expected_keys = {
        "schema_version", "runtime_session_id", "request_digest", "status",
        "started_at", "completed_at", "output_manifest", "result_digest",
    }
    unsigned = {
        key: value for key, value in result.items() if key != "result_digest"
    }
    if (
        set(result) != expected_keys
        or result["schema_version"]
        != "phase5.13E-2B-1/worker-result/v1"
        or result["runtime_session_id"] != runtime_session_id
        or result["request_digest"] != execution["request_digest"]
        or canonical_digest(unsigned) != result["result_digest"]
    ):
        raise ValueError("EXECUTION_RESULT_BINDING_INVALID")
    stamp = _now()
    if result["status"] != "completed":
        db.execute(
            """UPDATE local_authorized_reference_executions
               SET status='failed',result_digest=?,failed_at=? WHERE id=?""",
            (result["result_digest"], stamp, execution["id"]),
        )
        db.execute(
            """UPDATE local_authorized_runtime_sessions
               SET status='failed',failed_at=? WHERE id=?""",
            (stamp, runtime_session_id),
        )
        db.commit()
        return {
            "id": execution["id"], "status": "failed", "artifact_id": None
        }
    manifest = result["output_manifest"]
    if (
        not isinstance(manifest, list)
        or [item.get("name") for item in manifest]
        != list(REFERENCE_OUTPUT_FILES)
    ):
        raise ValueError("OUTPUT_ALLOWLIST_INVALID")
    output_dir = workspace / "output"
    actual_names = sorted(
        path.name for path in output_dir.iterdir() if path.is_file()
    )
    if actual_names != sorted(REFERENCE_OUTPUT_FILES):
        raise ValueError("OUTPUT_ALLOWLIST_INVALID")
    normalized_manifest = []
    for item in manifest:
        if set(item) != {"name", "media_type", "size_bytes", "digest"}:
            raise ValueError("OUTPUT_MANIFEST_INVALID")
        path = (output_dir / item["name"]).resolve()
        if (
            path.parent != output_dir.resolve()
            or path.stat().st_size != item["size_bytes"]
            or _sha256_file(path) != item["digest"]
        ):
            raise ValueError("OUTPUT_DIGEST_MISMATCH")
        normalized_manifest.append(item)
    summary = json.loads(
        (output_dir / "execution_summary.json").read_text(encoding="utf-8")
    )
    if (
        summary.get("sample_count") != 20
        or summary.get("processed_count") != 20
        or summary.get("failed_count") != 0
        or summary.get("correct_predictions") != 19
        or summary.get("accuracy") != "0.95"
    ):
        db.execute(
            """UPDATE local_authorized_reference_executions
               SET status='result_mismatch',result_digest=?,failed_at=?
               WHERE id=?""",
            (result["result_digest"], stamp, execution["id"]),
        )
        db.execute(
            """UPDATE local_authorized_runtime_sessions
               SET status='failed',failed_at=? WHERE id=?""",
            (stamp, runtime_session_id),
        )
        db.commit()
        return {
            "id": execution["id"], "status": "result_mismatch",
            "artifact_id": None,
        }
    snapshot = db.execute(
        """SELECT * FROM local_execution_authorization_snapshots
           WHERE id=?""",
        (execution["authorization_snapshot_id"],),
    ).fetchone()
    task = db.execute(
        "SELECT * FROM local_authorized_task_manifests WHERE id=?",
        (execution["task_manifest_id"],),
    ).fetchone()
    output_manifest_digest = canonical_digest(
        {"output_manifest": normalized_manifest}
    )
    artifact_id = str(uuid4())
    artifact_binding = {
        "schema_version":
            "phase5.13E-2C-R1/authorized-artifact-binding/v1",
        "reference_execution_id": execution["id"],
        "execution_result_digest": result["result_digest"],
        "authorization_snapshot_id": snapshot["id"],
        "authorization_snapshot_digest": snapshot["snapshot_digest"],
        "policy_digest": snapshot["policy_digest"],
        "execution_order_digest": snapshot["execution_order_digest"],
        "source_executor_status_event_digest":
            snapshot["source_executor_status_event_digest"],
        "task_manifest_id": task["id"],
        "task_manifest_digest": task["task_digest"],
        "output_manifest_digest": output_manifest_digest,
        "output_schema_digest": snapshot["output_schema_digest"],
    }
    artifact_digest = canonical_digest(artifact_binding)
    db.execute(
        """INSERT INTO local_authorized_execution_artifacts
           (id,execution_id,authorization_snapshot_id,binding_payload,
            relative_reference,output_manifest,artifact_digest,status,
            created_at)
           VALUES(?,?,?,?,?,?,?,'quarantined',?)""",
        (
            artifact_id, execution["id"], snapshot["id"],
            json.dumps(artifact_binding, sort_keys=True),
            f"sandbox/{runtime['sandbox_id']}/output",
            json.dumps(normalized_manifest, sort_keys=True),
            artifact_digest, stamp,
        ),
    )
    db.execute(
        """UPDATE local_authorized_reference_executions
           SET status='completed',result_digest=?,completed_at=? WHERE id=?""",
        (result["result_digest"], stamp, execution["id"]),
    )
    db.execute(
        """UPDATE local_authorized_runtime_sessions
           SET status='completed',completed_at=? WHERE id=?""",
        (stamp, runtime_session_id),
    )
    db.commit()
    return {
        "id": execution["id"], "status": "completed",
        "artifact_id": artifact_id, "artifact_status": "quarantined",
        "sample_count": 20, "correct_count": 19, "accuracy": "0.95",
    }


ARTIFACT_FORBIDDEN_SUFFIXES = {
    ".pth", ".pt", ".onnx", ".h5", ".h5ad", ".svs", ".dcm", ".zip", ".tar",
}
ARTIFACT_FORBIDDEN_TERMS = {
    "patient_id", "medical_record_number", "file_path", "absolute_path",
    "local_path", "raw_filename", "secret", "token", "private_key",
}


def inspect_reference_artifact_output(
    *, output: Path, manifest: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any] | None]:
    findings: list[str] = []
    if not output.is_dir() or output.is_symlink():
        return ["ARTIFACT_PATH_INVALID"], None
    entries = sorted(output.iterdir(), key=lambda item: item.name)
    if any(item.is_symlink() or not item.is_file() for item in entries):
        findings.append("UNSAFE_ARTIFACT_ENTRY")
    paths = [item for item in entries if item.is_file() and not item.is_symlink()]
    names = [path.name for path in paths]
    if names != sorted(REFERENCE_OUTPUT_FILES):
        findings.append("FILE_ALLOWLIST_MISMATCH")
    expected_media = {
        "aggregate_metrics.json": "application/json",
        "confusion_matrix.csv": "text/csv",
        "execution_summary.json": "application/json",
    }
    if (
        not isinstance(manifest, list)
        or len(manifest) != len(REFERENCE_OUTPUT_FILES)
        or any(
            not isinstance(item, dict)
            or set(item) != {"name", "media_type", "size_bytes", "digest"}
            for item in manifest
        )
    ):
        findings.append("OUTPUT_MANIFEST_INVALID")
        manifest_by_name: dict[str, dict[str, Any]] = {}
    else:
        manifest_by_name = {item["name"]: item for item in manifest}
        if set(manifest_by_name) != set(REFERENCE_OUTPUT_FILES):
            findings.append("OUTPUT_MANIFEST_INVALID")
    for path in paths:
        resolved = path.resolve()
        if resolved.parent != output.resolve():
            findings.append("ARTIFACT_PATH_INVALID")
            continue
        if path.suffix.lower() in ARTIFACT_FORBIDDEN_SUFFIXES:
            findings.append("FORBIDDEN_FILE_TYPE")
        if path.stat().st_size > 64 * 1024:
            findings.append("OUTPUT_TOO_LARGE")
            continue
        item = manifest_by_name.get(path.name)
        if (
            item is None
            or item["media_type"] != expected_media.get(path.name)
            or item["size_bytes"] != path.stat().st_size
            or item["digest"] != _sha256_file(path)
        ):
            findings.append("OUTPUT_DIGEST_MISMATCH")
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            findings.append("OUTPUT_ENCODING_INVALID")
            continue
        lowered = text.lower()
        if any(term in lowered for term in ARTIFACT_FORBIDDEN_TERMS):
            findings.append("FORBIDDEN_CONTENT")

    metrics: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    matrix: list[list[int]] | None = None
    try:
        metrics = json.loads(
            (output / "aggregate_metrics.json").read_text(encoding="utf-8")
        )
        summary = json.loads(
            (output / "execution_summary.json").read_text(encoding="utf-8")
        )
        with (output / "confusion_matrix.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.reader(handle))
        if (
            len(rows) != 10
            or any(len(row) != 10 for row in rows)
            or rows[0][0] != "expected/predicted"
        ):
            raise ValueError("matrix shape")
        matrix = [[int(value) for value in row[1:]] for row in rows[1:]]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        findings.append("OUTPUT_SCHEMA_MISMATCH")

    if metrics is not None and summary is not None and matrix is not None:
        if set(metrics) != {
            "schema_version", "sample_count", "accuracy", "mean_confidence",
            "confusion_matrix", "prediction_digest",
        }:
            findings.append("OUTPUT_SCHEMA_MISMATCH")
        if set(summary) != {
            "schema_version", "entrypoint_id", "sample_count",
            "processed_count", "failed_count", "correct_predictions",
            "accuracy", "mean_confidence", "split", "model_digest",
            "dataset_digest", "dataset_digest_after",
            "dataset_digest_unchanged", "model_digest_verified",
            "prediction_digest", "network_access", "inference_only",
            "non_clinical", "unexpected_output_count", "resource_usage",
        }:
            findings.append("OUTPUT_SCHEMA_MISMATCH")
        total = sum(sum(row) for row in matrix)
        correct = sum(matrix[index][index] for index in range(len(matrix)))
        if (
            metrics.get("schema_version")
            != "pathmnist-aggregate-metrics/v1"
            or summary.get("schema_version")
            != "pathmnist-execution-summary/v1"
            or metrics.get("sample_count") != 20
            or summary.get("sample_count") != 20
            or summary.get("processed_count") != 20
            or summary.get("failed_count") != 0
            or summary.get("correct_predictions") != 19
            or summary.get("accuracy") != "0.95"
            or metrics.get("accuracy") != "0.95"
            or total != 20
            or correct != 19
        ):
            findings.append("RESULT_VALUE_MISMATCH")
        if (
            metrics.get("confusion_matrix") != matrix
            or metrics.get("prediction_digest")
            != summary.get("prediction_digest")
            or metrics.get("mean_confidence") != summary.get("mean_confidence")
            or summary.get("dataset_digest")
            != summary.get("dataset_digest_after")
            or summary.get("dataset_digest_unchanged") is not True
            or summary.get("model_digest_verified") is not True
            or summary.get("network_access") is not False
            or summary.get("inference_only") is not True
            or summary.get("non_clinical") is not True
            or summary.get("unexpected_output_count") != 0
        ):
            findings.append("CROSS_FILE_CONSISTENCY_MISMATCH")
    result_summary = None
    if not findings and metrics is not None and summary is not None:
        result_summary = {
            "sample_count": 20,
            "correct_count": 19,
            "accuracy": "0.95",
            "mean_confidence": summary["mean_confidence"],
            "prediction_digest": summary["prediction_digest"],
            "dataset_digest": summary["dataset_digest"],
            "model_digest": summary["model_digest"],
            "non_clinical": True,
            "hard_isolation": False,
        }
    return sorted(set(findings)), result_summary


def scan_local_artifact(
    db: sqlite3.Connection, *, artifact_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    artifact = db.execute(
        """SELECT a.*,s.sandbox_id FROM local_execution_artifacts a
           JOIN local_executor_runtime_sessions s ON s.id=a.runtime_session_id
           WHERE a.id=?""", (artifact_id,),
    ).fetchone()
    if artifact is None:
        raise ValueError("LOCAL_ARTIFACT_UNKNOWN")
    existing = db.execute(
        "SELECT * FROM local_artifact_scan_reports WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        return {"id": existing["id"], "decision": existing["decision"],
                "created": False}
    if artifact["status"] != "quarantined":
        raise ValueError("LOCAL_ARTIFACT_NOT_QUARANTINED")
    root = sandbox_root.resolve()
    output = (root / artifact["sandbox_id"] / "output").resolve()
    if output.parent.parent != root or output.name != "output":
        raise ValueError("ARTIFACT_PATH_INVALID")
    db.execute(
        "UPDATE local_execution_artifacts SET status='scanning',updated_at=? WHERE id=?",
        (_now(), artifact_id),
    )
    findings: list[str] = []
    paths = sorted(path for path in output.iterdir() if path.is_file())
    names = [path.name for path in paths]
    if names != sorted(REFERENCE_OUTPUT_FILES):
        findings.append("FILE_ALLOWLIST_MISMATCH")
    if any(path.suffix.lower() in ARTIFACT_FORBIDDEN_SUFFIXES for path in paths):
        findings.append("FORBIDDEN_FILE_TYPE")
    for path in paths:
        if path.stat().st_size > 64 * 1024:
            findings.append("OUTPUT_TOO_LARGE")
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        lowered = text.lower()
        if any(term in lowered for term in ARTIFACT_FORBIDDEN_TERMS):
            findings.append("FORBIDDEN_CONTENT")
    try:
        metrics = json.loads((output / "aggregate_metrics.json").read_text())
        summary = json.loads((output / "execution_summary.json").read_text())
        if set(metrics) != {
            "schema_version", "sample_count", "accuracy", "mean_confidence",
            "confusion_matrix", "prediction_digest",
        } or metrics.get("sample_count") != 20:
            findings.append("OUTPUT_SCHEMA_MISMATCH")
        if set(summary) != {
            "schema_version", "entrypoint_id", "sample_count",
            "processed_count", "failed_count", "correct_predictions",
            "accuracy", "mean_confidence", "split", "model_digest",
            "dataset_digest", "dataset_digest_after",
            "dataset_digest_unchanged", "model_digest_verified",
            "prediction_digest", "network_access", "inference_only",
            "non_clinical", "unexpected_output_count", "resource_usage",
        }:
            findings.append("OUTPUT_SCHEMA_MISMATCH")
    except (OSError, UnicodeError, json.JSONDecodeError):
        findings.append("OUTPUT_SCHEMA_MISMATCH")
    findings = sorted(set(findings))
    manifest = json.loads(artifact["output_manifest"])
    decision = "failed" if findings else "passed"
    report = {
        "artifact_id": artifact_id, "scanner_version": "phase5.13E-2B-2/v1",
        "decision": decision, "findings": findings,
        "scanned_manifest": manifest,
    }
    report_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_artifact_scan_reports
           (id,artifact_id,scanner_version,decision,findings_json,
            scanned_manifest,scan_digest,scanned_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (report_id, artifact_id, report["scanner_version"], decision,
         json.dumps(findings), json.dumps(manifest, sort_keys=True),
         canonical_digest(report), stamp),
    )
    db.execute(
        """UPDATE local_execution_artifacts SET status=?,updated_at=? WHERE id=?""",
        ("review_pending" if decision == "passed" else "rejected", stamp,
         artifact_id),
    )
    db.commit()
    return {"id": report_id, "decision": decision,
            "findings": findings, "created": True}


def review_local_artifact(
    db: sqlite3.Connection, *, artifact_id: str, reviewer_id: str,
    decision: str, reason: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    if decision not in {"approved", "rejected"}:
        raise ValueError("ARTIFACT_REVIEW_DECISION_INVALID")
    reviewer = db.execute(
        "SELECT role,status FROM local_users WHERE id=?", (reviewer_id,)
    ).fetchone()
    if reviewer is None or reviewer["role"] != "local_artifact_reviewer":
        raise ValueError("ARTIFACT_REVIEWER_ROLE_REQUIRED")
    artifact = db.execute(
        "SELECT * FROM local_execution_artifacts WHERE id=?", (artifact_id,)
    ).fetchone()
    scan = db.execute(
        "SELECT * FROM local_artifact_scan_reports WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if artifact is None or artifact["status"] != "review_pending":
        raise ValueError("ARTIFACT_NOT_REVIEW_PENDING")
    if scan is None or scan["decision"] != "passed":
        raise ValueError("ARTIFACT_SCAN_NOT_PASSED")
    if len(reason.strip()) < 8:
        raise ValueError("ARTIFACT_REVIEW_REASON_REQUIRED")
    payload = {
        "artifact_id": artifact_id, "scan_report_id": scan["id"],
        "artifact_digest": artifact["artifact_digest"],
        "reviewer_id": reviewer_id, "decision": decision,
        "reason": reason.strip(), "evidence_bundle_created": False,
        "central_release": False,
    }
    review_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_artifact_review_decisions
           (id,artifact_id,scan_report_id,reviewer_id,decision,reason,
            review_digest,reviewed_at) VALUES(?,?,?,?,?,?,?,?)""",
        (review_id, artifact_id, scan["id"], reviewer_id, decision,
         reason.strip(), canonical_digest(payload), stamp),
    )
    db.execute(
        "UPDATE local_execution_artifacts SET status=?,updated_at=? WHERE id=?",
        (decision, stamp, artifact_id),
    )
    db.commit()
    return {"id": review_id, "artifact_id": artifact_id, "status": decision}


def _authorized_artifact_context(
    db: sqlite3.Connection, *, artifact_id: str, sandbox_root: Path,
) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row, Path]:
    artifact = db.execute(
        "SELECT * FROM local_authorized_execution_artifacts WHERE id=?",
        (artifact_id,),
    ).fetchone()
    if artifact is None:
        raise ValueError("AUTHORIZED_ARTIFACT_UNKNOWN")
    execution = db.execute(
        "SELECT * FROM local_authorized_reference_executions WHERE id=?",
        (artifact["execution_id"],),
    ).fetchone()
    runtime = (
        db.execute(
            "SELECT * FROM local_authorized_runtime_sessions WHERE id=?",
            (execution["runtime_session_id"],),
        ).fetchone()
        if execution is not None else None
    )
    if execution is None or runtime is None:
        raise ValueError("AUTHORIZED_ARTIFACT_CAUSAL_BINDING_MISSING")
    root = sandbox_root.resolve()
    output = (root / runtime["sandbox_id"] / "output").resolve()
    if output.parent.parent != root or output.name != "output":
        raise ValueError("ARTIFACT_PATH_INVALID")
    return artifact, execution, runtime, output


def scan_authorized_local_artifact(
    db: sqlite3.Connection, *, artifact_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    artifact, _, _, output = _authorized_artifact_context(
        db, artifact_id=artifact_id, sandbox_root=sandbox_root
    )
    existing = db.execute(
        """SELECT * FROM local_authorized_artifact_scan_reports
           WHERE artifact_id=?""",
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "decision": existing["decision"],
            "findings": json.loads(existing["findings_json"]),
            "created": False,
        }
    if artifact["status"] != "quarantined":
        raise ValueError("AUTHORIZED_ARTIFACT_NOT_QUARANTINED")
    manifest = json.loads(artifact["output_manifest"])
    findings, result_summary = inspect_reference_artifact_output(
        output=output, manifest=manifest
    )
    binding = json.loads(artifact["binding_payload"])
    if canonical_digest(binding) != artifact["artifact_digest"]:
        findings.append("ARTIFACT_DIGEST_MISMATCH")
    findings = sorted(set(findings))
    decision = "failed" if findings else "passed"
    report = {
        "schema_version": "phase5.13E-Final/artifact-scan/v1",
        "artifact_id": artifact_id,
        "artifact_digest": artifact["artifact_digest"],
        "scanner_version": "phase5.13E-2B-2/v2",
        "decision": decision,
        "findings": findings,
        "scanned_manifest": manifest,
        "result_summary": result_summary,
    }
    report_id, stamp = str(uuid4()), _now()
    report["scan_report_id"] = report_id
    scan_digest = canonical_digest(report)
    db.execute(
        """INSERT INTO local_authorized_artifact_scan_reports
           (id,artifact_id,scanner_version,decision,findings_json,
            scanned_manifest,scan_digest,scanned_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            report_id, artifact_id, report["scanner_version"], decision,
            json.dumps(findings), json.dumps(manifest, sort_keys=True),
            scan_digest, stamp,
        ),
    )
    db.commit()
    return {
        "id": report_id, "decision": decision, "findings": findings,
        "scan_digest": scan_digest, "result_summary": result_summary,
        "created": True,
    }


def review_authorized_local_artifact(
    db: sqlite3.Connection, *, artifact_id: str, reviewer_id: str,
    decision: str, reason: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    normalized = decision.upper()
    if normalized not in {"APPROVE_FOR_EVIDENCE_CANDIDACY", "REJECT"}:
        raise ValueError("ARTIFACT_REVIEW_DECISION_INVALID")
    reviewer = db.execute(
        "SELECT role,status FROM local_users WHERE id=?", (reviewer_id,)
    ).fetchone()
    if (
        reviewer is None
        or reviewer["status"] != "active"
        or reviewer["role"] != "local_artifact_reviewer"
    ):
        raise ValueError("ARTIFACT_REVIEWER_ROLE_REQUIRED")
    artifact = db.execute(
        "SELECT * FROM local_authorized_execution_artifacts WHERE id=?",
        (artifact_id,),
    ).fetchone()
    scan = db.execute(
        """SELECT * FROM local_authorized_artifact_scan_reports
           WHERE artifact_id=?""",
        (artifact_id,),
    ).fetchone()
    existing = db.execute(
        """SELECT * FROM local_authorized_artifact_review_decisions
           WHERE artifact_id=?""",
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "decision": existing["decision"],
            "review_digest": existing["review_digest"], "created": False,
        }
    if artifact is None or artifact["status"] != "quarantined":
        raise ValueError("AUTHORIZED_ARTIFACT_NOT_QUARANTINED")
    if scan is None or scan["decision"] != "passed":
        raise ValueError("ARTIFACT_SCAN_NOT_PASSED")
    if len(reason.strip()) < 12:
        raise ValueError("ARTIFACT_REVIEW_REASON_REQUIRED")
    review_id, stamp = str(uuid4()), _now()
    payload = {
        "schema_version": "phase5.13E-Final/artifact-review/v1",
        "review_id": review_id,
        "artifact_id": artifact_id,
        "artifact_digest": artifact["artifact_digest"],
        "scan_report_id": scan["id"],
        "scan_digest": scan["scan_digest"],
        "reviewer_id": reviewer_id,
        "reviewer_role": reviewer["role"],
        "decision": normalized,
        "reason": reason.strip(),
        "reviewed_at": stamp,
        "central_override": False,
        "evidence_bundle_created": False,
    }
    review_digest = canonical_digest(payload)
    db.execute(
        """INSERT INTO local_authorized_artifact_review_decisions
           (id,artifact_id,scan_report_id,reviewer_id,decision,reason,
            review_digest,reviewed_at) VALUES(?,?,?,?,?,?,?,?)""",
        (
            review_id, artifact_id, scan["id"], reviewer_id, normalized,
            reason.strip(), review_digest, stamp,
        ),
    )
    db.commit()
    return {
        "id": review_id, "artifact_id": artifact_id,
        "decision": normalized, "review_digest": review_digest,
        "created": True,
    }


def validate_authorized_artifact_causality(
    db: sqlite3.Connection, *, artifact_id: str, sandbox_root: Path,
    canonical_digest: Callable[[dict[str, Any]], str],
    verify_connector_signature: Callable[[dict[str, Any], str], bool],
    verify_policy_signature: Callable[[dict[str, Any], str, str], bool],
    local_audit_valid: bool,
) -> dict[str, Any]:
    artifact, execution, runtime, output = _authorized_artifact_context(
        db, artifact_id=artifact_id, sandbox_root=sandbox_root
    )
    existing = db.execute(
        "SELECT * FROM local_artifact_causal_validations WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "decision": existing["decision"],
            "checks": json.loads(existing["checks_json"]),
            "validation_digest": existing["validation_digest"],
            "created": False,
        }
    scan = db.execute(
        """SELECT * FROM local_authorized_artifact_scan_reports
           WHERE artifact_id=?""", (artifact_id,),
    ).fetchone()
    review = db.execute(
        """SELECT * FROM local_authorized_artifact_review_decisions
           WHERE artifact_id=?""", (artifact_id,),
    ).fetchone()
    if scan is None or scan["decision"] != "passed":
        raise ValueError("ARTIFACT_SCAN_NOT_PASSED")
    if (
        review is None
        or review["decision"] != "APPROVE_FOR_EVIDENCE_CANDIDACY"
    ):
        raise ValueError("ARTIFACT_REVIEW_APPROVAL_REQUIRED")
    task = db.execute(
        "SELECT * FROM local_authorized_task_manifests WHERE id=?",
        (execution["task_manifest_id"],),
    ).fetchone()
    input_manifest = db.execute(
        "SELECT * FROM local_authorized_input_manifests WHERE id=?",
        (execution["input_manifest_id"],),
    ).fetchone()
    snapshot = db.execute(
        "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
        (artifact["authorization_snapshot_id"],),
    ).fetchone()
    order = (
        db.execute(
            "SELECT * FROM local_control_orders WHERE id=?",
            (snapshot["local_order_id"],),
        ).fetchone()
        if snapshot is not None else None
    )
    consumption = (
        db.execute(
            """SELECT * FROM local_execution_consumption_receipts
               WHERE authorization_snapshot_id=?""", (snapshot["id"],),
        ).fetchone()
        if snapshot is not None else None
    )
    rows_present = all(
        row is not None for row in (
            scan, review, task, input_manifest, snapshot, order, consumption
        )
    )
    checks: dict[str, bool] = {
        "artifact_quarantined": artifact["status"] == "quarantined",
        "execution_completed": execution["status"] == "completed",
        "runtime_completed": runtime["status"] == "completed",
        "required_records_present": rows_present,
        "scan_passed": bool(scan and scan["decision"] == "passed"),
        "hospital_review_approved": bool(
            review
            and review["decision"] == "APPROVE_FOR_EVIDENCE_CANDIDACY"
        ),
        "local_audit_valid": local_audit_valid,
    }
    if rows_present:
        artifact_binding = json.loads(artifact["binding_payload"])
        execution_binding = json.loads(execution["binding_payload"])
        runtime_binding = json.loads(runtime["binding_payload"])
        task_binding = json.loads(task["binding_payload"])
        input_binding = json.loads(input_manifest["binding_payload"])
        order_payload = json.loads(order["order_payload"])
        policy_payload = json.loads(order["policy_payload"])
        consumption_payload = json.loads(consumption["payload_json"])
        snapshot_payload = {
            "schema_version":
                "phase5.13E-2C-R1/authorization-snapshot/v1",
            "snapshot_id": snapshot["id"],
            "local_order_id": snapshot["local_order_id"],
            **{
                name: snapshot[name]
                for name in AUTHORIZATION_BINDING_FIELDS
            },
        }
        manifest = json.loads(artifact["output_manifest"])
        findings, result_summary = inspect_reference_artifact_output(
            output=output, manifest=manifest
        )
        scan_payload = {
            "schema_version": "phase5.13E-Final/artifact-scan/v1",
            "artifact_id": artifact_id,
            "artifact_digest": artifact["artifact_digest"],
            "scanner_version": scan["scanner_version"],
            "decision": scan["decision"],
            "findings": json.loads(scan["findings_json"]),
            "scanned_manifest": json.loads(scan["scanned_manifest"]),
            "result_summary": result_summary,
            "scan_report_id": scan["id"],
        }
        review_payload = {
            "schema_version": "phase5.13E-Final/artifact-review/v1",
            "review_id": review["id"],
            "artifact_id": artifact_id,
            "artifact_digest": artifact["artifact_digest"],
            "scan_report_id": scan["id"],
            "scan_digest": scan["scan_digest"],
            "reviewer_id": review["reviewer_id"],
            "reviewer_role": "local_artifact_reviewer",
            "decision": review["decision"],
            "reason": review["reason"],
            "reviewed_at": review["reviewed_at"],
            "central_override": False,
            "evidence_bundle_created": False,
        }
        checks.update({
            "artifact_bytes_valid": not findings,
            "artifact_digest_valid":
                canonical_digest(artifact_binding)
                == artifact["artifact_digest"],
            "scan_digest_valid":
                canonical_digest(scan_payload) == scan["scan_digest"],
            "review_digest_valid":
                canonical_digest(review_payload) == review["review_digest"],
            "task_digest_valid":
                canonical_digest(task_binding) == task["task_digest"],
            "input_digest_valid":
                canonical_digest(input_binding) == input_manifest["input_digest"],
            "runtime_digest_valid":
                canonical_digest(runtime_binding) == runtime["runtime_digest"],
            "request_digest_bound":
                execution_binding.get("request_digest")
                == execution["request_digest"],
            "result_digest_bound":
                artifact_binding.get("execution_result_digest")
                == execution["result_digest"],
            "snapshot_digest_valid":
                canonical_digest(snapshot_payload) == snapshot["snapshot_digest"],
            "snapshot_signature_valid": verify_connector_signature(
                snapshot_payload, snapshot["connector_signature"]
            ),
            "policy_digest_valid":
                canonical_digest(policy_payload) == order["policy_digest"],
            "policy_signature_valid": verify_policy_signature(
                policy_payload, order["policy_signature"],
                order["signing_public_key"],
            ),
            "order_digest_valid":
                canonical_digest(order_payload) == order["order_digest"],
            "order_signature_valid": verify_policy_signature(
                order_payload, order["order_signature"],
                order["signing_public_key"],
            ),
            "snapshot_consumed_once":
                snapshot["status"] == "consumed"
                and snapshot["consumed_at"] is not None
                and order["consumed_count"] == 1,
            "consumption_digest_valid":
                canonical_digest(consumption_payload)
                == consumption["payload_digest"],
            "consumption_signature_valid": verify_connector_signature(
                consumption_payload, consumption["signature"]
            ),
            "consumption_confirmed":
                consumption["delivery_status"] == "delivered"
                and consumption["response_code"] == 200,
            "authorization_ids_bound":
                len({
                    artifact["authorization_snapshot_id"],
                    execution["authorization_snapshot_id"],
                    runtime["authorization_snapshot_id"],
                    task["authorization_snapshot_id"],
                    input_manifest["authorization_snapshot_id"],
                    snapshot["id"],
                    consumption["authorization_snapshot_id"],
                }) == 1,
            "task_ids_bound":
                len({
                    artifact_binding.get("task_manifest_id"),
                    execution["task_manifest_id"],
                    runtime["task_manifest_id"],
                    input_binding.get("task_manifest_id"),
                    task["id"],
                    consumption["task_manifest_id"],
                }) == 1,
            "execution_ids_bound":
                artifact["execution_id"] == execution["id"]
                == consumption["reference_execution_id"],
            "runtime_ids_bound":
                execution["runtime_session_id"] == runtime["id"]
                == consumption["runtime_session_id"],
            "policy_order_digests_bound":
                artifact_binding.get("policy_digest")
                == snapshot["policy_digest"] == order["policy_digest"]
                and artifact_binding.get("execution_order_digest")
                == snapshot["execution_order_digest"] == order["order_digest"],
            "fixed_nonclinical_result":
                bool(result_summary)
                and result_summary["sample_count"] == 20
                and result_summary["correct_count"] == 19
                and result_summary["accuracy"] == "0.95",
            "no_automatic_egress": policy_payload.get(
                "output_policy", {}
            ).get("auto_egress") is False,
            "hard_isolation_false":
                policy_payload.get("hard_isolation") is False,
        })
    failed = sorted(name for name, passed in checks.items() if not passed)
    decision = "passed" if not failed else "failed"
    validation_id, stamp = str(uuid4()), _now()
    payload = {
        "schema_version": "phase5.13E-Final/causal-validation/v1",
        "validation_id": validation_id,
        "artifact_id": artifact_id,
        "artifact_digest": artifact["artifact_digest"],
        "review_id": review["id"] if review else None,
        "checks": checks,
        "failed_checks": failed,
        "decision": decision,
        "validated_at": stamp,
    }
    validation_digest = canonical_digest(payload)
    db.execute(
        """INSERT INTO local_artifact_causal_validations
           (id,artifact_id,review_id,validation_version,decision,checks_json,
            validation_digest,validated_at) VALUES(?,?,?,?,?,?,?,?)""",
        (
            validation_id, artifact_id, review["id"] if review else "",
            payload["schema_version"], decision,
            json.dumps(checks, sort_keys=True), validation_digest, stamp,
        ),
    )
    db.commit()
    return {
        "id": validation_id, "decision": decision, "checks": checks,
        "failed_checks": failed, "validation_digest": validation_digest,
        "created": True,
    }


def create_execution_evidence_bundle(
    db: sqlite3.Connection, *, artifact_id: str, sandbox_root: Path,
    connector_id: str, signing_key_id: str, local_audit_head: str,
    canonical_digest: Callable[[dict[str, Any]], str],
    signer: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    existing = db.execute(
        "SELECT * FROM local_execution_evidence_bundles WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if existing is not None:
        return {
            "id": existing["id"], "bundle_digest": existing["bundle_digest"],
            "payload": json.loads(existing["payload_json"]),
            "signature": existing["signature"],
            "delivery_status": existing["delivery_status"],
            "created": False,
        }
    artifact, execution, runtime, output = _authorized_artifact_context(
        db, artifact_id=artifact_id, sandbox_root=sandbox_root
    )
    scan = db.execute(
        """SELECT * FROM local_authorized_artifact_scan_reports
           WHERE artifact_id=?""", (artifact_id,),
    ).fetchone()
    review = db.execute(
        """SELECT * FROM local_authorized_artifact_review_decisions
           WHERE artifact_id=?""", (artifact_id,),
    ).fetchone()
    validation = db.execute(
        "SELECT * FROM local_artifact_causal_validations WHERE artifact_id=?",
        (artifact_id,),
    ).fetchone()
    if (
        scan is None or scan["decision"] != "passed"
        or review is None
        or review["decision"] != "APPROVE_FOR_EVIDENCE_CANDIDACY"
        or validation is None or validation["decision"] != "passed"
    ):
        raise ValueError("EVIDENCE_BUNDLE_GATES_NOT_SATISFIED")
    snapshot = db.execute(
        "SELECT * FROM local_execution_authorization_snapshots WHERE id=?",
        (artifact["authorization_snapshot_id"],),
    ).fetchone()
    task = db.execute(
        "SELECT * FROM local_authorized_task_manifests WHERE id=?",
        (execution["task_manifest_id"],),
    ).fetchone()
    consumption = db.execute(
        """SELECT * FROM local_execution_consumption_receipts
           WHERE authorization_snapshot_id=?""", (snapshot["id"],),
    ).fetchone()
    order = db.execute(
        "SELECT * FROM local_control_orders WHERE id=?",
        (snapshot["local_order_id"],),
    ).fetchone()
    manifest = json.loads(artifact["output_manifest"])
    findings, result_summary = inspect_reference_artifact_output(
        output=output, manifest=manifest
    )
    if findings or result_summary is None:
        raise ValueError("EVIDENCE_BUNDLE_ARTIFACT_RECHECK_FAILED")
    policy_payload = json.loads(order["policy_payload"])
    bundle_id, stamp = str(uuid4()), _now()
    payload = {
        "schema_version": "phase5.13E-Final/evidence-bundle/v1",
        "bundle_id": bundle_id,
        "bundle_version": 1,
        "connector_id": connector_id,
        "organization_id": policy_payload["organization_id"],
        "task_type": "PATHMNIST_REFERENCE_V1",
        "local_artifact_ref": artifact_id,
        "artifact_digest": artifact["artifact_digest"],
        "policy_bundle_id": snapshot["policy_bundle_id"],
        "policy_bundle_version_id": snapshot["policy_bundle_version_id"],
        "policy_digest": snapshot["policy_digest"],
        "execution_order_id": snapshot["execution_order_id"],
        "execution_order_digest": snapshot["execution_order_digest"],
        "authorization_snapshot_id": snapshot["id"],
        "authorization_snapshot_digest": snapshot["snapshot_digest"],
        "consumption_receipt_digest": consumption["payload_digest"],
        "task_manifest_id": task["id"],
        "task_manifest_digest": task["task_digest"],
        "runtime_session_id": runtime["id"],
        "runtime_digest": runtime["runtime_digest"],
        "reference_execution_id": execution["id"],
        "execution_result_digest": execution["result_digest"],
        "image_digest": snapshot["image_digest"],
        "model_reference_digest": snapshot["model_reference_digest"],
        "dataset_digest": result_summary["dataset_digest"],
        "output_schema_digest": snapshot["output_schema_digest"],
        "output_manifest": manifest,
        "result_summary": result_summary,
        "scan_report_id": scan["id"],
        "scan_digest": scan["scan_digest"],
        "review_id": review["id"],
        "review_digest": review["review_digest"],
        "review_decision": review["decision"],
        "reviewer_role": "local_artifact_reviewer",
        "causal_validation_id": validation["id"],
        "causal_validation_digest": validation["validation_digest"],
        "local_audit_head": local_audit_head,
        "execution_started_at": execution["started_at"],
        "execution_completed_at": execution["completed_at"],
        "quality_limitations": [
            "Fixed 20-sample public PathMNIST engineering reference only.",
            "Not clinical evidence and not a production isolation claim.",
        ],
        "security_boundaries": {
            "network_access": False,
            "raw_data_transfer": False,
            "model_transfer": False,
            "artifact_auto_egress": False,
            "hard_isolation": False,
        },
        "generated_at": stamp,
        "signing_key_id": signing_key_id,
        "nonce": __import__("secrets").token_urlsafe(32),
    }
    bundle_digest = canonical_digest(payload)
    signed_payload = {**payload, "bundle_digest": bundle_digest}
    signature = signer(signed_payload)
    db.execute(
        """INSERT INTO local_execution_evidence_bundles
           (id,artifact_id,review_id,causal_validation_id,bundle_version,
            schema_version,payload_json,bundle_digest,signing_key_id,signature,
            delivery_status,created_at)
           VALUES(?,?,?,?,1,?,?,?,?,?,'pending',?)""",
        (
            bundle_id, artifact_id, review["id"], validation["id"],
            payload["schema_version"],
            json.dumps(signed_payload, sort_keys=True), bundle_digest,
            signing_key_id, signature, stamp,
        ),
    )
    db.commit()
    return {
        "id": bundle_id, "bundle_digest": bundle_digest,
        "payload": signed_payload, "signature": signature,
        "delivery_status": "pending", "created": True,
    }


def record_evidence_bundle_delivery(
    db: sqlite3.Connection, *, bundle_id: str, delivered: bool,
    response_code: int, central_receipt_id: str | None,
) -> None:
    source_states = "('pending','failed')" if delivered else "('pending')"
    db.execute(
        f"""UPDATE local_execution_evidence_bundles
           SET delivery_status=?,response_code=?,central_receipt_id=?,
               delivered_at=?
           WHERE id=? AND delivery_status IN {source_states}""",
        (
            "delivered" if delivered else "failed", response_code,
            central_receipt_id, _now() if delivered else None, bundle_id,
        ),
    )
    db.commit()


def reject_runtime_start(db: sqlite3.Connection, *, runtime_session_id: str) -> None:
    row = db.execute(
        "SELECT status FROM local_executor_runtime_sessions WHERE id=?",
        (runtime_session_id,),
    ).fetchone()
    if row is None:
        raise ValueError("RUNTIME_SESSION_UNKNOWN")
    raise ValueError("RUNTIME_START_FORBIDDEN")


def destroy_executor_runtime(
    db: sqlite3.Connection, *, runtime_session_id: str, sandbox_root: Path,
    checked_by: str, canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    session = db.execute(
        "SELECT * FROM local_executor_runtime_sessions WHERE id=?",
        (runtime_session_id,),
    ).fetchone()
    if session is None:
        raise ValueError("RUNTIME_SESSION_UNKNOWN")
    if session["status"] == "destroyed":
        return {"id": runtime_session_id, "status": "destroyed", "created": False}
    if session["status"] != "prepared":
        raise ValueError("RUNTIME_DESTROY_INVALID_STATE")
    root = sandbox_root.resolve()
    workspace = (root / session["sandbox_id"]).resolve()
    if workspace.parent != root or not workspace.name.startswith("sbx-"):
        raise ValueError("SANDBOX_PATH_INVALID")
    if workspace.exists():
        shutil.rmtree(workspace)
    stamp = _now()
    db.execute(
        """UPDATE local_executor_runtime_sessions
           SET status='destroyed',destroyed_at=? WHERE id=?""",
        (stamp, runtime_session_id),
    )
    db.execute(
        """UPDATE local_sandbox_workspaces
           SET status='destroyed',destroyed_at=? WHERE runtime_session_id=?""",
        (stamp, runtime_session_id),
    )
    sequence = db.execute(
        """SELECT COALESCE(MAX(sequence),0)+1 next_sequence
           FROM local_runtime_lifecycle_events WHERE runtime_session_id=?""",
        (runtime_session_id,),
    ).fetchone()["next_sequence"]
    detail = {
        "runtime_session_id": runtime_session_id,
        "sandbox_id": session["sandbox_id"], "checked_by": checked_by,
        "execution_enabled": False,
    }
    db.execute(
        """INSERT INTO local_runtime_lifecycle_events
           (id,runtime_session_id,sequence,event_type,status,detail_json,
            event_digest,occurred_at)
           VALUES(?,?,?,'runtime.destroyed','destroyed',?,?,?)""",
        (
            str(uuid4()), runtime_session_id, sequence,
            json.dumps(detail, sort_keys=True),
            canonical_digest({**detail, "sequence": sequence,
                              "event_type": "runtime.destroyed",
                              "status": "destroyed"}),
            stamp,
        ),
    )
    db.commit()
    return {"id": runtime_session_id, "status": "destroyed", "created": False}


def create_asset(
    db: sqlite3.Connection, *, connector_id: str, actor_id: str,
    local_asset_key: str, display_name: str, description: str, modality: str,
) -> str:
    stamp, asset_id = _now(), str(uuid4())
    db.execute(
        """INSERT INTO local_asset_descriptors
           (id,connector_id,local_asset_key,display_name,description,asset_kind,modality,
            source_category,sensitivity_classification,status,created_by,created_at,updated_at)
           VALUES(?,?,?,?,?,'dataset',?,'synthetic_metadata','non_sensitive',
                  'draft',?,?,?)""",
        (asset_id, connector_id, local_asset_key, display_name, description, modality,
         actor_id, stamp, stamp),
    )
    db.commit()
    return asset_id


def create_version(
    db: sqlite3.Connection, *, asset_id: str, actor_id: str, version_label: str,
    description: str, dictionary_summary: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> str:
    asset = db.execute(
        "SELECT * FROM local_asset_descriptors WHERE id=?", (asset_id,)
    ).fetchone()
    if not asset or asset["status"] not in {"draft", "local_approved", "rejected", "synced"}:
        raise ValueError("LOCAL_ASSET_VERSION_NOT_EDITABLE")
    prior = db.execute(
        "SELECT id FROM local_asset_versions WHERE asset_id=? ORDER BY created_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    metadata = {
        "display_name": asset["display_name"], "description": description,
        "asset_kind": asset["asset_kind"], "modality": asset["modality"],
        "source_category": asset["source_category"],
        "sensitivity_classification": asset["sensitivity_classification"],
        "data_dictionary_summary": dictionary_summary,
        "disclosure_policy": "approved_summary_only",
    }
    version_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_asset_versions
           (id,asset_id,version_label,schema_version,metadata_payload,metadata_digest,
            schema_digest,created_by,created_at,supersedes_version_id,is_current)
           VALUES(?,?,?,'phase5.13C/local-asset/v1',?,?,?,?,?,?,1)""",
        (version_id, asset_id, version_label, json.dumps(metadata),
         canonical_digest(metadata),
         canonical_digest({"fields": sorted(metadata), "schema": "phase5.13C/local-asset/v1"}),
         actor_id, stamp, prior["id"] if prior else None),
    )
    db.execute(
        "UPDATE local_asset_descriptors SET current_version_id=?,status='draft',updated_at=? WHERE id=?",
        (version_id, stamp, asset_id),
    )
    db.commit()
    return version_id


def create_quality_profile(
    db: sqlite3.Connection, *, version_id: str, actor_id: str,
    completeness: int, uniqueness: int, consistency: int, validity: int,
    timeliness: int, known_limitations: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> str:
    if not db.execute("SELECT id FROM local_asset_versions WHERE id=?", (version_id,)).fetchone():
        raise ValueError("LOCAL_ASSET_VERSION_NOT_FOUND")
    existing = db.execute(
        "SELECT id FROM local_data_quality_profiles WHERE asset_version_id=?", (version_id,)
    ).fetchone()
    if existing:
        raise ValueError("LOCAL_QUALITY_PROFILE_IMMUTABLE")
    summary = {
        "completeness": completeness, "uniqueness": uniqueness,
        "consistency": consistency, "validity": validity, "timeliness": timeliness,
        "missingness": "summary_only", "format": "declared",
        "coding": "not_assessed",
    }
    limitations = [line.strip() for line in known_limitations.splitlines() if line.strip()]
    disclosure = {
        "prohibited_field_scan": "passed", "raw_data_included": False,
        "local_path_included": False, "patient_identifier_included": False,
        "raw_filename_included": False,
    }
    profile_id, stamp = str(uuid4()), _now()
    db.execute(
        """INSERT INTO local_data_quality_profiles
           (id,asset_version_id,profile_version,assessment_scope,assessed_at,assessed_by,
            method_version,disclosure_summary,quality_summary,known_limitations,
            warning_flags,fitness_for_use_status,quality_digest,status,created_at)
           VALUES(?,?,'1','metadata-only',?,?,'phase5.13C-minimal-profile/v1',
                  ?,?,?,?,'pending_review',?,'draft',?)""",
        (profile_id, version_id, stamp, actor_id, json.dumps(disclosure),
         json.dumps(summary), json.dumps(limitations),
         json.dumps(["metadata_only", "not_executable"]),
         canonical_digest(summary),
         stamp),
    )
    db.commit()
    return profile_id


def list_assets(db: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = db.execute("""
      SELECT d.*, v.version_label, v.metadata_digest, q.quality_digest,
             q.fitness_for_use_status, q.disclosure_summary, q.quality_summary,
             q.known_limitations, q.warning_flags
      FROM local_asset_descriptors d
      LEFT JOIN local_asset_versions v ON v.id=d.current_version_id
      LEFT JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
      ORDER BY d.created_at, q.created_at DESC
    """).fetchall()
    seen: set[str] = set()
    result = []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        item = dict(row)
        for key in ("disclosure_summary", "quality_summary", "known_limitations", "warning_flags"):
            item[key] = json.loads(item[key]) if item.get(key) else None
        result.append(item)
    return result


def seed_public_fixture(
    db: sqlite3.Connection,
    *,
    connector_id: str,
    canonical_digest: Callable[[dict[str, Any]], str],
) -> dict[str, int]:
    existing = db.execute(
        "SELECT id FROM local_asset_descriptors WHERE connector_id=? AND local_asset_key=?",
        (connector_id, "pathmnist-fixed-20"),
    ).fetchone()
    if existing:
        return {"assets": 1, "versions": 2, "quality_profiles": 2, "bundles": 2}
    stamp = _now()
    asset_id = str(uuid4())
    db.execute("""
      INSERT INTO local_asset_descriptors
      (id,connector_id,local_asset_key,display_name,description,asset_kind,modality,
       source_category,sensitivity_classification,status,created_by,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        asset_id, connector_id, "pathmnist-fixed-20",
        "PathMNIST Fixed 20-Sample Local Demo Asset",
        "Public benchmark metadata fixture; no image content is copied or synchronized.",
        "dataset", "digital_pathology", "public_benchmark", "public",
        "local_approved", "local.curator", stamp, stamp,
    ))
    prior_version = None
    for sequence, version_label in enumerate(("2026.07-a", "2026.07-b"), start=1):
        version_id = str(uuid4())
        metadata = {
            "display_name": "PathMNIST Fixed 20-Sample Local Demo Asset",
            "description": "Metadata-only public benchmark fixture.",
            "asset_kind": "dataset", "modality": "digital_pathology",
            "data_object_type": "image_patch", "disease_areas": ["colorectal_pathology"],
            "organs": ["colon"], "species": "human-derived public benchmark",
            "source_category": "public_benchmark",
            "sensitivity_classification": "public",
            "disclosure_policy": "approved_summary_only",
            "version_note": f"Local metadata fixture revision {sequence}.",
        }
        metadata_digest = canonical_digest(metadata)
        schema_digest = canonical_digest({"fields": sorted(metadata), "schema": "phase5.13C/local-asset/v1"})
        db.execute("""
          INSERT INTO local_asset_versions
          (id,asset_id,version_label,schema_version,metadata_payload,metadata_digest,
           schema_digest,created_by,created_at,supersedes_version_id,is_current)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (
            version_id, asset_id, version_label, "phase5.13C/local-asset/v1",
            json.dumps(metadata, ensure_ascii=False), metadata_digest, schema_digest,
            "local.curator", stamp, prior_version, 1 if sequence == 2 else 0,
        ))
        db.execute("""
          INSERT INTO local_asset_location_refs
          (id,asset_version_id,storage_backend,location_alias,encrypted_location_reference,
           location_digest,access_mode,available,last_checked_at,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)
        """, (
            str(uuid4()), version_id, "fixture", "fixture://pathmnist-demo-20",
            None, canonical_digest({"alias": "fixture://pathmnist-demo-20"}),
            "metadata_reference_only", 1, stamp, stamp,
        ))
        disclosure = {
            "record_count": {"mode": "exact", "value": 20, "lower_bound": None, "upper_bound": None, "reason": "public fixture"},
            "patient_count": {"mode": "not_applicable", "value": None, "lower_bound": None, "upper_bound": None, "reason": "no patient-level registry"},
            "file_count": {"mode": "suppressed", "value": None, "lower_bound": None, "upper_bound": None, "reason": "object-level names are not metadata"},
            "date_range": {"mode": "unknown", "value": None, "lower_bound": None, "upper_bound": None, "reason": "not supplied by fixture"},
        }
        quality = {
            "assessment_scope": "metadata fixture only",
            "method_version": "phase5.13C-minimal-profile/v1",
            "completeness_summary": {"status": "reviewed", "note": "required fixture metadata present"},
            "uniqueness_summary": {"status": "not_assessed"},
            "consistency_summary": {"status": "reviewed", "note": "declared count and fixture definition agree"},
            "validity_summary": {"status": "reviewed", "note": "schema validation passed"},
            "timeliness_summary": {"status": "not_applicable"},
            "missingness_summary": {"status": "not_assessed"},
            "format_summary": {"status": "reviewed", "note": "metadata JSON only"},
            "coding_summary": {"status": "not_assessed"},
            "fitness_for_use_status": "locally_reviewed",
            "decision_scope": "Only for the specified demonstration use; not a general high-quality certification.",
        }
        quality_id = str(uuid4())
        quality_digest = canonical_digest(quality)
        limitations = ["No raw images were inspected.", "No clinical fitness or legal compliance determination."]
        warnings = ["metadata_only", "public_fixture", "execution_not_permitted"]
        db.execute("""
          INSERT INTO local_data_quality_profiles
          (id,asset_version_id,profile_version,assessment_scope,assessed_at,assessed_by,
           method_version,disclosure_summary,quality_summary,known_limitations,warning_flags,
           fitness_for_use_status,quality_digest,status,supersedes_profile_id,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            quality_id, version_id, f"q-{sequence}", "metadata fixture only", stamp,
            "local.curator", "phase5.13C-minimal-profile/v1",
            json.dumps(disclosure), json.dumps(quality), json.dumps(limitations),
            json.dumps(warnings), "locally_reviewed", quality_digest, "approved",
            None, stamp,
        ))
        db.execute("""
          INSERT INTO local_asset_reviews
          (id,asset_version_id,quality_profile_id,reviewer,decision,reason,reviewed_at)
          VALUES(?,?,?,?,?,?,?)
        """, (
            str(uuid4()), version_id, quality_id, "local.reviewer", "approved",
            "Public metadata fixture approved for metadata-only demonstration sync.", stamp,
        ))
        bundle_id = f"bundle-{uuid4()}"
        payload = {
            "schema_version": "phase5.13C/metadata-bundle/v1",
            "bundle_id": bundle_id, "bundle_sequence": sequence,
            "local_asset_key": "pathmnist-fixed-20", "version_label": version_label,
            "metadata_summary": metadata, "disclosure_summary": disclosure,
            "quality_summary": quality,
            "deidentification_summary": {
                "status": "not_applicable", "method_name": None, "method_version": None,
                "reversible": None, "key_holder_role": None,
                "reidentification_risk_status": "not_assessed",
                "independent_review_status": "not_applicable",
                "limitations": "Public benchmark metadata fixture; no individual records synchronized.",
            },
            "known_limitations": limitations, "warning_flags": warnings,
            "metadata_digest": metadata_digest, "schema_digest": schema_digest,
            "quality_digest": quality_digest, "signed_at": stamp,
            "nonce": uuid4().hex + uuid4().hex,
        }
        payload["bundle_digest"] = canonical_digest(payload)
        db.execute("""
          INSERT INTO local_asset_metadata_bundles
          (id,asset_version_id,bundle_sequence,payload_json,bundle_digest,status,created_at)
          VALUES(?,?,?,?,?,?,?)
        """, (
            bundle_id, version_id, sequence, json.dumps(payload, ensure_ascii=False),
            payload["bundle_digest"], "approved", stamp,
        ))
        prior_version = version_id
    db.execute(
        "UPDATE local_asset_descriptors SET current_version_id=?,status='sync_pending',updated_at=? WHERE id=?",
        (prior_version, stamp, asset_id),
    )
    db.commit()
    return {"assets": 1, "versions": 2, "quality_profiles": 2, "bundles": 2}

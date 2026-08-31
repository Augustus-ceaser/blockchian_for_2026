from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.registry import (
    consume_execution_authorization,
    migrate,
    record_execution_consumption_delivery,
)


def digest(value: dict) -> str:
    import hashlib
    import json

    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":")
    ).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def snapshot_values(snapshot_id: str) -> list[object]:
    return [
        snapshot_id, "order-2", "connector-1", "executor-1", "policy-1",
        "version-1", "sha256:policy", "sha256:readiness", "central-order-2",
        "sha256:order", "receipt-1", "sha256:receipt", "decision-1",
        "sha256:decision", "admission-1", "sha256:admission", "asset-1",
        "sha256:metadata", "sha256:quality", "sha256:model", "image-1",
        "sha256:image", "sha256:security", "sha256:resource", "sha256:task",
        "sha256:output", "PATHMNIST_REFERENCE_V1", 1,
        "2026-07-29T00:00:00+00:00", "2099-07-30T00:00:00+00:00",
        f"sha256:{snapshot_id}", "signature", "validated", None,
        "status-event-1", "sha256:status", "sha256:capability",
        "sha256:input",
    ]


def binding(snapshot_id: str) -> dict[str, str]:
    return {
        "authorization_snapshot_id": snapshot_id,
        "authorization_snapshot_digest": f"sha256:{snapshot_id}",
        "policy_digest": "sha256:policy",
        "execution_order_digest": "sha256:order",
        "connector_decision_digest": "sha256:decision",
        "admission_check_digest": "sha256:admission",
        "image_digest": "sha256:image",
        "task_definition_digest": "sha256:task",
        "output_schema_digest": "sha256:output",
    }


def insert_validated_snapshot(
    db: sqlite3.Connection, snapshot_id: str
) -> None:
    values = snapshot_values(snapshot_id)
    db.execute(
        "INSERT INTO local_execution_authorization_snapshots VALUES("
        + ",".join("?" for _ in values)
        + ")",
        values,
    )
    db.commit()


def test_authorized_execution_schema_is_parallel_and_immutable() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)

    tables = {
        row["name"]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {
        "local_execution_evidence_eligibility_assessments",
        "local_execution_authorization_snapshots",
            "local_authorized_task_manifests",
            "local_authorized_input_manifests",
            "local_authorized_runtime_sessions",
            "local_authorized_reference_executions",
            "local_authorized_execution_artifacts",
            "local_authorized_execution_dispatches",
            "local_execution_consumption_receipts",
            "local_authorized_artifact_scan_reports",
            "local_authorized_artifact_review_decisions",
            "local_artifact_causal_validations",
            "local_execution_evidence_bundles",
    } <= tables
    assert (
        db.execute(
            "SELECT version FROM local_schema_migrations ORDER BY version DESC"
        ).fetchone()["version"]
        == "phase5.13E_0012"
    )


def test_delivery_retry_transitions_preserve_signed_consumption_receipt() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    db.execute(
        """INSERT INTO local_execution_consumption_receipts
           (id,local_order_id,authorization_snapshot_id,task_manifest_id,
            runtime_session_id,reference_execution_id,payload_json,
            payload_digest,signature,delivery_status,response_code,created_at)
           VALUES('receipt-1','order-1','snapshot-1','task-1','runtime-1',
                  'execution-1','{}','sha256:receipt','signature','pending',
                  NULL,'2026-07-30T00:00:00+00:00')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET response_code=102 WHERE id='receipt-1'"""
        )
    assert record_execution_consumption_delivery(
        db, receipt_id="receipt-1", delivered=False, response_code=503,
    ) == "failed"
    assert record_execution_consumption_delivery(
        db, receipt_id="receipt-1", delivered=False, response_code=504,
    ) == "failed"
    assert db.execute(
        "SELECT response_code FROM local_execution_consumption_receipts"
    ).fetchone()[0] == 503
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET response_code=504 WHERE id='receipt-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET payload_json='tampered' WHERE id='receipt-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET delivery_status='pending' WHERE id='receipt-1'"""
        )
    db.execute(
        """UPDATE local_execution_consumption_receipts
           SET delivery_status='delivered',response_code=200,
               delivered_at='2026-07-30T00:01:00+00:00'
           WHERE id='receipt-1'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET response_code=201 WHERE id='receipt-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_consumption_receipts
               SET delivery_status='delivered' WHERE id='receipt-1'"""
        )


def test_dispatch_gate_payload_is_immutable_and_dispatched_is_terminal() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    db.execute(
        """INSERT INTO local_authorized_execution_dispatches
           (reference_execution_id,authorization_snapshot_id,
            runtime_session_id,payload_json,request_digest,status,created_at)
           VALUES('execution-1','snapshot-1','runtime-1','{}',
                  'sha256:request','pending','2026-07-30T00:00:00+00:00')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_authorized_execution_dispatches
               SET payload_json='tampered' WHERE reference_execution_id='execution-1'"""
        )
    db.execute(
        """UPDATE local_authorized_execution_dispatches
           SET status='dispatched',dispatched_at='2026-07-30T00:01:00+00:00'
           WHERE reference_execution_id='execution-1'"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_authorized_execution_dispatches
               SET status='pending',dispatched_at=NULL
               WHERE reference_execution_id='execution-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_authorized_execution_dispatches
               SET dispatched_at='2026-07-30T00:02:00+00:00'
               WHERE reference_execution_id='execution-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_authorized_execution_dispatches
               SET status='dispatched'
               WHERE reference_execution_id='execution-1'"""
        )


def test_signed_order_receipt_and_decision_bindings_are_immutable() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    stamp = "2026-07-30T00:00:00+00:00"
    db.execute(
        """INSERT INTO local_control_orders
           (id,central_order_id,connector_sequence,order_payload,order_digest,
            order_signature,policy_payload,policy_digest,policy_signature,
            signing_key_id,signing_public_key,signing_key_fingerprint,
            central_status,local_status,received_at,expires_at)
           VALUES('order-1','central-1',1,'{}','sha256:order','order-signature',
                  '{}','sha256:policy','policy-signature','key-1','public-key',
                  'sha256:key','available_for_connector',
                  'awaiting_local_review',?,?)""",
        (stamp, "2099-07-30T00:00:00+00:00"),
    )
    db.execute(
        "UPDATE local_control_orders SET local_status='accepted' "
        "WHERE id='order-1'"
    )
    db.execute(
        "UPDATE local_control_orders SET consumed_count=1 WHERE id='order-1'"
    )
    for statement in (
        "UPDATE local_control_orders SET order_payload='tampered' WHERE id='order-1'",
        "UPDATE local_control_orders SET local_status='awaiting_local_review' WHERE id='order-1'",
        "UPDATE local_control_orders SET consumed_count=0 WHERE id='order-1'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(statement)
    db.execute(
        """INSERT INTO local_order_receipts
           VALUES('receipt-1','order-1','{}','sha256:receipt','signature',
                  'pending',?,NULL)""", (stamp,),
    )
    db.execute(
        """UPDATE local_order_receipts
           SET delivery_status='delivered',delivered_at=?
           WHERE id='receipt-1'""", (stamp,),
    )
    db.execute(
        """INSERT INTO local_order_decisions
           VALUES('decision-1','order-1','{}','sha256:decision','signature',
                  'pending',?,NULL)""", (stamp,),
    )
    db.execute(
        """UPDATE local_order_decisions SET delivery_status='failed'
           WHERE id='decision-1'"""
    )
    for table, identifier in (
        ("local_order_receipts", "receipt-1"),
        ("local_order_decisions", "decision-1"),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute(
                f"UPDATE {table} SET payload_json='tampered' WHERE id=?",
                (identifier,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(f"DELETE FROM {table} WHERE id=?", (identifier,))
    db.execute(
        """INSERT INTO local_policy_validations
           VALUES('validation-1','order-1','passed','[]',NULL,?)""",
        (stamp,),
    )
    db.execute(
        """INSERT INTO local_policy_reviews
           VALUES('review-1','order-1','reviewer-1','accepted','ACCEPT',
                  'reviewed',?)""", (stamp,),
    )
    for table in ("local_policy_validations", "local_policy_reviews"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(f"DELETE FROM {table}")


def test_authorized_start_maps_central_event_by_signed_digest() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "registry.py"
    ).read_text(encoding="utf-8")

    assert (
        "WHERE executor_id=? AND payload_digest=?"
        in source
    )
    assert (
        'snapshot["executor_id"],\n'
        '                snapshot["source_executor_status_event_digest"]'
        in source
    )
    assert (
        "current_attestation[\"id\"] != attestation[\"id\"]"
        in source
    )
    task_spread = source.index("**task_binding")
    reference_schema = source.index(
        '"phase5.13E-2C-R1/authorized-reference-execution/v1"',
        task_spread,
    )
    assert task_spread < reference_schema
    main_source = (
        Path(__file__).resolve().parents[1] / "app" / "main.py"
    ).read_text(encoding="utf-8")
    assert "Fixed non-clinical reference:" in main_source
    assert "correct_predictions" in main_source
    assert "summary_path.is_file()" in main_source


def test_old_execution_eligibility_assessment_is_append_only() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    db.execute(
        """INSERT INTO local_execution_evidence_eligibility_assessments
           VALUES('assessment-1','old-execution','old-artifact',0,
                  'MISSING_PRE_EXECUTION_AUTHORIZATION_BINDING',
                  'sha256:assessment','2026-07-29T00:00:00+00:00')"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            """UPDATE local_execution_evidence_eligibility_assessments
               SET artifact_id='replacement' WHERE id='assessment-1'"""
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute(
            "DELETE FROM local_execution_evidence_eligibility_assessments"
        )


def test_consumed_authorization_snapshot_cannot_be_restored() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    values = [
        "snapshot-1", "order-1", "connector-1", "executor-1", "policy-1",
        "version-1", "sha256:policy", "sha256:readiness", "central-order-1",
        "sha256:order", "receipt-1", "sha256:receipt", "decision-1",
        "sha256:decision", "admission-1", "sha256:admission", "asset-1",
        "sha256:metadata", "sha256:quality", "sha256:model", "image-1",
        "sha256:image", "sha256:security", "sha256:resource", "sha256:task",
        "sha256:output", "PATHMNIST_REFERENCE_V1", 1,
        "2026-07-29T00:00:00+00:00", "2026-07-30T00:00:00+00:00",
        "sha256:snapshot", "signature", "consumed",
        "2026-07-29T01:00:00+00:00",
        "status-event-1", "sha256:status", "sha256:capability",
        "sha256:input",
    ]
    placeholders = ",".join("?" for _ in values)
    db.execute(
        "INSERT INTO local_execution_authorization_snapshots VALUES("
        + placeholders
        + ")",
        values,
    )
    with pytest.raises(sqlite3.IntegrityError, match="terminal"):
        db.execute(
            """UPDATE local_execution_authorization_snapshots
               SET status='validated' WHERE id='snapshot-1'"""
        )


def test_authorization_consumption_is_atomic_and_single_use() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    values = [
        "snapshot-2", "order-2", "connector-1", "executor-1", "policy-1",
        "version-1", "sha256:policy", "sha256:readiness", "central-order-2",
        "sha256:order", "receipt-1", "sha256:receipt", "decision-1",
        "sha256:decision", "admission-1", "sha256:admission", "asset-1",
        "sha256:metadata", "sha256:quality", "sha256:model", "image-1",
        "sha256:image", "sha256:security", "sha256:resource", "sha256:task",
        "sha256:output", "PATHMNIST_REFERENCE_V1", 1,
        "2026-07-29T00:00:00+00:00", "2099-07-30T00:00:00+00:00",
        "sha256:snapshot", "signature", "validated", None,
        "status-event-1", "sha256:status", "sha256:capability",
        "sha256:input",
    ]
    db.execute(
        "INSERT INTO local_execution_authorization_snapshots VALUES("
        + ",".join("?" for _ in values)
        + ")",
        values,
    )
    db.commit()
    binding = {
        "authorization_snapshot_id": "snapshot-2",
        "authorization_snapshot_digest": "sha256:snapshot",
        "policy_digest": "sha256:policy",
        "execution_order_digest": "sha256:order",
        "connector_decision_digest": "sha256:decision",
        "admission_check_digest": "sha256:admission",
        "image_digest": "sha256:image",
        "task_definition_digest": "sha256:task",
        "output_schema_digest": "sha256:output",
    }
    result = consume_execution_authorization(
        db, snapshot_id="snapshot-2", binding_payload=binding,
        canonical_digest=digest,
    )
    assert result["status"] == "consumed"
    assert db.execute(
        "SELECT count(*) FROM local_authorized_task_manifests"
    ).fetchone()[0] == 1
    with pytest.raises(
        ValueError, match="EXECUTION_AUTHORIZATION_ALREADY_CONSUMED"
    ):
        consume_execution_authorization(
            db, snapshot_id="snapshot-2", binding_payload=binding,
            canonical_digest=digest,
        )
    assert db.execute(
        "SELECT count(*) FROM local_authorized_task_manifests"
    ).fetchone()[0] == 1


def test_concurrent_authorization_consumption_has_one_winner(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "connector.sqlite3"
    setup = sqlite3.connect(database_path)
    setup.row_factory = sqlite3.Row
    migrate(setup)
    insert_validated_snapshot(setup, "snapshot-race")
    setup.close()

    def consume() -> str:
        db = sqlite3.connect(database_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            consume_execution_authorization(
                db,
                snapshot_id="snapshot-race",
                binding_payload=binding("snapshot-race"),
                canonical_digest=digest,
            )
            return "consumed"
        except ValueError as exc:
            return str(exc)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: consume(), range(2)))

    assert results.count("consumed") == 1
    assert results.count("EXECUTION_AUTHORIZATION_ALREADY_CONSUMED") == 1
    verify = sqlite3.connect(database_path)
    assert verify.execute(
        "SELECT count(*) FROM local_authorized_task_manifests"
    ).fetchone()[0] == 1
    assert verify.execute(
        """SELECT status FROM local_execution_authorization_snapshots
           WHERE id='snapshot-race'"""
    ).fetchone()[0] == "consumed"


def test_authorization_consumption_rolls_back_partial_task() -> None:
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    migrate(db)
    insert_validated_snapshot(db, "snapshot-rollback")
    db.execute(
        """CREATE TRIGGER force_consume_failure
           BEFORE UPDATE ON local_execution_authorization_snapshots
           WHEN NEW.status='consumed'
           BEGIN
             SELECT RAISE(ABORT, 'forced consume failure');
           END"""
    )
    db.commit()

    with pytest.raises(sqlite3.IntegrityError, match="forced consume failure"):
        consume_execution_authorization(
            db,
            snapshot_id="snapshot-rollback",
            binding_payload=binding("snapshot-rollback"),
            canonical_digest=digest,
        )

    assert db.execute(
        "SELECT count(*) FROM local_authorized_task_manifests"
    ).fetchone()[0] == 0
    snapshot = db.execute(
        """SELECT status,consumed_at
           FROM local_execution_authorization_snapshots
           WHERE id='snapshot-rollback'"""
    ).fetchone()
    assert snapshot["status"] == "validated"
    assert snapshot["consumed_at"] is None

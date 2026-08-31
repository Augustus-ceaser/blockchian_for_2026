from __future__ import annotations

import copy
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.registry import (
    bootstrap_users,
    create_execution_authorization_snapshot_from_order,
    dispatch_authorized_fixed_reference_execution,
    migrate,
    record_execution_consumption_delivery,
    start_authorized_fixed_reference_execution,
)
from tests.test_fixed_policy_order_snapshot import fixed_item


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _PullClient:
    def __init__(self, item_box: dict[str, dict]) -> None:
        self.item_box = item_box

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def get(self, *_args, **_kwargs) -> _Response:
        return _Response({"items": [copy.deepcopy(self.item_box["item"])]})


def _login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/local/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def _fixed_web_client(tmp_path: Path, monkeypatch):
    db, item = fixed_item(tmp_path, monkeypatch)
    database_path = Path(
        db.execute("PRAGMA database_list").fetchone()["file"]
    )
    db.execute(
        "INSERT OR REPLACE INTO state(key,value) VALUES(?,?)",
        ("connector_status", json.dumps("active")),
    )
    bootstrap_users(db, "", "", "", "", "", "operator-password")
    db.commit()
    db.close()
    main.STATE_DB = database_path
    item_box = {"item": item}
    monkeypatch.setattr(main, "client", lambda: _PullClient(item_box))
    monkeypatch.setattr(main, "deliver_signed_message", lambda *_args: True)
    monkeypatch.setattr(
        main,
        "sign_connector_payload",
        lambda payload: "signed:" + main.canonical_digest(payload),
    )
    web = TestClient(main.app)
    _login(web, "local.policy-reviewer", "policy")
    return web, item_box, database_path


def _pull(web: TestClient) -> None:
    response = web.post("/local/orders/pull", follow_redirects=False)
    assert response.status_code == 303, response.text


def _accept(web: TestClient, order_id: str) -> str:
    _pull(web)
    response = web.post(
        f"/local/orders/{order_id}/decision",
        data={
            "decision": "accepted",
            "reason_code": "ACCEPT_FIXED_REFERENCE_EXECUTION",
            "reason_text": "Fixed reference authorization reviewed.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    with main.connect() as db:
        return db.execute(
            "SELECT id FROM local_execution_authorization_snapshots "
            "WHERE local_order_id=?", (order_id,),
        ).fetchone()["id"]


def test_first_seen_revoked_order_never_enters_review(
    tmp_path: Path, monkeypatch,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    item_box["item"]["central_status"] = "revoked"
    _pull(web)
    order_id = item_box["item"]["execution_order_id"]
    with main.connect() as db:
        order = db.execute(
            "SELECT * FROM local_control_orders WHERE id=?", (order_id,),
        ).fetchone()
        validation = db.execute(
            "SELECT * FROM local_policy_validations WHERE local_order_id=?",
            (order_id,),
        ).fetchone()
        assert order["central_status"] == "revoked"
        assert order["local_status"] == "revoked"
        assert validation["validation_status"] == "failed"
        assert validation["failure_code"] == "CENTRAL_POLICY_REVOKED"
        assert db.execute(
            "SELECT count(*) FROM local_policy_reviews"
        ).fetchone()[0] == 0
        assert db.execute(
            "SELECT count(*) FROM local_execution_authorization_snapshots"
        ).fetchone()[0] == 0


def test_pending_order_becoming_revoked_cannot_be_reviewed(
    tmp_path: Path, monkeypatch,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    _pull(web)
    order_id = item_box["item"]["execution_order_id"]
    item_box["item"]["central_status"] = "revoked"
    _pull(web)
    with main.connect() as db:
        row = db.execute(
            "SELECT central_status,local_status FROM local_control_orders "
            "WHERE id=?", (order_id,),
        ).fetchone()
        assert tuple(row) == ("revoked", "revoked")
    response = web.post(
        f"/local/orders/{order_id}/decision",
        data={
            "decision": "accepted", "reason_code": "ACCEPT",
            "reason_text": "must not be accepted",
        },
    )
    assert response.status_code == 409


def test_accepted_revocation_is_local_fail_closed_when_delivery_is_offline(
    tmp_path: Path, monkeypatch,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    order_id = item_box["item"]["execution_order_id"]
    snapshot_id = _accept(web, order_id)
    item_box["item"]["central_status"] = "revoked"

    def offline(*_args):
        raise main.httpx.ConnectError("offline")

    monkeypatch.setattr(main, "deliver_signed_message", offline)
    _pull(web)
    _pull(web)
    with main.connect() as db:
        order = db.execute(
            "SELECT central_status,local_status FROM local_control_orders "
            "WHERE id=?", (order_id,),
        ).fetchone()
        assert tuple(order) == ("revoked", "revoked_after_acceptance")
        assert db.execute(
            "SELECT status FROM local_execution_authorization_snapshots "
            "WHERE id=?", (snapshot_id,),
        ).fetchone()[0] == "revoked"


def test_decision_snapshot_and_start_all_fail_closed_on_revocation(
    tmp_path: Path, monkeypatch,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    order_id = item_box["item"]["execution_order_id"]
    _pull(web)
    with main.connect() as db:
        db.execute(
            """UPDATE local_control_orders
               SET central_status='revoked',local_status='revoked'
               WHERE id=?""", (order_id,),
        )
        db.commit()
    response = web.post(
        f"/local/orders/{order_id}/decision",
        data={
            "decision": "accepted", "reason_code": "ACCEPT",
            "reason_text": "race must be rejected",
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "CENTRAL_POLICY_REVOKED"
    with main.connect() as db:
        assert db.execute(
            "SELECT count(*) FROM local_policy_reviews"
        ).fetchone()[0] == 0
    accepted_root = tmp_path / "accepted"
    accepted_root.mkdir()
    web, item_box, _ = _fixed_web_client(accepted_root, monkeypatch)
    order_id = item_box["item"]["execution_order_id"]
    snapshot_id = _accept(web, order_id)
    root = Path("D:/MedTrustCache/phase5.13E-p0-tests") / uuid4().hex
    try:
        with main.connect() as db:
            db.execute(
                """UPDATE local_control_orders
                   SET central_status='revoked',
                       local_status='revoked_after_acceptance'
                   WHERE id=?""", (order_id,),
            )
            db.commit()
            with pytest.raises(ValueError, match="CENTRAL_POLICY_REVOKED"):
                create_execution_authorization_snapshot_from_order(
                    db, local_order_id=order_id,
                    canonical_digest=main.canonical_digest,
                    signer=lambda _payload: "signature",
                )
            with pytest.raises(ValueError, match="CENTRAL_POLICY_REVOKED"):
                start_authorized_fixed_reference_execution(
                    db, snapshot_id=snapshot_id, sandbox_root=root,
                    approved_execution_image_digest=
                        item_box["item"]["policy"]["image_digest"],
                    checked_by="operator", safety_margin_seconds=300,
                    canonical_digest=main.canonical_digest,
                    signer=lambda _payload: "signature",
                    local_audit_head=None,
                )
            assert db.execute(
                "SELECT count(*) FROM local_execution_consumption_receipts"
            ).fetchone()[0] == 0
    finally:
        if root.exists():
            shutil.rmtree(root)


def test_failed_consumption_confirmation_reuses_receipt_and_dispatches_once(
    tmp_path: Path, monkeypatch,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    order_id = item_box["item"]["execution_order_id"]
    snapshot_id = _accept(web, order_id)
    _login(web, "local.execution-operator", "operator-password")
    root = Path("D:/MedTrustCache/phase5.13E-p0-tests") / uuid4().hex
    monkeypatch.setattr(main, "RUNTIME_SANDBOX_ROOT", root)
    monkeypatch.setattr(
        main, "FIXED_EXECUTION_IMAGE_DIGEST",
        item_box["item"]["policy"]["image_digest"],
    )
    outcomes = iter((False, True))
    sent: list[dict] = []

    def deliver(_path: str, payload: dict) -> bool:
        sent.append(copy.deepcopy(payload))
        return next(outcomes)

    monkeypatch.setattr(main, "deliver_signed_message", deliver)
    try:
        first = web.post(
            f"/local/approved-execution/{snapshot_id}/start",
            follow_redirects=False,
        )
        assert first.status_code == 502
        with main.connect() as db:
            first_receipt = dict(db.execute(
                "SELECT * FROM local_execution_consumption_receipts"
            ).fetchone())
            assert first_receipt["delivery_status"] == "failed"
            assert db.execute(
                "SELECT count(*) FROM local_authorized_task_manifests"
            ).fetchone()[0] == 1
            assert db.execute(
                "SELECT count(*) FROM local_authorized_reference_executions"
            ).fetchone()[0] == 1
            assert db.execute(
                "SELECT status FROM local_authorized_execution_dispatches"
            ).fetchone()[0] == "pending"
        page = web.get("/local/approved-execution")
        assert "Retry existing consumption confirmation" in page.text

        original_replace = Path.replace
        forced_failure = {"raised": False}

        def fail_first_request_replace(path: Path, target: Path):
            if path.name.startswith("request.") and not forced_failure["raised"]:
                forced_failure["raised"] = True
                raise OSError("forced request replace failure")
            return original_replace(path, target)

        monkeypatch.setattr(Path, "replace", fail_first_request_replace)
        with pytest.raises(OSError, match="forced request replace failure"):
            web.post(
                f"/local/approved-execution/{snapshot_id}/start",
                follow_redirects=False,
            )
        with main.connect() as db:
            assert db.execute(
                "SELECT delivery_status FROM "
                "local_execution_consumption_receipts"
            ).fetchone()[0] == "delivered"
            assert db.execute(
                "SELECT status FROM local_authorized_execution_dispatches"
            ).fetchone()[0] == "pending"
        assert not list(root.glob("*/runtime/request.json"))

        third = web.post(
            f"/local/approved-execution/{snapshot_id}/start",
            follow_redirects=False,
        )
        assert third.status_code == 303, third.text
        fourth = web.post(
            f"/local/approved-execution/{snapshot_id}/start",
            follow_redirects=False,
        )
        assert fourth.status_code == 303, fourth.text
        assert len(sent) == 2
        assert sent[0] == sent[1]
        with main.connect() as db:
            receipt = dict(db.execute(
                "SELECT * FROM local_execution_consumption_receipts"
            ).fetchone())
            immutable = (
                "id", "local_order_id", "authorization_snapshot_id",
                "task_manifest_id", "runtime_session_id",
                "reference_execution_id", "payload_json", "payload_digest",
                "signature", "created_at",
            )
            assert {key: receipt[key] for key in immutable} == {
                key: first_receipt[key] for key in immutable
            }
            assert receipt["delivery_status"] == "delivered"
            assert db.execute(
                "SELECT consumed_count FROM local_control_orders"
            ).fetchone()[0] == 1
            dispatch = db.execute(
                "SELECT * FROM local_authorized_execution_dispatches"
            ).fetchone()
            assert dispatch["status"] == "dispatched"
            sandbox_id = db.execute(
                "SELECT sandbox_id FROM local_authorized_runtime_sessions"
            ).fetchone()[0]
        request_files = list(root.glob("*/runtime/request.json"))
        assert len(request_files) == 1
        assert request_files[0].parent.parent.name == sandbox_id
    finally:
        if root.exists():
            shutil.rmtree(root)


@pytest.mark.parametrize(
    ("invalidated_by", "expected_error"),
    (
        ("revocation", "CENTRAL_POLICY_REVOKED"),
        ("expiry", "EXECUTION_AUTHORIZATION_EXPIRED"),
    ),
)
def test_dispatch_rechecks_revocation_and_expiry_before_request_side_effect(
    tmp_path: Path, monkeypatch, invalidated_by: str, expected_error: str,
) -> None:
    web, item_box, _ = _fixed_web_client(tmp_path, monkeypatch)
    order_id = item_box["item"]["execution_order_id"]
    snapshot_id = _accept(web, order_id)
    _login(web, "local.execution-operator", "operator-password")
    root = Path("D:/MedTrustCache/phase5.13E-p0-tests") / uuid4().hex
    monkeypatch.setattr(main, "RUNTIME_SANDBOX_ROOT", root)
    monkeypatch.setattr(
        main, "FIXED_EXECUTION_IMAGE_DIGEST",
        item_box["item"]["policy"]["image_digest"],
    )
    monkeypatch.setattr(main, "deliver_signed_message", lambda *_args: False)
    try:
        response = web.post(
            f"/local/approved-execution/{snapshot_id}/start",
            follow_redirects=False,
        )
        assert response.status_code == 502
        with main.connect() as db:
            receipt = db.execute(
                "SELECT * FROM local_execution_consumption_receipts"
            ).fetchone()
            record_execution_consumption_delivery(
                db, receipt_id=receipt["id"], delivered=True,
                response_code=200,
            )
            execution_id = receipt["reference_execution_id"]
            request_payload = json.loads(db.execute(
                "SELECT payload_json FROM local_authorized_execution_dispatches"
            ).fetchone()[0])
            if invalidated_by == "revocation":
                db.execute(
                    """UPDATE local_control_orders
                       SET central_status='revoked',
                           local_status='revoked_after_acceptance'
                       WHERE id=?""", (order_id,),
                )
            else:
                db.execute(
                    "DROP TRIGGER trg_authorization_snapshot_core_immutable"
                )
                db.execute(
                    """UPDATE local_execution_authorization_snapshots
                       SET expires_at='2000-01-01T00:00:00+00:00'
                       WHERE id=?""", (snapshot_id,),
                )
            db.commit()
            with pytest.raises(ValueError, match=expected_error):
                dispatch_authorized_fixed_reference_execution(
                    db, reference_execution_id=execution_id,
                    sandbox_root=root, request_payload=request_payload,
                )
            assert db.execute(
                "SELECT status FROM local_authorized_execution_dispatches"
            ).fetchone()[0] == "pending"
        assert not list(root.glob("*/runtime/request.json"))
        page = web.get("/local/approved-execution")
        assert "Retry existing consumption confirmation" not in page.text
        assert "Resume confirmed fixed reference dispatch" not in page.text
    finally:
        if root.exists():
            shutil.rmtree(root)


class _EvidenceClient:
    def __init__(self, posted: list[dict]) -> None:
        self.posted = posted

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def post(self, _path: str, json: dict, **_kwargs) -> _Response:
        self.posted.append(copy.deepcopy(json))
        return _Response({"receipt_id": "central-receipt-1"})


def test_failed_evidence_registration_retries_same_bundle_from_ui(
    tmp_path: Path, monkeypatch,
) -> None:
    main.STATE_DB = tmp_path / "state" / "connector.sqlite3"
    main.STATE_DB.parent.mkdir(parents=True)
    stamp = "2026-07-30T00:00:00+00:00"
    payload = {
        "schema_version": "phase5.13E-Final/evidence-bundle/v1",
        "bundle_id": "bundle-1", "bundle_digest": "sha256:bundle",
    }
    with sqlite3.connect(main.STATE_DB) as db:
        db.row_factory = sqlite3.Row
        migrate(db)
        bootstrap_users(db, "", "", "", "admin-password")
        db.execute(
            "CREATE TABLE IF NOT EXISTS state "
            "(key TEXT PRIMARY KEY,value TEXT NOT NULL)"
        )
        for key, value in (
            ("central_connector_id", "connector-1"),
            ("certificate_fingerprint", "sha256:certificate"),
            ("certificate_status", "active"),
        ):
            db.execute(
                "INSERT INTO state(key,value) VALUES(?,?)",
                (key, json.dumps(value)),
            )
        db.execute(
            """INSERT INTO local_authorized_reference_executions
               (id,authorization_snapshot_id,task_manifest_id,
                runtime_session_id,input_manifest_id,binding_payload,
                request_digest,result_digest,status,created_at,started_at,
                completed_at)
               VALUES('execution-1','snapshot-1','task-1','runtime-1','input-1',
                      '{}','sha256:request','sha256:result','completed',?,?,?)""",
            (stamp, stamp, stamp),
        )
        db.execute(
            """INSERT INTO local_authorized_execution_artifacts
               (id,execution_id,authorization_snapshot_id,binding_payload,
                relative_reference,output_manifest,artifact_digest,status,
                created_at)
               VALUES('artifact-1','execution-1','snapshot-1','{}',
                      'sbx/output','[]','sha256:artifact','quarantined',?)""",
            (stamp,),
        )
        db.execute(
            """INSERT INTO local_authorized_artifact_scan_reports
               VALUES('scan-1','artifact-1','v1','passed','[]','[]',
                      'sha256:scan',?)""", (stamp,),
        )
        db.execute(
            """INSERT INTO local_authorized_artifact_review_decisions
               VALUES('review-1','artifact-1','scan-1','reviewer-1',
                      'APPROVE_FOR_EVIDENCE_CANDIDACY','reviewed',
                      'sha256:review',?)""", (stamp,),
        )
        db.execute(
            """INSERT INTO local_artifact_causal_validations
               VALUES('validation-1','artifact-1','review-1','v1','passed',
                      '[]','sha256:validation',?)""", (stamp,),
        )
        db.execute(
            """INSERT INTO local_execution_evidence_bundles
               (id,artifact_id,review_id,causal_validation_id,bundle_version,
                schema_version,payload_json,bundle_digest,signing_key_id,
                signature,delivery_status,response_code,created_at)
               VALUES('bundle-1','artifact-1','review-1','validation-1',1,
                      'phase5.13E-Final/evidence-bundle/v1',?,
                      'sha256:bundle','key-1','signature-1','failed',503,?)""",
            (json.dumps(payload, sort_keys=True), stamp),
        )
        db.commit()
    posted: list[dict] = []
    monkeypatch.setattr(main, "client", lambda: _EvidenceClient(posted))
    monkeypatch.setattr(main, "_connector_signing_key_id", lambda: "key-1")
    web = TestClient(main.app)
    _login(web, "local.connector-admin", "admin-password")
    before = web.get("/local/artifact-reviews")
    assert "Retry existing signed EvidenceBundle registration" in before.text
    response = web.post(
        "/local/authorized-artifacts/artifact-1/evidence-bundle",
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert posted == [{**payload, "signature": "signature-1"}]
    with main.connect() as db:
        row = db.execute(
            "SELECT * FROM local_execution_evidence_bundles"
        ).fetchone()
        assert row["delivery_status"] == "delivered"
        assert row["central_receipt_id"] == "central-receipt-1"
        assert row["payload_json"] == json.dumps(payload, sort_keys=True)
        assert row["bundle_digest"] == "sha256:bundle"
        assert row["signature"] == "signature-1"
        assert db.execute(
            "SELECT count(*) FROM local_execution_evidence_bundles"
        ).fetchone()[0] == 1
    after = web.get("/local/artifact-reviews")
    assert "Retry existing signed EvidenceBundle registration" not in after.text

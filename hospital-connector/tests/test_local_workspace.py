from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.registry import bootstrap_users, migrate, password_hash, password_matches


def prepare(tmp_path: Path) -> TestClient:
    main.STATE_DB = tmp_path / "state" / "connector.sqlite3"
    main.IDENTITY_DIR = tmp_path / "identity"
    main.CERT_DIR = tmp_path / "certificates"
    main.STATE_DB.parent.mkdir(parents=True)
    with sqlite3.connect(main.STATE_DB) as db:
        db.row_factory = sqlite3.Row
        migrate(db)
        bootstrap_users(
            db,
            "curator-test-password",
            "reviewer-test-password",
            "",
            "admin-test-password",
        )
    main.set_state("central_connector_id", "00000000-0000-0000-0000-000000000001")
    return TestClient(main.app)


def login(client: TestClient, username: str, password: str) -> None:
    response = client.post(
        "/local/login", data={"username": username, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_password_hash_is_salted_and_verified() -> None:
    first = password_hash("not-a-real-secret")
    second = password_hash("not-a-real-secret")
    assert first != second
    assert "not-a-real-secret" not in first
    assert password_matches("not-a-real-secret", first)
    assert not password_matches("wrong", first)


@pytest.mark.parametrize(
    "path",
    (
        "/local/register",
        "/local/poll",
        "/local/heartbeat",
        "/local/rotate-certificate",
    ),
)
def test_connector_admin_actions_require_session_role_and_same_origin(
    tmp_path: Path, path: str,
) -> None:
    client = prepare(tmp_path)
    form = {
        "organization_id": "org-test",
        "display_name": "Synthetic Hospital Alpha",
        "enrollment_token": "test-token",
    }

    anonymous = client.post(
        path,
        data=form,
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"] == "LOCAL_AUTH_REQUIRED"

    login(client, "local.curator", "curator-test-password")
    wrong_role = client.post(
        path,
        data=form,
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )
    assert wrong_role.status_code == 403
    assert wrong_role.json()["detail"] == "LOCAL_ROLE_FORBIDDEN"

    login(client, "local.connector-admin", "admin-test-password")
    cross_site = client.post(
        path,
        data=form,
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )
    assert cross_site.status_code == 403
    assert cross_site.json()["detail"] == "LOCAL_CSRF_REJECTED"


def test_connector_admin_actions_fail_closed_without_origin_or_referer(
    tmp_path: Path,
) -> None:
    client = prepare(tmp_path)
    login(client, "local.connector-admin", "admin-test-password")
    response = client.post("/local/poll", follow_redirects=False)
    assert response.status_code == 403
    assert response.json()["detail"] == "LOCAL_CSRF_REJECTED"


def test_admin_home_renders_only_state_appropriate_machine_controls(
    tmp_path: Path,
) -> None:
    client = prepare(tmp_path)
    login(client, "local.curator", "curator-test-password")
    curator_page = client.get("/local")
    assert curator_page.status_code == 200
    for action in (
        "/local/register", "/local/poll", "/local/heartbeat",
        "/local/rotate-certificate",
    ):
        assert f'action="{action}"' not in curator_page.text

    login(client, "local.connector-admin", "admin-test-password")
    initial = client.get("/local")
    assert '<meta name="referrer" content="same-origin">' in initial.text
    assert 'action="/local/register"' in initial.text
    assert 'action="/local/poll"' not in initial.text
    assert 'action="/local/heartbeat"' not in initial.text

    main.set_state("registration_status", "under_review")
    main.set_state("registration_request_id", "registration-1")
    awaiting = client.get("/local")
    assert 'action="/local/register"' not in awaiting.text
    assert 'action="/local/poll"' in awaiting.text
    assert 'action="/local/heartbeat"' not in awaiting.text

    main.set_state("registration_status", "certificate_issued")
    main.set_state("certificate_status", "active")
    main.set_state("connector_status", "active")
    active = client.get("/local")
    assert 'action="/local/register"' not in active.text
    assert 'action="/local/poll"' not in active.text
    assert 'action="/local/heartbeat"' in active.text
    assert 'action="/local/rotate-certificate"' in active.text


def test_bootstrap_identity_csr_keeps_cn_and_adds_connector_uri_san(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    main.STATE_DB = tmp_path / "state" / "connector.sqlite3"
    main.IDENTITY_DIR = tmp_path / "identity"
    main.CERT_DIR = tmp_path / "certificates"
    main.STATE_DB.parent.mkdir(parents=True)
    with sqlite3.connect(main.STATE_DB) as db:
        db.row_factory = sqlite3.Row
        migrate(db)

    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        commands.append(command)
        key = Path(command[command.index("-keyout") + 1])
        csr = Path(command[command.index("-out") + 1])
        key.write_text("test-private-key")
        csr.write_text("test-csr")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    main.bootstrap_identity()

    instance_id = main.get_state("connector_instance_id")
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-subj") + 1] == (
        f"/CN={instance_id}/O=MedTrust Synthetic Hospital Alpha"
    )
    assert command.count("-addext") == 1
    assert command[command.index("-addext") + 1] == (
        f"subjectAltName=URI:urn:medtrust:connector:{instance_id}"
    )


def test_rotate_certificate_csr_keeps_cn_and_adds_connector_uri_san(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = prepare(tmp_path)
    main.IDENTITY_DIR.mkdir(parents=True)
    main.CERT_DIR.mkdir(parents=True)
    instance_id = "hc-csr-san-test"
    main.set_state("connector_instance_id", instance_id)
    main.set_state("certificate_fingerprint", "sha256:test")
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        commands.append(command)
        key = Path(command[command.index("-keyout") + 1])
        csr = Path(command[command.index("-out") + 1])
        key.write_text("rotated-private-key")
        csr.write_text("rotated-csr")
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {
                "certificate_pem": "rotated-certificate",
                "certificate_fingerprint": "sha256:rotated",
                "certificate_valid_to": "2026-09-24T00:00:00+00:00",
                "supersedes_certificate_id": "certificate-before-rotation",
            }

    class FakeClient:
        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def post(self, *_: object, **kwargs: object) -> FakeResponse:
            assert kwargs["json"] == {"csr_pem": "rotated-csr"}
            return FakeResponse()

    monkeypatch.setattr(main.subprocess, "run", fake_run)
    monkeypatch.setattr(main, "client", lambda: FakeClient())
    login(client, "local.connector-admin", "admin-test-password")
    response = client.post(
        "/local/rotate-certificate",
        headers={"Origin": "http://testserver"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/local"
    assert len(commands) == 1
    command = commands[0]
    assert command[command.index("-subj") + 1] == (
        f"/CN={instance_id}/O=MedTrust Synthetic Hospital Alpha"
    )
    assert command.count("-addext") == 1
    assert command[command.index("-addext") + 1] == (
        f"subjectAltName=URI:urn:medtrust:connector:{instance_id}"
    )


def test_login_wrong_password_logout_and_revocation(tmp_path: Path) -> None:
    client = prepare(tmp_path)
    rejected = client.post(
        "/local/login",
        data={"username": "local.curator", "password": "wrong"},
        follow_redirects=False,
    )
    assert rejected.status_code == 401
    login(client, "local.curator", "curator-test-password")
    assert client.get("/local").status_code == 200
    assert client.post("/local/logout", follow_redirects=False).status_code == 303
    assert client.get("/local").status_code == 401
    with main.connect() as db:
        assert db.execute(
            "SELECT count(*) c FROM local_sessions WHERE revoked_at IS NOT NULL"
        ).fetchone()["c"] == 1


def test_curator_browser_form_chain_and_role_guard(tmp_path: Path) -> None:
    client = prepare(tmp_path)
    login(client, "local.curator", "curator-test-password")
    created = client.post(
        "/local/assets",
        data={
            "local_asset_key": "A1-LOCAL-ASSET-TEST",
            "display_name": "Synthetic Pathology Metadata Fixture A1",
            "description": "Synthetic metadata only.",
            "modality": "digital_pathology",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    asset_id = created.headers["location"].rsplit("/", 1)[-1]
    version = client.post(
        f"/local/assets/{asset_id}/versions",
        data={
            "version_label": "v1",
            "description": "Synthetic metadata revision one.",
            "dictionary_summary": "patch_id: synthetic identifier; label: synthetic class",
        },
        follow_redirects=False,
    )
    version_id = version.headers["location"].rsplit("/", 1)[-1]
    quality = client.post(
        f"/local/assets/{asset_id}/versions/{version_id}/quality",
        data={
            "completeness": 100, "uniqueness": 100, "consistency": 100,
            "validity": 100, "timeliness": 100,
            "known_limitations": "No raw images were inspected.",
        },
        follow_redirects=False,
    )
    assert quality.status_code == 303
    assert client.post(
        f"/local/assets/{asset_id}/versions/{version_id}/submit",
        follow_redirects=False,
    ).status_code == 303
    assert client.get("/local/reviews").status_code == 403
    with main.connect() as db:
        assert db.execute("SELECT count(*) c FROM local_asset_submissions").fetchone()["c"] == 1
        assert db.execute(
            "SELECT version FROM local_schema_migrations ORDER BY version DESC"
        ).fetchone()["version"] == "phase5.13E_0012"


def test_independent_sessions_reviewer_approval_and_self_review_rejection(tmp_path: Path) -> None:
    curator = prepare(tmp_path)
    reviewer = TestClient(main.app)
    login(curator, "local.curator", "curator-test-password")
    login(reviewer, "local.reviewer", "reviewer-test-password")
    assert curator.cookies.get(main.SESSION_COOKIE) != reviewer.cookies.get(main.SESSION_COOKIE)
    assert curator.get("/local/reviews").status_code == 403
    assert reviewer.get("/local/assets/new").status_code == 403

    with main.connect() as db:
        reviewer_id = db.execute(
            "SELECT id FROM local_users WHERE username='local.reviewer'"
        ).fetchone()["id"]
        stamp = main.now()
        asset_id, version_id, quality_id, submission_id = ("a", "v", "q", "s")
        db.execute(
            """INSERT INTO local_asset_descriptors
               (id,connector_id,local_asset_key,display_name,description,asset_kind,modality,
                source_category,sensitivity_classification,status,created_by,created_at,updated_at)
               VALUES(?,?,?,?,?,'dataset','digital_pathology','synthetic_metadata',
                      'non_sensitive','under_review',?,?,?)""",
            (asset_id, "c", "A1-SELF-REVIEW", "Self review test", "Synthetic", reviewer_id, stamp, stamp),
        )
        db.execute(
            """INSERT INTO local_asset_versions
               (id,asset_id,version_label,schema_version,metadata_payload,metadata_digest,
                schema_digest,created_by,created_at,is_current)
               VALUES(?,?,'v1','test','{}','m','s',?,?,1)""",
            (version_id, asset_id, reviewer_id, stamp),
        )
        db.execute(
            """INSERT INTO local_data_quality_profiles
               (id,asset_version_id,profile_version,assessment_scope,assessed_at,assessed_by,
                method_version,disclosure_summary,quality_summary,known_limitations,
                warning_flags,fitness_for_use_status,quality_digest,status,created_at)
               VALUES(?,?,'1','test',?,?,'test','{}','{}','[]','[]',
                      'pending_review','q','draft',?)""",
            (quality_id, version_id, stamp, reviewer_id, stamp),
        )
        db.execute(
            """INSERT INTO local_asset_submissions
               (id,asset_version_id,quality_profile_id,submitted_by,status,submitted_at)
               VALUES(?,?,?,?, 'pending',?)""",
            (submission_id, version_id, quality_id, reviewer_id, stamp),
        )
        db.commit()
    denied = reviewer.post(
        f"/local/reviews/{submission_id}/decision",
        data={"decision": "approved", "reason": "must be rejected"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "LOCAL_ASSET_SELF_REVIEW_FORBIDDEN"
    with main.connect() as db:
        assert db.execute(
            "SELECT count(*) c FROM audit WHERE event_type='local_asset.review.self_review_rejected'"
        ).fetchone()["c"] == 1

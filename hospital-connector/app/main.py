from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import platform
import secrets
import sqlite3
import ssl
import subprocess
import base64
import tempfile
from email.utils import parsedate_to_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from fastapi import Cookie, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from app.registry import (
    approve_executor_registration, bootstrap_users, create_asset,
    create_execution_image_manifest, create_executor_security_profile,
    create_execution_authorization_snapshot_from_order,
    create_executor_fixed_execution_readiness_attestation,
    create_executor_registration, create_quality_profile, create_version,
    destroy_executor_runtime, dispatch_authorized_fixed_reference_execution,
    evaluate_executor_admission,
    create_execution_evidence_bundle,
    list_assets, list_executors, migrate as migrate_registry, password_matches,
    prepare_executor_runtime, reconcile_authorized_fixed_reference_execution,
    reconcile_fixed_reference_execution,
    record_execution_consumption_delivery,
    record_evidence_bundle_delivery,
    reject_runtime_start, review_local_artifact, scan_local_artifact,
    review_authorized_local_artifact, scan_authorized_local_artifact,
    start_authorized_fixed_reference_execution, start_fixed_reference_execution,
    validate_authorized_artifact_causality,
    record_executor_heartbeat, reject_executor_registration,
    seed_public_fixture, transition_execution_image, transition_executor,
)

DATA_ROOT = Path(os.getenv("CONNECTOR_DATA_ROOT", "/var/lib/medtrust-connector"))
STATE_DB = DATA_ROOT / "state" / "connector.sqlite3"
IDENTITY_DIR = DATA_ROOT / "identity"
CERT_DIR = DATA_ROOT / "certificates"
EXECUTOR_IDENTITY_DIR = DATA_ROOT / "executor-identities"
EXECUTOR_CA_DIR = DATA_ROOT / "executor-ca"
RUNTIME_SANDBOX_ROOT = DATA_ROOT / "runtime-sandboxes"
FIXED_EXECUTION_IMAGE_DIGEST = os.getenv(
    "CONNECTOR_FIXED_EXECUTION_IMAGE_DIGEST", ""
)
EXECUTOR_READINESS_ATTESTATION_TTL_SECONDS = int(
    os.getenv("MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_TTL_SECONDS", "3600")
)
FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS = int(
    os.getenv(
        "MEDTRUST_FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS", "300"
    )
)
FIXED_TASK_DEFINITION = {
    "task_type": "PATHMNIST_REFERENCE_V1",
    "task_version": "1",
    "sample_count": 20,
    "dataset": "PathMNIST",
    "model": "fixed ResNet-18 reference",
}
FIXED_INPUT_SCHEMA = {
    "schema_version": "phase5.13E/pathmnist-input/v1",
    "sample_indices": list(range(20)),
    "raw_data_transfer": False,
}
FIXED_OUTPUT_SCHEMA = {
    "schema_version": "phase5.13E/pathmnist-output/v1",
    "allowed_files": [
        "aggregate_metrics.json",
        "confusion_matrix.csv",
        "execution_summary.json",
    ],
    "artifact_auto_egress": False,
}
CENTRAL = os.getenv("CONNECTOR_CENTRAL_URL", "http://host.docker.internal:8000/api/v1/connector-control")
INGRESS = os.getenv("CONNECTOR_INGRESS_URL", "https://connector-ingress:8443/api/v1/connector-control/ingress")
CA_CERT = Path(os.getenv("CONNECTOR_CA_CERT", "/pki/local-test-ca.cert.pem"))
VERSION = "5.13E-2B-1-alpha.1"
SESSION_COOKIE = "medtrust_local_session"
SESSION_HOURS = 8

app = FastAPI(title="MedTrust Hospital Connector Control Alpha", docs_url=None, redoc_url=None)


class ExecutorRegistrationPayload(BaseModel):
    executor_instance_id: str = Field(min_length=12, max_length=96)
    executor_version: str = Field(min_length=1, max_length=32)
    architecture: str = Field(min_length=2, max_length=24)
    csr_pem: str = Field(min_length=300, max_length=8192)
    installation_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capability_payload: dict
    runtime_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    nonce: str = Field(min_length=16, max_length=96)
    request_timestamp: datetime


class ExecutorHeartbeatPayload(BaseModel):
    executor_id: str
    sequence: int = Field(gt=0)
    timestamp: datetime
    status: str
    capability_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    nonce: str = Field(min_length=16, max_length=96)
    message_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: dict) -> str:
    return digest(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def connect() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(STATE_DB)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit (
      sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
      occurred_at TEXT NOT NULL, detail_json TEXT NOT NULL,
      previous_digest TEXT, event_digest TEXT NOT NULL UNIQUE
    );
    CREATE TRIGGER IF NOT EXISTS guard_audit_append_only
    BEFORE UPDATE ON audit
    BEGIN
      SELECT RAISE(ABORT, 'audit is append-only');
    END;
    CREATE TRIGGER IF NOT EXISTS guard_audit_no_delete
    BEFORE DELETE ON audit
    BEGIN
      SELECT RAISE(ABORT, 'audit is append-only');
    END;
    """)
    migrate_registry(db)
    return db


def get_state(key: str, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return default if row is None else json.loads(row["value"])


def set_state(key: str, value) -> None:
    with connect() as db:
        db.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
        db.commit()


def audit(event_type: str, detail: dict) -> None:
    safe = {key: value for key, value in detail.items() if key not in {"token", "private_key", "password", "csr_pem"}}
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        prior = db.execute("SELECT event_digest FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        previous = prior["event_digest"] if prior else None
        payload = {"event_type": event_type, "occurred_at": now(), "detail": safe, "previous_digest": previous}
        event_digest = canonical_digest(payload)
        db.execute("INSERT INTO audit(event_type,occurred_at,detail_json,previous_digest,event_digest) VALUES(?,?,?,?,?)", (event_type, payload["occurred_at"], json.dumps(safe), previous, event_digest))
        db.commit()


def current_audit_head(db: sqlite3.Connection) -> str | None:
    row = db.execute(
        "SELECT event_digest FROM audit ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    return row["event_digest"] if row else None


def bootstrap_identity() -> None:
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if get_state("installation_id"):
        return
    installation_id = str(uuid4())
    instance_id = f"hc-{uuid4()}"
    key = IDENTITY_DIR / "connector.key.pem"
    csr = IDENTITY_DIR / "connector.csr.pem"
    subprocess.run(
        [
            "openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(key), "-out", str(csr), "-subj",
            f"/CN={instance_id}/O=MedTrust Synthetic Hospital Alpha",
            "-addext",
            f"subjectAltName=URI:urn:medtrust:connector:{instance_id}",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(key, 0o600)
    set_state("installation_id", installation_id)
    set_state("connector_instance_id", instance_id)
    set_state("installation_digest", digest(installation_id.encode()))
    set_state("registration_status", "pending_registration")
    set_state("heartbeat_sequence", 0)
    set_state("manifest_sequence", 0)
    audit("identity.generated", {"connector_instance_id": instance_id})
    audit("csr.generated", {"csr_fingerprint": digest(csr.read_bytes())})
    with connect() as db:
        bootstrap_users(
            db,
            os.getenv("CONNECTOR_LOCAL_CURATOR_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_POLICY_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_ADMIN_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_EXECUTION_OPERATOR_PASSWORD", ""),
        )


def ensure_executor_ca() -> None:
    EXECUTOR_CA_DIR.mkdir(parents=True, exist_ok=True)
    key = EXECUTOR_CA_DIR / "executor-local-test-ca.key.pem"
    cert = EXECUTOR_CA_DIR / "executor-local-test-ca.cert.pem"
    signing_key = EXECUTOR_CA_DIR / "image-manifest-signing.key"
    if not signing_key.exists():
        signing_key.write_bytes(secrets.token_bytes(32))
        os.chmod(signing_key, 0o600)
    if key.exists() and cert.exists():
        return
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "30",
            "-sha256", "-subj",
            "/CN=MedTrust Executor Local Test CA/O=Synthetic Hospital Alpha",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(key, 0o600)
    audit(
        "executor.ca.generated",
        {"scope": "local_test_only", "certificate_fingerprint": digest(cert.read_bytes())},
    )


def sign_image_manifest(payload: dict) -> str:
    key = (EXECUTOR_CA_DIR / "image-manifest-signing.key").read_bytes()
    message = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return "hmac-sha256:" + hmac.new(key, message, hashlib.sha256).hexdigest()


def verify_image_manifest_signature(payload: dict, signature: str) -> bool:
    return hmac.compare_digest(sign_image_manifest(payload), signature)


@app.on_event("startup")
def startup() -> None:
    bootstrap_identity()
    ensure_executor_ca()
    with connect() as db:
        bootstrap_users(
            db,
            os.getenv("CONNECTOR_LOCAL_CURATOR_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_POLICY_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_ADMIN_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_REVIEWER_PASSWORD", ""),
            os.getenv("CONNECTOR_LOCAL_EXECUTION_OPERATOR_PASSWORD", ""),
        )


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def session_user(request: Request, *, role: str | None = None) -> sqlite3.Row:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "LOCAL_AUTH_REQUIRED")
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row = db.execute(
            """SELECT u.*,s.id session_id,s.expires_at,s.revoked_at
               FROM local_sessions s JOIN local_users u ON u.id=s.user_id
               WHERE s.session_digest=?""",
            (token_digest,),
        ).fetchone()
        if (
            not row or row["revoked_at"] or row["status"] != "active"
            or datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc)
        ):
            raise HTTPException(401, "LOCAL_SESSION_INVALID")
        if role and row["role"] != role:
            audit("authorization.rejected", {
                "actor_id": row["id"], "required_role": role, "actual_role": row["role"]
            })
            raise HTTPException(403, "LOCAL_ROLE_FORBIDDEN")
        db.execute(
            "UPDATE local_sessions SET last_seen_at=? WHERE id=?", (now(), row["session_id"])
        )
        db.commit()
    return row


def require_same_origin(request: Request) -> None:
    """Reject browser state changes that were not submitted by this origin."""
    expected = urlsplit(str(request.base_url))
    source_value = request.headers.get("origin") or request.headers.get("referer")
    source = urlsplit(source_value) if source_value else None
    if (
        source is None
        or source.username is not None
        or source.password is not None
        or source.scheme.lower() != expected.scheme.lower()
        or source.netloc.lower() != expected.netloc.lower()
    ):
        audit(
            "csrf.rejected",
            {
                "path": request.url.path,
                "source_present": bool(source_value),
            },
        )
        raise HTTPException(403, "LOCAL_CSRF_REJECTED")


def page(title: str, body: str, user: sqlite3.Row | None = None) -> str:
    navigation = ""
    if user:
        policy_link = '<a href="/local/orders">控制策略</a>' if user["role"] == "local_policy_reviewer" else ""
        executor_link = (
            '<a href="/local/executors">Executors</a>'
            if user["role"] == "connector_local_admin" else ""
        )
        runtime_link = (
            '<a href="/local/runtime">Executor Runtime</a>'
            if user["role"] == "connector_local_admin" else ""
        )
        authorized_execution_link = (
            '<a href="/local/approved-execution">Approved Execution</a>'
            if user["role"] == "local_execution_operator" else ""
        )
        artifact_link = (
            '<a href="/local/artifact-reviews">Artifact Review</a>'
            if user["role"] in {
                "connector_local_admin", "local_artifact_reviewer"
            } else ""
        )
        artifact_link = authorized_execution_link + artifact_link
        navigation = f"""<nav><a href="/local">首页</a><a href="/local/assets">资产</a>
        <a href="/local/reviews">审核</a>{policy_link}{executor_link}{runtime_link}{artifact_link}<a href="/local/sync-history">同步历史</a>
        <a href="/local/audit">本地审计</a><span>{html.escape(user['display_name'])}</span>
        <form method="post" action="/local/logout"><button>退出</button></form></nav>"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
    <meta name="referrer" content="same-origin">
    <meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,">
    <title>{html.escape(title)}</title><style>
    *{{box-sizing:border-box}}body{{margin:0;font-family:system-ui;color:#17212b;background:#f4f6f8}}
    nav{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;padding:12px max(16px,calc((100% - 1120px)/2));background:#fff;border-bottom:1px solid #d9dee3}}
    nav span{{margin-left:auto}}nav form{{margin:0}}main{{max-width:1120px;margin:auto;padding:20px}}
    .notice,.panel{{background:#fff;border:1px solid #d9dee3;border-radius:6px;padding:16px;margin:12px 0;min-width:0;overflow-wrap:anywhere}}
    .notice{{border-left:4px solid #b5472c}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .grid>*{{min-width:0}}
    label{{display:block;font-weight:600;margin-top:10px}}input,textarea,select,button{{font:inherit;padding:10px;max-width:100%}}
    input,textarea,select{{width:100%;border:1px solid #aeb7c0;border-radius:4px}}textarea{{min-height:90px}}
    button{{border:1px solid #47616f;background:#fff;border-radius:4px;cursor:pointer}}button.primary{{background:#176b55;color:#fff}}
    table{{width:100%;border-collapse:collapse;background:#fff}}th,td{{padding:10px;border-bottom:1px solid #ddd;text-align:left;overflow-wrap:anywhere}}
    .table-wrap{{max-width:100%;overflow-x:auto}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}}
    code{{overflow-wrap:anywhere}}pre{{max-width:100%;white-space:pre-wrap;overflow-wrap:anywhere}}
    @media(max-width:600px){{main{{padding:12px}}.grid{{grid-template-columns:1fr}}nav span{{margin-left:0;width:100%}}}}
    </style></head><body>{navigation}<main><h1>{html.escape(title)}</h1>{body}</main></body></html>"""


@app.get("/local/login", response_class=HTMLResponse)
def login_page(request: Request) -> str:
    if request.cookies.get(SESSION_COOKIE):
        try:
            session_user(request)
            return page("本地登录", '<p class="panel">已有本地会话。<a href="/local">进入工作台</a></p>')
        except HTTPException:
            pass
    return page("Hospital Connector 本地登录", """
    <p class="notice">本地 metadata-only 工作台。执行、数据传输和模型传输均禁用。</p>
    <form class="panel" method="post" action="/local/login">
      <label for="username">用户名</label><input id="username" name="username" autocomplete="username" required>
      <label for="password">密码</label><input id="password" name="password" type="password" autocomplete="current-password" required>
      <div class="actions"><button class="primary" type="submit">登录</button></div>
    </form>""")


@app.post("/local/login")
def login(request: Request, username: str = Form(), password: str = Form()) -> RedirectResponse:
    stamp = datetime.now(timezone.utc)
    with connect() as db:
        user = db.execute("SELECT * FROM local_users WHERE username=?", (username,)).fetchone()
        valid = bool(
            user and user["status"] == "active"
            and (not user["locked_until"] or datetime.fromisoformat(user["locked_until"]) <= stamp)
            and password_matches(password, user["password_hash"])
        )
        if not valid:
            if user:
                failures = int(user["failed_login_count"]) + 1
                locked = (stamp + timedelta(minutes=5)).isoformat() if failures >= 5 else None
                db.execute(
                    "UPDATE local_users SET failed_login_count=?,locked_until=?,updated_at=? WHERE id=?",
                    (failures, locked, now(), user["id"]),
                )
                db.commit()
            audit("auth.login.rejected", {"username_digest": digest(username.encode())})
            raise HTTPException(401, "LOCAL_LOGIN_INVALID")
        token = secrets.token_urlsafe(48)
        issued = now()
        expires = (stamp + timedelta(hours=SESSION_HOURS)).isoformat()
        db.execute(
            """INSERT INTO local_sessions
               (id,user_id,session_digest,issued_at,expires_at,last_seen_at,user_agent_digest,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (str(uuid4()), user["id"], hashlib.sha256(token.encode()).hexdigest(),
             issued, expires, issued, digest(request.headers.get("user-agent", "").encode()), issued),
        )
        db.execute(
            "UPDATE local_users SET failed_login_count=0,locked_until=NULL,last_login_at=?,updated_at=? WHERE id=?",
            (issued, issued, user["id"]),
        )
        db.commit()
    audit("auth.login.succeeded", {"actor_id": user["id"], "role": user["role"]})
    response = redirect("/local")
    response.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_HOURS * 3600, httponly=True,
        samesite="strict", secure=os.getenv("CONNECTOR_LOCAL_COOKIE_SECURE", "false").lower() == "true",
        path="/local",
    )
    return response


@app.post("/local/logout")
def logout(request: Request) -> RedirectResponse:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with connect() as db:
            db.execute(
                "UPDATE local_sessions SET revoked_at=? WHERE session_digest=? AND revoked_at IS NULL",
                (now(), hashlib.sha256(token.encode()).hexdigest()),
            )
            db.commit()
    response = redirect("/local/login")
    response.delete_cookie(SESSION_COOKIE, path="/local")
    return response


def summary() -> dict:
    with connect() as db:
        last_audit = db.execute("SELECT sequence,event_digest FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
    return {
        "installation_id_short": get_state("installation_id", "")[:8],
        "connector_instance_id": get_state("connector_instance_id"),
        "registration_status": get_state("registration_status"),
        "connector_status": get_state("connector_status", "not_active"),
        "central_connector_id": get_state("central_connector_id"),
        "certificate_status": get_state("certificate_status", "not_issued"),
        "certificate_fingerprint": get_state("certificate_fingerprint"),
        "certificate_valid_to": get_state("certificate_valid_to"),
        "heartbeat_status": get_state("heartbeat_status", "stopped"),
        "last_heartbeat": get_state("last_heartbeat"),
        "manifest_version": get_state("manifest_version"),
        "local_audit_sequence": last_audit["sequence"] if last_audit else 0,
        "local_audit_head": last_audit["event_digest"] if last_audit else None,
        "central_endpoint": CENTRAL,
        "hard_isolation": False,
        "execution_enabled": False,
        "data_access_enabled": False,
        "model_transfer_enabled": False,
        "artifact_egress_enabled": False,
        "local_asset_registry_enabled": True,
        "metadata_sync_enabled": True,
        "data_quality_summary_enabled": True,
        "executor_control_enabled": True,
        "executor_execution_enabled": False,
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "hospital-connector-control-alpha", **summary()}


@app.get("/local", response_class=HTMLResponse)
def local_page(request: Request) -> str:
    user = session_user(request)
    state = summary()
    visible_keys = (
        "registration_status", "connector_status", "certificate_status",
        "heartbeat_status", "last_heartbeat", "manifest_version",
        "local_audit_sequence", "hard_isolation", "execution_enabled",
        "data_access_enabled", "model_transfer_enabled", "artifact_egress_enabled",
        "local_asset_registry_enabled", "metadata_sync_enabled",
        "data_quality_summary_enabled",
    )
    rows = "".join(
        f"<tr><th>{html.escape(key)}</th><td>{html.escape(str(state[key]))}</td></tr>"
        for key in visible_keys
    )
    with connect() as db:
        counts = {
            "Local Assets": db.execute("SELECT count(*) c FROM local_asset_descriptors").fetchone()["c"],
            "待审核": db.execute("SELECT count(*) c FROM local_asset_submissions WHERE status='pending'").fetchone()["c"],
            "同步记录": db.execute("SELECT count(*) c FROM local_sync_history").fetchone()["c"],
            "控制策略": db.execute("SELECT count(*) c FROM local_control_orders").fetchone()["c"],
            "Executors": db.execute("SELECT count(*) c FROM local_executors").fetchone()["c"],
        }
    cards = "".join(f"<div class='panel'><strong>{k}</strong><p>{v}</p></div>" for k, v in counts.items())
    admin_controls = ""
    if user["role"] == "connector_local_admin":
        registration_status = state.get("registration_status") or "initial"
        request_id = get_state("registration_request_id")
        if registration_status in {
            "initial", "pending_registration", "rejected", "expired",
            "cancelled",
        }:
            admin_controls = """
            <section class="panel"><h2>Register this Connector</h2>
            <form method="post" action="/local/register">
              <label for="organization_id">Organization ID</label>
              <input id="organization_id" name="organization_id" required>
              <label for="display_name">Display name</label>
              <input id="display_name" name="display_name" required>
              <label for="enrollment_token">Enrollment token</label>
              <input id="enrollment_token" name="enrollment_token"
                type="password" autocomplete="off" required>
              <div class="actions"><button class="primary" type="submit">
              Submit registration</button></div>
            </form></section>"""
        elif (
            registration_status in {"submitted", "under_review", "approved"}
            and request_id
            and state["certificate_status"] != "active"
        ):
            admin_controls = """
            <section class="panel"><h2>Registration status</h2>
            <p>The registration is awaiting a central decision or certificate.</p>
            <form method="post" action="/local/poll">
              <button class="primary" type="submit">Poll registration</button>
            </form></section>"""
        if (
            state["connector_status"] == "active"
            and state["certificate_status"] == "active"
        ):
            admin_controls += """
            <section class="panel"><h2>Active Connector controls</h2>
            <div class="actions">
              <form method="post" action="/local/heartbeat">
                <button class="primary" type="submit">Send heartbeat</button>
              </form>
              <form method="post" action="/local/rotate-certificate">
                <button type="submit">Rotate certificate</button>
              </form>
            </div></section>"""
    return page("Hospital Connector 本地工作台", f"""
    <p class="notice">Local Test CA · loopback-only · hard_isolation=false · execution disabled · data transfer disabled</p>
    <div class="grid">{cards}</div><div class="table-wrap"><table>{rows}</table></div>
    {admin_controls}
    <div class="actions"><a href="/local/assets">查看本地资产</a>
    {('<a href="/local/assets/new">创建 metadata-only 资产</a>' if user['role'] == 'local_asset_curator'
      else '<a href="/local/executors">Manage inert Executors</a>' if user['role'] == 'connector_local_admin'
      else '<a href="/local/orders">进入控制策略审核</a>' if user['role'] == 'local_policy_reviewer'
      else '<a href="/local/reviews">进入审核队列</a>')}</div>
    """, user)


@app.post("/local/register")
def register(
    request: Request, organization_id: str = Form(), display_name: str = Form(),
    enrollment_token: str = Form(),
) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    require_same_origin(request)
    csr = (IDENTITY_DIR / "connector.csr.pem").read_text()
    payload = {
        "enrollment_token": enrollment_token, "organization_id": organization_id,
        "connector_instance_id": get_state("connector_instance_id"),
        "installation_digest": get_state("installation_digest"), "display_name": display_name,
        "csr_pem": csr, "connector_version": VERSION, "operating_system": platform.system(),
        "architecture": platform.machine(), "bootstrap_manifest_digest": canonical_digest({"execution_enabled": False, "data_transfer_enabled": False, "hard_isolation": False}),
        "nonce": secrets.token_urlsafe(32), "request_timestamp": now(),
    }
    with httpx.Client(timeout=15) as client:
        response = client.post(f"{CENTRAL}/bootstrap/registrations", json=payload)
    if response.status_code >= 400:
        audit("registration.failed", {"status": response.status_code})
        raise HTTPException(response.status_code, response.json().get("detail", "registration failed"))
    result = response.json()
    set_state("registration_request_id", result["id"])
    set_state("registration_status", result["status"])
    audit("registration.submitted", {"registration_request_id": result["id"]})
    return redirect("/local")


@app.post("/local/poll")
def poll(request: Request) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    require_same_origin(request)
    request_id = get_state("registration_request_id")
    if not request_id:
        raise HTTPException(409, "registration has not been submitted")
    with httpx.Client(timeout=15) as client:
        response = client.get(f"{CENTRAL}/bootstrap/registrations/{request_id}", params={"connector_instance_id": get_state("connector_instance_id")})
    response.raise_for_status()
    result = response.json()
    set_state("registration_status", result["status"])
    if result.get("certificate_pem"):
        cert = CERT_DIR / "connector.cert.pem"
        cert.write_text(result["certificate_pem"])
        set_state("central_connector_id", result["connector_id"])
        set_state("certificate_status", "active")
        set_state("certificate_fingerprint", result["certificate_fingerprint"])
        set_state("certificate_valid_to", result["certificate_valid_to"])
        set_state("connector_status", "active")
        audit("certificate.installed", {"certificate_fingerprint": result["certificate_fingerprint"]})
    return redirect("/local")


def client() -> httpx.Client:
    context = ssl.create_default_context(cafile=str(CA_CERT))
    context.load_cert_chain(
        certfile=str(CERT_DIR / "connector.cert.pem"),
        keyfile=str(IDENTITY_DIR / "connector.key.pem"),
    )
    return httpx.Client(verify=context, timeout=15)


@app.post("/local/heartbeat")
def heartbeat(request: Request) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    require_same_origin(request)
    connector_id = get_state("central_connector_id")
    if not connector_id:
        raise HTTPException(409, "active certificate is required")
    manifest_sequence = int(get_state("manifest_sequence", 0)) + 1
    capability = {
        "schema_version": "phase5.13C/capability/v1", "auth_protocols": ["mtls-local-test"],
        "heartbeat_protocols": ["phase5.13B/v1"], "metadata_sync_protocols": ["phase5.13C/v1"],
        "policy_schema_versions": ["1.0"],
        "execution_enabled": False, "data_transfer_enabled": False, "model_transfer_enabled": False,
        "local_asset_registry_enabled": True, "metadata_sync_enabled": True,
        "data_quality_summary_enabled": True, "artifact_egress_enabled": False,
        "hard_isolation": False, "isolation_maturity": "L1",
    }
    manifest = {
        "schema_version": "phase5.13C/capability/v1", "manifest_version": f"alpha-{manifest_sequence}",
        "sequence": manifest_sequence, "connector_version": VERSION, "operating_system": platform.system(),
        "architecture": platform.machine(), "capability_payload": capability,
        "execution_enabled": False, "data_transfer_enabled": False, "model_transfer_enabled": False,
        "local_asset_registry_enabled": True, "metadata_sync_enabled": True,
        "data_quality_summary_enabled": True, "artifact_egress_enabled": False,
        "hard_isolation": False, "isolation_maturity": "L1", "manifest_digest": canonical_digest(capability),
        "signed_at": now(),
    }
    with client() as mtls:
        response = mtls.post(f"{INGRESS}/connectors/{connector_id}/manifests", json=manifest, headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")})
        if response.status_code >= 400:
            audit("capability_manifest.rejected", {"status": response.status_code})
            if response.status_code == 403 and "CONNECTOR_REVOKED" in response.text:
                set_state("connector_status", "revoked")
                set_state("certificate_status", "revoked")
                set_state("heartbeat_status", "rejected_revoked")
            raise HTTPException(response.status_code, response.text[:500])
        set_state("manifest_sequence", manifest_sequence)
        set_state("manifest_version", manifest["manifest_version"])
        audit("capability_manifest.signed", {"sequence": manifest_sequence, "digest": manifest["manifest_digest"]})
        hb_sequence = int(get_state("heartbeat_sequence", 0)) + 1
        with connect() as db:
            last = db.execute("SELECT event_digest FROM audit ORDER BY sequence DESC LIMIT 1").fetchone()
        hb = {
            "sequence": hb_sequence, "sent_at": now(), "status": "healthy", "connector_version": VERSION,
            "capability_manifest_digest": manifest["manifest_digest"], "local_audit_head": last["event_digest"],
            "health_summary": {"control_plane": "healthy", "execution": "disabled", "data_access": "disabled"},
            "nonce": secrets.token_urlsafe(32),
        }
        hb["message_digest"] = canonical_digest(hb)
        response = mtls.post(f"{INGRESS}/connectors/{connector_id}/heartbeat", json=hb, headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")})
        if response.status_code >= 400:
            audit("heartbeat.rejected", {"status": response.status_code})
            raise HTTPException(response.status_code, response.text[:500])
    set_state("heartbeat_sequence", hb_sequence)
    set_state("heartbeat_status", response.json()["acceptance_result"])
    set_state("connector_status", "paused" if response.json()["acceptance_result"] == "paused_read_only" else "active")
    set_state("last_heartbeat", now())
    audit("heartbeat.sent", {"sequence": hb_sequence})
    return redirect("/local")


@app.post("/local/rotate-certificate")
def rotate_local_certificate(request: Request) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    require_same_origin(request)
    connector_id = get_state("central_connector_id")
    if not connector_id:
        raise HTTPException(409, "active certificate is required")
    next_key = IDENTITY_DIR / "connector.next.key.pem"
    next_csr = IDENTITY_DIR / "connector.next.csr.pem"
    next_cert = CERT_DIR / "connector.next.cert.pem"
    connector_instance_id = get_state("connector_instance_id")
    subprocess.run(
        [
            "openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(next_key), "-out", str(next_csr),
            "-subj",
            f"/CN={connector_instance_id}/O=MedTrust Synthetic Hospital Alpha",
            "-addext",
            "subjectAltName=URI:urn:medtrust:connector:"
            f"{connector_instance_id}",
        ],
        check=True,
        capture_output=True,
    )
    os.chmod(next_key, 0o600)
    try:
        with client() as mtls:
            response = mtls.post(
                f"{INGRESS}/connectors/{connector_id}/rotate-certificate",
                json={"csr_pem": next_csr.read_text()},
                headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")},
            )
            response.raise_for_status()
        result = response.json()
        next_cert.write_text(result["certificate_pem"])
        next_key.replace(IDENTITY_DIR / "connector.key.pem")
        next_cert.replace(CERT_DIR / "connector.cert.pem")
        set_state("certificate_status", "active")
        set_state("certificate_fingerprint", result["certificate_fingerprint"])
        set_state("certificate_valid_to", result["certificate_valid_to"])
        audit(
            "certificate.rotated",
            {
                "certificate_fingerprint": result["certificate_fingerprint"],
                "supersedes_certificate_id": result["supersedes_certificate_id"],
            },
        )
    finally:
        next_csr.unlink(missing_ok=True)
        next_key.unlink(missing_ok=True)
        next_cert.unlink(missing_ok=True)
    return redirect("/local")


def audit_result() -> dict:
    with connect() as db:
        rows = db.execute("SELECT sequence,event_type,occurred_at,detail_json,previous_digest,event_digest FROM audit ORDER BY sequence").fetchall()
    items = [{**dict(row), "detail": json.loads(row["detail_json"])} for row in rows]
    previous = None
    seen_digests: set[str] = set()
    valid = True
    fork_count = 0
    for expected_sequence, item in enumerate(items, start=1):
        expected = canonical_digest({
            "event_type": item["event_type"], "occurred_at": item["occurred_at"],
            "detail": item["detail"],
            "previous_digest": item["previous_digest"],
        })
        valid = valid and item["sequence"] == expected_sequence
        valid = valid and item["event_digest"] == expected
        if expected_sequence == 1:
            valid = valid and item["previous_digest"] is None
        else:
            valid = (
                valid
                and item["previous_digest"] is not None
                and item["previous_digest"] in seen_digests
            )
            fork_count += int(item["previous_digest"] != previous)
        seen_digests.add(item["event_digest"])
        previous = item["event_digest"]
    return {
        "items": items,
        "total": len(items),
        "chain_valid": valid,
        "head_digest": previous,
        "concurrency_forks": fork_count,
    }


@app.get("/local/audit", response_class=HTMLResponse)
def local_audit(request: Request) -> str:
    user = session_user(request)
    result = audit_result()
    rows = "".join(
        f"<tr><td>{item['sequence']}</td><td>{html.escape(item['event_type'])}</td>"
        f"<td>{html.escape(item['occurred_at'])}</td><td><code>{html.escape(item['event_digest'][:24])}…</code></td></tr>"
        for item in reversed(result["items"])
    )
    return page("Connector 本地审计", f"""
    <p class="notice">审计链：{'有效' if result['chain_valid'] else '无效'} · 事件 {result['total']} 条 · 并发分支 {result['concurrency_forks']} 条</p>
    <div class="table-wrap"><table><thead><tr><th>序号</th><th>事件</th><th>时间</th><th>摘要</th></tr></thead><tbody>{rows}</tbody></table></div>
    """, user)


@app.get("/local/assets", response_class=HTMLResponse)
def local_assets_page(request: Request) -> str:
    user = session_user(request)
    with connect() as db:
        assets = list_assets(db)
    rows = "".join(
        "<tr>"
        f"<td><a href='/local/assets/{item['id']}'>{html.escape(item['display_name'])}</a></td>"
        f"<td>{html.escape(item.get('version_label') or '')}</td>"
        f"<td>{html.escape(item['status'])}</td>"
        f"<td>{html.escape(item.get('fitness_for_use_status') or 'not_assessed')}</td>"
        "<td>metadata only / execution disabled</td></tr>"
        for item in assets
    )
    action = '<p><a href="/local/assets/new">创建 metadata-only 资产</a></p>' if user["role"] == "local_asset_curator" else ""
    return page("Local Asset Registry", f"""
    <p class="notice">仅合成 metadata；原始数据不上传；位置引用不出 Connector；hard_isolation=false。</p>
    {action}<div class="table-wrap"><table><thead><tr><th>资产</th><th>版本</th><th>状态</th><th>本地判断</th><th>边界</th></tr></thead><tbody>{rows}</tbody></table></div>
    """, user)


@app.get("/local/assets/new", response_class=HTMLResponse)
def new_asset_page(request: Request) -> str:
    user = session_user(request, role="local_asset_curator")
    return page("创建 metadata-only Local Asset", """
    <form class="panel" method="post" action="/local/assets">
      <label for="key">Local Asset Key</label><input id="key" name="local_asset_key" required pattern="[A-Za-z0-9._-]+">
      <label for="name">名称</label><input id="name" name="display_name" required>
      <label for="description">描述</label><textarea id="description" name="description" required></textarea>
      <label for="modality">模态</label><select id="modality" name="modality"><option value="digital_pathology">数字病理</option><option value="medical_imaging">医学影像</option></select>
      <div class="actions"><button class="primary" type="submit">创建草稿</button></div>
    </form>""", user)


@app.post("/local/assets")
def post_asset(
    request: Request, local_asset_key: str = Form(), display_name: str = Form(),
    description: str = Form(), modality: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    connector_id = get_state("central_connector_id") or "unregistered-local"
    with connect() as db:
        asset_id = create_asset(
            db, connector_id=connector_id, actor_id=user["id"],
            local_asset_key=local_asset_key, display_name=display_name,
            description=description, modality=modality,
        )
    audit("local_asset.created", {"asset_id": asset_id, "actor_id": user["id"]})
    return redirect(f"/local/assets/{asset_id}")


@app.get("/local/assets/{asset_id}", response_class=HTMLResponse)
def asset_detail(request: Request, asset_id: str) -> str:
    user = session_user(request)
    with connect() as db:
        asset = db.execute("SELECT * FROM local_asset_descriptors WHERE id=?", (asset_id,)).fetchone()
        versions = db.execute(
            """SELECT v.*,q.id quality_id,
                      COALESCE(r.decision,q.status) quality_status,
                      s.id submission_id,s.status review_status
               FROM local_asset_versions v
               LEFT JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
               LEFT JOIN local_asset_submissions s ON s.asset_version_id=v.id
               LEFT JOIN local_asset_reviews r ON r.asset_version_id=v.id
               WHERE v.asset_id=? ORDER BY v.created_at""", (asset_id,)
        ).fetchall()
        bundles = db.execute(
            """SELECT b.* FROM local_asset_metadata_bundles b
               JOIN local_asset_versions v ON v.id=b.asset_version_id WHERE v.asset_id=?
               ORDER BY b.bundle_sequence""", (asset_id,)
        ).fetchall()
    if not asset:
        raise HTTPException(404, "LOCAL_ASSET_NOT_FOUND")
    version_rows = "".join(
        f"<tr><td><a href='/local/assets/{asset_id}/versions/{v['id']}'>{html.escape(v['version_label'])}</a></td>"
        f"<td>{html.escape(v['quality_status'] or 'not_created')}</td><td>{html.escape(v['review_status'] or 'not_submitted')}</td></tr>"
        for v in versions
    )
    bundle_rows = "".join(
        f"<tr><td><a href='/local/assets/{asset_id}/bundles/{b['id']}'>#{b['bundle_sequence']}</a></td><td>{b['status']}</td><td>{b['created_at']}</td></tr>"
        for b in bundles
    )
    create_form = ""
    if user["role"] == "local_asset_curator":
        create_form = f"""<form class="panel" method="post" action="/local/assets/{asset_id}/versions">
        <h2>创建新版本</h2><label>版本标签</label><input name="version_label" required>
        <label>非敏感描述</label><textarea name="description" required></textarea>
        <label>Data Dictionary 摘要</label><textarea name="dictionary_summary" required></textarea>
        <button class="primary" type="submit">创建版本</button></form>"""
    return page(asset["display_name"], f"""
    <p class="notice">metadata-only · execution permitted=false · data transfer disabled</p>
    <div class="panel"><strong>Key</strong> <code>{html.escape(asset['local_asset_key'])}</code><p>{html.escape(asset['description'])}</p><p>状态：{html.escape(asset['status'])}</p></div>
    <h2>版本历史</h2><div class="table-wrap"><table><tr><th>版本</th><th>质量</th><th>审核</th></tr>{version_rows}</table></div>
    {create_form}<h2>Metadata Bundles</h2><div class="table-wrap"><table><tr><th>序号</th><th>状态</th><th>创建时间</th></tr>{bundle_rows}</table></div>
    """, user)


@app.post("/local/assets/{asset_id}/versions")
def post_version(
    request: Request, asset_id: str, version_label: str = Form(),
    description: str = Form(), dictionary_summary: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    with connect() as db:
        version_id = create_version(
            db, asset_id=asset_id, actor_id=user["id"], version_label=version_label,
            description=description, dictionary_summary=dictionary_summary,
            canonical_digest=canonical_digest,
        )
    audit("local_asset.version.created", {"asset_id": asset_id, "version_id": version_id, "actor_id": user["id"]})
    return redirect(f"/local/assets/{asset_id}/versions/{version_id}")


@app.get("/local/assets/{asset_id}/versions/{version_id}", response_class=HTMLResponse)
def version_detail(request: Request, asset_id: str, version_id: str) -> str:
    user = session_user(request)
    with connect() as db:
        version = db.execute(
            "SELECT * FROM local_asset_versions WHERE id=? AND asset_id=?", (version_id, asset_id)
        ).fetchone()
        quality = db.execute(
            "SELECT * FROM local_data_quality_profiles WHERE asset_version_id=?", (version_id,)
        ).fetchone()
        submission = db.execute(
            "SELECT * FROM local_asset_submissions WHERE asset_version_id=?", (version_id,)
        ).fetchone()
        review = db.execute(
            "SELECT * FROM local_asset_reviews WHERE asset_version_id=?", (version_id,)
        ).fetchone()
    if not version:
        raise HTTPException(404, "LOCAL_ASSET_VERSION_NOT_FOUND")
    metadata = json.loads(version["metadata_payload"])
    metadata_rows = "".join(
        f"<tr><th>{html.escape(str(k))}</th><td>{html.escape(str(v))}</td></tr>"
        for k, v in metadata.items()
    )
    actions = [f"<a href='/local/assets/{asset_id}/versions/{version_id}/quality'>查看 Quality Profile</a>"]
    if user["role"] == "local_asset_curator" and quality and not submission:
        actions.append(
            f"""<form method="post" action="/local/assets/{asset_id}/versions/{version_id}/submit">
            <button class="primary" type="submit">提交本地审核</button></form>"""
        )
    if user["role"] == "local_asset_curator" and review and review["decision"] == "approved":
        actions.append(
            f"""<form method="post" action="/local/assets/{asset_id}/versions/{version_id}/bundles">
            <button class="primary" type="submit">生成 Metadata Bundle</button></form>"""
        )
    return page(f"Local Asset Version {version['version_label']}", f"""
    <p class="notice">Append-only · metadata-only · 不包含本地路径和原始记录</p>
    <div class="table-wrap"><table>{metadata_rows}</table></div>
    <div class="panel"><strong>Metadata digest</strong><br><code>{version['metadata_digest']}</code>
    <p>审核：{html.escape(submission['status'] if submission else 'not_submitted')}</p></div>
    <div class="actions">{''.join(actions)}</div>
    """, user)


@app.get("/local/assets/{asset_id}/versions/{version_id}/quality", response_class=HTMLResponse)
def quality_page(request: Request, asset_id: str, version_id: str) -> str:
    user = session_user(request)
    with connect() as db:
        quality = db.execute(
            "SELECT * FROM local_data_quality_profiles WHERE asset_version_id=?", (version_id,)
        ).fetchone()
    if not quality and user["role"] != "local_asset_curator":
        raise HTTPException(404, "LOCAL_QUALITY_PROFILE_NOT_FOUND")
    if not quality:
        return page("创建 Data Quality Profile", f"""
        <form class="panel" method="post" action="/local/assets/{asset_id}/versions/{version_id}/quality">
        <p>范围：metadata-only。禁止字段扫描会验证路径、患者标识、原始文件名和连接信息均未进入摘要。</p>
        <div class="grid">{''.join(f'<label>{name}<input type="number" min="0" max="100" name="{name}" value="100" required></label>' for name in ('completeness','uniqueness','consistency','validity','timeliness'))}</div>
        <label>Known limitations</label><textarea name="known_limitations" required></textarea>
        <button class="primary" type="submit">保存质量画像</button></form>""", user)
    disclosure = json.loads(quality["disclosure_summary"])
    summary_data = json.loads(quality["quality_summary"])
    rows = "".join(f"<tr><th>{k}</th><td>{html.escape(str(v))}</td></tr>" for k, v in summary_data.items())
    scan = "".join(f"<li>{html.escape(k)}: {html.escape(str(v))}</li>" for k, v in disclosure.items())
    return page("Data Quality Profile", f"""
    <p class="notice">禁止字段扫描：passed</p><div class="table-wrap"><table>{rows}</table></div>
    <div class="panel"><h2>Disclosure Scan</h2><ul>{scan}</ul><h2>Known limitations</h2>
    <p>{html.escape(', '.join(json.loads(quality['known_limitations'])))}</p></div>""", user)


@app.post("/local/assets/{asset_id}/versions/{version_id}/quality")
def post_quality(
    request: Request, asset_id: str, version_id: str,
    completeness: int = Form(), uniqueness: int = Form(), consistency: int = Form(),
    validity: int = Form(), timeliness: int = Form(), known_limitations: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    with connect() as db:
        profile_id = create_quality_profile(
            db, version_id=version_id, actor_id=user["id"], completeness=completeness,
            uniqueness=uniqueness, consistency=consistency, validity=validity,
            timeliness=timeliness, known_limitations=known_limitations,
            canonical_digest=canonical_digest,
        )
    audit("local_asset.quality.created", {"version_id": version_id, "profile_id": profile_id, "actor_id": user["id"]})
    return redirect(f"/local/assets/{asset_id}/versions/{version_id}/quality")


@app.post("/local/assets/{asset_id}/versions/{version_id}/submit")
def submit_review(request: Request, asset_id: str, version_id: str) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    with connect() as db:
        quality = db.execute(
            "SELECT id FROM local_data_quality_profiles WHERE asset_version_id=?", (version_id,)
        ).fetchone()
        if not quality:
            raise HTTPException(409, "LOCAL_QUALITY_PROFILE_REQUIRED")
        submission_id = str(uuid4())
        db.execute(
            """INSERT INTO local_asset_submissions
               (id,asset_version_id,quality_profile_id,submitted_by,status,submitted_at)
               VALUES(?,?,?,?, 'pending',?)""",
            (submission_id, version_id, quality["id"], user["id"], now()),
        )
        db.execute("UPDATE local_asset_descriptors SET status='under_review',updated_at=? WHERE id=?", (now(), asset_id))
        db.commit()
    audit("local_asset.review.submitted", {"submission_id": submission_id, "actor_id": user["id"]})
    return redirect(f"/local/assets/{asset_id}/versions/{version_id}")


@app.get("/local/reviews", response_class=HTMLResponse)
def review_queue(request: Request) -> str:
    user = session_user(request, role="local_asset_reviewer")
    with connect() as db:
        rows = db.execute(
            """SELECT s.*,d.display_name,v.version_label FROM local_asset_submissions s
               JOIN local_asset_versions v ON v.id=s.asset_version_id
               JOIN local_asset_descriptors d ON d.id=v.asset_id ORDER BY s.submitted_at DESC"""
        ).fetchall()
    items = "".join(
        f"<tr><td><a href='/local/reviews/{r['id']}'>{html.escape(r['display_name'])}</a></td><td>{html.escape(r['version_label'])}</td><td>{r['status']}</td></tr>"
        for r in rows
    )
    return page("本地审核队列", f"<div class='table-wrap'><table><tr><th>资产</th><th>版本</th><th>状态</th></tr>{items}</table></div>", user)


@app.get("/local/reviews/{review_id}", response_class=HTMLResponse)
def review_detail(request: Request, review_id: str) -> str:
    user = session_user(request, role="local_asset_reviewer")
    with connect() as db:
        row = db.execute(
            """SELECT s.*,d.id asset_id,d.display_name,v.version_label,v.metadata_payload,
                      q.disclosure_summary,q.quality_summary,q.known_limitations
               FROM local_asset_submissions s JOIN local_asset_versions v ON v.id=s.asset_version_id
               JOIN local_asset_descriptors d ON d.id=v.asset_id
               JOIN local_data_quality_profiles q ON q.id=s.quality_profile_id WHERE s.id=?""",
            (review_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "LOCAL_REVIEW_NOT_FOUND")
    decision = ""
    if row["status"] == "pending":
        decision = f"""<form class="panel" method="post" action="/local/reviews/{review_id}/decision">
        <label>审核意见</label><textarea name="reason" required></textarea>
        <label>决定</label><select name="decision"><option value="approved">批准</option><option value="rejected">拒绝</option></select>
        <button class="primary" type="submit">提交一次性决定</button></form>"""
    return page(f"审核 {row['display_name']} / {row['version_label']}", f"""
    <p class="notice">Reviewer 只读检查；不能修改 curator metadata。</p>
    <div class="panel"><h2>Metadata</h2><pre>{html.escape(json.dumps(json.loads(row['metadata_payload']), ensure_ascii=False, indent=2))}</pre>
    <h2>Quality</h2><pre>{html.escape(json.dumps(json.loads(row['quality_summary']), ensure_ascii=False, indent=2))}</pre>
    <h2>禁止字段扫描</h2><pre>{html.escape(json.dumps(json.loads(row['disclosure_summary']), ensure_ascii=False, indent=2))}</pre>
    <h2>Known limitations</h2><p>{html.escape(', '.join(json.loads(row['known_limitations'])))}</p></div>{decision}
    """, user)


@app.post("/local/reviews/{review_id}/decision")
def decide_review(
    request: Request, review_id: str, reason: str = Form(), decision: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_asset_reviewer")
    if decision not in {"approved", "rejected"}:
        raise HTTPException(422, "LOCAL_REVIEW_DECISION_INVALID")
    with connect() as db:
        submission = db.execute(
            """SELECT s.*,v.asset_id FROM local_asset_submissions s
               JOIN local_asset_versions v ON v.id=s.asset_version_id WHERE s.id=?""",
            (review_id,),
        ).fetchone()
        if not submission or submission["status"] != "pending":
            raise HTTPException(409, "LOCAL_REVIEW_ALREADY_DECIDED")
        if submission["submitted_by"] == user["id"]:
            audit("local_asset.review.self_review_rejected", {
                "submission_id": review_id, "actor_id": user["id"]
            })
            raise HTTPException(403, "LOCAL_ASSET_SELF_REVIEW_FORBIDDEN")
        db.execute(
            """INSERT INTO local_asset_reviews
               (id,asset_version_id,quality_profile_id,reviewer,decision,reason,reviewed_at)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid4()), submission["asset_version_id"], submission["quality_profile_id"],
             user["id"], decision, reason, now()),
        )
        db.execute("UPDATE local_asset_submissions SET status=? WHERE id=?", (decision, review_id))
        db.execute(
            "UPDATE local_asset_descriptors SET status=?,updated_at=? WHERE id=?",
            ("local_approved" if decision == "approved" else "rejected", now(), submission["asset_id"]),
        )
        db.commit()
    audit("local_asset.review.decided", {
        "submission_id": review_id, "actor_id": user["id"], "decision": decision
    })
    return redirect(f"/local/reviews/{review_id}")


@app.post("/local/assets/{asset_id}/versions/{version_id}/bundles")
def create_bundle(request: Request, asset_id: str, version_id: str) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    with connect() as db:
        version = db.execute(
            """SELECT v.*,d.local_asset_key FROM local_asset_versions v
               JOIN local_asset_descriptors d ON d.id=v.asset_id
               WHERE v.id=? AND v.asset_id=?""", (version_id, asset_id)
        ).fetchone()
        quality = db.execute(
            "SELECT * FROM local_data_quality_profiles WHERE asset_version_id=?", (version_id,)
        ).fetchone()
        review = db.execute(
            "SELECT * FROM local_asset_reviews WHERE asset_version_id=? AND decision='approved'",
            (version_id,),
        ).fetchone()
        if not version or not quality or not review:
            raise HTTPException(409, "LOCAL_APPROVED_VERSION_REQUIRED")
        existing = db.execute(
            "SELECT id FROM local_asset_metadata_bundles WHERE asset_version_id=?", (version_id,)
        ).fetchone()
        if existing:
            return redirect(f"/local/assets/{asset_id}/bundles/{existing['id']}")
        sequence = db.execute(
            "SELECT COALESCE(MAX(bundle_sequence),0)+1 n FROM local_asset_metadata_bundles"
        ).fetchone()["n"]
        bundle_id, stamp = f"bundle-{uuid4()}", now()
        payload = {
            "schema_version": "phase5.13C/metadata-bundle/v1",
            "bundle_id": bundle_id, "bundle_sequence": sequence,
            "local_asset_key": version["local_asset_key"],
            "version_label": version["version_label"],
            "metadata_summary": json.loads(version["metadata_payload"]),
            "disclosure_summary": json.loads(quality["disclosure_summary"]),
            "quality_summary": json.loads(quality["quality_summary"]),
            "deidentification_summary": {
                "status": "not_applicable", "method_name": None, "method_version": None,
                "reversible": None, "key_holder_role": None,
                "reidentification_risk_status": "not_assessed",
                "independent_review_status": "not_applicable",
                "limitations": "Synthetic metadata only; no individual records synchronized.",
            },
            "known_limitations": json.loads(quality["known_limitations"]),
            "warning_flags": json.loads(quality["warning_flags"]),
            "metadata_digest": version["metadata_digest"],
            "schema_digest": version["schema_digest"],
            "quality_digest": quality["quality_digest"],
            "signed_at": stamp, "nonce": uuid4().hex + uuid4().hex,
        }
        payload["bundle_digest"] = canonical_digest(payload)
        db.execute(
            """INSERT INTO local_asset_metadata_bundles
               (id,asset_version_id,bundle_sequence,payload_json,bundle_digest,status,created_at)
               VALUES(?,?,?,?,?,'approved',?)""",
            (bundle_id, version_id, sequence, json.dumps(payload), payload["bundle_digest"], stamp),
        )
        db.execute("UPDATE local_asset_descriptors SET status='sync_pending',updated_at=? WHERE id=?", (stamp, asset_id))
        db.commit()
    audit("local_asset.bundle.created", {"bundle_id": bundle_id, "actor_id": user["id"]})
    return redirect(f"/local/assets/{asset_id}/bundles/{bundle_id}")


@app.get("/local/assets/{asset_id}/bundles/{bundle_id}", response_class=HTMLResponse)
def bundle_detail(request: Request, asset_id: str, bundle_id: str) -> str:
    user = session_user(request)
    with connect() as db:
        bundle = db.execute(
            """SELECT b.* FROM local_asset_metadata_bundles b JOIN local_asset_versions v
               ON v.id=b.asset_version_id WHERE b.id=? AND v.asset_id=?""",
            (bundle_id, asset_id),
        ).fetchone()
    if not bundle:
        raise HTTPException(404, "LOCAL_METADATA_BUNDLE_NOT_FOUND")
    sync = ""
    if user["role"] == "local_asset_curator" and bundle["status"] != "synced":
        sync = f"""<form method="post" action="/local/assets/{asset_id}/bundles/{bundle_id}/sync">
        <button class="primary" type="submit">通过 mTLS 同步中央</button></form>"""
    payload = json.loads(bundle["payload_json"])
    return page("Metadata Bundle", f"""
    <p class="notice">批准摘要专用；不含路径、患者标识、文件名、原始数据或模型权重。</p>
    <div class="panel"><p>状态：{bundle['status']}</p><code>{bundle['bundle_digest']}</code>
    <pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></div>{sync}
    """, user)


@app.post("/local/assets/{asset_id}/bundles/{bundle_id}/sync")
def sync_one(request: Request, asset_id: str, bundle_id: str) -> RedirectResponse:
    user = session_user(request, role="local_asset_curator")
    sync_metadata_impl(user["id"], bundle_id)
    return redirect(f"/local/assets/{asset_id}/bundles/{bundle_id}")


@app.get("/local/sync-history", response_class=HTMLResponse)
def sync_history(request: Request) -> str:
    user = session_user(request)
    with connect() as db:
        history = db.execute(
            "SELECT * FROM local_sync_history ORDER BY attempted_at DESC"
        ).fetchall()
    rows = "".join(
        f"<tr><td>{r['attempted_at']}</td><td><code>{r['bundle_id']}</code></td><td>{r['status']}</td><td>{r['response_code'] or '-'}</td></tr>"
        for r in history
    )
    return page("Metadata Sync History", f"<div class='table-wrap'><table><tr><th>时间</th><th>Bundle</th><th>状态</th><th>HTTP</th></tr>{rows}</table></div>", user)


@app.post("/local/seed-public-fixture")
def seed_fixture(request: Request) -> HTMLResponse:
    session_user(request, role="connector_local_admin")
    connector_id = get_state("central_connector_id")
    if not connector_id:
        raise HTTPException(409, "active Connector registration is required")
    with connect() as db:
        result = seed_public_fixture(
            db, connector_id=connector_id, canonical_digest=canonical_digest
        )
    audit("local_asset.fixture_seeded", result)
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/local/assets">', status_code=303)


@app.post("/local/sync-metadata")
def sync_metadata(request: Request, retry_synced: bool = Form(False)) -> HTMLResponse:
    user = session_user(request, role="local_asset_curator")
    sync_metadata_impl(user["id"], None, retry_synced=retry_synced)
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/local/assets">', status_code=303)


def sync_metadata_impl(actor_id: str, bundle_id: str | None, *, retry_synced: bool = False) -> None:
    connector_id = get_state("central_connector_id")
    if not connector_id or get_state("connector_status") != "active":
        raise HTTPException(409, "active Connector is required for metadata sync")
    with connect() as db:
        if retry_synced:
            sources = db.execute(
                """SELECT b.* FROM local_asset_metadata_bundles b
                   JOIN (
                     SELECT asset_version_id,MAX(bundle_sequence) latest_sequence
                     FROM local_asset_metadata_bundles GROUP BY asset_version_id
                   ) latest ON latest.asset_version_id=b.asset_version_id
                   AND latest.latest_sequence=b.bundle_sequence
                   ORDER BY b.bundle_sequence"""
            ).fetchall()
            bundles = []
            for source in sources:
                payload = json.loads(source["payload_json"])
                stamp = now()
                bundle_key = f"bundle-{uuid4()}"
                sequence = db.execute(
                    "SELECT COALESCE(MAX(bundle_sequence),0)+1 n FROM local_asset_metadata_bundles"
                ).fetchone()["n"]
                payload.update({
                    "bundle_id": bundle_key,
                    "bundle_sequence": sequence,
                    "signed_at": stamp,
                    "nonce": uuid4().hex + uuid4().hex,
                })
                payload["bundle_digest"] = canonical_digest(
                    {key: value for key, value in payload.items() if key != "bundle_digest"}
                )
                db.execute(
                    """INSERT INTO local_asset_metadata_bundles
                       (id,asset_version_id,bundle_sequence,payload_json,bundle_digest,status,created_at)
                       VALUES(?,?,?,?,?,'approved',?)""",
                    (bundle_key, source["asset_version_id"], sequence, json.dumps(payload),
                     payload["bundle_digest"], stamp),
                )
                bundles.append(db.execute(
                    "SELECT * FROM local_asset_metadata_bundles WHERE id=?", (bundle_key,)
                ).fetchone())
            db.commit()
        else:
            statuses = ("approved", "sync_failed")
            placeholders = ",".join("?" for _ in statuses)
            where_bundle = " AND id=?" if bundle_id else ""
            bundles = db.execute(
                f"SELECT * FROM local_asset_metadata_bundles WHERE status IN ({placeholders}){where_bundle} ORDER BY bundle_sequence",
                (*statuses, *((bundle_id,) if bundle_id else ())),
            ).fetchall()
    if bundle_id and not bundles:
        raise HTTPException(409, "LOCAL_METADATA_BUNDLE_NOT_SYNCABLE")
    for row in bundles:
        payload = json.loads(row["payload_json"])
        with client() as mtls:
            response = mtls.post(
                f"{INGRESS}/connectors/{connector_id}/asset-metadata",
                json=payload,
                headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")},
            )
        if response.status_code >= 400:
            audit("local_asset.metadata_sync_rejected", {
                "bundle_id": row["id"], "bundle_sequence": row["bundle_sequence"],
                "status": response.status_code,
            })
            with connect() as db:
                db.execute(
                    "UPDATE local_asset_metadata_bundles SET status='sync_failed' WHERE id=?",
                    (row["id"],),
                )
                db.execute(
                    """INSERT INTO local_sync_history
                       (id,bundle_id,actor_id,status,response_code,detail,attempted_at)
                       VALUES(?,?,?,'rejected',?,?,?)""",
                    (str(uuid4()), row["id"], actor_id, response.status_code,
                     "Central metadata ingress rejected the approved bundle.", now()),
                )
                db.commit()
            raise HTTPException(response.status_code, response.text[:500])
        result = response.json()
        with connect() as db:
            db.execute("""
              UPDATE local_asset_metadata_bundles
              SET status='synced',synced_at=?,central_mirror_id=?,central_version_id=?
              WHERE id=?
            """, (now(), result["mirror_id"], result["version_id"], row["id"]))
            db.execute("""
              UPDATE local_asset_descriptors SET status='synced',updated_at=?
              WHERE id=(SELECT asset_id FROM local_asset_versions WHERE id=?)
            """, (now(), row["asset_version_id"]))
            db.execute(
                """INSERT INTO local_sync_history
                   (id,bundle_id,actor_id,status,response_code,detail,attempted_at)
                   VALUES(?,?,?,'succeeded',200,'Approved metadata mirror synchronized.',?)""",
                (str(uuid4()), row["id"], actor_id, now()),
            )
            db.commit()
        audit("local_asset.metadata_synced", {
            "bundle_id": row["id"], "bundle_sequence": row["bundle_sequence"],
            "metadata_digest": payload["metadata_digest"],
            "quality_digest": payload["quality_digest"],
        })


def _openssl_value(cert_path: Path, option: str) -> str:
    result = subprocess.run(
        ["openssl", "x509", "-in", str(cert_path), "-noout", option],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return result.split("=", 1)[-1]


def sign_executor_csr(
    registration_id: str, executor_instance_id: str, csr_pem: str,
) -> dict[str, str]:
    directory = EXECUTOR_IDENTITY_DIR / executor_instance_id
    directory.mkdir(parents=True, exist_ok=True)
    csr = directory / "executor.csr.pem"
    cert = directory / "executor.cert.pem"
    csr.write_text(csr_pem, encoding="utf-8")
    subprocess.run(
        [
            "openssl", "x509", "-req", "-in", str(csr),
            "-CA", str(EXECUTOR_CA_DIR / "executor-local-test-ca.cert.pem"),
            "-CAkey", str(EXECUTOR_CA_DIR / "executor-local-test-ca.key.pem"),
            "-CAcreateserial", "-out", str(cert), "-days", "7", "-sha256",
        ],
        check=True, capture_output=True,
    )
    fingerprint = subprocess.run(
        ["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"],
        check=True, capture_output=True, text=True,
    ).stdout.strip().split("=", 1)[1].replace(":", "").lower()
    valid_from = parsedate_to_datetime(_openssl_value(cert, "-startdate"))
    valid_to = parsedate_to_datetime(_openssl_value(cert, "-enddate"))
    audit(
        "executor.certificate.issued",
        {
            "registration_id": registration_id,
            "executor_instance_id": executor_instance_id,
            "certificate_fingerprint": f"sha256:{fingerprint}",
            "local_test_only": True,
        },
    )
    return {
        "serial_number": _openssl_value(cert, "-serial"),
        "subject": _openssl_value(cert, "-subject"),
        "issuer": _openssl_value(cert, "-issuer"),
        "fingerprint_sha256": f"sha256:{fingerprint}",
        "certificate_pem": cert.read_text(encoding="utf-8"),
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat(),
    }


def _executor_status_payload(executor: dict, event_type: str) -> dict:
    payload = {
        "schema_version": "phase5.13E-1A/executor-status/v1",
        "executor_instance_id": executor["executor_instance_id"],
        "executor_version": executor["executor_version"],
        "architecture": executor["architecture"],
        "status": executor["status"],
        "certificate_fingerprint": executor["fingerprint_sha256"],
        "capability_manifest_digest": executor["manifest_digest"],
        "runtime_digest": executor["runtime_digest"],
        "image_digest": executor["image_digest"],
        "security_status": executor["security_status"],
        "status_sequence": executor["status_sequence"],
        "heartbeat_sequence": executor["last_heartbeat_sequence"],
        "heartbeat_at": executor["last_heartbeat_at"],
        "event_type": event_type,
        "execution_enabled": False,
        "hard_isolation": False,
        "sent_at": now(),
        "nonce": secrets.token_urlsafe(32),
    }
    payload["payload_digest"] = canonical_digest(payload)
    return payload


def sync_executor_status(executor_id: str, event_type: str) -> bool:
    connector_id = get_state("central_connector_id")
    with connect() as db:
        executor = next(
            (item for item in list_executors(db) if item["id"] == executor_id),
            None,
        )
    if executor is None:
        raise HTTPException(404, "EXECUTOR_UNKNOWN")
    payload = _executor_status_payload(executor, event_type)
    status_code = None
    detail = "central connector registration unavailable"
    delivered = False
    if connector_id and get_state("certificate_status") == "active":
        try:
            response = client().post(
                f"{INGRESS}/connectors/{connector_id}/executors/status",
                json=payload,
                headers={
                    "X-Client-Certificate-Fingerprint": get_state(
                        "certificate_fingerprint"
                    )
                },
            )
            status_code = response.status_code
            detail = "accepted" if response.status_code < 400 else response.text[:500]
            delivered = response.status_code < 400
        except (httpx.HTTPError, OSError) as exc:
            detail = type(exc).__name__
    with connect() as db:
        try:
            db.execute(
                """INSERT INTO local_executor_status_sync_history
                   (id,executor_id,status_sequence,event_type,payload_digest,
                    delivery_status,response_code,detail,created_at,delivered_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(uuid4()), executor_id, executor["status_sequence"],
                    event_type, payload["payload_digest"],
                    "delivered" if delivered else "failed", status_code,
                    detail, now(), now() if delivered else None,
                ),
            )
            db.commit()
        except sqlite3.IntegrityError:
            prior = db.execute(
                """SELECT delivery_status FROM local_executor_status_sync_history
                   WHERE executor_id=? AND status_sequence=?""",
                (executor_id, executor["status_sequence"]),
            ).fetchone()
            return bool(prior and prior["delivery_status"] == "delivered")
    audit(
        "executor.status.synced" if delivered else "executor.status.sync_failed",
        {
            "executor_id": executor_id,
            "event_type": event_type,
            "status_sequence": executor["status_sequence"],
            "payload_digest": payload["payload_digest"],
            "response_code": status_code,
        },
    )
    return delivered


def _connector_signing_key_id() -> str:
    csr_fingerprint = digest((IDENTITY_DIR / "connector.csr.pem").read_bytes())
    return digest(csr_fingerprint.encode("utf-8"))[:40]


def sync_executor_readiness_attestation(executor_id: str) -> dict:
    connector_id = get_state("central_connector_id")
    connector_fingerprint = get_state("certificate_fingerprint")
    if not connector_id or not connector_fingerprint:
        raise HTTPException(409, "CENTRAL_CONNECTOR_IDENTITY_UNAVAILABLE")
    with connect() as db:
        try:
            result = create_executor_fixed_execution_readiness_attestation(
                db,
                executor_id=executor_id,
                connector_id=connector_id,
                connector_certificate_fingerprint=connector_fingerprint,
                signing_key_id=_connector_signing_key_id(),
                ttl_seconds=EXECUTOR_READINESS_ATTESTATION_TTL_SECONDS,
                canonical_digest=canonical_digest,
                signer=sign_connector_payload,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    payload = result["payload"]
    status_code = None
    detail = "central connector registration unavailable"
    delivered = False
    if get_state("certificate_status") == "active":
        try:
            response = client().post(
                f"{INGRESS}/connectors/{connector_id}/executors/status",
                json=payload,
                headers={
                    "X-Client-Certificate-Fingerprint": connector_fingerprint
                },
            )
            status_code = response.status_code
            detail = "accepted" if response.status_code < 400 else response.text[:500]
            delivered = response.status_code < 400
        except (httpx.HTTPError, OSError) as exc:
            detail = type(exc).__name__
    with connect() as db:
        db.execute(
            """UPDATE local_executor_readiness_attestations
               SET delivery_status=?,response_code=?,delivered_at=?
               WHERE id=? AND delivery_status='pending'""",
            (
                "delivered" if delivered else "failed", status_code,
                now() if delivered else None, result["id"],
            ),
        )
        db.commit()
    audit(
        "executor.readiness_attestation.synced",
        {
            "executor_id": executor_id,
            "event_sequence": payload["event_sequence"],
            "payload_digest": payload["payload_digest"],
            "readiness_result": payload["readiness_result"],
            "delivered": delivered,
            "response_code": status_code,
            "hard_isolation": False,
            "execution_started": False,
        },
    )
    return {**result, "delivered": delivered, "detail": detail}


@app.post("/executor-ingress/registrations")
def executor_registration(payload: ExecutorRegistrationPayload) -> dict:
    with connect() as db:
        try:
            registration_id = create_executor_registration(
                db,
                executor_instance_id=payload.executor_instance_id,
                executor_version=payload.executor_version,
                architecture=payload.architecture,
                csr_pem=payload.csr_pem,
                installation_digest=payload.installation_digest,
                capability_payload=payload.capability_payload,
                runtime_digest=payload.runtime_digest,
                nonce=payload.nonce,
                request_timestamp=payload.request_timestamp.isoformat(),
                canonical_digest=canonical_digest,
            )
        except ValueError as exc:
            audit(
                "executor.registration.rejected",
                {"executor_instance_id": payload.executor_instance_id, "reason": str(exc)},
            )
            raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.registration.submitted",
        {
            "registration_id": registration_id,
            "executor_instance_id": payload.executor_instance_id,
            "execution_enabled": False,
        },
    )
    return {"id": registration_id, "status": "pending", "execution_enabled": False}


@app.get("/executor-ingress/registrations/{registration_id}")
def executor_registration_status(
    registration_id: str, executor_instance_id: str,
) -> dict:
    with connect() as db:
        row = db.execute(
            """SELECT r.*,c.certificate_pem,c.fingerprint_sha256
               FROM local_executor_registrations r
               LEFT JOIN local_executors e ON e.id=r.executor_id
               LEFT JOIN local_executor_certificates c ON c.id=e.current_certificate_id
               WHERE r.id=? AND r.executor_instance_id=?""",
            (registration_id, executor_instance_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "EXECUTOR_REGISTRATION_NOT_FOUND")
    return {
        "id": row["id"], "status": row["status"],
        "executor_id": row["executor_id"],
        "certificate_pem": row["certificate_pem"],
        "certificate_fingerprint": row["fingerprint_sha256"],
        "execution_enabled": False,
    }


@app.post("/executor-ingress/executors/{executor_id}/heartbeat")
def executor_heartbeat(
    executor_id: str, payload: ExecutorHeartbeatPayload, request: Request,
) -> dict:
    fingerprint = request.headers.get("X-Executor-Certificate-Fingerprint", "")
    normalized = payload.model_dump()
    normalized["timestamp"] = payload.timestamp.isoformat()
    try:
        with connect() as db:
            result = record_executor_heartbeat(
                db,
                executor_id=executor_id,
                certificate_fingerprint=fingerprint,
                payload=normalized,
                canonical_digest=canonical_digest,
            )
    except ValueError as exc:
        audit(
            "executor.heartbeat.rejected",
            {"executor_id": executor_id, "reason": str(exc)},
        )
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.heartbeat.accepted",
        {
            "executor_id": executor_id,
            "sequence": result["heartbeat_sequence"],
            "execution_enabled": False,
        },
    )
    sync_executor_status(executor_id, "heartbeat")
    return {**result, "execution_enabled": False, "hard_isolation": False}


@app.get("/local/executors", response_class=HTMLResponse)
def local_executors_page(request: Request) -> str:
    user = session_user(request, role="connector_local_admin")
    with connect() as db:
        registrations = db.execute(
            "SELECT * FROM local_executor_registrations ORDER BY created_at DESC"
        ).fetchall()
        executors = list_executors(db)
        security_profiles = db.execute(
            """SELECT p.*,e.executor_instance_id
               FROM local_executor_security_profiles p
               JOIN local_executors e ON e.id=p.executor_id
               ORDER BY p.created_at DESC"""
        ).fetchall()
        images = db.execute(
            "SELECT * FROM local_execution_image_manifests ORDER BY created_at DESC"
        ).fetchall()
        admissions = db.execute(
            """SELECT a.*,e.executor_instance_id,i.image_id
               FROM local_executor_admission_checks a
               LEFT JOIN local_executors e ON e.id=a.executor_id
               LEFT JOIN local_execution_image_manifests i
                 ON i.id=a.image_manifest_id
               ORDER BY a.checked_at DESC"""
        ).fetchall()
        readiness_attestations = db.execute(
            """SELECT a.*,e.executor_instance_id
               FROM local_executor_readiness_attestations a
               JOIN local_executors e ON e.id=a.executor_id
               ORDER BY a.event_sequence DESC"""
        ).fetchall()
    registration_rows = "".join(
        f"""<tr><td>{html.escape(row['executor_instance_id'])}</td>
        <td>{html.escape(row['executor_version'])}</td>
        <td>{html.escape(row['status'])}</td><td>
        {f'<form method="post" action="/local/executor-registrations/{row["id"]}/approve"><button class="primary">Approve</button></form>' if row["status"] == "pending" else ""}
        {f'<form method="post" action="/local/executor-registrations/{row["id"]}/reject"><button>Reject</button></form>' if row["status"] == "pending" else ""}
        </td></tr>"""
        for row in registrations
    )
    executor_rows = "".join(
        f"""<tr><td>{html.escape(item['executor_instance_id'])}</td>
        <td>{html.escape(item['executor_version'])}</td>
        <td>{html.escape(item['status'])}</td>
        <td>{html.escape(item['security_status'])}</td>
        <td>{item['last_heartbeat_sequence']}</td>
        <td><code>{html.escape(item['manifest_digest'] or '-')}</code></td>
        <td><span>execution disabled</span>
        {f'<form method="post" action="/local/executors/{item["id"]}/heartbeat"><button>Inert heartbeat</button></form>' if item["status"] in {"active", "paused"} else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/lifecycle/pause"><button>Pause</button></form>' if item["status"] == "active" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/lifecycle/resume"><button>Resume</button></form>' if item["status"] == "paused" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/lifecycle/revoke"><button>Revoke</button></form>' if item["status"] != "revoked" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/security-profile"><button>Freeze security profile</button></form>' if item["status"] == "active" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/image-manifest"><button>Approve inert image</button></form>' if item["status"] == "active" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/admission"><button>Evaluate admission</button></form>' if item["status"] == "active" else ""}
        {f'<form method="post" action="/local/executors/{item["id"]}/readiness-attestation"><button>Sign readiness proof v2</button></form>' if item["status"] == "active" else ""}
        </td></tr>"""
        for item in executors
    )
    profile_rows = "".join(
        f"""<tr><td>{html.escape(row['executor_instance_id'])}</td>
        <td>{html.escape(row['status'])}</td>
        <td>{html.escape(row['network_mode'])}</td>
        <td>{html.escape(row['filesystem_mode'])}</td>
        <td>{'yes' if row['rootless'] else 'no'}</td>
        <td><code>{html.escape(row['profile_digest'])}</code></td></tr>"""
        for row in security_profiles
    )
    image_rows = "".join(
        f"""<tr><td>{html.escape(row['image_id'])}</td>
        <td>{html.escape(row['status'])}</td>
        <td>{html.escape(row['security_scan_status'])}</td>
        <td>{'verified' if row['signature_verified'] else 'unverified'}</td>
        <td><code>{html.escape(row['image_digest'])}</code></td>
        <td>{f'<form method="post" action="/local/execution-images/{row["id"]}/revoke"><button>Revoke image</button></form>' if row["status"] != "revoked" else ""}</td></tr>"""
        for row in images
    )
    admission_rows = "".join(
        f"""<tr><td>{html.escape(row['executor_instance_id'] or '-')}</td>
        <td>{html.escape(row['image_id'] or '-')}</td>
        <td>{html.escape(row['decision'])}</td>
        <td>{html.escape(', '.join(json.loads(row['rejection_reasons'])) or 'none')}</td>
        <td>execution disabled</td></tr>"""
        for row in admissions
    )
    readiness_rows = "".join(
        f"""<tr><td>{html.escape(row['executor_instance_id'])}</td>
        <td>{row['event_sequence']}</td>
        <td>{html.escape(row['readiness_result'])}</td>
        <td>{html.escape(row['generated_at'])}</td>
        <td>{html.escape(row['expires_at'])}</td>
        <td>{html.escape(row['delivery_status'])}</td>
        <td><code>{html.escape(row['payload_digest'])}</code></td>
        <td>signed / not executed / hard_isolation=false</td></tr>"""
        for row in readiness_attestations
    )
    return page(
        "Hospital Local Executors",
        f"""<p class="notice">Identity, heartbeat and security admission only. No task,
        model, data, Run, Artifact, or EvidenceBundle. hard_isolation=false.</p>
        <div class="panel"><h2>Register fixed reference Executor</h2>
        <form method="post" action="/local/executors/register-fixture">
        <button class="primary">Generate identity and submit</button></form></div>
        <h2>Registrations</h2><div class="table-wrap"><table>
        <tr><th>Instance</th><th>Version</th><th>Status</th><th>Decision</th></tr>
        {registration_rows}</table></div>
        <h2>Executor status</h2><div class="table-wrap"><table>
        <tr><th>Instance</th><th>Version</th><th>Status</th><th>Security</th>
        <th>Heartbeat</th><th>Capability</th><th>Control</th></tr>
        {executor_rows}</table></div>
        <h2>Executor Security</h2>
        <p class="notice">Admission approval is a recorded preflight result.
        It does not enable or start execution.</p>
        <div class="table-wrap"><table>
        <tr><th>Executor</th><th>Profile</th><th>Network</th>
        <th>Filesystem</th><th>Rootless</th><th>Digest</th></tr>
        {profile_rows}</table></div>
        <h2>Image trust</h2><div class="table-wrap"><table>
        <tr><th>Image</th><th>Status</th><th>Scan</th><th>Signature</th>
        <th>Digest</th><th>Control</th></tr>{image_rows}</table></div>
        <h2>Admission checks</h2><div class="table-wrap"><table>
        <tr><th>Executor</th><th>Image</th><th>Decision</th>
        <th>Reasons</th><th>Boundary</th></tr>{admission_rows}</table></div>
        <h2>Executor Readiness Attestation v2</h2>
        <p class="notice">Connector-attested for fixed-reference policy
        compilation only. Central has not independently inspected local
        objects. No execution has started.</p>
        <div class="table-wrap"><table>
        <tr><th>Executor</th><th>Sequence</th><th>Result</th>
        <th>Generated</th><th>Expires</th><th>Sync</th><th>Digest</th>
        <th>Boundary</th></tr>{readiness_rows}</table></div>""",
        user,
    )


@app.post("/local/executors/register-fixture")
def register_executor_fixture(
    request: Request,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    if not FIXED_EXECUTION_IMAGE_DIGEST:
        raise HTTPException(503, "FIXED_EXECUTION_IMAGE_NOT_CONFIGURED")
    executor_instance_id = f"hex-{uuid4()}"
    directory = EXECUTOR_IDENTITY_DIR / executor_instance_id
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "executor.key.pem"
    csr = directory / "executor.csr.pem"
    subprocess.run(
        [
            "openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
            "-keyout", str(key), "-out", str(csr),
            "-subj", f"/CN={executor_instance_id}/O=MedTrust Inert Executor Alpha",
        ],
        check=True, capture_output=True,
    )
    os.chmod(key, 0o600)
    capability = {
        "schema_version": "phase5.13E-1A/executor-capability/v1",
        "manifest_version": "1", "executor_version": "0.1.0-alpha",
        "runtime": "container",
        "image_digest": FIXED_EXECUTION_IMAGE_DIGEST,
        "architecture": platform.machine().lower() or "amd64",
        "network_mode": "none", "filesystem_mode": "readonly_input",
        "rootless": True, "gpu": False,
        "supported_task_types": ["PATHMNIST_REFERENCE_V1"],
        "resource_limits": {
            "cpu_cores": 2, "memory_mb": 2048, "disk_mb": 1024,
            "processes": 64, "timeout_seconds": 900,
        },
        "security_features": [
            "no_new_privileges", "drop_all_capabilities", "read_only_root",
            "no_runtime_install", "no_runtime_download",
        ],
        "execution_enabled": False, "hard_isolation": False,
    }
    payload = ExecutorRegistrationPayload(
        executor_instance_id=executor_instance_id,
        executor_version=capability["executor_version"],
        architecture=capability["architecture"],
        csr_pem=csr.read_text(encoding="utf-8"),
        installation_digest=digest(executor_instance_id.encode()),
        capability_payload=capability,
        runtime_digest=canonical_digest({
            "runtime": "fixed-reference-worker",
            "image_digest": FIXED_EXECUTION_IMAGE_DIGEST,
            "task_type": "PATHMNIST_REFERENCE_V1",
        }),
        nonce=secrets.token_urlsafe(32),
        request_timestamp=datetime.now(timezone.utc),
    )
    result = executor_registration(payload)
    audit(
        "executor.fixture_identity.generated",
        {
            "actor_id": user["id"], "registration_id": result["id"],
            "executor_instance_id": executor_instance_id,
            "private_key_local_only": True,
        },
    )
    return redirect("/local/executors")


@app.post("/local/executor-registrations/{registration_id}/{decision}")
def decide_executor_registration(
    request: Request, registration_id: str, decision: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    with connect() as db:
        registration = db.execute(
            "SELECT * FROM local_executor_registrations WHERE id=?",
            (registration_id,),
        ).fetchone()
        if registration is None:
            raise HTTPException(404, "EXECUTOR_REGISTRATION_NOT_FOUND")
        try:
            if decision == "approve":
                certificate = sign_executor_csr(
                    registration_id,
                    registration["executor_instance_id"],
                    registration["csr_pem"],
                )
                executor_id = approve_executor_registration(
                    db, registration_id=registration_id,
                    connector_id=get_state("central_connector_id", "unregistered"),
                    reviewer_id=user["id"], certificate=certificate,
                )
            elif decision == "reject":
                reject_executor_registration(
                    db, registration_id=registration_id,
                    reviewer_id=user["id"],
                    reason="Local administrator rejected the registration.",
                )
                executor_id = None
            else:
                raise ValueError("EXECUTOR_DECISION_INVALID")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    audit(
        f"executor.registration.{decision}d",
        {
            "registration_id": registration_id,
            "executor_id": executor_id, "actor_id": user["id"],
        },
    )
    if executor_id:
        sync_executor_status(executor_id, "registered")
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/heartbeat")
def inert_executor_heartbeat(
    request: Request, executor_id: str,
) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    with connect() as db:
        executor = next(
            (item for item in list_executors(db) if item["id"] == executor_id),
            None,
        )
    if executor is None:
        raise HTTPException(404, "EXECUTOR_UNKNOWN")
    payload = {
        "executor_id": executor_id,
        "sequence": executor["last_heartbeat_sequence"] + 1,
        "timestamp": now(), "status": "healthy",
        "capability_digest": executor["manifest_digest"],
        "runtime_digest": executor["runtime_digest"],
        "nonce": secrets.token_urlsafe(32),
    }
    payload["message_digest"] = canonical_digest(payload)
    request_type = type(
        "LocalRequest", (),
        {"headers": {
            "X-Executor-Certificate-Fingerprint": executor["fingerprint_sha256"],
        }},
    )
    executor_heartbeat(
        executor_id, ExecutorHeartbeatPayload(**payload), request_type()
    )
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/lifecycle/{action}")
def local_executor_transition(
    request: Request, executor_id: str, action: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    if action not in {"pause", "resume", "revoke"}:
        raise HTTPException(404, "EXECUTOR_TRANSITION_UNKNOWN")
    try:
        with connect() as db:
            result = transition_executor(
                db, executor_id=executor_id, action=action,
                reason=f"Local administrator {action}.",
            )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        f"executor.{action}d" if action != "pause" else "executor.paused",
        {
            "executor_id": executor_id, "actor_id": user["id"],
            "status": result["status"], "execution_enabled": False,
        },
    )
    sync_executor_status(
        executor_id, "resumed" if action == "resume" else f"{action}d"
    )
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/security-profile")
def freeze_executor_security_profile(
    request: Request, executor_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            profile_id = create_executor_security_profile(
                db, executor_id=executor_id, checked_by=user["id"],
                canonical_digest=canonical_digest,
            )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.security_profile.frozen",
        {
            "executor_id": executor_id, "profile_id": profile_id,
            "execution_enabled": False,
        },
    )
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/image-manifest")
def approve_inert_execution_image(
    request: Request, executor_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    with connect() as db:
        executor = next(
            (item for item in list_executors(db) if item["id"] == executor_id),
            None,
        )
        if executor is None or executor["status"] != "active":
            raise HTTPException(409, "EXECUTOR_NOT_ACTIVE")
        unsigned = {
            "image_id": f"pathmnist-reference-{executor_id[:8]}",
            "image_digest": executor["image_digest"],
            "builder": "MedTrust controlled fixture builder",
            "build_time": now(),
            "dependency_hash": digest(b"phase5.13E-1B-fixed-dependencies"),
            "runtime_version": "python-3.12-control-only",
            "security_scan_status": "passed",
        }
        signature = sign_image_manifest(unsigned)
        payload = {
            **unsigned, "signature": signature,
            "signature_verified": verify_image_manifest_signature(
                unsigned, signature
            ),
        }
        try:
            manifest_id = create_execution_image_manifest(
                db, payload=payload, canonical_digest=canonical_digest
            )
            transition_execution_image(
                db, manifest_id=manifest_id, action="approve"
            )
        except (sqlite3.IntegrityError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.image_manifest.approved",
        {
            "executor_id": executor_id, "image_manifest_id": manifest_id,
            "image_digest": executor["image_digest"],
            "runtime_download": False, "execution_enabled": False,
            "actor_id": user["id"],
        },
    )
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/admission")
def evaluate_inert_executor_admission(
    request: Request, executor_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    with connect() as db:
        executor = next(
            (item for item in list_executors(db) if item["id"] == executor_id),
            None,
        )
        image = db.execute(
            """SELECT * FROM local_execution_image_manifests
               WHERE image_digest=? ORDER BY created_at DESC LIMIT 1""",
            (executor["image_digest"] if executor else "",),
        ).fetchone()
        if image is None:
            image_id = "missing-image"
        else:
            image_id = image["id"]
        result = evaluate_executor_admission(
            db, executor_id=executor_id, image_manifest_id=image_id,
            checked_by=user["id"], canonical_digest=canonical_digest,
        )
    audit(
        f"executor.admission.{result['decision']}",
        {
            "executor_id": executor_id, "admission_id": result["id"],
            "rejection_reasons": result["rejection_reasons"],
            "execution_enabled": False,
        },
    )
    return redirect("/local/executors")


@app.post("/local/executors/{executor_id}/readiness-attestation")
def create_readiness_attestation(
    request: Request, executor_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    result = sync_executor_readiness_attestation(executor_id)
    audit(
        "executor.readiness_attestation.created",
        {
            "executor_id": executor_id,
            "attestation_id": result["id"],
            "readiness_result": result["payload"]["readiness_result"],
            "actor_id": user["id"],
            "signed": True,
            "not_executed": True,
            "hard_isolation": False,
        },
    )
    return redirect("/local/executors")


@app.post("/local/execution-images/{manifest_id}/revoke")
def revoke_execution_image(
    request: Request, manifest_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            transition_execution_image(
                db, manifest_id=manifest_id, action="revoke"
            )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.image_manifest.revoked",
        {
            "image_manifest_id": manifest_id, "actor_id": user["id"],
            "execution_enabled": False,
        },
    )
    return redirect("/local/executors")


@app.get("/local/approved-execution", response_class=HTMLResponse)
def approved_execution_page(request: Request) -> str:
    user = session_user(request, role="local_execution_operator")
    reconciled = []
    with connect() as db:
        running_ids = [
            row["id"] for row in db.execute(
                """SELECT r.id
                     FROM local_authorized_runtime_sessions r
                     JOIN local_authorized_execution_dispatches d
                       ON d.runtime_session_id=r.id
                    WHERE r.status='running' AND d.status='dispatched'"""
            ).fetchall()
        ]
        for runtime_id in running_ids:
            try:
                result = reconcile_authorized_fixed_reference_execution(
                    db,
                    runtime_session_id=runtime_id,
                    sandbox_root=RUNTIME_SANDBOX_ROOT,
                    canonical_digest=canonical_digest,
                )
                if result is not None:
                    reconciled.append(result)
            except ValueError as exc:
                reconciled.append({
                    "id": runtime_id,
                    "status": "reconciliation_rejected",
                    "reason": str(exc),
                })
        rows = db.execute(
            """SELECT s.*,o.consumed_count,o.local_status,o.central_status,
                      t.id task_id,t.task_digest,
                      r.id runtime_id,r.runtime_digest,r.status runtime_status,
                      r.sandbox_id,
                      x.id execution_id,x.status execution_status,
                      x.result_digest,a.id artifact_id,
                      a.status artifact_status,a.output_manifest,
                      c.id consumption_receipt_id,
                      c.delivery_status consumption_delivery_status,
                      d.status dispatch_status
                 FROM local_execution_authorization_snapshots s
                 JOIN local_control_orders o ON o.id=s.local_order_id
                 LEFT JOIN local_authorized_task_manifests t
                   ON t.authorization_snapshot_id=s.id
                 LEFT JOIN local_authorized_runtime_sessions r
                   ON r.authorization_snapshot_id=s.id
                 LEFT JOIN local_authorized_reference_executions x
                   ON x.authorization_snapshot_id=s.id
                 LEFT JOIN local_authorized_execution_artifacts a
                   ON a.authorization_snapshot_id=s.id
                 LEFT JOIN local_execution_consumption_receipts c
                   ON c.authorization_snapshot_id=s.id
                 LEFT JOIN local_authorized_execution_dispatches d
                   ON d.authorization_snapshot_id=s.id
                ORDER BY s.authorized_at DESC"""
        ).fetchall()
    cards = []
    current_time = datetime.now(timezone.utc)
    for row in rows:
        expiry = datetime.fromisoformat(row["expires_at"])
        usable = (
            row["status"] == "validated"
            and row["local_status"] == "accepted"
            and row["central_status"] != "revoked"
            and expiry > current_time
        )
        recoverable = (
            row["status"] == "consumed"
            and row["local_status"] == "accepted"
            and row["central_status"] != "revoked"
            and row["execution_status"] == "running"
            and row["runtime_status"] == "running"
            and row["dispatch_status"] == "pending"
            and expiry > current_time
            and row["consumption_delivery_status"]
            in {"pending", "failed", "delivered"}
        )
        action = (
            f"""<form method="post"
            action="/local/approved-execution/{row['id']}/start">
            <button class="primary" type="submit">
            Start approved fixed reference execution</button></form>"""
            if usable else (
                f"""<form method="post"
                action="/local/approved-execution/{row['id']}/start">
                <button class="primary" type="submit">
                {'Resume confirmed fixed reference dispatch'
                 if row['consumption_delivery_status'] == 'delivered'
                 else 'Retry existing consumption confirmation'}
                </button></form>"""
                if recoverable else
                f"""<form method="post"
                action="/local/approved-execution/{row['id']}/start">
                <button type="submit">
                Verify expired authorization is unusable</button></form>"""
                if row["status"] == "validated" and expiry <= current_time
                else ""
            )
        )
        files = "None"
        if row["output_manifest"]:
            files = ", ".join(
                item["name"] for item in json.loads(row["output_manifest"])
            )
        result_summary = "Not available"
        if row["artifact_status"] == "quarantined" and row["sandbox_id"]:
            output_dir = (
                RUNTIME_SANDBOX_ROOT.resolve()
                / row["sandbox_id"]
                / "output"
            ).resolve()
            if output_dir.parent.parent == RUNTIME_SANDBOX_ROOT.resolve():
                summary_path = output_dir / "execution_summary.json"
                if summary_path.is_file():
                    summary = json.loads(summary_path.read_text(
                        encoding="utf-8"
                    ))
                    result_summary = (
                        f"Fixed non-clinical reference: "
                        f"{int(summary['sample_count'])} samples / "
                        f"{int(summary['correct_predictions'])} correct / "
                        f"accuracy {html.escape(str(summary['accuracy']))}"
                    )
        cards.append(f"""
        <section class="panel">
        <h2>Authorization Snapshot</h2>
        <strong>{html.escape(row['status'])}</strong>
        / consumed_count={row['consumed_count']}<br>
        Snapshot ID: <code>{html.escape(row['id'])}</code><br>
        Policy: <code>{html.escape(row['policy_digest'])}</code><br>
        Order: <code>{html.escape(row['execution_order_digest'])}</code><br>
        Decision: <code>{html.escape(row['connector_decision_digest'])}</code><br>
        Admission: <code>{html.escape(row['admission_check_digest'])}</code><br>
        Expires: {html.escape(row['expires_at'])}<br>
        Task: <code>{html.escape(row['task_id'] or 'prebinding pending')}</code><br>
        Runtime: <strong>{html.escape(row['runtime_status'] or 'not created')}</strong><br>
        Consumption confirmation:
        <strong>{html.escape(row['consumption_delivery_status'] or 'not created')}</strong><br>
        Dispatch: <strong>{html.escape(row['dispatch_status'] or 'not created')}</strong><br>
        ReferenceExecution:
        <strong>{html.escape(row['execution_status'] or 'not created')}</strong><br>
        Result: <code>{html.escape(row['result_digest'] or 'not available')}</code><br>
        Result summary: <strong>{result_summary}</strong><br>
        Artifact: <strong>{html.escape(row['artifact_status'] or 'not created')}</strong><br>
        Artifact files: {html.escape(files)}
        {action}</section>
        """)
    for result in reconciled:
        audit(
            "authorized_reference_execution.reconciled",
            {
                "reference_execution_id": result["id"],
                "status": result["status"],
                "artifact_id": result.get("artifact_id"),
                "artifact_status": result.get("artifact_status"),
                "raw_data_transfer": False,
                "model_transfer": False,
                "artifact_egress": False,
            },
        )
    return page(
        "Approved Fixed Reference Execution",
        f"""<p class="notice">
        PATHMNIST_REFERENCE_V1. Fixed 20-sample non-clinical reference.
        Max executions=1. No network. No data or model transfer.
        Results remain local and quarantined. hard_isolation=false.
        </p>
        <div class="panel"><h2>Required prebindings</h2>
        Snapshot, Policy, Readiness, Order, Status v2, Receipt, Decision,
        Admission, Executor, Asset, metadata, quality, model, image,
        security, resource, task, input and output schema digests are frozen
        before Worker dispatch.</div>
        {''.join(cards) or '<p class="panel">No accepted authorization.</p>'}""",
        user,
    )


@app.post("/local/approved-execution/{snapshot_id}/start")
def start_approved_execution(
    request: Request, snapshot_id: str,
) -> RedirectResponse:
    user = session_user(request, role="local_execution_operator")
    if not FIXED_EXECUTION_IMAGE_DIGEST:
        raise HTTPException(503, "FIXED_EXECUTION_IMAGE_NOT_CONFIGURED")
    try:
        with connect() as db:
            result = start_authorized_fixed_reference_execution(
                db,
                snapshot_id=snapshot_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                approved_execution_image_digest=FIXED_EXECUTION_IMAGE_DIGEST,
                checked_by=user["id"],
                safety_margin_seconds=
                    FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS,
                canonical_digest=canonical_digest,
                signer=sign_connector_payload,
                local_audit_head=current_audit_head(db),
            )
    except (ValueError, OSError) as exc:
        audit(
            "authorized_reference_execution.start_rejected",
            {
                "authorization_snapshot_id": snapshot_id,
                "reason": str(exc),
                "reference_execution_only": True,
            },
        )
        raise HTTPException(409, str(exc)) from exc
    delivered = result["consumption_delivery_status"] == "delivered"
    response_code = 200 if delivered else 409
    if not delivered:
        try:
            delivered = deliver_signed_message(
                f"orders/{result['consumption_payload']['execution_order_id']}"
                "/consumption",
                result["consumption_payload"],
            )
            response_code = 200 if delivered else 409
        except httpx.HTTPError:
            delivered = False
            response_code = 503
    dispatched = False
    with connect() as db:
        record_execution_consumption_delivery(
            db,
            receipt_id=result["consumption_receipt_id"],
            delivered=delivered,
            response_code=response_code,
        )
        if delivered:
            dispatched = dispatch_authorized_fixed_reference_execution(
                db,
                reference_execution_id=result["reference_execution_id"],
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                request_payload=result["request_payload"],
            )
    audit(
        "authorized_reference_execution.reserved",
        {
            "authorization_snapshot_id": snapshot_id,
            "task_manifest_id": result["task_manifest_id"],
            "runtime_session_id": result["runtime_session_id"],
            "reference_execution_id": result["reference_execution_id"],
            "consumption_receipt_delivered": delivered,
            "reservation_recovered": result["recovered"],
            "remaining_validity_seconds":
                result["remaining_validity_seconds"],
            "worker_dispatched": dispatched,
            "hard_isolation": False,
        },
    )
    if not delivered:
        raise HTTPException(502, "CENTRAL_CONSUMPTION_CONFIRMATION_FAILED")
    return redirect("/local/approved-execution")


@app.get("/local/runtime", response_class=HTMLResponse)
def local_runtime_page(request: Request) -> str:
    user = session_user(request, role="connector_local_admin")
    reconciled = []
    with connect() as db:
        runtime_ids = [
            row["id"] for row in db.execute(
                """SELECT id FROM local_executor_runtime_sessions
                   WHERE status='running'"""
            ).fetchall()
        ]
        for runtime_id in runtime_ids:
            try:
                result = reconcile_fixed_reference_execution(
                    db, runtime_session_id=runtime_id,
                    sandbox_root=RUNTIME_SANDBOX_ROOT,
                    canonical_digest=canonical_digest,
                )
                if result is not None:
                    reconciled.append(result)
            except ValueError as exc:
                reconciled.append({
                    "id": runtime_id, "status": "reconciliation_rejected",
                    "reason": str(exc),
                })
        sessions = db.execute(
            """SELECT s.*,e.executor_instance_id,i.image_digest,
                      p.profile_digest,a.decision admission_decision,
                      x.id execution_id,x.status execution_status,
                      t.task_type,t.task_digest execution_task_digest,
                      ar.id artifact_id,ar.status artifact_status
               FROM local_executor_runtime_sessions s
               JOIN local_executors e ON e.id=s.executor_id
               JOIN local_execution_image_manifests i ON i.id=s.image_manifest_id
               JOIN local_executor_security_profiles p ON p.id=s.security_profile_id
               JOIN local_executor_admission_checks a ON a.id=s.admission_check_id
               LEFT JOIN local_reference_executions x
                 ON x.runtime_session_id=s.id
               LEFT JOIN local_execution_task_manifests t
                 ON t.runtime_session_id=s.id
               LEFT JOIN local_execution_artifacts ar
                 ON ar.runtime_session_id=s.id
               ORDER BY s.created_at DESC"""
        ).fetchall()
        eligible = db.execute(
            """SELECT a.id admission_id,a.executor_id,e.executor_instance_id,
                      i.image_id
               FROM local_executor_admission_checks a
               JOIN local_executors e ON e.id=a.executor_id
               JOIN local_execution_image_manifests i ON i.id=a.image_manifest_id
               WHERE a.decision='approved' AND e.status='active'
                 AND i.status='approved'
               ORDER BY a.checked_at DESC"""
        ).fetchall()
    for result in reconciled:
        audit(
            "executor.reference_execution.reconciled",
            {
                "execution_id": result["id"], "status": result["status"],
                "artifact_id": result.get("artifact_id"),
                "artifact_status": result.get("artifact_status"),
                "reason": result.get("reason"),
                "raw_data_transfer": False, "model_transfer": False,
                "artifact_egress": False,
            },
        )
    options = "".join(
        f"""<option value="{row['executor_id']}|{row['admission_id']}">
        {html.escape(row['executor_instance_id'])} / {html.escape(row['image_id'])}
        </option>"""
        for row in eligible
    )
    rows = "".join(
        f"""<tr><td>{html.escape(row['executor_instance_id'])}</td>
        <td><code>{html.escape(row['image_digest'])}</code></td>
        <td><code>{html.escape(row['profile_digest'])}</code></td>
        <td>{html.escape(row['admission_decision'])}</td>
        <td>{html.escape(row['status'])}</td>
        <td>{html.escape(row['created_at'])}</td>
        <td>{'<strong>Reference execution only</strong><br>' + html.escape(row['task_type'] or '') if row['execution_id'] else '<strong>Not Executed</strong>'}</td>
        <td>{html.escape(row['artifact_status'] or 'None')}</td>
        <td>{
          f'<form method="post" action="/local/runtime/{row["id"]}/reference-execution"><button class="primary">Launch PATHMNIST_REFERENCE_V1</button></form>'
          if row["status"] == "prepared" and row["image_digest"] == FIXED_EXECUTION_IMAGE_DIGEST
          else (
            f'<form method="post" action="/local/runtime/{row["id"]}/destroy"><button>Destroy</button></form>'
            if row["status"] == "prepared" else ""
          )
        }</td>
        </tr>"""
        for row in sessions
    )
    return page(
        "Executor Runtime",
        f"""<p class="notice">Fixed non-clinical reference execution only.
        PATHMNIST_REFERENCE_V1 uses exactly 20 public samples and the fixed
        ResNet-18 image. No uploads, runtime downloads, network, raw-data
        transfer, model transfer, or automatic egress. General
        execution_enabled=false; hard_isolation=false.</p>
        <div class="panel"><h2>Prepare admitted runtime</h2>
        <form method="post" action="/local/runtime/prepare">
        <label>Approved admission binding</label>
        <select name="binding" required>{options}</select>
        <button class="primary">Prepare empty sandbox</button></form></div>
        <h2>Runtime Sessions</h2><div class="table-wrap"><table>
        <tr><th>Executor</th><th>Image digest</th><th>Security profile</th>
        <th>Admission</th><th>Status</th><th>Created</th>
        <th>Execution</th><th>Local Artifact</th><th>Control</th></tr>
        {rows}</table></div>""",
        user,
    )


@app.post("/local/runtime/prepare")
def prepare_local_runtime(
    request: Request, binding: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        executor_id, admission_check_id = binding.split("|", 1)
        with connect() as db:
            result = prepare_executor_runtime(
                db, executor_id=executor_id,
                admission_check_id=admission_check_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                checked_by=user["id"], canonical_digest=canonical_digest,
            )
    except (ValueError, OSError) as exc:
        audit(
            "executor.runtime.preparation_rejected",
            {"reason": str(exc), "execution_enabled": False},
        )
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.runtime.prepared",
        {
            "runtime_session_id": result["id"],
            "sandbox_id": result["sandbox_id"],
            "created": result["created"], "execution_enabled": False,
        },
    )
    return redirect("/local/runtime")


@app.post("/local/runtime/{runtime_session_id}/start")
def start_local_runtime(
    request: Request, runtime_session_id: str,
) -> RedirectResponse:
    session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            reject_runtime_start(db, runtime_session_id=runtime_session_id)
    except ValueError as exc:
        audit(
            "executor.runtime.start_rejected",
            {
                "runtime_session_id": runtime_session_id,
                "reason": str(exc), "execution_enabled": False,
            },
        )
        raise HTTPException(409, str(exc)) from exc
    raise HTTPException(409, "RUNTIME_START_FORBIDDEN")


@app.post("/local/runtime/{runtime_session_id}/reference-execution")
def launch_fixed_reference_execution(
    request: Request, runtime_session_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    if not FIXED_EXECUTION_IMAGE_DIGEST:
        raise HTTPException(503, "FIXED_EXECUTION_IMAGE_NOT_CONFIGURED")
    try:
        with connect() as db:
            result = start_fixed_reference_execution(
                db, runtime_session_id=runtime_session_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                approved_execution_image_digest=FIXED_EXECUTION_IMAGE_DIGEST,
                checked_by=user["id"], canonical_digest=canonical_digest,
            )
    except (ValueError, OSError) as exc:
        audit(
            "executor.reference_execution.rejected",
            {
                "runtime_session_id": runtime_session_id,
                "reason": str(exc), "reference_execution_only": True,
            },
        )
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.reference_execution.started",
        {
            "runtime_session_id": runtime_session_id,
            "execution_id": result["id"], "task_type": "PATHMNIST_REFERENCE_V1",
            "created": result["created"], "network_mode": "none",
            "raw_data_transfer": False, "model_transfer": False,
        },
    )
    return redirect("/local/runtime")


@app.post("/local/runtime/{runtime_session_id}/destroy")
def destroy_local_runtime(
    request: Request, runtime_session_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            result = destroy_executor_runtime(
                db, runtime_session_id=runtime_session_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                checked_by=user["id"], canonical_digest=canonical_digest,
            )
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        "executor.runtime.destroyed",
        {
            "runtime_session_id": result["id"],
            "execution_enabled": False,
        },
    )
    return redirect("/local/runtime")


@app.get("/local/artifact-reviews", response_class=HTMLResponse)
def local_artifact_reviews_page(request: Request) -> str:
    user = session_user(request)
    if user["role"] not in {"connector_local_admin", "local_artifact_reviewer"}:
        raise HTTPException(403, "LOCAL_ROLE_FORBIDDEN")
    with connect() as db:
        rows = db.execute(
            """SELECT a.*,x.id reference_execution_id,s.decision scan_decision,
                      s.findings_json,s.id scan_report_id,
                      r.decision review_decision,r.reason review_reason
               FROM local_execution_artifacts a
               JOIN local_reference_executions x ON x.id=a.execution_id
               LEFT JOIN local_artifact_scan_reports s ON s.artifact_id=a.id
               LEFT JOIN local_artifact_review_decisions r ON r.artifact_id=a.id
               ORDER BY a.created_at DESC"""
        ).fetchall()
        authorized_rows = db.execute(
            """SELECT a.*,x.id reference_execution_id,
                      x.status execution_status,x.started_at,x.completed_at,
                      s.decision scan_decision,s.findings_json,
                      s.id scan_report_id,s.scan_digest,
                      r.decision review_decision,r.reason review_reason,
                      r.review_digest,
                      c.decision causal_decision,c.validation_digest,
                      b.id evidence_bundle_id,b.bundle_digest,
                      b.delivery_status bundle_delivery_status,
                      b.central_receipt_id
               FROM local_authorized_execution_artifacts a
               JOIN local_authorized_reference_executions x
                 ON x.id=a.execution_id
               LEFT JOIN local_authorized_artifact_scan_reports s
                 ON s.artifact_id=a.id
               LEFT JOIN local_authorized_artifact_review_decisions r
                 ON r.artifact_id=a.id
               LEFT JOIN local_artifact_causal_validations c
                 ON c.artifact_id=a.id
               LEFT JOIN local_execution_evidence_bundles b
                 ON b.artifact_id=a.id
               ORDER BY a.created_at DESC"""
        ).fetchall()
    rendered = ""
    for row in authorized_rows:
        controls = ""
        if user["role"] == "connector_local_admin":
            if row["scan_decision"] is None:
                controls = (
                    f'<form method="post" action="/local/authorized-artifacts/'
                    f'{row["id"]}/scan"><button class="primary">'
                    "Start local scan</button></form>"
                )
            elif (
                row["review_decision"]
                == "APPROVE_FOR_EVIDENCE_CANDIDACY"
                and row["causal_decision"] is None
            ):
                controls = (
                    f'<form method="post" action="/local/authorized-artifacts/'
                    f'{row["id"]}/validate-causality">'
                    '<button class="primary">Validate complete causal chain'
                    "</button></form>"
                )
            elif (
                row["causal_decision"] == "passed"
                and row["evidence_bundle_id"] is None
            ):
                controls = (
                    f'<form method="post" action="/local/authorized-artifacts/'
                    f'{row["id"]}/evidence-bundle">'
                    '<button class="primary">Generate signed EvidenceBundle '
                    "and register summary</button></form>"
                )
            elif row["bundle_delivery_status"] in {"pending", "failed"}:
                controls = (
                    f'<form method="post" action="/local/authorized-artifacts/'
                    f'{row["id"]}/evidence-bundle">'
                    '<button class="primary">Retry existing signed '
                    'EvidenceBundle registration</button></form>'
                )
        elif (
            user["role"] == "local_artifact_reviewer"
            and row["scan_decision"] == "passed"
            and row["review_decision"] is None
        ):
            controls = f"""
            <form method="post"
              action="/local/authorized-artifacts/{row['id']}/review">
            <label>Independent hospital review</label>
            <textarea name="reason" required>Three aggregate-only files, scanner report, authorization summary, and non-clinical boundaries independently reviewed.</textarea>
            <button class="primary" name="decision"
              value="APPROVE_FOR_EVIDENCE_CANDIDACY">
              Approve for evidence candidacy</button>
            <button name="decision" value="REJECT">Reject</button></form>"""
        manifest = json.loads(row["output_manifest"])
        files = ", ".join(
            f"{item['name']} ({item['size_bytes']} bytes)"
            for item in manifest
        )
        rendered += f"""<tr>
        <td><code>{html.escape(row['id'])}</code><br>
        <small>authorized R1 / bytes remain hospital-local</small></td>
        <td><code>{html.escape(row['reference_execution_id'])}</code><br>
        20 samples / 19 correct / accuracy 0.95</td>
        <td>{html.escape(row['status'])}<br>{html.escape(files)}</td>
        <td>{html.escape(row['scan_decision'] or 'not_scanned')}<br>
        <code>{html.escape(row['scan_digest'] or '')}</code></td>
        <td>{html.escape(row['findings_json'] or '[]')}</td>
        <td>{html.escape(row['review_decision'] or 'pending')}<br>
        causal={html.escape(row['causal_decision'] or 'not_validated')}<br>
        bundle={html.escape(row['bundle_delivery_status'] or 'not_generated')}
        </td><td>{controls}</td></tr>"""
    for row in rows:
        controls = ""
        if user["role"] == "connector_local_admin" and row["status"] == "quarantined":
            controls = (
                f'<form method="post" action="/local/artifacts/{row["id"]}/scan">'
                '<button class="primary">Scan quarantined Artifact</button></form>'
            )
        if user["role"] == "local_artifact_reviewer" and row["status"] == "review_pending":
            controls = f"""
            <form method="post" action="/local/artifacts/{row['id']}/review">
            <label>Independent review reason</label>
            <textarea name="reason" required>Exact aggregate-only output and passed scanner evidence reviewed.</textarea>
            <button class="primary" name="decision" value="approved">Approve</button>
            <button name="decision" value="rejected">Reject</button></form>"""
        rendered += f"""<tr><td><code>{html.escape(row['id'])}</code></td>
        <td><code>{html.escape(row['reference_execution_id'])}</code></td>
        <td>{html.escape(row['status'])}</td>
        <td>{html.escape(row['scan_decision'] or 'not_scanned')}</td>
        <td>{html.escape(row['findings_json'] or '[]')}</td>
        <td>{html.escape(row['review_decision'] or 'pending')}</td>
        <td>{controls}</td></tr>"""
    return page(
        "Hospital Artifact Review",
        f"""<p class="notice">Hospital-local aggregate Artifact only.
        The authorized R1 Artifact is scanned and independently reviewed
        before causal validation. Only a signed aggregate evidence summary may
        be registered centrally. Raw Artifact bytes and local paths never
        leave the hospital. hard_isolation=false; non-clinical engineering
        Alpha.</p>
        <div class="table-wrap"><table><tr><th>Artifact ID</th>
        <th>Execution ID</th><th>Status</th><th>Scanner</th>
        <th>Findings</th><th>Review</th><th>Control</th></tr>
        {rendered}</table></div>""",
        user,
    )


@app.post("/local/artifacts/{artifact_id}/scan")
def scan_quarantined_artifact(
    request: Request, artifact_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            result = scan_local_artifact(
                db, artifact_id=artifact_id, sandbox_root=RUNTIME_SANDBOX_ROOT,
                canonical_digest=canonical_digest,
            )
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    audit("artifact.local_scan.completed", {
        "artifact_id": artifact_id, "scan_report_id": result["id"],
        "decision": result["decision"], "actor_id": user["id"],
        "central_artifact_created": False, "evidence_bundle_created": False,
    })
    return redirect("/local/artifact-reviews")


@app.post("/local/artifacts/{artifact_id}/review")
def decide_local_artifact(
    request: Request, artifact_id: str, decision: str = Form(),
    reason: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_artifact_reviewer")
    try:
        with connect() as db:
            result = review_local_artifact(
                db, artifact_id=artifact_id, reviewer_id=user["id"],
                decision=decision, reason=reason,
                canonical_digest=canonical_digest,
            )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit("artifact.local_review.decided", {
        "artifact_id": artifact_id, "review_id": result["id"],
        "decision": result["status"], "actor_id": user["id"],
        "central_artifact_created": False, "evidence_bundle_created": False,
        "release_created": False,
    })
    return redirect("/local/artifact-reviews")


@app.post("/local/authorized-artifacts/{artifact_id}/scan")
def scan_authorized_artifact(
    request: Request, artifact_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    try:
        with connect() as db:
            result = scan_authorized_local_artifact(
                db, artifact_id=artifact_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                canonical_digest=canonical_digest,
            )
    except (ValueError, OSError, UnicodeError) as exc:
        audit("artifact.authorized_scan.rejected", {
            "artifact_id": artifact_id, "reason": str(exc),
            "actor_id": user["id"], "artifact_egress": False,
        })
        raise HTTPException(409, str(exc)) from exc
    audit("artifact.authorized_scan.completed", {
        "artifact_id": artifact_id, "scan_report_id": result["id"],
        "decision": result["decision"], "findings": result["findings"],
        "actor_id": user["id"], "artifact_egress": False,
    })
    return redirect("/local/artifact-reviews")


@app.post("/local/authorized-artifacts/{artifact_id}/review")
def review_authorized_artifact(
    request: Request, artifact_id: str, decision: str = Form(),
    reason: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_artifact_reviewer")
    try:
        with connect() as db:
            result = review_authorized_local_artifact(
                db, artifact_id=artifact_id, reviewer_id=user["id"],
                decision=decision, reason=reason,
                canonical_digest=canonical_digest,
            )
    except ValueError as exc:
        audit("artifact.authorized_review.rejected", {
            "artifact_id": artifact_id, "reason": str(exc),
            "actor_id": user["id"], "central_override": False,
        })
        raise HTTPException(409, str(exc)) from exc
    audit("artifact.authorized_review.decided", {
        "artifact_id": artifact_id, "review_id": result["id"],
        "decision": result["decision"], "actor_id": user["id"],
        "evidence_bundle_created": False, "central_override": False,
    })
    return redirect("/local/artifact-reviews")


@app.post("/local/authorized-artifacts/{artifact_id}/validate-causality")
def validate_authorized_artifact(
    request: Request, artifact_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    local_audit = audit_result()
    try:
        with connect() as db:
            result = validate_authorized_artifact_causality(
                db, artifact_id=artifact_id,
                sandbox_root=RUNTIME_SANDBOX_ROOT,
                canonical_digest=canonical_digest,
                verify_connector_signature=verify_connector_payload_signature,
                verify_policy_signature=verify_ed25519_payload_signature,
                local_audit_valid=local_audit["chain_valid"],
            )
    except (ValueError, OSError) as exc:
        audit("artifact.causal_validation.rejected", {
            "artifact_id": artifact_id, "reason": str(exc),
            "actor_id": user["id"], "evidence_bundle_created": False,
        })
        raise HTTPException(409, str(exc)) from exc
    audit("artifact.causal_validation.completed", {
        "artifact_id": artifact_id,
        "causal_validation_id": result["id"],
        "decision": result["decision"],
        "failed_checks": result.get("failed_checks", []),
        "actor_id": user["id"], "evidence_bundle_created": False,
    })
    if result["decision"] != "passed":
        raise HTTPException(
            409, "CAUSAL_VALIDATION_FAILED:"
            + ",".join(result.get("failed_checks", []))
        )
    return redirect("/local/artifact-reviews")


@app.post("/local/authorized-artifacts/{artifact_id}/evidence-bundle")
def generate_authorized_evidence_bundle(
    request: Request, artifact_id: str,
) -> RedirectResponse:
    user = session_user(request, role="connector_local_admin")
    connector_id = get_state("central_connector_id")
    fingerprint = get_state("certificate_fingerprint")
    if (
        not connector_id or not fingerprint
        or get_state("certificate_status") != "active"
    ):
        raise HTTPException(409, "CENTRAL_CONNECTOR_IDENTITY_UNAVAILABLE")
    with connect() as db:
        existing_bundle = db.execute(
            "SELECT signing_key_id FROM local_execution_evidence_bundles "
            "WHERE artifact_id=?", (artifact_id,),
        ).fetchone()
        bundle = create_execution_evidence_bundle(
            db, artifact_id=artifact_id,
            sandbox_root=RUNTIME_SANDBOX_ROOT,
            connector_id=connector_id,
            signing_key_id=(
                existing_bundle["signing_key_id"]
                if existing_bundle else _connector_signing_key_id()
            ),
            local_audit_head=current_audit_head(db),
            canonical_digest=canonical_digest,
            signer=sign_connector_payload,
        )
    if bundle["delivery_status"] == "delivered":
        return redirect("/local/artifact-reviews")
    audit(
        "evidence_bundle.local_signed"
        if bundle["created"] else "evidence_bundle.retry_started",
        {
            "bundle_id": bundle["id"], "artifact_id": artifact_id,
            "bundle_digest": bundle["bundle_digest"],
            "actor_id": user["id"], "immutable_bundle_reused": not bundle["created"],
            "artifact_bytes_transferred": False,
            "raw_data_transferred": False, "local_path_transferred": False,
            "hard_isolation": False,
        },
    )
    response_code = 503
    receipt_id = None
    delivered = False
    try:
        with client() as mtls:
            response = mtls.post(
                f"{INGRESS}/connectors/{connector_id}/evidence-bundles",
                json={**bundle["payload"], "signature": bundle["signature"]},
                headers={
                    "X-Client-Certificate-Fingerprint": fingerprint
                },
            )
        response_code = response.status_code
        delivered = response.status_code < 400
        if delivered:
            receipt_id = response.json()["receipt_id"]
    except (httpx.HTTPError, KeyError, ValueError):
        delivered = False
    with connect() as db:
        record_evidence_bundle_delivery(
            db, bundle_id=bundle["id"], delivered=delivered,
            response_code=response_code, central_receipt_id=receipt_id,
        )
    audit(
        "evidence_bundle.central_registered"
        if delivered else "evidence_bundle.central_registration_failed",
        {
            "bundle_id": bundle["id"],
            "bundle_digest": bundle["bundle_digest"],
            "central_receipt_id": receipt_id,
            "response_code": response_code,
            "artifact_bytes_transferred": False,
            "raw_data_transferred": False,
            "local_path_transferred": False,
            "hard_isolation": False,
        },
    )
    if not delivered:
        raise HTTPException(502, "EVIDENCE_SUMMARY_REGISTRATION_FAILED")
    return redirect("/local/artifact-reviews")


def policy_ingress() -> str:
    marker = "/connector-control/ingress"
    if marker not in INGRESS:
        raise HTTPException(500, "policy ingress is not configured")
    return INGRESS.replace(marker, "/policy-control/ingress")


def sign_connector_payload(payload: dict) -> str:
    with tempfile.TemporaryDirectory() as root:
        message = Path(root) / "message.bin"
        signature = Path(root) / "signature.bin"
        message.write_bytes(canonical_json_text(payload).encode("utf-8"))
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign",
             str(IDENTITY_DIR / "connector.key.pem"), "-out", str(signature), str(message)],
            capture_output=True, check=False,
        )
        if result.returncode:
            raise HTTPException(500, "CONNECTOR_SIGNING_FAILED")
        return base64.b64encode(signature.read_bytes()).decode("ascii")


def verify_connector_payload_signature(
    payload: dict, signature_value: str,
) -> bool:
    with tempfile.TemporaryDirectory() as root:
        cert = Path(root) / "cert.pem"
        public = Path(root) / "public.pem"
        message = Path(root) / "message.bin"
        signature = Path(root) / "signature.bin"
        cert.write_bytes((CERT_DIR / "connector.cert.pem").read_bytes())
        message.write_bytes(canonical_json_text(payload).encode("utf-8"))
        try:
            signature.write_bytes(
                base64.b64decode(signature_value, validate=True)
            )
        except (ValueError, TypeError):
            return False
        exported = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
            capture_output=True, check=False,
        )
        public.write_bytes(exported.stdout)
        verified = subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-verify", str(public),
                "-signature", str(signature), str(message),
            ],
            capture_output=True, check=False,
        )
        return exported.returncode == 0 and verified.returncode == 0


def verify_ed25519_payload_signature(
    payload: dict, signature_value: str, public_key_value: str,
) -> bool:
    with tempfile.TemporaryDirectory() as root:
        public = Path(root) / "public.pem"
        message = Path(root) / "message.bin"
        signature = Path(root) / "signature.bin"
        public.write_text(public_key_value, encoding="ascii")
        message.write_bytes(canonical_json_text(payload).encode("utf-8"))
        try:
            signature.write_bytes(
                base64.b64decode(signature_value, validate=True)
            )
        except (ValueError, TypeError):
            return False
        verified = subprocess.run(
            [
                "openssl", "pkeyutl", "-verify", "-pubin",
                "-inkey", str(public), "-rawin", "-in", str(message),
                "-sigfile", str(signature),
            ],
            capture_output=True, check=False,
        )
        return verified.returncode == 0


def canonical_json_text(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_fixed_execution_order(
    item: dict, db: sqlite3.Connection
) -> tuple[list[dict], str | None]:
    order = item["order"]
    policy = item["policy"]
    key_info = item["signing_key"]
    checks: list[dict] = []

    def check(code: str, passed: bool) -> None:
        checks.append({"code": code, "passed": bool(passed)})

    policy_fields = {
        "schema_version", "connector_id", "organization_id",
        "executor_mirror_id", "executor_id", "application_id",
        "application_snapshot_digest", "contract_id",
        "contract_revision_id", "contract_digest", "control_readiness_id",
        "readiness_digest", "source_executor_status_event_id",
        "source_executor_status_event_digest",
        "source_attestation_expires_at", "central_asset_record_id",
        "central_asset_version_id", "local_asset_key",
        "local_asset_version_ref", "local_asset_metadata_digest",
        "quality_digest", "model_product_version_id",
        "model_reference_digest", "model_materialization_status",
        "attested_image_manifest_id", "attested_image_manifest_digest",
        "image_digest", "attested_security_profile_id",
        "security_profile_digest", "attested_resource_policy_id",
        "resource_policy_digest", "attested_admission_check_id",
        "admission_digest", "capability_digest", "purpose_code",
        "purpose_summary", "requested_action", "execution_scope",
        "task_type", "max_execution_count", "task_definition_digest",
        "runtime_timeout_seconds", "minimum_remaining_validity_seconds",
        "input_schema_digest", "output_schema_digest", "network_policy",
        "filesystem_policy", "security_policy", "output_policy",
        "review_policy", "execution_authorized", "hard_isolation",
        "issued_at", "not_before", "expires_at", "nonce",
        "signing_key_id",
    }
    order_fields = {
        "schema_version", "execution_order_id", "order_mode",
        "requested_action", "execution_scope", "task_type",
        "max_execution_count", "consumed_count", "policy_bundle_id",
        "policy_bundle_version_id", "policy_payload_digest",
        "readiness_id", "readiness_digest",
        "source_executor_status_event_id",
        "source_executor_status_event_digest", "connector_id",
        "executor_mirror_id", "executor_id", "central_asset_version_id",
        "local_asset_metadata_digest", "quality_digest",
        "model_reference_digest", "attested_image_manifest_id",
        "attested_image_manifest_digest", "image_digest",
        "security_profile_digest", "resource_policy_digest",
        "admission_digest", "capability_digest",
        "task_definition_digest", "input_schema_digest",
        "output_schema_digest", "connector_sequence", "correlation_id",
        "issued_at", "not_before", "expires_at", "nonce",
        "signing_key_id", "execution_authorized", "hard_isolation",
    }
    check(
        "policy_schema_supported",
        policy.get("schema_version")
        == "phase5.13E-2C-R1/policy-bundle/v1",
    )
    check(
        "order_schema_supported",
        order.get("schema_version")
        == "phase5.13E-2C-R1/execution-order/v1",
    )
    check("policy_additional_properties", set(policy) == policy_fields)
    check("order_additional_properties", set(order) == order_fields)
    check("payload_size", len(canonical_json_text(item).encode()) <= 65536)
    prohibited = {
        "local_path", "sandbox_path", "patient_id", "private_key",
        "raw_data", "model_weight", "database_url", "token",
    }

    def contains_prohibited(value) -> bool:
        if isinstance(value, dict):
            return any(
                key.lower() in prohibited or contains_prohibited(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(contains_prohibited(child) for child in value)
        return False

    check(
        "prohibited_fields_absent",
        not contains_prohibited({"policy": policy, "order": order}),
    )
    connector_state = db.execute(
        "SELECT value FROM state WHERE key='central_connector_id'"
    ).fetchone()
    expected_connector = (
        json.loads(connector_state["value"]) if connector_state else None
    )
    check(
        "connector_binding",
        bool(expected_connector)
        and policy.get("connector_id") == expected_connector
        and order.get("connector_id") == expected_connector,
    )
    check("known_active_signing_key", key_info.get("status") == "active")
    check("signing_algorithm_ed25519", key_info.get("algorithm") == "Ed25519")
    public_pem = key_info.get("public_key_material", "")
    check(
        "public_key_fingerprint",
        digest(public_pem.encode("ascii")) == key_info.get("fingerprint"),
    )

    def verify_ed25519(payload: dict, signature_value: str) -> bool:
        with tempfile.TemporaryDirectory() as root:
            public_file = Path(root) / "public.pem"
            message_file = Path(root) / "message.bin"
            signature_file = Path(root) / "signature.bin"
            public_file.write_text(public_pem, encoding="ascii")
            message_file.write_bytes(
                canonical_json_text(payload).encode("utf-8")
            )
            try:
                signature_file.write_bytes(
                    base64.b64decode(signature_value, validate=True)
                )
            except Exception:
                return False
            return subprocess.run(
                [
                    "openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
                    "-inkey", str(public_file), "-in", str(message_file),
                    "-sigfile", str(signature_file),
                ],
                capture_output=True,
                check=False,
            ).returncode == 0

    check("policy_digest", canonical_digest(policy) == item.get("policy_digest"))
    check("order_digest", canonical_digest(order) == item.get("order_digest"))
    check(
        "policy_signature",
        verify_ed25519(policy, item.get("policy_signature", "")),
    )
    check(
        "order_signature",
        verify_ed25519(order, item.get("order_signature", "")),
    )
    check(
        "policy_order_binding",
        order.get("policy_payload_digest") == item.get("policy_digest")
        and order.get("readiness_digest") == policy.get("readiness_digest")
        and order.get("source_executor_status_event_digest")
        == policy.get("source_executor_status_event_digest")
        and order.get("executor_id") == policy.get("executor_id")
        and order.get("central_asset_version_id")
        == policy.get("central_asset_version_id"),
    )
    fixed = {
        "order_mode": "FIXED_REFERENCE_EXECUTION",
        "requested_action": "EXECUTE_FIXED_REFERENCE_TASK",
        "execution_scope": "FIXED_REFERENCE_ONLY",
        "task_type": "PATHMNIST_REFERENCE_V1",
        "max_execution_count": 1,
        "execution_authorized": True,
        "hard_isolation": False,
    }
    check(
        "fixed_reference_mode",
        all(order.get(key) == value for key, value in fixed.items())
        and order.get("consumed_count") == 0
        and all(policy.get(key) == value for key, value in fixed.items()
                if key != "order_mode"),
    )
    security = policy.get("security_policy") or {}
    check("network_none", policy.get("network_policy") == {"network_mode": "none"})
    expected_security = {
        "rootless": True,
        "privileged": False,
        "docker_socket_access": False,
        "runtime_download": False,
        "arbitrary_code_execution_enabled": False,
        "user_supplied_code_enabled": False,
        "user_supplied_model_enabled": False,
        "data_transfer_enabled": False,
        "model_transfer_enabled": False,
        "artifact_auto_egress_enabled": False,
        "hard_isolation": False,
    }
    check("security_schema_closed", set(security) == set(expected_security))
    for field, expected in expected_security.items():
        check(f"security_{field}", security.get(field) is expected)
    check(
        "filesystem_input_readonly",
        policy.get("filesystem_policy") == {"input_readonly": True},
    )
    check(
        "fixed_output_allowlist",
        policy.get("output_policy")
        == {
            "allowed_files": [
                "aggregate_metrics.json",
                "confusion_matrix.csv",
                "execution_summary.json",
            ],
            "auto_egress": False,
        },
    )
    check(
        "local_review_without_central_override",
        policy.get("review_policy")
        == {
            "local_policy_reviewer_required": True,
            "central_override": False,
        },
    )
    check(
        "fixed_task_definition",
        policy.get("task_definition_digest")
        == canonical_digest(FIXED_TASK_DEFINITION)
        and policy.get("input_schema_digest")
        == canonical_digest(FIXED_INPUT_SCHEMA)
        and policy.get("output_schema_digest")
        == canonical_digest(FIXED_OUTPUT_SCHEMA),
    )
    now_at = datetime.now(timezone.utc)
    try:
        time_valid = (
            datetime.fromisoformat(policy["not_before"]) <= now_at
            < datetime.fromisoformat(policy["expires_at"])
            and datetime.fromisoformat(order["not_before"]) <= now_at
            < datetime.fromisoformat(order["expires_at"])
            and datetime.fromisoformat(order["expires_at"])
            <= datetime.fromisoformat(policy["expires_at"])
            and now_at
            < datetime.fromisoformat(policy["source_attestation_expires_at"])
        )
    except Exception:
        time_valid = False
    check("valid_time_window", time_valid)
    remaining_validity = min(
        (
            datetime.fromisoformat(policy["expires_at"]) - now_at
        ).total_seconds(),
        (
            datetime.fromisoformat(order["expires_at"]) - now_at
        ).total_seconds(),
        (
            datetime.fromisoformat(policy["source_attestation_expires_at"])
            - now_at
        ).total_seconds(),
    ) if time_valid else -1
    check(
        "minimum_remaining_validity",
        policy.get("runtime_timeout_seconds") == 900
        and policy.get("minimum_remaining_validity_seconds")
        == 900 + FIXED_REFERENCE_AUTHORIZATION_SAFETY_MARGIN_SECONDS
        and remaining_validity
        >= policy.get("minimum_remaining_validity_seconds", 10**9),
    )
    check(
        "nonce_not_replayed",
        not db.execute(
            "SELECT 1 FROM local_order_replay_cache WHERE nonce=?",
            (order.get("nonce", ""),),
        ).fetchone(),
    )
    prior = db.execute(
        "SELECT max(connector_sequence) value FROM local_control_orders"
    ).fetchone()["value"]
    check(
        "sequence_monotonic",
        prior is None
        or int(order.get("connector_sequence", 0)) > int(prior),
    )
    attestation = db.execute(
        """SELECT * FROM local_executor_readiness_attestations
           WHERE payload_digest=? AND executor_id=?
           ORDER BY event_sequence DESC LIMIT 1""",
        (
            policy.get("source_executor_status_event_digest"),
            policy.get("executor_id"),
        ),
    ).fetchone()
    latest_attestation = db.execute(
        """SELECT * FROM local_executor_readiness_attestations
           WHERE executor_id=? ORDER BY event_sequence DESC LIMIT 1""",
        (policy.get("executor_id"),),
    ).fetchone()
    check(
        "status_v2_current",
        attestation is not None
        and latest_attestation is not None
        and attestation["id"] == latest_attestation["id"]
        and attestation["readiness_result"]
        == "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
        and datetime.fromisoformat(attestation["expires_at"]) > now_at,
    )
    executor = db.execute(
        "SELECT * FROM local_executors WHERE id=?",
        (policy.get("executor_id"),),
    ).fetchone()
    check(
        "executor_active",
        executor is not None and executor["status"] == "active",
    )
    image = db.execute(
        "SELECT * FROM local_execution_image_manifests WHERE id=?",
        (policy.get("attested_image_manifest_id"),),
    ).fetchone()
    check(
        "image_manifest_binding",
        image is not None
        and image["status"] == "approved"
        and image["image_digest"] == policy.get("image_digest")
        and image["manifest_digest"]
        == policy.get("attested_image_manifest_digest"),
    )
    profile = db.execute(
        "SELECT * FROM local_executor_security_profiles WHERE id=?",
        (policy.get("attested_security_profile_id"),),
    ).fetchone()
    check(
        "security_profile_binding",
        profile is not None
        and profile["status"] == "valid"
        and profile["profile_digest"]
        == policy.get("security_profile_digest"),
    )
    admission = db.execute(
        "SELECT * FROM local_executor_admission_checks WHERE id=?",
        (policy.get("attested_admission_check_id"),),
    ).fetchone()
    try:
        admission_snapshot = (
            json.loads(admission["policy_snapshot"]) if admission else {}
        )
    except (TypeError, json.JSONDecodeError):
        admission_snapshot = {}
    try:
        admission_current = (
            datetime.fromisoformat(admission_snapshot["valid_until"]) > now_at
        )
    except (KeyError, TypeError, ValueError):
        admission_current = False
    check(
        "admission_binding",
        admission is not None
        and admission["decision"] == "approved"
        and admission_current
        and admission["admission_digest"] == policy.get("admission_digest")
        and admission_snapshot.get("image_digest")
        == policy.get("image_digest")
        and admission_snapshot.get("security_profile_digest")
        == policy.get("security_profile_digest")
        and admission_snapshot.get("resource_policy_digest")
        == policy.get("resource_policy_digest")
        and admission_snapshot.get("capability_digest")
        == policy.get("capability_digest"),
    )
    asset = db.execute(
        """SELECT v.id,v.metadata_digest,q.quality_digest,d.status
           FROM local_asset_versions v
           JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
           JOIN local_asset_reviews r ON r.asset_version_id=v.id
             AND r.quality_profile_id=q.id AND r.decision='approved'
           JOIN local_asset_descriptors d ON d.id=v.asset_id
           WHERE d.local_asset_key=? AND v.version_label=?
           ORDER BY q.created_at DESC LIMIT 1""",
        (
            policy.get("local_asset_key"),
            policy.get("local_asset_version_ref"),
        ),
    ).fetchone()
    check(
        "local_asset_binding",
        asset is not None
        and asset["status"] not in {"unavailable", "archived", "deleted"}
        and asset["metadata_digest"]
        == policy.get("local_asset_metadata_digest")
        and asset["quality_digest"] == policy.get("quality_digest"),
    )
    digest_pairs = (
        "local_asset_metadata_digest", "quality_digest",
        "model_reference_digest", "attested_image_manifest_digest",
        "image_digest", "security_profile_digest",
        "resource_policy_digest", "admission_digest", "capability_digest",
        "task_definition_digest", "input_schema_digest",
        "output_schema_digest",
    )
    check(
        "order_proof_digests_bound",
        all(order.get(key) == policy.get(key) for key in digest_pairs),
    )
    failure = next(
        (row["code"] for row in checks if not row["passed"]), None
    )
    return checks, failure


def validate_control_order(item: dict, db: sqlite3.Connection) -> tuple[list[dict], str | None]:
    order = item["order"]
    if (
        order.get("schema_version")
        == "phase5.13E-2C-R1/execution-order/v1"
    ):
        return validate_fixed_execution_order(item, db)
    policy = item["policy"]
    key_info = item["signing_key"]
    checks: list[dict] = []
    policy_fields = {
        "schema_version", "application_id", "application_snapshot_digest",
        "contract_id", "contract_revision_id", "contract_digest",
        "control_readiness_id", "readiness_digest", "connector_id",
        "organization_id", "central_asset_record_id", "central_asset_version_id",
        "local_asset_key", "local_asset_version_ref", "local_asset_metadata_digest",
        "quality_digest", "model_product_version_id", "model_reference_digest",
        "model_materialization_status", "purpose_code", "purpose_summary",
        "requested_action", "execution_authorized", "hard_isolation",
        "allowed_operations", "forbidden_operations", "filesystem_policy",
        "network_policy", "resource_policy", "output_policy", "retention_policy",
        "review_policy", "revocation_policy", "signing_key_id", "issued_at",
        "not_before", "expires_at", "nonce",
    }
    order_fields = {
        "schema_version", "execution_order_id", "policy_bundle_id",
        "policy_bundle_version_id", "policy_payload_digest", "connector_id",
        "connector_sequence", "order_mode", "requested_action",
        "execution_authorized", "correlation_id", "signing_key_id",
        "issued_at", "not_before", "expires_at", "nonce",
    }

    def check(code: str, passed: bool) -> None:
        checks.append({"code": code, "passed": bool(passed)})

    serialized_size = len(canonical_json_text(item).encode("utf-8"))
    forbidden_keys = {"local_path", "patient_id", "private_key", "raw_data", "model_weight"}
    def contains_forbidden(value) -> bool:
        if isinstance(value, dict):
            return any(
                key.lower() in forbidden_keys or contains_forbidden(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(contains_forbidden(child) for child in value)
        return False

    connector_state = db.execute(
        "SELECT value FROM state WHERE key='central_connector_id'"
    ).fetchone()
    expected_connector = json.loads(connector_state["value"]) if connector_state else None
    check("payload_size", serialized_size <= 65536)
    check("policy_schema_supported", policy.get("schema_version") == "phase5.13D/policy-bundle/v1")
    check("order_schema_supported", order.get("schema_version") == "phase5.13D/execution-order/v1")
    check("policy_additional_properties", set(policy) == policy_fields)
    check("order_additional_properties", set(order) == order_fields)
    check("prohibited_fields_absent", not contains_forbidden({"policy": policy, "order": order}))
    check(
        "connector_binding",
        bool(expected_connector)
        and policy.get("connector_id") == expected_connector
        and order.get("connector_id") == expected_connector,
    )
    check("known_active_signing_key", key_info.get("status") == "active")
    check("signing_algorithm_ed25519", key_info.get("algorithm") == "Ed25519")
    public_pem = key_info.get("public_key_material", "")
    check("public_key_fingerprint", digest(public_pem.encode("ascii")) == key_info.get("fingerprint"))
    check("policy_digest", canonical_digest(policy) == item.get("policy_digest"))
    check("order_digest", canonical_digest(order) == item.get("order_digest"))
    def verify_ed25519(payload: dict, signature_value: str) -> bool:
        with tempfile.TemporaryDirectory() as root:
            public_file = Path(root) / "public.pem"
            message_file = Path(root) / "message.bin"
            signature_file = Path(root) / "signature.bin"
            public_file.write_text(public_pem, encoding="ascii")
            message_file.write_bytes(canonical_json_text(payload).encode("utf-8"))
            try:
                signature_file.write_bytes(base64.b64decode(signature_value, validate=True))
            except Exception:
                return False
            return subprocess.run(
                ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin",
                 "-inkey", str(public_file), "-in", str(message_file),
                 "-sigfile", str(signature_file)],
                capture_output=True, check=False,
            ).returncode == 0
    policy_signature_valid = verify_ed25519(policy, item["policy_signature"])
    check("policy_signature", policy_signature_valid)
    order_signature_valid = verify_ed25519(order, item["order_signature"])
    check("order_signature", order_signature_valid)
    check("policy_digest_bound", order.get("policy_payload_digest") == item.get("policy_digest"))
    check("control_mode", order.get("order_mode") == "CONTROL_VALIDATION_ONLY")
    check("validate_only", order.get("requested_action") == "VALIDATE_POLICY_ONLY")
    check("execution_disabled", order.get("execution_authorized") is False and policy.get("execution_authorized") is False)
    check("hard_isolation_false", policy.get("hard_isolation") is False)
    allowed = set(policy.get("allowed_operations") or [])
    forbidden = set(policy.get("forbidden_operations") or [])
    check("operation_sets_disjoint", not allowed.intersection(forbidden))
    check("output_policy_supported", policy.get("output_policy") == {"artifact_creation": False})
    check(
        "model_reference_format",
        str(policy.get("model_reference_digest", "")).startswith("sha256:")
        and len(str(policy.get("model_reference_digest", ""))) == 71,
    )
    current = datetime.now(timezone.utc)
    try:
        temporal = (
            datetime.fromisoformat(order["not_before"]) <= current
            < datetime.fromisoformat(order["expires_at"])
            and datetime.fromisoformat(policy["not_before"]) <= current
            < datetime.fromisoformat(policy["expires_at"])
        )
    except Exception:
        temporal = False
    check("valid_time_window", temporal)
    nonce = order.get("nonce", "")
    check("nonce_not_replayed", not db.execute(
        "SELECT 1 FROM local_order_replay_cache WHERE nonce=?", (nonce,)
    ).fetchone())
    prior = db.execute("SELECT max(connector_sequence) value FROM local_control_orders").fetchone()["value"]
    check("sequence_monotonic", prior is None or int(order.get("connector_sequence", 0)) > int(prior))
    asset = db.execute(
        """SELECT v.metadata_digest,q.quality_digest,d.status asset_status FROM local_asset_versions v
           JOIN local_data_quality_profiles q ON q.asset_version_id=v.id
           JOIN local_asset_reviews r ON r.asset_version_id=v.id
             AND r.quality_profile_id=q.id AND r.decision='approved'
           JOIN local_asset_descriptors d ON d.id=v.asset_id
           WHERE d.local_asset_key=? AND v.version_label=?
           ORDER BY q.created_at DESC LIMIT 1""",
        (policy.get("local_asset_key"), policy.get("local_asset_version_ref")),
    ).fetchone()
    check("local_asset_version_approved", asset is not None)
    check("local_asset_available", bool(asset and asset["asset_status"] not in {"unavailable", "archived", "deleted"}))
    check("metadata_digest_match", bool(asset and asset["metadata_digest"] == policy.get("local_asset_metadata_digest")))
    check("quality_digest_match", bool(asset and asset["quality_digest"] == policy.get("quality_digest")))
    check("model_reference_metadata_only", policy.get("model_materialization_status") == "NOT_EVALUATED_IN_PHASE_5_13D")
    failure = next((row["code"] for row in checks if not row["passed"]), None)
    return checks, failure


def deliver_signed_message(path: str, payload: dict) -> bool:
    connector_id = get_state("central_connector_id")
    envelope = {
        "payload": payload, "payload_digest": canonical_digest(payload),
        "signature": sign_connector_payload(payload),
    }
    with client() as mtls:
        response = mtls.post(
            f"{policy_ingress()}/connectors/{connector_id}/{path}",
            json=envelope,
            headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")},
        )
    return response.status_code < 400


@app.get("/local/orders", response_class=HTMLResponse)
def local_orders(request: Request) -> str:
    user = session_user(request, role="local_policy_reviewer")
    with connect() as db:
        rows = db.execute(
            "SELECT * FROM local_control_orders ORDER BY connector_sequence DESC"
        ).fetchall()

    def order_row(row: sqlite3.Row) -> str:
        payload = json.loads(row["order_payload"])
        fixed = payload.get("order_mode") == "FIXED_REFERENCE_EXECUTION"
        boundary = (
            "Fixed reference only / authorized once / not executed"
            if fixed else "Validation only / not executed"
        )
        return (
            f"<tr><td><a href='/local/orders/{row['id']}'>"
            f"{html.escape(row['central_order_id'])}</a></td>"
            f"<td>{row['connector_sequence']}</td>"
            f"<td>{html.escape(row['local_status'])}</td>"
            f"<td>{html.escape(boundary)}</td></tr>"
        )

    body = """
    <p class="notice">Incoming signed orders require automated validation and
    an independent local policy decision. No task is started on this page.</p>
    <form method="post" action="/local/orders/pull"><button class="primary">Pull signed control orders</button></form>
    <div class="table-wrap"><table><thead><tr><th>Order</th><th>Sequence</th><th>Local status</th><th>Boundary</th></tr></thead><tbody>
    """ + "".join(order_row(row) for row in rows) + "</tbody></table></div>"
    return page("Incoming Control Orders", body, user)


@app.post("/local/orders/pull")
def pull_control_orders(request: Request) -> RedirectResponse:
    user = session_user(request, role="local_policy_reviewer")
    connector_id = get_state("central_connector_id")
    if not connector_id or get_state("connector_status") != "active":
        raise HTTPException(409, "active Connector is required")
    with connect() as db:
        after = db.execute("SELECT coalesce(max(connector_sequence),0) value FROM local_control_orders").fetchone()["value"]
    with client() as mtls:
        response = mtls.get(
            f"{policy_ingress()}/connectors/{connector_id}/orders/available",
            params={"after_sequence": max(0, int(after) - 10)},
            headers={"X-Client-Certificate-Fingerprint": get_state("certificate_fingerprint")},
        )
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text[:500])
    for item in response.json()["items"]:
        order = item["order"]
        central_order_id = item["order_key"]
        with connect() as db:
            existing = db.execute(
                "SELECT * FROM local_control_orders WHERE central_order_id=?", (central_order_id,)
            ).fetchone()
            if item["central_status"] == "revoked" and existing:
                if (
                    existing["central_status"] == "revoked"
                    and existing["local_status"]
                    in {"revoked", "revoked_after_acceptance"}
                ):
                    audit(
                        "policy_revocation.duplicate_received",
                        {"execution_order_id": existing["id"]},
                    )
                    continue
                if existing["local_status"] == "accepted":
                    decision_payload = {
                        "schema_version": "phase5.13D/connector-decision/v1",
                        "execution_order_id": existing["id"],
                        "decision": "revoked_after_acceptance",
                        "reason_code": "CENTRAL_POLICY_REVOKED",
                        "reason_text": "Previously accepted control-only order was revoked before execution.",
                        "decided_at": now(), "execution_started": False,
                    }
                    db.execute(
                        "UPDATE local_control_orders SET local_status='revoked_after_acceptance',central_status='revoked' WHERE id=?",
                        (existing["id"],),
                    )
                    db.execute(
                        """UPDATE local_execution_authorization_snapshots
                           SET status='revoked'
                           WHERE local_order_id=? AND status='validated'""",
                        (existing["id"],),
                    )
                    db.commit()
                    try:
                        delivered = deliver_signed_message(
                            f"orders/{existing['id']}/decision",
                            decision_payload,
                        )
                    except httpx.HTTPError:
                        delivered = False
                    audit("policy_revocation.received", {"execution_order_id": existing["id"], "delivered": delivered})
                else:
                    db.execute(
                        """UPDATE local_control_orders
                           SET local_status='revoked',central_status='revoked'
                           WHERE id=?""",
                        (existing["id"],),
                    )
                    db.execute(
                        """UPDATE local_execution_authorization_snapshots
                           SET status='revoked'
                           WHERE local_order_id=? AND status='validated'""",
                        (existing["id"],),
                    )
                    db.commit()
                    audit(
                        "policy_revocation.received",
                        {
                            "execution_order_id": existing["id"],
                            "prior_local_status": existing["local_status"],
                            "delivered": False,
                        },
                    )
                continue
            if existing:
                audit("control_order.duplicate_received", {"execution_order_id": existing["id"]})
                continue
            checks, failure = validate_control_order(item, db)
            if item["central_status"] == "revoked":
                checks.append({"code": "central_not_revoked", "passed": False})
                failure = "CENTRAL_POLICY_REVOKED"
            local_order_id = item["execution_order_id"]
            status = (
                "revoked" if item["central_status"] == "revoked"
                else "validation_failed" if failure
                else "awaiting_local_review"
            )
            db.execute(
                """INSERT INTO local_control_orders
                   (id,central_order_id,connector_sequence,order_payload,order_digest,order_signature,
                    policy_payload,policy_digest,policy_signature,signing_key_id,signing_public_key,
                    signing_key_fingerprint,central_status,local_status,received_at,expires_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (local_order_id, central_order_id, order["connector_sequence"],
                 json.dumps(order), item["order_digest"], item["order_signature"],
                 json.dumps(item["policy"]), item["policy_digest"], item["policy_signature"],
                 item["signing_key"]["key_id"], item["signing_key"]["public_key_material"],
                 item["signing_key"]["fingerprint"], item["central_status"], status,
                 now(), order["expires_at"]),
            )
            db.execute(
                """INSERT INTO local_policy_validations
                   (id,local_order_id,validation_status,checks_json,failure_code,validated_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(uuid4()), local_order_id, "failed" if failure else "passed",
                 json.dumps(checks), failure, now()),
            )
            db.execute(
                "INSERT INTO local_order_replay_cache(nonce,central_order_id,connector_sequence,first_seen_at) VALUES(?,?,?,?)",
                (order["nonce"], central_order_id, order["connector_sequence"], now()),
            )
            fixed_order = (
                order.get("order_mode") == "FIXED_REFERENCE_EXECUTION"
            )
            receipt_id = str(uuid4())
            validation_digest = canonical_digest(
                {"checks": checks, "failure_code": failure}
            )
            if fixed_order:
                receipt_payload = {
                    "schema_version":
                        "phase5.13E-2C-R1/connector-receipt/v1",
                    "receipt_id": receipt_id,
                    "execution_order_id": local_order_id,
                    "central_order_key": central_order_id,
                    "connector_sequence": order["connector_sequence"],
                    "order_digest": item["order_digest"],
                    "policy_digest": item["policy_digest"],
                    "source_executor_status_event_digest":
                        order["source_executor_status_event_digest"],
                    "validation_status": (
                        "failed" if failure else "passed"
                    ),
                    "automated_validation_digest": validation_digest,
                    "received_at": now(),
                    "local_audit_head": current_audit_head(db),
                    "execution_started": False,
                    "hard_isolation": False,
                }
            else:
                receipt_payload = {
                    "schema_version": "phase5.13D/connector-receipt/v1",
                    "execution_order_id": local_order_id,
                    "central_order_key": central_order_id,
                    "connector_sequence": order["connector_sequence"],
                    "order_digest": item["order_digest"],
                    "policy_digest": item["policy_digest"],
                    "validation_status": (
                        "failed" if failure else "passed"
                    ),
                    "failure_code": failure,
                    "received_at": now(),
                    "execution_started": False,
                }
            receipt_digest = canonical_digest(receipt_payload)
            receipt_signature = sign_connector_payload(receipt_payload)
            db.execute(
                """INSERT INTO local_order_receipts
                   (id,local_order_id,payload_json,payload_digest,signature,delivery_status,created_at)
                   VALUES(?,?,?,?,?,'pending',?)""",
                (receipt_id, local_order_id, json.dumps(receipt_payload), receipt_digest,
                 receipt_signature, now()),
            )
            db.commit()
        delivered = deliver_signed_message(f"orders/{local_order_id}/receipt", receipt_payload)
        decision_payload = None
        decision_delivered = False
        if failure:
            decision_id = str(uuid4())
            if fixed_order:
                decision_payload = {
                    "schema_version":
                        "phase5.13E-2C-R1/connector-decision/v1",
                    "decision_id": decision_id,
                    "execution_order_id": local_order_id,
                    "receipt_id": receipt_id,
                    "receipt_digest": receipt_digest,
                    "policy_digest": item["policy_digest"],
                    "order_digest": item["order_digest"],
                    "source_executor_status_event_digest":
                        order["source_executor_status_event_digest"],
                    "automated_validation_digest": validation_digest,
                    "reviewer_id": "automated-validator",
                    "decision": "validation_failed",
                    "reason_code": failure,
                    "reason_text":
                        "Automated fixed policy validation failed.",
                    "decided_at": now(),
                    "local_audit_head": receipt_payload["local_audit_head"],
                    "execution_started": False,
                    "hard_isolation": False,
                }
            else:
                decision_payload = {
                    "schema_version": "phase5.13D/connector-decision/v1",
                    "execution_order_id": local_order_id,
                    "decision": "validation_failed",
                    "reason_code": failure,
                    "reason_text": "Automated policy validation failed.",
                    "decided_at": now(),
                    "execution_started": False,
                }
            decision_delivered = deliver_signed_message(
                f"orders/{local_order_id}/decision", decision_payload
            )
        with connect() as db:
            db.execute(
                "UPDATE local_order_receipts SET delivery_status=?,delivered_at=? WHERE local_order_id=?",
                ("delivered" if delivered else "failed", now() if delivered else None, local_order_id),
            )
            if decision_payload:
                db.execute(
                    """INSERT INTO local_order_decisions
                       (id,local_order_id,payload_json,payload_digest,signature,delivery_status,created_at,delivered_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (decision_id, local_order_id, json.dumps(decision_payload),
                     canonical_digest(decision_payload), sign_connector_payload(decision_payload),
                     "delivered" if decision_delivered else "failed", now(),
                     now() if decision_delivered else None),
                )
            db.commit()
        audit("control_order.received", {"execution_order_id": local_order_id, "validation_status": "failed" if failure else "passed"})
    return redirect("/local/orders")


@app.get("/local/orders/{order_id}", response_class=HTMLResponse)
def local_order_detail(request: Request, order_id: str) -> str:
    user = session_user(request, role="local_policy_reviewer")
    with connect() as db:
        row = db.execute("SELECT * FROM local_control_orders WHERE id=?", (order_id,)).fetchone()
        validation = db.execute("SELECT * FROM local_policy_validations WHERE local_order_id=?", (order_id,)).fetchone()
        review = db.execute("SELECT * FROM local_policy_reviews WHERE local_order_id=?", (order_id,)).fetchone()
        receipt = db.execute("SELECT * FROM local_order_receipts WHERE local_order_id=?", (order_id,)).fetchone()
        decision_row = db.execute("SELECT * FROM local_order_decisions WHERE local_order_id=?", (order_id,)).fetchone()
        snapshot = db.execute(
            "SELECT * FROM local_execution_authorization_snapshots WHERE local_order_id=?",
            (order_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "LOCAL_ORDER_NOT_FOUND")
    policy = json.loads(row["policy_payload"])
    order_payload = json.loads(row["order_payload"])
    fixed = order_payload.get("order_mode") == "FIXED_REFERENCE_EXECUTION"
    checks = json.loads(validation["checks_json"])
    checklist = "".join(
        f"<li>{'PASS' if item['passed'] else 'FAIL'} - {html.escape(item['code'])}</li>"
        for item in checks
    )
    action = ""
    if row["local_status"] == "awaiting_local_review" and not review:
        accept_label = "Accept fixed reference authorization" if fixed else "Accept control validation only"
        default_code = "ACCEPT_FIXED_REFERENCE_EXECUTION" if fixed else "LOCAL_POLICY_REVIEWED"
        default_note = (
            "Fixed reference scope reviewed; authorization remains unconsumed."
            if fixed else "Reviewed for control validation only; no execution authorized."
        )
        action = f"""<form class="panel" method="post" action="/local/orders/{order_id}/decision">
        <label for="decision">Independent local decision</label>
        <select id="decision" name="decision"><option value="accepted">{accept_label}</option><option value="rejected">Reject</option></select>
        <label for="reason_code">Reason code</label><input id="reason_code" name="reason_code" value="{default_code}" required>
        <label for="reason_text">Review note</label><textarea id="reason_text" name="reason_text" required>{default_note}</textarea>
        <button class="primary" type="submit">Record signed local decision</button></form>"""
    if fixed:
        snapshot_panel = (
            f"<div class='panel'><h2>Authorization Snapshot</h2>Status: <strong>{html.escape(snapshot['status'])}</strong><br>Digest: <code>{html.escape(snapshot['snapshot_digest'])}</code></div>"
            if snapshot else "<div class='panel'><h2>Authorization Snapshot</h2>Not created</div>"
        )
        body = f"""
    <p class="notice"><strong>{html.escape(row['local_status'])}</strong> - fixed reference authorization only; not executed; hard_isolation=false.</p>
    <div class="grid"><div class="panel"><strong>Order</strong><br>{html.escape(row['central_order_id'])}<br>
    {html.escape(order_payload['requested_action'])}<br>{html.escape(order_payload['task_type'])}<br>
    execution_authorized=true / max executions=1</div>
    <div class="panel"><strong>Asset reference</strong><br>{html.escape(policy['local_asset_key'])} / {html.escape(policy['local_asset_version_ref'])}<br>Model: fixed metadata reference</div></div>
    <div class="grid"><div class="panel"><strong>Status v2</strong><br>verified / Connector-attested<br>
    <code>{html.escape(policy['source_executor_status_event_digest'])}</code><br>Not independently inspected by central</div>
    <div class="panel"><strong>Security boundary</strong><br>network=none / rootless=true<br>
    no Docker socket / no dynamic transfer / no auto egress<br>hard_isolation=false</div></div>
    <div class="panel"><h2>Automated validation</h2><ul>{checklist}</ul></div>
    <div class="panel"><h2>Allowed outputs</h2><pre>{html.escape(json.dumps(policy['output_policy']['allowed_files'], indent=2))}</pre></div>
    <div class="grid"><div class="panel"><h2>Receipt</h2>{html.escape(receipt['payload_digest']) if receipt else 'Not created'}<br>
    Delivered: {str(bool(receipt and receipt['delivery_status'] == 'delivered')).lower()}</div>
    <div class="panel"><h2>Decision</h2>{html.escape(decision_row['payload_digest']) if decision_row else 'Not created'}</div></div>
    {snapshot_panel}{action}
    """
    else:
        body = f"""
    <p class="notice"><strong>{html.escape(row['local_status'])}</strong> - accepted means control validation only, not executed.</p>
    <div class="grid"><div class="panel"><strong>Order</strong><br>{html.escape(row['central_order_id'])}<br>VALIDATE_POLICY_ONLY<br>execution_authorized=false</div>
    <div class="panel"><strong>Asset reference</strong><br>{html.escape(policy['local_asset_key'])} / {html.escape(policy['local_asset_version_ref'])}<br>Model: metadata reference only</div></div>
    <div class="panel"><h2>Automated validation</h2><ul>{checklist}</ul></div>
    <div class="panel"><h2>Allowed</h2><pre>{html.escape(json.dumps(policy['allowed_operations'], indent=2))}</pre>
    <h2>Forbidden</h2><pre>{html.escape(json.dumps(policy['forbidden_operations'], indent=2))}</pre></div>{action}
    """
    return page("Control Order Detail", body, user)


@app.post("/local/orders/{order_id}/decision")
def local_order_decision(
    request: Request, order_id: str, decision: str = Form(),
    reason_code: str = Form(), reason_text: str = Form(),
) -> RedirectResponse:
    user = session_user(request, role="local_policy_reviewer")
    if decision not in {"accepted", "rejected"}:
        raise HTTPException(400, "LOCAL_POLICY_DECISION_INVALID")
    with connect() as db:
        order = db.execute("SELECT * FROM local_control_orders WHERE id=?", (order_id,)).fetchone()
        validation = db.execute("SELECT * FROM local_policy_validations WHERE local_order_id=?", (order_id,)).fetchone()
        receipt = db.execute(
            "SELECT * FROM local_order_receipts WHERE local_order_id=?",
            (order_id,),
        ).fetchone()
        if order and order["central_status"] == "revoked":
            raise HTTPException(409, "CENTRAL_POLICY_REVOKED")
        if not order or order["local_status"] != "awaiting_local_review":
            raise HTTPException(409, "LOCAL_ORDER_NOT_REVIEWABLE")
        if not validation or validation["validation_status"] != "passed":
            raise HTTPException(409, "AUTOMATED_FAILURE_CANNOT_BE_OVERRIDDEN")
        decided = now()
        order_payload = json.loads(order["order_payload"])
        fixed = (
            order_payload.get("order_mode") == "FIXED_REFERENCE_EXECUTION"
        )
        decision_id = str(uuid4())
        db.execute(
            """INSERT INTO local_policy_reviews
               (id,local_order_id,reviewer_id,decision,reason_code,reason_text,decided_at)
               VALUES(?,?,?,?,?,?,?)""",
            (str(uuid4()), order_id, user["id"], decision, reason_code, reason_text, decided),
        )
        db.execute("UPDATE local_control_orders SET local_status=? WHERE id=?", (decision, order_id))
        if fixed:
            if receipt is None:
                raise HTTPException(409, "SIGNED_RECEIPT_REQUIRED")
            receipt_payload = json.loads(receipt["payload_json"])
            payload = {
                "schema_version":
                    "phase5.13E-2C-R1/connector-decision/v1",
                "decision_id": decision_id,
                "execution_order_id": order_id,
                "receipt_id": receipt["id"],
                "receipt_digest": receipt["payload_digest"],
                "policy_digest": order["policy_digest"],
                "order_digest": order["order_digest"],
                "source_executor_status_event_digest":
                    order_payload["source_executor_status_event_digest"],
                "automated_validation_digest":
                    receipt_payload["automated_validation_digest"],
                "reviewer_id": user["id"],
                "decision": decision,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "decided_at": decided,
                "local_audit_head": current_audit_head(db),
                "execution_started": False,
                "hard_isolation": False,
            }
        else:
            payload = {
                "schema_version": "phase5.13D/connector-decision/v1",
                "execution_order_id": order_id,
                "decision": decision,
                "reason_code": reason_code,
                "reason_text": reason_text,
                "decided_at": decided,
                "execution_started": False,
            }
        db.execute(
            """INSERT INTO local_order_decisions
               (id,local_order_id,payload_json,payload_digest,signature,delivery_status,created_at)
               VALUES(?,?,?,?,?,'pending',?)""",
            (decision_id, order_id, json.dumps(payload), canonical_digest(payload),
             sign_connector_payload(payload), decided),
        )
        db.commit()
        snapshot = None
        if fixed and decision == "accepted":
            try:
                snapshot = create_execution_authorization_snapshot_from_order(
                    db,
                    local_order_id=order_id,
                    canonical_digest=canonical_digest,
                    signer=sign_connector_payload,
                )
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
    delivered = deliver_signed_message(f"orders/{order_id}/decision", payload)
    with connect() as db:
        db.execute(
            "UPDATE local_order_decisions SET delivery_status=?,delivered_at=? WHERE local_order_id=?",
            ("delivered" if delivered else "failed", now() if delivered else None, order_id),
        )
        db.commit()
    audit(f"policy_review.{decision}", {
        "execution_order_id": order_id, "actor_id": user["id"],
        "reason_code": reason_code, "execution_started": False,
        "authorization_snapshot_id": snapshot["id"] if snapshot else None,
    })
    return redirect(f"/local/orders/{order_id}")

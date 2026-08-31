from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import shutil
import ssl
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.phase4 import DemoActor
from app.modules.audit import canonical_json_digest_v1, canonical_json_text_v1
from app.modules.connector_control.models import (
    ConnectorCapabilityManifest,
    ConnectorCertificate,
    ConnectorEnrollmentToken,
    ConnectorHeartbeat,
    ConnectorRegistrationRequest,
    ConnectorControlAuditEvent,
    ConnectorAssetMirror,
    ConnectorAssetMirrorVersion,
    HospitalEvidenceBundleReceipt,
    HospitalExecutorMirror,
    HospitalExecutorStatusEvent,
    HospitalConnector,
)
from app.modules.policy_control.models import (
    ExecutionOrder,
    ExecutionOrderConsumptionReceipt,
    PolicyBundleVersion,
)
from app.modules.spaces.models import Space


class ConnectorControlError(ValueError):
    pass


EXECUTOR_STATUS_FIELDS = {
    "schema_version", "executor_instance_id", "executor_version", "architecture",
    "status", "certificate_fingerprint", "capability_manifest_digest",
    "runtime_digest", "image_digest", "security_status", "status_sequence",
    "heartbeat_sequence", "heartbeat_at", "event_type", "execution_enabled",
    "hard_isolation", "sent_at", "nonce", "payload_digest",
}

EXECUTOR_PROHIBITED_FIELDS = {
    "command", "arguments", "environment", "input_path", "output_path",
    "local_path", "patient_id", "patient_identifier", "raw_data", "model_weights",
    "private_key", "certificate_pem", "script", "source_code", "job_id",
    "run_id", "artifact_id",
}

EXECUTOR_READINESS_V2_SCHEMA = "hospital_executor_status_v2"
EXECUTOR_READINESS_V2_EVENT = (
    "EXECUTOR_FIXED_EXECUTION_READINESS_ATTESTATION"
)
EVIDENCE_BUNDLE_SCHEMA = "phase5.13E-Final/evidence-bundle/v1"
EVIDENCE_PROHIBITED_FIELDS = {
    "local_path", "relative_reference", "object_key", "download_url",
    "patient_id", "patient_identifier", "medical_record_number",
    "accession_number", "raw_data", "raw_filename", "model_weights",
    "private_key", "password", "token", "environment", "stdout", "stderr",
}
CONNECTOR_IDENTITY_URI_PREFIX = "urn:medtrust:connector:"
LEGACY_CN_ONLY_CSR_ALLOWED = True


class ConnectorCertificateMetadata:
    def __init__(
        self, *, der_bytes: bytes, fingerprint_sha256: str, serial_number: str,
        subject: str, issuer: str, valid_from: datetime, valid_to: datetime,
        san_entries: tuple[str, ...], has_san_extension: bool,
    ) -> None:
        self.der_bytes = der_bytes
        self.fingerprint_sha256 = fingerprint_sha256
        self.serial_number = serial_number
        self.subject = subject
        self.issuer = issuer
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.san_entries = san_entries
        self.san_uris = tuple(
            entry[len("URI:"):] for entry in san_entries
            if entry.startswith("URI:")
        )
        self.has_san_extension = has_san_extension


def sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _openssl() -> str:
    executable = shutil.which(os.environ.get("MEDTRUST_OPENSSL", "openssl"))
    if not executable:
        raise ConnectorControlError("OPENSSL_UNAVAILABLE")
    return executable


def _common_name(subject: str) -> str | None:
    match = re.search(r"(?:^|,)CN=([^,]+)", subject)
    return None if match is None else match.group(1)


def _subject_alt_name_entries(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if "X509v3 Subject Alternative Name:" not in line:
            continue
        entries: list[str] = []
        for child in lines[index + 1:]:
            stripped = child.strip()
            if not stripped:
                continue
            if stripped.startswith("X509v3 ") or not child[:1].isspace():
                break
            entries.extend(
                item.strip() for item in stripped.split(",") if item.strip()
            )
        return tuple(entries)
    return ()


def _validate_connector_identity(
    *, subject: str, san_entries: tuple[str, ...], has_san_extension: bool,
    connector_instance_id: str, error_code: str,
) -> None:
    expected_uri = f"{CONNECTOR_IDENTITY_URI_PREFIX}{connector_instance_id}"
    if has_san_extension:
        if san_entries != (f"URI:{expected_uri}",):
            raise ConnectorControlError(error_code)
        return
    if (
        not LEGACY_CN_ONLY_CSR_ALLOWED
        or _common_name(subject) != connector_instance_id
    ):
        raise ConnectorControlError(error_code)


def _certificate_metadata(certificate_pem: bytes) -> ConnectorCertificateMetadata:
    try:
        text = certificate_pem.decode("ascii").replace("\r\n", "\n")
        if "\r" in text:
            raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID")
        text = text.strip() + "\n"
    except UnicodeDecodeError as exc:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID") from exc
    if (
        text.count("-----BEGIN CERTIFICATE-----") != 1
        or text.count("-----END CERTIFICATE-----") != 1
        or not text.startswith("-----BEGIN CERTIFICATE-----\n")
        or not text.endswith("-----END CERTIFICATE-----\n")
    ):
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID")
    try:
        der = ssl.PEM_cert_to_DER_cert(text)
    except (ValueError, binascii.Error) as exc:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID") from exc
    try:
        result = subprocess.run(
            [
                _openssl(), "x509", "-noout", "-subject", "-issuer",
                "-serial", "-dates", "-ext", "subjectAltName", "-nameopt",
                "RFC2253",
            ],
            input=text.encode("ascii"), capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID") from exc
    if result.returncode != 0:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID")
    output = result.stdout.decode("utf-8", "replace")
    values: dict[str, str] = {}
    for line in output.splitlines():
        for prefix, key in (
            ("subject=", "subject"), ("issuer=", "issuer"),
            ("serial=", "serial"), ("notBefore=", "not_before"),
            ("notAfter=", "not_after"),
        ):
            if line.startswith(prefix):
                values[key] = line[len(prefix):].strip()
    if set(values) != {"subject", "issuer", "serial", "not_before", "not_after"}:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID")
    try:
        valid_from = parsedate_to_datetime(values["not_before"]).astimezone(timezone.utc)
        valid_to = parsedate_to_datetime(values["not_after"]).astimezone(timezone.utc)
    except (TypeError, ValueError) as exc:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID") from exc
    return ConnectorCertificateMetadata(
        der_bytes=der,
        fingerprint_sha256=sha256_bytes(der),
        serial_number=values["serial"].upper(),
        subject=values["subject"],
        issuer=values["issuer"],
        valid_from=valid_from,
        valid_to=valid_to,
        san_entries=_subject_alt_name_entries(output),
        has_san_extension="X509v3 Subject Alternative Name" in output,
    )


def verify_presented_connector_certificate(
    *, escaped_certificate: str, connector: HospitalConnector,
    certificate: ConnectorCertificate, checked_at: datetime | None = None,
) -> str:
    try:
        presented_pem = unquote(escaped_certificate, errors="strict").encode("ascii")
    except (UnicodeEncodeError, UnicodeDecodeError) as exc:
        raise ConnectorControlError("CLIENT_CERTIFICATE_INVALID") from exc
    presented = _certificate_metadata(presented_pem)
    stored = _certificate_metadata(certificate.certificate_pem)
    now = checked_at or _now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if (
        certificate.id != connector.current_certificate_id
        or certificate.connector_id != connector.id
        or certificate.status != "active"
        or certificate.revoked_at is not None
        or presented.der_bytes != stored.der_bytes
        or presented.valid_from > now
        or presented.valid_to <= now
    ):
        raise ConnectorControlError("CLIENT_CERTIFICATE_RECORD_MISMATCH")
    _validate_connector_identity(
        subject=presented.subject,
        san_entries=presented.san_entries,
        has_san_extension=presented.has_san_extension,
        connector_instance_id=connector.connector_instance_id,
        error_code="CLIENT_CERTIFICATE_IDENTITY_MISMATCH",
    )
    # Phase 5.13B rows used a PEM hash and synthetic metadata. Compatibility
    # is limited to the exact stored DER certificate; headers remain irrelevant.
    legacy_record = (
        certificate.fingerprint_sha256
        == sha256_bytes(certificate.certificate_pem)
    )
    if legacy_record:
        return certificate.fingerprint_sha256
    recorded_valid_from = certificate.valid_from
    recorded_valid_to = certificate.valid_to
    if recorded_valid_from.tzinfo is None:
        recorded_valid_from = recorded_valid_from.replace(tzinfo=timezone.utc)
    if recorded_valid_to.tzinfo is None:
        recorded_valid_to = recorded_valid_to.replace(tzinfo=timezone.utc)
    if (
        presented.fingerprint_sha256 != certificate.fingerprint_sha256
        or presented.serial_number != certificate.serial_number.upper()
        or presented.subject != certificate.subject
        or presented.issuer != certificate.issuer
        or presented.valid_from != recorded_valid_from.astimezone(timezone.utc)
        or presented.valid_to != recorded_valid_to.astimezone(timezone.utc)
    ):
        raise ConnectorControlError("CLIENT_CERTIFICATE_RECORD_MISMATCH")
    return certificate.fingerprint_sha256


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_operator(actor: DemoActor) -> None:
    if actor.role != "space_operator":
        raise ConnectorControlError("only the platform operator may perform this action")


async def append_control_audit(
    session: AsyncSession,
    *,
    space_id: UUID,
    event_type: str,
    subject_type: str,
    subject_id: UUID,
    evidence: dict[str, Any],
    actor: DemoActor | None = None,
    actor_connector_id: UUID | None = None,
) -> ConnectorControlAuditEvent:
    await session.scalar(select(Space).where(Space.id == space_id).with_for_update())
    prior = await session.scalar(
        select(ConnectorControlAuditEvent)
        .where(ConnectorControlAuditEvent.space_id == space_id)
        .order_by(ConnectorControlAuditEvent.stream_sequence.desc())
        .limit(1)
    )
    sequence = 1 if prior is None else prior.stream_sequence + 1
    occurred_at = _now()
    body = {
        "space_id": str(space_id), "sequence": sequence, "event_type": event_type,
        "subject_type": subject_type, "subject_id": str(subject_id),
        "actor_type": "operator" if actor else ("hospital_connector" if actor_connector_id else "system"),
        "actor_user_id": str(actor.user_id) if actor else None,
        "actor_connector_id": str(actor_connector_id) if actor_connector_id else None,
        "occurred_at": occurred_at.isoformat(), "evidence": evidence,
        "previous_event_digest": prior.event_digest if prior else None,
    }
    row = ConnectorControlAuditEvent(
        space_id=space_id, stream_sequence=sequence, event_type=event_type,
        subject_type=subject_type, subject_id=subject_id,
        actor_type=body["actor_type"], actor_user_id=actor.user_id if actor else None,
        actor_connector_id=actor_connector_id, occurred_at=occurred_at,
        evidence_snapshot=evidence, previous_event_digest=body["previous_event_digest"],
        event_digest=canonical_json_digest_v1(body),
    )
    session.add(row)
    await session.flush()
    return row


async def create_enrollment_token(
    session: AsyncSession,
    *,
    actor: DemoActor,
    space_id: UUID,
    organization_id: UUID,
    connector_name: str,
    lifetime_minutes: int = 15,
) -> tuple[ConnectorEnrollmentToken, str]:
    _require_operator(actor)
    raw = secrets.token_urlsafe(48)
    row = ConnectorEnrollmentToken(
        space_id=space_id,
        organization_id=organization_id,
        connector_name=connector_name.strip(),
        token_digest=sha256_text(raw),
        expires_at=_now() + timedelta(minutes=lifetime_minutes),
        created_by=actor.user_id,
    )
    session.add(row)
    await session.flush()
    await append_control_audit(
        session, space_id=space_id, event_type="connector.enrollment_token.created",
        subject_type="connector_enrollment_token", subject_id=row.id,
        evidence={"organization_id": str(organization_id), "connector_name": row.connector_name, "expires_at": row.expires_at.isoformat(), "token_disclosed_once": True},
        actor=actor,
    )
    return row, raw


def _validate_csr(csr: bytes, *, connector_instance_id: str) -> None:
    if not (300 <= len(csr) <= 8192) or b"BEGIN CERTIFICATE REQUEST" not in csr:
        raise ConnectorControlError("CSR_FORMAT_INVALID")
    openssl = _openssl()
    result = subprocess.run(
        [openssl, "req", "-verify", "-noout"],
        input=csr,
        capture_output=True,
        timeout=10,
    )
    if result.returncode != 0:
        raise ConnectorControlError("CSR_SIGNATURE_INVALID")
    detail = subprocess.run(
        [openssl, "req", "-text", "-noout"],
        input=csr,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.decode("utf-8", "replace")
    if "Public-Key: (2048 bit)" not in detail and "Public-Key: (3072 bit)" not in detail and "Public-Key: (4096 bit)" not in detail:
        raise ConnectorControlError("CSR_PUBLIC_KEY_TOO_WEAK")
    requested_extensions = set(re.findall(r"^\s*X509v3 ([^:]+):", detail, re.MULTILINE))
    if requested_extensions - {"Subject Alternative Name"}:
        raise ConnectorControlError("CSR_EXTENSION_NOT_ALLOWED")
    subject_result = subprocess.run(
        [openssl, "req", "-subject", "-noout", "-nameopt", "RFC2253"],
        input=csr, capture_output=True, timeout=10,
    )
    if subject_result.returncode != 0:
        raise ConnectorControlError("CSR_FORMAT_INVALID")
    subject = subject_result.stdout.decode("utf-8", "replace").strip()
    if subject.startswith("subject="):
        subject = subject[len("subject="):].strip()
    _validate_connector_identity(
        subject=subject,
        san_entries=_subject_alt_name_entries(detail),
        has_san_extension="X509v3 Subject Alternative Name" in detail,
        connector_instance_id=connector_instance_id,
        error_code="CSR_IDENTITY_MISMATCH",
    )


async def submit_registration(
    session: AsyncSession, *, raw_token: str, payload: dict[str, Any]
) -> ConnectorRegistrationRequest:
    now = _now()
    token = await session.scalar(
        select(ConnectorEnrollmentToken)
        .where(ConnectorEnrollmentToken.token_digest == sha256_text(raw_token))
        .with_for_update()
    )
    if token is None:
        raise ConnectorControlError("ENROLLMENT_TOKEN_INVALID")
    if token.status != "active" or token.used_at is not None:
        raise ConnectorControlError("ENROLLMENT_TOKEN_CONSUMED")
    if token.expires_at <= now:
        token.status = "expired"
        raise ConnectorControlError("ENROLLMENT_TOKEN_EXPIRED")
    if payload["display_name"].strip() != token.connector_name:
        raise ConnectorControlError("CONNECTOR_NAME_MISMATCH")
    if UUID(str(payload["organization_id"])) != token.organization_id:
        raise ConnectorControlError("ORGANIZATION_MISMATCH")
    sent = payload["request_timestamp"]
    if abs((now - sent).total_seconds()) > 300:
        raise ConnectorControlError("TIMESTAMP_OUT_OF_WINDOW")
    csr = payload["csr_pem"].encode("ascii")
    _validate_csr(csr, connector_instance_id=payload["connector_instance_id"])
    row = ConnectorRegistrationRequest(
        enrollment_token_id=token.id,
        space_id=token.space_id,
        organization_id=token.organization_id,
        connector_instance_id=payload["connector_instance_id"],
        installation_digest=payload["installation_digest"],
        display_name=payload["display_name"].strip(),
        csr_pem=csr,
        csr_fingerprint=sha256_bytes(csr),
        connector_version=payload["connector_version"],
        operating_system=payload["operating_system"],
        architecture=payload["architecture"],
        bootstrap_manifest_digest=payload["bootstrap_manifest_digest"],
        nonce=payload["nonce"],
        request_timestamp=sent,
    )
    session.add(row)
    await session.flush()
    token.status = "consumed"
    token.used_at = now
    token.used_by_connector_request_id = row.id
    await append_control_audit(
        session, space_id=row.space_id, event_type="connector.registration.submitted",
        subject_type="connector_registration_request", subject_id=row.id,
        evidence={"connector_instance_id": row.connector_instance_id, "installation_digest": row.installation_digest, "csr_fingerprint": row.csr_fingerprint, "bootstrap_manifest_digest": row.bootstrap_manifest_digest},
    )
    await append_control_audit(
        session, space_id=row.space_id, event_type="connector.enrollment_token.consumed",
        subject_type="connector_enrollment_token", subject_id=token.id,
        evidence={"registration_request_id": str(row.id)},
    )
    return row


def _pki_root() -> Path:
    root = Path(os.environ.get("MEDTRUST_CONNECTOR_PKI_ROOT", "D:/MedTrustData/hospital-connector-alpha/pki")).resolve()
    windows_d_drive = root.drive.upper() == "D:"
    container_mount = os.name != "nt" and root == Path("/var/lib/medtrust/connector-pki")
    if not windows_d_drive and not container_mount:
        raise ConnectorControlError("test PKI root must be on D drive")
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_test_ca() -> tuple[Path, Path]:
    root = _pki_root()
    key, cert = root / "local-test-ca.key.pem", root / "local-test-ca.cert.pem"
    if key.exists() and cert.exists():
        return key, cert
    openssl = _openssl()
    subprocess.run(
        [openssl, "req", "-x509", "-newkey", "rsa:3072", "-nodes", "-days", "30",
         "-subj", "/CN=MedTrust Local Test CA/O=Non-Production",
         "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True, timeout=30,
    )
    return key, cert


def _sign_csr_bytes(csr: bytes) -> bytes:
    ca_key, ca_cert = ensure_test_ca()
    openssl = _openssl()
    with tempfile.TemporaryDirectory(dir=_pki_root()) as temp:
        csr_path, cert_path = Path(temp) / "request.csr.pem", Path(temp) / "connector.cert.pem"
        csr_path.write_bytes(csr)
        subprocess.run(
            [openssl, "x509", "-req", "-in", str(csr_path), "-CA", str(ca_cert),
             "-CAkey", str(ca_key), "-CAcreateserial", "-days", "7",
             "-sha256", "-copy_extensions", "copy", "-out", str(cert_path)],
            check=True, capture_output=True, timeout=30,
        )
        return cert_path.read_bytes()


def _sign_csr(request: ConnectorRegistrationRequest) -> bytes:
    return _sign_csr_bytes(request.csr_pem)


async def decide_registration(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID, request_id: UUID, approve: bool, reason: str | None
) -> tuple[ConnectorRegistrationRequest, HospitalConnector | None, ConnectorCertificate | None]:
    _require_operator(actor)
    request = await session.get(ConnectorRegistrationRequest, request_id, with_for_update=True)
    if request is None or request.space_id != space_id:
        raise ConnectorControlError("registration not found")
    if request.status not in {"submitted", "under_review"}:
        raise ConnectorControlError("registration has already been decided")
    request.reviewed_by, request.reviewed_at = actor.user_id, _now()
    if not approve:
        request.status, request.rejection_reason = "rejected", (reason or "rejected")
        await append_control_audit(
            session, space_id=request.space_id, event_type="connector.registration.rejected",
            subject_type="connector_registration_request", subject_id=request.id,
            evidence={"reason": request.rejection_reason}, actor=actor,
        )
        return request, None, None
    connector = HospitalConnector(
        space_id=request.space_id,
        organization_id=request.organization_id,
        connector_instance_id=request.connector_instance_id,
        installation_digest=request.installation_digest,
        display_name=request.display_name,
        connector_version=request.connector_version,
        operating_system=request.operating_system,
        architecture=request.architecture,
        status="pending_certificate",
    )
    session.add(connector)
    await session.flush()
    cert_pem = _sign_csr(request)
    metadata = _certificate_metadata(cert_pem)
    _validate_connector_identity(
        subject=metadata.subject, san_entries=metadata.san_entries,
        has_san_extension=metadata.has_san_extension,
        connector_instance_id=connector.connector_instance_id,
        error_code="CERTIFICATE_IDENTITY_MISMATCH",
    )
    now = _now()
    cert = ConnectorCertificate(
        connector_id=connector.id,
        serial_number=metadata.serial_number,
        subject=metadata.subject,
        issuer=metadata.issuer,
        fingerprint_sha256=metadata.fingerprint_sha256,
        valid_from=metadata.valid_from,
        valid_to=metadata.valid_to,
        key_id=sha256_text(request.csr_fingerprint)[:40],
        certificate_pem=cert_pem,
        status="active",
    )
    session.add(cert)
    await session.flush()
    connector.current_certificate_id = cert.id
    connector.status = "active"
    connector.activated_at = now
    request.status = "certificate_issued"
    request.connector_id = connector.id
    await append_control_audit(
        session, space_id=request.space_id, event_type="connector.registration.approved",
        subject_type="connector_registration_request", subject_id=request.id,
        evidence={"connector_id": str(connector.id), "csr_fingerprint": request.csr_fingerprint}, actor=actor,
    )
    await append_control_audit(
        session, space_id=request.space_id, event_type="connector.certificate.issued",
        subject_type="hospital_connector", subject_id=connector.id,
        evidence={"certificate_id": str(cert.id), "fingerprint": cert.fingerprint_sha256, "issuer": cert.issuer, "valid_to": cert.valid_to.isoformat()}, actor=actor,
    )
    await append_control_audit(
        session, space_id=request.space_id, event_type="connector.activated",
        subject_type="hospital_connector", subject_id=connector.id,
        evidence={"execution_enabled": False, "data_transfer_enabled": False, "hard_isolation": False}, actor=actor,
    )
    return request, connector, cert


async def rotate_certificate(
    session: AsyncSession,
    *,
    connector: HospitalConnector,
    current_fingerprint: str,
    csr_pem: str,
) -> ConnectorCertificate:
    if connector.status not in {"active", "paused", "offline", "certificate_rotation_required"}:
        raise ConnectorControlError("CONNECTOR_NOT_ACTIVE")
    current = await session.get(ConnectorCertificate, connector.current_certificate_id, with_for_update=True)
    if current is None or current.status != "active" or current.fingerprint_sha256 != current_fingerprint:
        raise ConnectorControlError("CERTIFICATE_INVALID")
    csr = csr_pem.encode("ascii")
    _validate_csr(csr, connector_instance_id=connector.connector_instance_id)
    await append_control_audit(
        session, space_id=connector.space_id, event_type="connector.certificate.rotation_requested",
        subject_type="hospital_connector", subject_id=connector.id,
        evidence={"current_certificate_id": str(current.id), "new_csr_fingerprint": sha256_bytes(csr)},
        actor_connector_id=connector.id,
    )
    cert_pem = _sign_csr_bytes(csr)
    metadata = _certificate_metadata(cert_pem)
    _validate_connector_identity(
        subject=metadata.subject, san_entries=metadata.san_entries,
        has_san_extension=metadata.has_san_extension,
        connector_instance_id=connector.connector_instance_id,
        error_code="CERTIFICATE_IDENTITY_MISMATCH",
    )
    replacement = ConnectorCertificate(
        connector_id=connector.id,
        serial_number=metadata.serial_number,
        subject=metadata.subject,
        issuer=metadata.issuer,
        fingerprint_sha256=metadata.fingerprint_sha256,
        valid_from=metadata.valid_from,
        valid_to=metadata.valid_to,
        key_id=sha256_text(sha256_bytes(csr))[:40],
        certificate_pem=cert_pem,
        status="active",
        supersedes_certificate_id=current.id,
    )
    session.add(replacement)
    await session.flush()
    current.status = "superseded"
    connector.current_certificate_id = replacement.id
    if connector.status == "certificate_rotation_required":
        connector.status = "active"
    await append_control_audit(
        session, space_id=connector.space_id, event_type="connector.certificate.rotated",
        subject_type="hospital_connector", subject_id=connector.id,
        evidence={
            "old_certificate_id": str(current.id),
            "new_certificate_id": str(replacement.id),
            "new_fingerprint": replacement.fingerprint_sha256,
            "valid_to": replacement.valid_to.isoformat(),
        },
        actor_connector_id=connector.id,
    )
    return replacement


async def submit_manifest(
    session: AsyncSession, *, connector: HospitalConnector, payload: dict[str, Any]
) -> ConnectorCapabilityManifest:
    if connector.status == "revoked":
        raise ConnectorControlError("CONNECTOR_REVOKED")
    if connector.status not in {"active", "paused", "offline"}:
        raise ConnectorControlError("CONNECTOR_NOT_ACTIVE")
    forbidden_true = [
        "execution_enabled", "data_transfer_enabled", "model_transfer_enabled",
        "artifact_egress_enabled", "hard_isolation",
    ]
    if any(payload.get(key) is not False for key in forbidden_true):
        raise ConnectorControlError("ALPHA_CAPABILITY_MUST_BE_DISABLED")
    manifest = payload["capability_payload"]
    expected = canonical_json_digest_v1(manifest)
    if payload["manifest_digest"] != expected:
        raise ConnectorControlError("DIGEST_MISMATCH")
    prior = await session.scalar(
        select(ConnectorCapabilityManifest)
        .where(ConnectorCapabilityManifest.connector_id == connector.id)
        .order_by(ConnectorCapabilityManifest.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    if prior and payload["sequence"] <= prior.sequence:
        raise ConnectorControlError("SEQUENCE_NOT_INCREASING")
    if prior:
        prior.is_current = False
    row = ConnectorCapabilityManifest(connector_id=connector.id, **payload)
    session.add(row)
    await session.flush()
    connector.current_capability_manifest_id = row.id
    await append_control_audit(
        session, space_id=connector.space_id, event_type="connector.capability_manifest.received",
        subject_type="connector_capability_manifest", subject_id=row.id,
        evidence={"connector_id": str(connector.id), "sequence": row.sequence, "manifest_digest": row.manifest_digest, "execution_enabled": False, "data_transfer_enabled": False, "model_transfer_enabled": False, "hard_isolation": False},
        actor_connector_id=connector.id,
    )
    return row


PROHIBITED_METADATA_KEYS = {
    "absolute_path", "path", "local_path", "directory", "filename", "file_name",
    "file_list", "patient_id", "patient_ids", "subject_id", "medical_record_number",
    "database_url", "connection_string", "password", "secret", "private_key",
    "encryption_key", "internal_ip", "host", "hostname", "location_alias",
    "encrypted_location_reference", "location_digest",
}


def _reject_prohibited_metadata(value: Any, trail: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in PROHIBITED_METADATA_KEYS or normalized.endswith("_path"):
                raise ConnectorControlError(f"PROHIBITED_METADATA_FIELD:{'.'.join((*trail, key))}")
            _reject_prohibited_metadata(child, (*trail, key))
    elif isinstance(value, list):
        for child in value:
            _reject_prohibited_metadata(child, trail)
    elif isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith(("/", "\\\\", "file://")) or (
            len(value) > 2 and value[1:3] in {":\\", ":/"}
        ):
            raise ConnectorControlError("PROHIBITED_METADATA_VALUE")
        if any(marker in lowered for marker in ("postgresql://", "mysql://", "mongodb://")):
            raise ConnectorControlError("PROHIBITED_METADATA_VALUE")


async def accept_asset_metadata_bundle(
    session: AsyncSession,
    *,
    connector: HospitalConnector,
    certificate_fingerprint: str,
    payload: dict[str, Any],
) -> tuple[ConnectorAssetMirror, ConnectorAssetMirrorVersion, bool]:
    now = _now()
    if connector.status == "revoked":
        raise ConnectorControlError("CONNECTOR_REVOKED")
    if connector.status == "paused":
        raise ConnectorControlError("CONNECTOR_PAUSED")
    if connector.status != "active":
        raise ConnectorControlError("CONNECTOR_NOT_ACTIVE")
    cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
    if cert is None or cert.status != "active" or cert.valid_to <= now:
        raise ConnectorControlError("CERTIFICATE_INVALID")
    if cert.fingerprint_sha256 != certificate_fingerprint:
        raise ConnectorControlError("CERTIFICATE_FINGERPRINT_MISMATCH")
    manifest = await session.get(ConnectorCapabilityManifest, connector.current_capability_manifest_id)
    if (
        manifest is None
        or not manifest.local_asset_registry_enabled
        or not manifest.metadata_sync_enabled
        or not manifest.data_quality_summary_enabled
    ):
        raise ConnectorControlError("METADATA_SYNC_CAPABILITY_DISABLED")
    if abs((now - payload["signed_at"]).total_seconds()) > 300:
        raise ConnectorControlError("TIMESTAMP_OUT_OF_WINDOW")
    _reject_prohibited_metadata(payload)
    digest_payload = {key: value for key, value in payload.items() if key != "bundle_digest"}
    normalized = json.loads(json.dumps(digest_payload, default=lambda value: value.isoformat()))
    if payload["bundle_digest"] != canonical_json_digest_v1(normalized):
        raise ConnectorControlError("DIGEST_MISMATCH")
    existing = await session.scalar(
        select(ConnectorAssetMirrorVersion).where(
            ConnectorAssetMirrorVersion.connector_id == connector.id,
            ConnectorAssetMirrorVersion.bundle_id == payload["bundle_id"],
        )
    )
    if existing:
        if existing.bundle_digest != payload["bundle_digest"]:
            raise ConnectorControlError("IDEMPOTENCY_CONFLICT")
        mirror = await session.get(ConnectorAssetMirror, existing.mirror_id)
        assert mirror is not None
        return mirror, existing, False
    prior = await session.scalar(
        select(ConnectorAssetMirrorVersion)
        .where(ConnectorAssetMirrorVersion.connector_id == connector.id)
        .order_by(ConnectorAssetMirrorVersion.bundle_sequence.desc())
        .limit(1)
        .with_for_update()
    )
    if prior and payload["bundle_sequence"] <= prior.bundle_sequence:
        raise ConnectorControlError("SEQUENCE_NOT_INCREASING")
    metadata = payload["metadata_summary"]
    if payload["metadata_digest"] != canonical_json_digest_v1(metadata):
        raise ConnectorControlError("METADATA_DIGEST_MISMATCH")
    if payload["quality_digest"] != canonical_json_digest_v1(payload["quality_summary"]):
        raise ConnectorControlError("QUALITY_DIGEST_MISMATCH")
    mirror = await session.scalar(
        select(ConnectorAssetMirror).where(
            ConnectorAssetMirror.connector_id == connector.id,
            ConnectorAssetMirror.local_asset_key == payload["local_asset_key"],
        ).with_for_update()
    )
    if mirror is None:
        mirror = ConnectorAssetMirror(
            connector_id=connector.id, space_id=connector.space_id,
            organization_id=connector.organization_id,
            local_asset_key=payload["local_asset_key"],
            display_name=metadata["display_name"], asset_kind=metadata["asset_kind"],
            modality=metadata["modality"], source_category=metadata["source_category"],
            sensitivity_classification=metadata["sensitivity_classification"],
            status="synced",
        )
        session.add(mirror)
        await session.flush()
    version = ConnectorAssetMirrorVersion(
        mirror_id=mirror.id, connector_id=connector.id,
        bundle_id=payload["bundle_id"], bundle_sequence=payload["bundle_sequence"],
        version_label=payload["version_label"], schema_version=payload["schema_version"],
        metadata_digest=payload["metadata_digest"], schema_digest=payload["schema_digest"],
        quality_digest=payload["quality_digest"], bundle_digest=payload["bundle_digest"],
        disclosure_summary=payload["disclosure_summary"],
        metadata_summary=metadata, quality_summary=payload["quality_summary"],
        deidentification_summary=payload["deidentification_summary"],
        known_limitations=payload["known_limitations"],
        warning_flags=payload["warning_flags"],
    )
    session.add(version)
    await session.flush()
    mirror.current_version_id = version.id
    mirror.display_name = metadata["display_name"]
    mirror.last_synced_at = now
    await append_control_audit(
        session, space_id=connector.space_id,
        event_type="connector.asset_metadata.received",
        subject_type="connector_asset_mirror_version", subject_id=version.id,
        evidence={
            "connector_id": str(connector.id), "mirror_id": str(mirror.id),
            "bundle_sequence": version.bundle_sequence,
            "metadata_digest": version.metadata_digest,
            "quality_digest": version.quality_digest,
            "contains_raw_data": False, "execution_permitted": False,
        },
        actor_connector_id=connector.id,
    )
    return mirror, version, True


async def accept_heartbeat(
    session: AsyncSession, *, connector: HospitalConnector, certificate_fingerprint: str, payload: dict[str, Any]
) -> ConnectorHeartbeat:
    now = _now()
    if connector.status == "revoked":
        raise ConnectorControlError("CONNECTOR_REVOKED")
    if connector.status == "paused":
        acceptance = "paused_read_only"
    elif connector.status not in {"active", "offline"}:
        raise ConnectorControlError("CONNECTOR_NOT_ACTIVE")
    else:
        acceptance = "accepted"
    cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
    if cert is None or cert.status != "active":
        raise ConnectorControlError("CERTIFICATE_INVALID")
    if cert.valid_to <= now:
        raise ConnectorControlError("CERTIFICATE_EXPIRED")
    if certificate_fingerprint != cert.fingerprint_sha256:
        raise ConnectorControlError("CERTIFICATE_FINGERPRINT_MISMATCH")
    if payload["sequence"] <= connector.last_heartbeat_sequence:
        raise ConnectorControlError("SEQUENCE_NOT_INCREASING")
    if abs((now - payload["sent_at"]).total_seconds()) > 300:
        raise ConnectorControlError("TIMESTAMP_OUT_OF_WINDOW")
    manifest = await session.scalar(
        select(ConnectorCapabilityManifest).where(
            ConnectorCapabilityManifest.connector_id == connector.id,
            ConnectorCapabilityManifest.manifest_digest == payload["capability_manifest_digest"],
        )
    )
    if manifest is None:
        raise ConnectorControlError("MANIFEST_UNKNOWN")
    digest_payload = {key: value for key, value in payload.items() if key != "message_digest"}
    normalized = json.loads(json.dumps(digest_payload, default=lambda value: value.isoformat()))
    if payload["message_digest"] != canonical_json_digest_v1(normalized):
        raise ConnectorControlError("DIGEST_MISMATCH")
    row = ConnectorHeartbeat(
        connector_id=connector.id,
        certificate_fingerprint=certificate_fingerprint,
        acceptance_result=acceptance,
        **payload,
    )
    session.add(row)
    await session.flush()
    connector.last_heartbeat_sequence = payload["sequence"]
    connector.last_heartbeat_at = now
    connector.heartbeat_status = "paused" if connector.status == "paused" else "online"
    if connector.status == "offline":
        connector.status = "active"
    await append_control_audit(
        session, space_id=connector.space_id, event_type="connector.heartbeat.accepted",
        subject_type="connector_heartbeat", subject_id=row.id,
        evidence={"connector_id": str(connector.id), "sequence": row.sequence, "message_digest": row.message_digest, "acceptance_result": acceptance},
        actor_connector_id=connector.id,
    )
    return row


async def transition_connector(
    session: AsyncSession, *, actor: DemoActor, space_id: UUID, connector_id: UUID, action: str, reason: str
) -> HospitalConnector:
    _require_operator(actor)
    connector = await session.get(HospitalConnector, connector_id, with_for_update=True)
    if connector is None or connector.space_id != space_id:
        raise ConnectorControlError("connector not found")
    now = _now()
    if action == "pause" and connector.status in {"active", "offline"}:
        connector.status, connector.paused_at, connector.paused_by = "paused", now, actor.user_id
    elif action == "resume" and connector.status == "paused":
        cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
        if cert is None or cert.status != "active" or cert.valid_to <= now:
            raise ConnectorControlError("valid certificate required")
        connector.status, connector.paused_at, connector.paused_by = "active", None, None
    elif action == "revoke" and connector.status != "revoked":
        connector.status, connector.revoked_at, connector.revoked_by = "revoked", now, actor.user_id
        connector.heartbeat_status = "revoked"
        connector.revocation_reason = reason
        cert = await session.get(ConnectorCertificate, connector.current_certificate_id)
        if cert:
            cert.status, cert.revoked_at, cert.revocation_reason = "revoked", now, reason
    else:
        raise ConnectorControlError("invalid connector transition")
    await append_control_audit(
        session, space_id=connector.space_id, event_type=f"connector.{action}d" if action != "pause" else "connector.paused",
        subject_type="hospital_connector", subject_id=connector.id,
        evidence={"status": connector.status, "reason": reason}, actor=actor,
    )
    return connector


def _contains_prohibited_executor_field(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            if key.lower() in EXECUTOR_PROHIBITED_FIELDS:
                return True
            if _contains_prohibited_executor_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_prohibited_executor_field(item) for item in value)
    return False


def _verify_executor_attestation_signature(
    certificate_pem: bytes, payload: dict[str, Any], signature: str,
) -> None:
    with tempfile.TemporaryDirectory() as root:
        cert = Path(root) / "cert.pem"
        public = Path(root) / "public.pem"
        message = Path(root) / "message.bin"
        signed = Path(root) / "signature.bin"
        cert.write_bytes(certificate_pem)
        message.write_bytes(canonical_json_text_v1(payload).encode("utf-8"))
        try:
            signed.write_bytes(base64.b64decode(signature, validate=True))
        except Exception as exc:
            raise ConnectorControlError(
                "EXECUTOR_STATUS_SIGNATURE_INVALID"
            ) from exc
        exported = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-pubkey", "-noout"],
            capture_output=True, check=False,
        )
        public.write_bytes(exported.stdout)
        verified = subprocess.run(
            [
                "openssl", "dgst", "-sha256", "-verify", str(public),
                "-signature", str(signed), str(message),
            ],
            capture_output=True, check=False,
        )
        if exported.returncode or verified.returncode:
            raise ConnectorControlError("EXECUTOR_STATUS_SIGNATURE_INVALID")


def _contains_prohibited_evidence_field(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            key.lower() in EVIDENCE_PROHIBITED_FIELDS
            or _contains_prohibited_evidence_field(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_prohibited_evidence_field(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return (
            lowered.startswith(("c:\\", "d:\\", "/", "file://"))
            or "../" in lowered or "..\\" in lowered
        )
    return False


async def accept_hospital_evidence_bundle(
    session: AsyncSession, *, connector: HospitalConnector,
    payload: dict[str, Any],
) -> tuple[HospitalEvidenceBundleReceipt, bool]:
    now = _now()
    cert = await session.get(
        ConnectorCertificate, connector.current_certificate_id
    )
    if (
        connector.status != "active"
        or cert is None
        or cert.status != "active"
        or cert.valid_from > now
        or cert.valid_to <= now
    ):
        raise ConnectorControlError("EVIDENCE_SIGNING_KEY_REVOKED")
    if (
        payload["schema_version"] != EVIDENCE_BUNDLE_SCHEMA
        or payload["bundle_version"] != 1
    ):
        raise ConnectorControlError("EVIDENCE_SCHEMA_INVALID")
    if (
        str(payload["connector_id"]) != str(connector.id)
        or str(payload["organization_id"]) != str(connector.organization_id)
        or payload["signing_key_id"] != cert.key_id
    ):
        raise ConnectorControlError("EVIDENCE_IDENTITY_MISMATCH")
    if _contains_prohibited_evidence_field(payload):
        raise ConnectorControlError("EVIDENCE_PROHIBITED_FIELD")
    normalized = _normalized_json(payload)
    signature = normalized.pop("signature")
    signed_payload = dict(normalized)
    unsigned = {
        key: value for key, value in signed_payload.items()
        if key != "bundle_digest"
    }
    if payload["bundle_digest"] != canonical_json_digest_v1(unsigned):
        raise ConnectorControlError("EVIDENCE_DIGEST_MISMATCH")
    _verify_executor_attestation_signature(
        cert.certificate_pem, signed_payload, signature
    )
    generated_at = payload["generated_at"]
    if (
        generated_at.tzinfo is None
        or generated_at > now + timedelta(minutes=5)
        or now - generated_at > timedelta(hours=24)
    ):
        raise ConnectorControlError("EVIDENCE_TIMESTAMP_INVALID")
    prior = await session.scalar(
        select(HospitalEvidenceBundleReceipt).where(
            HospitalEvidenceBundleReceipt.bundle_id == payload["bundle_id"]
        )
    )
    if prior is not None:
        if prior.bundle_digest != payload["bundle_digest"]:
            raise ConnectorControlError("EVIDENCE_REPLAY_MISMATCH")
        return prior, False
    digest_replay = await session.scalar(
        select(HospitalEvidenceBundleReceipt).where(
            HospitalEvidenceBundleReceipt.bundle_digest
            == payload["bundle_digest"]
        )
    )
    if digest_replay is not None:
        return digest_replay, False
    order = await session.get(ExecutionOrder, payload["execution_order_id"])
    version = await session.get(
        PolicyBundleVersion, payload["policy_bundle_version_id"]
    )
    consumption = await session.scalar(
        select(ExecutionOrderConsumptionReceipt).where(
            ExecutionOrderConsumptionReceipt.execution_order_id
            == payload["execution_order_id"]
        )
    )
    if (
        order is None
        or version is None
        or consumption is None
        or order.connector_id != connector.id
        or order.policy_bundle_id != payload["policy_bundle_id"]
        or order.policy_bundle_version_id
        != payload["policy_bundle_version_id"]
        or order.payload_digest != payload["execution_order_digest"]
        or version.payload_digest != payload["policy_digest"]
        or consumption.authorization_snapshot_id
        != payload["authorization_snapshot_id"]
        or consumption.reference_execution_id
        != payload["reference_execution_id"]
        or consumption.payload_digest
        != payload["consumption_receipt_digest"]
        or order.consumed_count != 1
    ):
        raise ConnectorControlError("EVIDENCE_CAUSAL_BINDING_MISMATCH")
    boundaries = payload["security_boundaries"]
    result = payload["result_summary"]
    output_manifest = payload["output_manifest"]
    expected_outputs = {
        "aggregate_metrics.json": "application/json",
        "confusion_matrix.csv": "text/csv",
        "execution_summary.json": "application/json",
    }
    output_manifest_valid = (
        isinstance(output_manifest, list)
        and len(output_manifest) == 3
        and {
            item.get("name") for item in output_manifest
            if isinstance(item, dict)
        } == set(expected_outputs)
        and all(
            isinstance(item, dict)
            and set(item) == {
                "name", "media_type", "size_bytes", "digest"
            }
            and item["media_type"] == expected_outputs[item["name"]]
            and isinstance(item["size_bytes"], int)
            and 0 < item["size_bytes"] <= 64 * 1024
            and isinstance(item["digest"], str)
            and len(item["digest"]) == 71
            and item["digest"].startswith("sha256:")
            for item in output_manifest
        )
    )
    if (
        boundaries != {
            "network_access": False,
            "raw_data_transfer": False,
            "model_transfer": False,
            "artifact_auto_egress": False,
            "hard_isolation": False,
        }
        or result.get("sample_count") != 20
        or result.get("correct_count") != 19
        or result.get("accuracy") != "0.95"
        or result.get("non_clinical") is not True
        or result.get("hard_isolation") is not False
        or payload["review_decision"]
        != "APPROVE_FOR_EVIDENCE_CANDIDACY"
        or not output_manifest_valid
    ):
        raise ConnectorControlError("EVIDENCE_BOUNDARY_INVALID")
    row = HospitalEvidenceBundleReceipt(
        bundle_id=payload["bundle_id"],
        connector_id=connector.id,
        space_id=connector.space_id,
        organization_id=connector.organization_id,
        schema_version=payload["schema_version"],
        bundle_version=payload["bundle_version"],
        local_artifact_ref=payload["local_artifact_ref"],
        reference_execution_id=payload["reference_execution_id"],
        policy_bundle_id=payload["policy_bundle_id"],
        policy_bundle_version_id=payload["policy_bundle_version_id"],
        execution_order_id=payload["execution_order_id"],
        artifact_digest=payload["artifact_digest"],
        review_digest=payload["review_digest"],
        causal_validation_digest=payload["causal_validation_digest"],
        local_audit_head=payload["local_audit_head"],
        bundle_digest=payload["bundle_digest"],
        signing_key_id=payload["signing_key_id"],
        signature=signature,
        evidence_summary=signed_payload,
        verification_status="verified",
    )
    session.add(row)
    await session.flush()
    await append_control_audit(
        session,
        space_id=connector.space_id,
        event_type="hospital.evidence_bundle.registered",
        subject_type="hospital_evidence_bundle",
        subject_id=row.id,
        evidence={
            "bundle_id": str(row.bundle_id),
            "bundle_digest": row.bundle_digest,
            "artifact_digest": row.artifact_digest,
            "causal_validation_digest": row.causal_validation_digest,
            "raw_data_received": False,
            "artifact_received": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return row, True


def _normalized_json(value: dict[str, Any]) -> dict[str, Any]:
    def serialize(item: Any) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, UUID):
            return str(item)
        return str(item)

    return json.loads(
        json.dumps(
            value,
            default=serialize,
            sort_keys=True,
        )
    )


def validate_executor_readiness_v2_document(
    payload: dict[str, Any], *, now: datetime,
) -> str | None:
    capability = payload["capability"]
    image = payload["image_manifest"]
    security = payload["security_profile"]
    resource = payload["resource_policy"]
    admission = payload["admission"]
    checks = (
        (
            payload["schema_version"] == EXECUTOR_READINESS_V2_SCHEMA,
            "EXECUTOR_STATUS_SCHEMA_UNSUPPORTED",
        ),
        (
            payload["event_type"] == EXECUTOR_READINESS_V2_EVENT,
            "EXECUTOR_STATUS_EVENT_TYPE_INVALID",
        ),
        (payload["executor_status"] == "active", "EXECUTOR_STATUS_INVALID"),
        (payload["heartbeat_at"] is not None, "EXECUTOR_STATUS_TIMESTAMP_INVALID"),
        (
            capability["fixed_reference_execution_enabled"] is True
            and "PATHMNIST_REFERENCE_V1"
            in capability["supported_task_types"],
            "FIXED_REFERENCE_CAPABILITY_MISSING",
        ),
        (
            not capability["arbitrary_execution_enabled"]
            and not capability["user_code_enabled"]
            and not capability["user_model_enabled"],
            "ARBITRARY_EXECUTION_FORBIDDEN_STATE_INVALID",
        ),
        (not capability["data_transfer_enabled"], "DATA_TRANSFER_FORBIDDEN"),
        (not capability["model_transfer_enabled"], "MODEL_TRANSFER_FORBIDDEN"),
        (
            not capability["artifact_auto_egress_enabled"],
            "AUTO_EGRESS_FORBIDDEN",
        ),
        (capability["hard_isolation"] is False, "HARD_ISOLATION_CLAIM_INVALID"),
        (image["lifecycle_status"] == "approved", "IMAGE_MANIFEST_NOT_APPROVED"),
        (image["revoked_at"] is None, "IMAGE_MANIFEST_REVOKED"),
        (image["signature_status"] == "verified", "IMAGE_SIGNATURE_STATUS_INVALID"),
        (image["security_scan_status"] == "passed", "IMAGE_SCAN_STATUS_INVALID"),
        (security["status"] == "valid", "SECURITY_PROFILE_INVALID"),
        (resource["status"] == "active", "RESOURCE_POLICY_INVALID"),
        (security["network_mode"] == "none", "NETWORK_POLICY_INVALID"),
        (security["filesystem_mode"] == "readonly_input", "SECURITY_PROFILE_INVALID"),
        (security["rootless"] is True, "ROOTLESS_REQUIRED"),
        (security["privileged"] is False, "PRIVILEGED_FORBIDDEN"),
        (
            security["docker_socket_access"] is False,
            "DOCKER_SOCKET_FORBIDDEN",
        ),
        (security["runtime_download"] is False, "RUNTIME_DOWNLOAD_FORBIDDEN"),
        (security["input_readonly"] is True, "SECURITY_PROFILE_INVALID"),
        (security["capabilities_dropped"] is True, "SECURITY_PROFILE_INVALID"),
        (admission["result"] == "approved", "ADMISSION_NOT_APPROVED"),
        (
            admission["valid_until"] is not None
            and admission["valid_until"] > now,
            "ADMISSION_EXPIRED",
        ),
        (
            admission["executor_id"] == payload["executor_id"],
            "EXECUTOR_STATUS_IDENTITY_MISMATCH",
        ),
        (
            admission["image_manifest_digest"] == image["manifest_digest"]
            and admission["image_digest"] == image["image_digest"],
            "ADMISSION_IMAGE_DIGEST_MISMATCH",
        ),
        (
            admission["security_profile_digest"] == security["profile_digest"],
            "ADMISSION_SECURITY_DIGEST_MISMATCH",
        ),
        (
            admission["resource_policy_digest"] == resource["policy_digest"],
            "ADMISSION_RESOURCE_DIGEST_MISMATCH",
        ),
        (
            admission["capability_digest"] == capability["digest"],
            "ADMISSION_CAPABILITY_DIGEST_MISMATCH",
        ),
        (
            isinstance(payload["local_audit_head"], str)
            and payload["local_audit_head"].startswith("sha256:")
            and len(payload["local_audit_head"]) == 71,
            "LOCAL_AUDIT_HEAD_INVALID",
        ),
    )
    failure = next((code for passed, code in checks if not passed), None)
    resource_document = {
        key: resource[key] for key in (
            "cpu_cores", "memory_mb", "disk_mb", "processes",
            "timeout_seconds",
        )
    }
    if resource["policy_digest"] != canonical_json_digest_v1(resource_document):
        failure = failure or "RESOURCE_POLICY_INVALID"
    security_document = {
        "executor_id": payload["executor_id"],
        "security_version": security["security_version"],
        "network_mode": security["network_mode"],
        "filesystem_mode": security["filesystem_mode"],
        "rootless": security["rootless"],
        "privileged": security["privileged"],
        "docker_socket_access": security["docker_socket_access"],
        "runtime_download": security["runtime_download"],
        "resource_policy": resource_document,
    }
    if security["profile_digest"] != canonical_json_digest_v1(security_document):
        failure = failure or "SECURITY_PROFILE_INVALID"
    normalized = _normalized_json(payload)
    admission_document = {
        "executor_id": payload["executor_id"],
        "security_profile_id": security["local_object_id"],
        "image_manifest_id": image["local_object_id"],
        "image_manifest_digest": image["manifest_digest"],
        "image_digest": image["image_digest"],
        "security_profile_digest": security["profile_digest"],
        "resource_policy_digest": resource["policy_digest"],
        "capability_digest": capability["digest"],
        "rejection_reasons": [],
        "execution_enabled": False,
        "checked_at": normalized["admission"]["checked_at"],
        "valid_until": normalized["admission"]["valid_until"],
    }
    if admission["admission_digest"] != canonical_json_digest_v1(
        admission_document
    ):
        failure = failure or "ADMISSION_DIGEST_MISMATCH"
    return failure


async def _accept_executor_readiness_v2(
    session: AsyncSession,
    *,
    connector: HospitalConnector,
    payload: dict[str, Any],
) -> tuple[HospitalExecutorMirror, HospitalExecutorStatusEvent, bool]:
    now = _now()
    if connector.status != "active":
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    cert = await session.get(
        ConnectorCertificate, connector.current_certificate_id
    )
    if cert is None or cert.status != "active":
        raise ConnectorControlError("EXECUTOR_STATUS_SIGNING_KEY_REVOKED")
    if cert.valid_from > now or cert.valid_to <= now:
        raise ConnectorControlError("EXECUTOR_STATUS_SIGNING_KEY_REVOKED")
    if str(payload["connector_id"]) != str(connector.id):
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    if (
        payload["connector_certificate_fingerprint"]
        != cert.fingerprint_sha256
    ):
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    if payload["signing_key_id"] != cert.key_id:
        raise ConnectorControlError("EXECUTOR_STATUS_SIGNING_KEY_UNKNOWN")
    if payload["event_type"] != EXECUTOR_READINESS_V2_EVENT:
        raise ConnectorControlError("EXECUTOR_STATUS_EVENT_TYPE_INVALID")
    if _contains_prohibited_executor_field(payload):
        raise ConnectorControlError("EXECUTOR_STATUS_PROHIBITED_FIELD")

    normalized = _normalized_json(payload)
    unsigned = {
        key: value for key, value in normalized.items()
        if key not in {"payload_digest", "signature"}
    }
    if payload["payload_digest"] != canonical_json_digest_v1(unsigned):
        raise ConnectorControlError("EXECUTOR_STATUS_DIGEST_MISMATCH")
    signed_payload = {
        key: value for key, value in normalized.items() if key != "signature"
    }
    _verify_executor_attestation_signature(
        cert.certificate_pem, signed_payload, payload["signature"]
    )

    generated_at = payload["generated_at"]
    not_before = payload["not_before"]
    expires_at = payload["expires_at"]
    if (
        generated_at.tzinfo is None
        or not_before.tzinfo is None
        or expires_at.tzinfo is None
        or abs((now - generated_at).total_seconds()) > 300
        or not_before > now
        or expires_at <= generated_at
        or (expires_at - generated_at).total_seconds() > 3600
    ):
        raise ConnectorControlError("EXECUTOR_STATUS_TIMESTAMP_INVALID")
    if expires_at <= now:
        raise ConnectorControlError("EXECUTOR_STATUS_EXPIRED")
    document_failure = validate_executor_readiness_v2_document(
        payload, now=now
    )

    mirror = await session.scalar(
        select(HospitalExecutorMirror)
        .where(
            HospitalExecutorMirror.connector_id == connector.id,
            HospitalExecutorMirror.executor_instance_id
            == payload["executor_instance_id"],
        )
        .with_for_update()
    )
    if mirror is None:
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    if (
        mirror.local_executor_id
        and mirror.local_executor_id != payload["executor_id"]
    ):
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    if (
        mirror.certificate_fingerprint
        != payload["executor_certificate_fingerprint"]
    ):
        raise ConnectorControlError("EXECUTOR_STATUS_IDENTITY_MISMATCH")
    if payload["event_sequence"] <= mirror.last_status_sequence:
        prior = await session.scalar(
            select(HospitalExecutorStatusEvent).where(
                HospitalExecutorStatusEvent.mirror_id == mirror.id,
                HospitalExecutorStatusEvent.status_sequence
                == payload["event_sequence"],
                HospitalExecutorStatusEvent.payload_digest
                == payload["payload_digest"],
            )
        )
        if prior is not None:
            return mirror, prior, False
        raise ConnectorControlError("EXECUTOR_STATUS_SEQUENCE_REPLAY")
    replay = await session.scalar(
        select(HospitalExecutorStatusEvent).where(
            HospitalExecutorStatusEvent.nonce == payload["nonce"]
        )
    )
    if replay is not None:
        raise ConnectorControlError("EXECUTOR_STATUS_NONCE_REPLAY")

    capability = payload["capability"]
    image = payload["image_manifest"]
    security = payload["security_profile"]
    resource = payload["resource_policy"]
    admission = payload["admission"]
    ready_checks = (
        (payload["executor_status"] == "active", "EXECUTOR_STATUS_INVALID"),
        (payload["heartbeat_at"] is not None, "EXECUTOR_STATUS_TIMESTAMP_INVALID"),
        (
            capability["fixed_reference_execution_enabled"] is True
            and "PATHMNIST_REFERENCE_V1"
            in capability["supported_task_types"],
            "FIXED_REFERENCE_CAPABILITY_MISSING",
        ),
        (
            not capability["arbitrary_execution_enabled"]
            and not capability["user_code_enabled"]
            and not capability["user_model_enabled"],
            "ARBITRARY_EXECUTION_FORBIDDEN_STATE_INVALID",
        ),
        (
            not capability["data_transfer_enabled"],
            "DATA_TRANSFER_FORBIDDEN",
        ),
        (
            not capability["model_transfer_enabled"],
            "MODEL_TRANSFER_FORBIDDEN",
        ),
        (
            not capability["artifact_auto_egress_enabled"],
            "AUTO_EGRESS_FORBIDDEN",
        ),
        (capability["hard_isolation"] is False, "HARD_ISOLATION_CLAIM_INVALID"),
        (image["lifecycle_status"] == "approved", "IMAGE_MANIFEST_NOT_APPROVED"),
        (image["revoked_at"] is None, "IMAGE_MANIFEST_REVOKED"),
        (image["signature_status"] == "verified", "IMAGE_SIGNATURE_STATUS_INVALID"),
        (image["security_scan_status"] == "passed", "IMAGE_SCAN_STATUS_INVALID"),
        (security["status"] == "valid", "SECURITY_PROFILE_INVALID"),
        (resource["status"] == "active", "RESOURCE_POLICY_INVALID"),
        (security["network_mode"] == "none", "NETWORK_POLICY_INVALID"),
        (security["filesystem_mode"] == "readonly_input", "SECURITY_PROFILE_INVALID"),
        (security["rootless"] is True, "ROOTLESS_REQUIRED"),
        (security["privileged"] is False, "PRIVILEGED_FORBIDDEN"),
        (
            security["docker_socket_access"] is False,
            "DOCKER_SOCKET_FORBIDDEN",
        ),
        (security["runtime_download"] is False, "RUNTIME_DOWNLOAD_FORBIDDEN"),
        (security["input_readonly"] is True, "SECURITY_PROFILE_INVALID"),
        (security["capabilities_dropped"] is True, "SECURITY_PROFILE_INVALID"),
        (admission["result"] == "approved", "ADMISSION_NOT_APPROVED"),
        (
            admission["valid_until"] is not None
            and admission["valid_until"] > now,
            "ADMISSION_EXPIRED",
        ),
        (
            admission["executor_id"] == payload["executor_id"],
            "EXECUTOR_STATUS_IDENTITY_MISMATCH",
        ),
        (
            admission["image_manifest_digest"] == image["manifest_digest"]
            and admission["image_digest"] == image["image_digest"],
            "ADMISSION_IMAGE_DIGEST_MISMATCH",
        ),
        (
            admission["security_profile_digest"] == security["profile_digest"],
            "ADMISSION_SECURITY_DIGEST_MISMATCH",
        ),
        (
            admission["resource_policy_digest"] == resource["policy_digest"],
            "ADMISSION_RESOURCE_DIGEST_MISMATCH",
        ),
        (
            admission["capability_digest"] == capability["digest"],
            "ADMISSION_CAPABILITY_DIGEST_MISMATCH",
        ),
    )
    failure = document_failure or next(
        (code for passed, code in ready_checks if not passed), None
    )

    resource_document = {
        key: resource[key] for key in (
            "cpu_cores", "memory_mb", "disk_mb", "processes",
            "timeout_seconds",
        )
    }
    if resource["policy_digest"] != canonical_json_digest_v1(
        resource_document
    ):
        failure = failure or "RESOURCE_POLICY_INVALID"
    security_document = {
        "executor_id": payload["executor_id"],
        "security_version": security["security_version"],
        "network_mode": security["network_mode"],
        "filesystem_mode": security["filesystem_mode"],
        "rootless": security["rootless"],
        "privileged": security["privileged"],
        "docker_socket_access": security["docker_socket_access"],
        "runtime_download": security["runtime_download"],
        "resource_policy": resource_document,
    }
    if security["profile_digest"] != canonical_json_digest_v1(
        security_document
    ):
        failure = failure or "SECURITY_PROFILE_INVALID"
    admission_document = {
        "executor_id": payload["executor_id"],
        "security_profile_id": security["local_object_id"],
        "image_manifest_id": image["local_object_id"],
        "image_manifest_digest": image["manifest_digest"],
        "image_digest": image["image_digest"],
        "security_profile_digest": security["profile_digest"],
        "resource_policy_digest": resource["policy_digest"],
        "capability_digest": capability["digest"],
        "rejection_reasons": [],
        "execution_enabled": False,
        "checked_at": normalized["admission"]["checked_at"],
        "valid_until": normalized["admission"]["valid_until"],
    }
    if admission["admission_digest"] != canonical_json_digest_v1(
        admission_document
    ):
        failure = failure or "ADMISSION_DIGEST_MISMATCH"
    if payload["readiness_result"] == (
        "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
    ) and failure:
        raise ConnectorControlError(failure)
    if payload["readiness_result"] == "NOT_READY" and not payload[
        "readiness_reason"
    ]:
        raise ConnectorControlError("EXECUTOR_STATUS_SCHEMA_INVALID")

    event = HospitalExecutorStatusEvent(
        mirror_id=mirror.id,
        connector_id=connector.id,
        status_sequence=payload["event_sequence"],
        schema_version=payload["schema_version"],
        event_type=payload["event_type"],
        status=payload["executor_status"],
        payload_digest=payload["payload_digest"],
        nonce=payload["nonce"],
        signing_key_id=payload["signing_key_id"],
        signature=payload["signature"],
        verification_status="verified",
        verified_at=now,
        payload_snapshot=normalized,
    )
    session.add(event)
    await session.flush()
    mirror.local_executor_id = payload["executor_id"]
    mirror.executor_version = payload["executor_version"]
    mirror.status = payload["executor_status"]
    mirror.last_status_sequence = payload["event_sequence"]
    mirror.last_heartbeat_at = payload["heartbeat_at"]
    mirror.last_synced_at = now
    mirror.latest_status_event_id = event.id
    mirror.latest_status_event_sequence = event.status_sequence
    mirror.latest_status_event_digest = event.payload_digest
    mirror.latest_status_schema_version = payload["schema_version"]
    if payload["readiness_result"] == (
        "READY_FOR_FIXED_REFERENCE_POLICY_COMPILATION"
    ) and failure is None:
        mirror.latest_verified_readiness_event_id = event.id
        mirror.latest_verified_readiness_digest = event.payload_digest
        mirror.latest_verified_readiness_at = now
        mirror.readiness_valid_until = expires_at
        mirror.fixed_reference_readiness_status = "ready"
        mirror.fixed_reference_readiness_reason = None
        mirror.attested_image_digest = image["image_digest"]
        mirror.attested_security_profile_digest = security["profile_digest"]
        mirror.attested_resource_policy_digest = resource["policy_digest"]
        mirror.attested_admission_digest = admission["admission_digest"]
        mirror.attested_capability_digest = capability["digest"]
    else:
        mirror.fixed_reference_readiness_status = "not_ready"
        mirror.fixed_reference_readiness_reason = payload["readiness_reason"]
    await append_control_audit(
        session,
        space_id=connector.space_id,
        event_type="executor.readiness_attestation.verified",
        subject_type="hospital_executor_status_event",
        subject_id=event.id,
        evidence={
            "connector_id": str(connector.id),
            "executor_id": payload["executor_id"],
            "event_sequence": event.status_sequence,
            "payload_digest": event.payload_digest,
            "readiness_result": payload["readiness_result"],
            "source_is_connector_attestation": True,
            "central_independent_inspection": False,
            "execution_started": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return mirror, event, True


async def get_verified_executor_readiness_source(
    session: AsyncSession,
    *,
    executor_mirror_id: UUID,
    task_type: str,
) -> dict[str, Any]:
    if task_type != "PATHMNIST_REFERENCE_V1":
        raise ConnectorControlError("FIXED_REFERENCE_CAPABILITY_MISSING")
    mirror = await session.get(HospitalExecutorMirror, executor_mirror_id)
    now = _now()
    if (
        mirror is None
        or mirror.status != "active"
        or mirror.fixed_reference_readiness_status != "ready"
        or mirror.latest_verified_readiness_event_id is None
        or mirror.latest_status_event_id
        != mirror.latest_verified_readiness_event_id
        or mirror.readiness_valid_until is None
        or mirror.readiness_valid_until <= now
    ):
        raise ConnectorControlError(
            "VERIFIED_EXECUTOR_READINESS_UNAVAILABLE"
        )
    event = await session.get(
        HospitalExecutorStatusEvent,
        mirror.latest_verified_readiness_event_id,
    )
    if (
        event is None
        or event.schema_version != EXECUTOR_READINESS_V2_SCHEMA
        or event.event_type != EXECUTOR_READINESS_V2_EVENT
        or event.verification_status != "verified"
        or event.payload_digest != mirror.latest_verified_readiness_digest
        or event.payload_snapshot.get("expires_at")
        != mirror.readiness_valid_until.isoformat()
        or task_type
        not in event.payload_snapshot.get("capability", {}).get(
            "supported_task_types", []
        )
    ):
        raise ConnectorControlError(
            "VERIFIED_EXECUTOR_READINESS_UNAVAILABLE"
        )
    return {
        "executor_mirror_id": str(mirror.id),
        "task_type": task_type,
        "source_executor_status_event_id": str(event.id),
        "source_executor_status_event_digest": event.payload_digest,
        "source_attestation_expires_at": mirror.readiness_valid_until,
        "attested_image_digest": mirror.attested_image_digest,
        "attested_security_profile_digest":
            mirror.attested_security_profile_digest,
        "attested_resource_policy_digest":
            mirror.attested_resource_policy_digest,
        "attested_admission_digest": mirror.attested_admission_digest,
        "attested_capability_digest": mirror.attested_capability_digest,
        "hard_isolation": False,
        "execution_started": False,
    }


async def accept_executor_status(
    session: AsyncSession,
    *,
    connector: HospitalConnector,
    payload: dict[str, Any],
) -> tuple[HospitalExecutorMirror, HospitalExecutorStatusEvent, bool]:
    if payload.get("schema_version") == EXECUTOR_READINESS_V2_SCHEMA:
        return await _accept_executor_readiness_v2(
            session, connector=connector, payload=payload
        )
    if set(payload) != EXECUTOR_STATUS_FIELDS:
        raise ConnectorControlError("EXECUTOR_STATUS_SCHEMA_INVALID")
    if _contains_prohibited_executor_field(payload):
        raise ConnectorControlError("EXECUTOR_STATUS_PROHIBITED_FIELD")
    if payload["schema_version"] != "phase5.13E-1A/executor-status/v1":
        raise ConnectorControlError("EXECUTOR_STATUS_SCHEMA_UNSUPPORTED")
    if payload["execution_enabled"] or payload["hard_isolation"]:
        raise ConnectorControlError("EXECUTOR_STATUS_CAPABILITY_FORBIDDEN")
    if payload["status"] not in {
        "pending", "approved", "active", "paused", "revoked", "offline",
    }:
        raise ConnectorControlError("EXECUTOR_STATUS_INVALID")
    if payload["security_status"] not in {
        "pending", "passed", "failed", "revoked",
    }:
        raise ConnectorControlError("EXECUTOR_SECURITY_STATUS_INVALID")
    if payload["event_type"] not in {
        "registered", "heartbeat", "paused", "resumed", "revoked",
    }:
        raise ConnectorControlError("EXECUTOR_EVENT_TYPE_INVALID")
    now = _now()
    if abs((now - payload["sent_at"]).total_seconds()) > 300:
        raise ConnectorControlError("EXECUTOR_STATUS_TIMESTAMP_OUT_OF_WINDOW")
    digest_payload = {
        key: value for key, value in payload.items() if key != "payload_digest"
    }
    normalized = json.loads(
        json.dumps(
            digest_payload,
            default=lambda value: value.isoformat(),
            sort_keys=True,
        )
    )
    if payload["payload_digest"] != canonical_json_digest_v1(normalized):
        raise ConnectorControlError("EXECUTOR_STATUS_DIGEST_MISMATCH")

    mirror = await session.scalar(
        select(HospitalExecutorMirror)
        .where(
            HospitalExecutorMirror.connector_id == connector.id,
            HospitalExecutorMirror.executor_instance_id
            == payload["executor_instance_id"],
        )
        .with_for_update()
    )
    created = mirror is None
    if mirror is None:
        if payload["status_sequence"] != 1 or payload["event_type"] != "registered":
            raise ConnectorControlError("EXECUTOR_STATUS_INITIAL_EVENT_INVALID")
        mirror = HospitalExecutorMirror(
            connector_id=connector.id,
            space_id=connector.space_id,
            organization_id=connector.organization_id,
            executor_instance_id=payload["executor_instance_id"],
            executor_version=payload["executor_version"],
            architecture=payload["architecture"],
            status=payload["status"],
            certificate_fingerprint=payload["certificate_fingerprint"],
            capability_manifest_digest=payload["capability_manifest_digest"],
            runtime_digest=payload["runtime_digest"],
            image_digest=payload["image_digest"],
            security_status=payload["security_status"],
            last_status_sequence=payload["status_sequence"],
            last_heartbeat_sequence=payload["heartbeat_sequence"],
            last_heartbeat_at=payload["heartbeat_at"],
            execution_enabled=False,
            hard_isolation=False,
        )
        session.add(mirror)
        await session.flush()
    else:
        if payload["status_sequence"] <= mirror.last_status_sequence:
            prior = await session.scalar(
                select(HospitalExecutorStatusEvent).where(
                    HospitalExecutorStatusEvent.mirror_id == mirror.id,
                    HospitalExecutorStatusEvent.status_sequence
                    == payload["status_sequence"],
                    HospitalExecutorStatusEvent.payload_digest
                    == payload["payload_digest"],
                )
            )
            if prior is not None:
                return mirror, prior, False
            raise ConnectorControlError("EXECUTOR_STATUS_SEQUENCE_NOT_INCREASING")
        if payload["heartbeat_sequence"] < mirror.last_heartbeat_sequence:
            raise ConnectorControlError("EXECUTOR_HEARTBEAT_SEQUENCE_DECREASED")
        if (
            mirror.status == "revoked"
            and payload["status"] != "revoked"
        ):
            raise ConnectorControlError("EXECUTOR_REVOKED")
        mirror.executor_version = payload["executor_version"]
        mirror.architecture = payload["architecture"]
        mirror.status = payload["status"]
        mirror.certificate_fingerprint = payload["certificate_fingerprint"]
        mirror.capability_manifest_digest = payload["capability_manifest_digest"]
        mirror.runtime_digest = payload["runtime_digest"]
        mirror.image_digest = payload["image_digest"]
        mirror.security_status = payload["security_status"]
        mirror.last_status_sequence = payload["status_sequence"]
        mirror.last_heartbeat_sequence = payload["heartbeat_sequence"]
        mirror.last_heartbeat_at = payload["heartbeat_at"]
        mirror.last_synced_at = now

    event = HospitalExecutorStatusEvent(
        mirror_id=mirror.id,
        connector_id=connector.id,
        status_sequence=payload["status_sequence"],
        schema_version=payload["schema_version"],
        event_type=payload["event_type"],
        status=payload["status"],
        payload_digest=payload["payload_digest"],
        nonce=payload["nonce"],
        verification_status="verified",
        verified_at=now,
        payload_snapshot=normalized,
    )
    session.add(event)
    await session.flush()
    mirror.latest_status_event_id = event.id
    mirror.latest_status_event_sequence = event.status_sequence
    mirror.latest_status_event_digest = event.payload_digest
    mirror.latest_status_schema_version = payload["schema_version"]
    await append_control_audit(
        session,
        space_id=connector.space_id,
        event_type=f"executor.status.{payload['event_type']}",
        subject_type="hospital_executor_mirror",
        subject_id=mirror.id,
        evidence={
            "connector_id": str(connector.id),
            "executor_instance_id": mirror.executor_instance_id,
            "status": mirror.status,
            "status_sequence": mirror.last_status_sequence,
            "heartbeat_sequence": mirror.last_heartbeat_sequence,
            "payload_digest": event.payload_digest,
            "execution_enabled": False,
            "hard_isolation": False,
        },
        actor_connector_id=connector.id,
    )
    return mirror, event, created

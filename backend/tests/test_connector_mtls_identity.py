from __future__ import annotations

import asyncio
import base64
import hashlib
import ssl
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes.connector_control import _ingress_connector
from app.api.routes.policy_control import _verified_policy_connector
from app.modules.connector_control import services
from app.modules.connector_control.models import ConnectorCertificate, HospitalConnector
from app.modules.connector_control.services import ConnectorControlError


def _result(*, stdout: bytes = b"", returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr=b"", returncode=returncode)


def _pem(label: str) -> bytes:
    encoded = base64.b64encode(f"medtrust-test-certificate:{label}".encode()).decode()
    return (
        "-----BEGIN CERTIFICATE-----\n"
        f"{encoded}\n"
        "-----END CERTIFICATE-----\n"
    ).encode("ascii")


def _metadata(certificate_pem: bytes, instance_id: str, serial: str):
    der = ssl.PEM_cert_to_DER_cert(certificate_pem.decode("ascii"))
    return services.ConnectorCertificateMetadata(
        der_bytes=der,
        fingerprint_sha256="sha256:" + hashlib.sha256(der).hexdigest(),
        serial_number=serial,
        subject=f"O=MedTrust Test Hospital,CN={instance_id}",
        issuer="O=Non-Production,CN=MedTrust Local Test CA",
        valid_from=datetime(2025, 1, 1, tzinfo=timezone.utc),
        valid_to=datetime(2030, 1, 1, tzinfo=timezone.utc),
        san_entries=(f"URI:urn:medtrust:connector:{instance_id}",),
        has_san_extension=True,
    )


def _request(*, forged_fingerprint: str = "sha256:" + "0" * 64) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": [
            (b"x-client-certificate-fingerprint", forged_fingerprint.encode()),
        ],
        "client": ("10.0.0.20", 443),
        "server": ("backend", 8000),
        "scheme": "https",
    })


def _identity_record(
    instance_id: str, certificate_pem: bytes, metadata, *, legacy: bool = False,
):
    connector_id, certificate_id = uuid4(), uuid4()
    connector = SimpleNamespace(
        id=connector_id,
        current_certificate_id=certificate_id,
        connector_instance_id=instance_id,
        status="active",
    )
    certificate = SimpleNamespace(
        id=certificate_id,
        connector_id=connector_id,
        status="active",
        revoked_at=None,
        fingerprint_sha256=(
            services.sha256_bytes(certificate_pem)
            if legacy else metadata.fingerprint_sha256
        ),
        serial_number=("legacy-synthetic-serial" if legacy else metadata.serial_number),
        subject=("CN=legacy-synthetic" if legacy else metadata.subject),
        issuer=("CN=legacy-synthetic-ca" if legacy else metadata.issuer),
        valid_from=(datetime(2024, 1, 1, tzinfo=timezone.utc)
                    if legacy else metadata.valid_from),
        valid_to=(datetime(2031, 1, 1, tzinfo=timezone.utc)
                  if legacy else metadata.valid_to),
        certificate_pem=certificate_pem,
    )
    return connector, certificate


class _Session:
    def __init__(self, connector, certificate) -> None:
        self.connector = connector
        self.certificate = certificate

    async def get(self, model, key, **_kwargs):
        if model is HospitalConnector and key == self.connector.id:
            return self.connector
        if model is ConnectorCertificate and key == self.certificate.id:
            return self.certificate
        return None


def _stub_csr(
    monkeypatch, *, instance_id: str, san_uri: str | None,
    extra_extension: str | None = None,
) -> bytes:
    detail = "Public-Key: (2048 bit)\n"
    if san_uri is not None:
        detail += f"X509v3 Subject Alternative Name:\n    URI:{san_uri}\n"
    if extra_extension is not None:
        detail += extra_extension

    def run(command, **_kwargs):
        if "-verify" in command:
            return _result()
        if "-text" in command:
            return _result(stdout=detail.encode())
        if "-subject" in command:
            return _result(
                stdout=f"subject=O=MedTrust Test Hospital,CN={instance_id}\n".encode()
            )
        raise AssertionError(command)

    monkeypatch.setattr(services, "_openssl", lambda: "openssl")
    monkeypatch.setattr(services.subprocess, "run", run)
    return (
        b"-----BEGIN CERTIFICATE REQUEST-----\n"
        + b"A" * 400
        + b"\n-----END CERTIFICATE REQUEST-----\n"
    )


def test_certificate_parser_uses_canonical_der_and_actual_fields(monkeypatch) -> None:
    instance_id = "hc-11111111-1111-4111-8111-111111111111"
    certificate_pem = _pem("actual-fields")
    output = (
        f"subject=O=MedTrust Test Hospital,CN={instance_id}\n"
        "issuer=O=Non-Production,CN=MedTrust Local Test CA\n"
        "serial=03E9\n"
        "notBefore=Jan  1 00:00:00 2025 GMT\n"
        "notAfter=Jan  1 00:00:00 2030 GMT\n"
        "X509v3 Subject Alternative Name:\n"
        f"    URI:urn:medtrust:connector:{instance_id}\n"
    ).encode()
    monkeypatch.setattr(services, "_openssl", lambda: "openssl")
    monkeypatch.setattr(
        services.subprocess,
        "run",
        lambda *_args, **_kwargs: _result(stdout=output),
    )

    metadata = services._certificate_metadata(certificate_pem)
    der = ssl.PEM_cert_to_DER_cert(certificate_pem.decode("ascii"))

    assert metadata.fingerprint_sha256 == "sha256:" + hashlib.sha256(der).hexdigest()
    assert metadata.serial_number == "03E9"
    assert metadata.subject == f"O=MedTrust Test Hospital,CN={instance_id}"
    assert metadata.issuer == "O=Non-Production,CN=MedTrust Local Test CA"
    assert metadata.san_uris == (f"urn:medtrust:connector:{instance_id}",)


def test_csr_requires_exact_uri_san(monkeypatch) -> None:
    instance_id = "hc-22222222-2222-4222-8222-222222222222"
    expected_uri = f"urn:medtrust:connector:{instance_id}"
    csr = _stub_csr(monkeypatch, instance_id=instance_id, san_uri=expected_uri)

    services._validate_csr(csr, connector_instance_id=instance_id)


def test_csr_rejects_mismatched_uri_san(monkeypatch) -> None:
    instance_id = "hc-33333333-3333-4333-8333-333333333333"
    csr = _stub_csr(
        monkeypatch,
        instance_id=instance_id,
        san_uri="urn:medtrust:connector:hc-44444444-4444-4444-8444-444444444444",
    )

    with pytest.raises(ConnectorControlError, match="CSR_IDENTITY_MISMATCH"):
        services._validate_csr(csr, connector_instance_id=instance_id)


def test_legacy_csr_compatibility_is_exact_cn_only(monkeypatch) -> None:
    instance_id = "hc-55555555-5555-4555-8555-555555555555"
    csr = _stub_csr(monkeypatch, instance_id=instance_id, san_uri=None)

    services._validate_csr(csr, connector_instance_id=instance_id)
    with pytest.raises(ConnectorControlError, match="CSR_IDENTITY_MISMATCH"):
        services._validate_csr(csr, connector_instance_id=f"prefix-{instance_id}")


def test_csr_rejects_extensions_that_copy_extensions_must_not_sign(
    monkeypatch,
) -> None:
    instance_id = "hc-eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    csr = _stub_csr(
        monkeypatch,
        instance_id=instance_id,
        san_uri=f"urn:medtrust:connector:{instance_id}",
        extra_extension="X509v3 Basic Constraints: critical\n    CA:TRUE\n",
    )

    with pytest.raises(ConnectorControlError, match="CSR_EXTENSION_NOT_ALLOWED"):
        services._validate_csr(csr, connector_instance_id=instance_id)


def test_signing_command_preserves_csr_extensions(tmp_path, monkeypatch) -> None:
    csr = b"test-csr"
    ca_key, ca_cert = tmp_path / "ca.key.pem", tmp_path / "ca.cert.pem"
    captured: list[str] = []

    def run(command, **_kwargs):
        captured.extend(command)
        output = command[command.index("-out") + 1]
        Path(output).write_bytes(_pem("signed"))
        return _result()

    monkeypatch.setattr(services, "ensure_test_ca", lambda: (ca_key, ca_cert))
    monkeypatch.setattr(services, "_pki_root", lambda: tmp_path)
    monkeypatch.setattr(services, "_openssl", lambda: "openssl")
    monkeypatch.setattr(services.subprocess, "run", run)

    assert services._sign_csr_bytes(csr) == _pem("signed")
    assert captured[captured.index("-copy_extensions") + 1] == "copy"


def _patch_certificate_metadata(monkeypatch, mapping: dict[bytes, object]) -> None:
    monkeypatch.setattr(
        services,
        "_certificate_metadata",
        lambda certificate_pem: mapping[certificate_pem],
    )


def test_ingress_ignores_fingerprint_header_and_accepts_actual_certificate(
    monkeypatch,
) -> None:
    instance_id = "hc-66666666-6666-4666-8666-666666666666"
    certificate_pem = _pem("target")
    metadata = _metadata(certificate_pem, instance_id, "03EA")
    _patch_certificate_metadata(monkeypatch, {certificate_pem: metadata})
    connector, certificate = _identity_record(instance_id, certificate_pem, metadata)

    verified_connector, verified_certificate = asyncio.run(_ingress_connector(
        _Session(connector, certificate),
        connector.id,
        quote(certificate_pem.decode("ascii"), safe=""),
        "true",
        _request(),
    ))

    assert verified_connector is connector
    assert verified_certificate is certificate
    assert certificate.fingerprint_sha256 != "sha256:" + "0" * 64


def test_cross_connector_certificate_cannot_impersonate_target(monkeypatch) -> None:
    target_id = "hc-77777777-7777-4777-8777-777777777777"
    attacker_id = "hc-88888888-8888-4888-8888-888888888888"
    target_pem, attacker_pem = _pem("target"), _pem("attacker")
    target_metadata = _metadata(target_pem, target_id, "03EB")
    attacker_metadata = _metadata(attacker_pem, attacker_id, "03EC")
    _patch_certificate_metadata(
        monkeypatch,
        {target_pem: target_metadata, attacker_pem: attacker_metadata},
    )
    connector, certificate = _identity_record(
        target_id, target_pem, target_metadata
    )

    with pytest.raises(HTTPException) as rejected:
        asyncio.run(_ingress_connector(
            _Session(connector, certificate),
            connector.id,
            quote(attacker_pem.decode("ascii"), safe=""),
            "true",
            _request(forged_fingerprint=certificate.fingerprint_sha256),
        ))

    assert rejected.value.status_code == 403
    assert "CLIENT_CERTIFICATE_RECORD_MISMATCH" in rejected.value.detail


def test_legacy_record_accepts_only_the_exact_stored_certificate(monkeypatch) -> None:
    instance_id = "hc-bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    certificate_pem = _pem("legacy-target")
    metadata = _metadata(certificate_pem, instance_id, "03EF")
    _patch_certificate_metadata(monkeypatch, {certificate_pem: metadata})
    connector, certificate = _identity_record(
        instance_id, certificate_pem, metadata, legacy=True
    )

    verified_connector, verified_certificate = asyncio.run(_ingress_connector(
        _Session(connector, certificate),
        connector.id,
        quote(certificate_pem.decode("ascii"), safe=""),
        "true",
        _request(forged_fingerprint=certificate.fingerprint_sha256),
    ))

    assert verified_connector is connector
    assert verified_certificate.fingerprint_sha256 == services.sha256_bytes(
        certificate_pem
    )


def test_legacy_record_rejects_another_valid_connector_certificate(
    monkeypatch,
) -> None:
    target_id = "hc-cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    attacker_id = "hc-dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    target_pem, attacker_pem = _pem("legacy-target"), _pem("legacy-attacker")
    target_metadata = _metadata(target_pem, target_id, "03F0")
    attacker_metadata = _metadata(attacker_pem, attacker_id, "03F1")
    _patch_certificate_metadata(
        monkeypatch,
        {target_pem: target_metadata, attacker_pem: attacker_metadata},
    )
    connector, certificate = _identity_record(
        target_id, target_pem, target_metadata, legacy=True
    )

    with pytest.raises(HTTPException) as rejected:
        asyncio.run(_ingress_connector(
            _Session(connector, certificate),
            connector.id,
            quote(attacker_pem.decode("ascii"), safe=""),
            "true",
            _request(forged_fingerprint=certificate.fingerprint_sha256),
        ))

    assert rejected.value.status_code == 403


def test_policy_ingress_reuses_verified_actual_certificate(monkeypatch) -> None:
    instance_id = "hc-99999999-9999-4999-8999-999999999999"
    certificate_pem = _pem("policy")
    metadata = _metadata(certificate_pem, instance_id, "03ED")
    _patch_certificate_metadata(monkeypatch, {certificate_pem: metadata})
    connector, certificate = _identity_record(instance_id, certificate_pem, metadata)

    verified = asyncio.run(_verified_policy_connector(
        _Session(connector, certificate),
        connector.id,
        quote(certificate_pem.decode("ascii"), safe=""),
        "true",
        _request(),
    ))

    assert verified is connector


def test_revoked_certificate_record_is_rejected(monkeypatch) -> None:
    instance_id = "hc-aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    certificate_pem = _pem("revoked")
    metadata = _metadata(certificate_pem, instance_id, "03EE")
    _patch_certificate_metadata(monkeypatch, {certificate_pem: metadata})
    connector, certificate = _identity_record(instance_id, certificate_pem, metadata)
    certificate.status = "revoked"
    certificate.revoked_at = datetime.now(timezone.utc)

    with pytest.raises(HTTPException) as rejected:
        asyncio.run(_ingress_connector(
            _Session(connector, certificate),
            connector.id,
            quote(certificate_pem.decode("ascii"), safe=""),
            "true",
            _request(),
        ))

    assert rejected.value.status_code == 403

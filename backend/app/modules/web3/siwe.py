"""Strict EIP-4361 profile for EOA authentication.

This module deliberately supports a small SIWE profile: implicit HTTPS (or
loopback HTTP) origin, an optional one-line statement, and a mandatory
expiration time.  Role and organization claims are intentionally absent;
authorization must come from server-side wallet bindings after authentication.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from urllib.parse import urlsplit, urlunsplit

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import to_checksum_address

_HEADER_SUFFIX = " wants you to sign in with your Ethereum account:"
_ADDRESS_RE = re.compile(r"0x[0-9A-Fa-f]{40}\Z")
_NONCE_RE = re.compile(r"[A-Za-z0-9]{8,64}\Z")
_SIGNATURE_RE = re.compile(r"0x[0-9A-Fa-f]{130}\Z")
_RFC3339_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\Z"
)
_DNS_RE = re.compile(
    r"(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*\Z"
)
_STATEMENT_RE = re.compile(r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=% ]{1,256}\Z")
_MAX_MESSAGE_LENGTH = 4096
_MAX_URI_LENGTH = 2048
_MAX_CHAIN_ID = 2**256 - 1


class SiweValidationError(ValueError):
    """Safe, machine-readable SIWE validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SiweMessage:
    domain: str
    address: str
    uri: str
    chain_id: int
    nonce: str
    issued_at: datetime
    expiration_time: datetime
    statement: str | None = None
    version: str = "1"


def _fail(code: str, message: str) -> NoReturn:
    raise SiweValidationError(code, message)


def _require_text(value: object, *, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        _fail(f"invalid_{name}", f"{name} is missing or too long")
    if not value.isascii() or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        _fail(f"invalid_{name}", f"{name} must contain printable ASCII only")
    return value


def _require_message(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_MESSAGE_LENGTH:
        _fail("invalid_message", "message is missing or too long")
    if not value.isascii() or any(
        (ord(character) < 32 and character != "\n") or ord(character) == 127
        for character in value
    ):
        _fail("invalid_message", "message contains an unsafe character")
    return value


def normalize_domain(domain: str) -> str:
    value = _require_text(domain, name="domain", maximum=300)
    if (
        "://" in value
        or "\\" in value
        or value.startswith("//")
        or any(character.isspace() for character in value)
    ):
        _fail("invalid_domain", "domain must be an authority without a scheme")
    parsed = urlsplit(f"//{value}")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        _fail("invalid_domain", "domain must contain only a host and optional port")
    try:
        port = parsed.port
    except ValueError:
        _fail("invalid_domain", "domain contains an invalid port")
    if port is not None and not 1 <= port <= 65535:
        _fail("invalid_domain", "domain port is outside the valid range")

    host = parsed.hostname.lower()
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        if not _DNS_RE.fullmatch(host):
            _fail("invalid_domain", "domain contains an invalid host")
        normalized_host = host
    else:
        normalized_host = (
            f"[{parsed_ip.compressed}]"
            if parsed_ip.version == 6
            else parsed_ip.compressed
        )
    return f"{normalized_host}:{port}" if port is not None else normalized_host


def normalize_uri(uri: str) -> str:
    value = _require_text(uri, name="uri", maximum=_MAX_URI_LENGTH)
    if "\\" in value or any(character.isspace() for character in value):
        _fail("invalid_uri", "URI contains an unsafe character")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.netloc:
        _fail("invalid_uri", "URI must be an absolute HTTP(S) URI")
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        _fail("invalid_uri", "URI user information and fragments are not allowed")
    authority = normalize_domain(parsed.netloc)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if scheme == "http" and not _is_loopback_host(host):
        _fail("invalid_uri", "plain HTTP is allowed only for loopback development")
    return urlunsplit((scheme, authority, parsed.path, parsed.query, ""))


def _is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_eoa_address(address: str) -> str:
    value = _require_text(address, name="address", maximum=42)
    if not _ADDRESS_RE.fullmatch(value):
        _fail("invalid_address", "address must contain exactly 20 hexadecimal bytes")
    try:
        return to_checksum_address(value)
    except (TypeError, ValueError):
        _fail("invalid_address", "address is not a valid Ethereum address")


def validate_nonce(nonce: str) -> str:
    value = _require_text(nonce, name="nonce", maximum=64)
    if not _NONCE_RE.fullmatch(value):
        _fail("invalid_nonce", "nonce must contain 8 to 64 ASCII letters or digits")
    return value


def generate_nonce() -> str:
    """Return a 128-bit, EIP-4361-compatible server challenge nonce."""

    return secrets.token_hex(16)


def nonce_digest(nonce: str) -> str:
    validated = validate_nonce(nonce)
    return f"sha256:{hashlib.sha256(validated.encode('ascii')).hexdigest()}"


def validate_chain_id(chain_id: int) -> int:
    if isinstance(chain_id, bool) or not isinstance(chain_id, int):
        _fail("invalid_chain_id", "chain ID must be an integer")
    if not 1 <= chain_id <= _MAX_CHAIN_ID:
        _fail("invalid_chain_id", "chain ID is outside the EIP-155 range")
    return chain_id


def _normalize_datetime(value: datetime, *, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        _fail(f"invalid_{name}", f"{name} must be timezone-aware")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError):
        _fail(f"invalid_{name}", f"{name} is outside the supported range")


def _format_datetime(value: datetime, *, name: str) -> str:
    normalized = _normalize_datetime(value, name=name)
    return normalized.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime(value: str, *, name: str) -> datetime:
    if not _RFC3339_RE.fullmatch(value):
        _fail(f"invalid_{name}", f"{name} must be an RFC 3339 date-time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"invalid_{name}", f"{name} must be an RFC 3339 date-time")
    return _normalize_datetime(parsed, name=name)


def _validate_statement(statement: str | None) -> str | None:
    if statement is None:
        return None
    if not isinstance(statement, str) or not _STATEMENT_RE.fullmatch(statement):
        _fail("invalid_statement", "statement must be one safe printable line")
    return statement


def _uri_authority(uri: str) -> str:
    return normalize_domain(urlsplit(uri).netloc)


def build_siwe_message(
    *,
    domain: str,
    address: str,
    uri: str,
    chain_id: int,
    nonce: str,
    issued_at: datetime,
    expiration_time: datetime,
    statement: str | None = None,
) -> str:
    normalized_domain = normalize_domain(domain)
    normalized_uri = normalize_uri(uri)
    if _uri_authority(normalized_uri) != normalized_domain:
        _fail("uri_domain_mismatch", "URI authority must match the signing domain")
    normalized_address = normalize_eoa_address(address)
    validated_chain_id = validate_chain_id(chain_id)
    validated_nonce = validate_nonce(nonce)
    normalized_issued_at = _normalize_datetime(issued_at, name="issued_at")
    normalized_expiration = _normalize_datetime(
        expiration_time, name="expiration_time"
    )
    if normalized_expiration <= normalized_issued_at:
        _fail("invalid_expiration_time", "expiration time must follow issued time")
    validated_statement = _validate_statement(statement)

    lines = [
        f"{normalized_domain}{_HEADER_SUFFIX}",
        normalized_address,
        "",
    ]
    if validated_statement is not None:
        lines.append(validated_statement)
    lines.extend(
        [
            "",
            f"URI: {normalized_uri}",
            "Version: 1",
            f"Chain ID: {validated_chain_id}",
            f"Nonce: {validated_nonce}",
            f"Issued At: {_format_datetime(normalized_issued_at, name='issued_at')}",
            "Expiration Time: "
            f"{_format_datetime(normalized_expiration, name='expiration_time')}",
        ]
    )
    return "\n".join(lines)


def parse_siwe_message(message: str) -> SiweMessage:
    value = _require_message(message)
    if "\r" in value or value.endswith("\n"):
        _fail("invalid_message", "message must use canonical LF line endings")
    lines = value.split("\n")
    if len(lines) < 10 or not lines[0].endswith(_HEADER_SUFFIX):
        _fail("invalid_message", "message does not match the SIWE profile")

    domain_text = lines[0][: -len(_HEADER_SUFFIX)]
    domain = normalize_domain(domain_text)
    address = normalize_eoa_address(lines[1])
    if lines[1] != address:
        _fail("invalid_address", "SIWE EOA address must use EIP-55 checksum casing")
    if lines[2] != "":
        _fail("invalid_message", "message is missing the address separator")

    if lines[3] == "":
        statement = None
        field_start = 4
    else:
        statement = _validate_statement(lines[3])
        if len(lines) <= 4 or lines[4] != "":
            _fail("invalid_message", "message is missing the statement separator")
        field_start = 5

    if len(lines) != field_start + 6:
        _fail("invalid_message", "message contains missing or unsupported fields")

    def field(offset: int, prefix: str) -> str:
        line = lines[field_start + offset]
        if not line.startswith(prefix):
            _fail("invalid_message", f"expected {prefix.rstrip()}")
        result = line[len(prefix) :]
        if not result:
            _fail("invalid_message", f"{prefix.rstrip(': ')} is empty")
        return result

    uri = normalize_uri(field(0, "URI: "))
    version = field(1, "Version: ")
    if version != "1":
        _fail("invalid_version", "only SIWE version 1 is supported")
    chain_text = field(2, "Chain ID: ")
    if not re.fullmatch(r"[1-9][0-9]*", chain_text):
        _fail("invalid_chain_id", "chain ID must be a canonical positive integer")
    chain_id = validate_chain_id(int(chain_text))
    nonce = validate_nonce(field(3, "Nonce: "))
    issued_at = _parse_datetime(field(4, "Issued At: "), name="issued_at")
    expiration_time = _parse_datetime(
        field(5, "Expiration Time: "), name="expiration_time"
    )
    if _uri_authority(uri) != domain:
        _fail("uri_domain_mismatch", "URI authority must match the signing domain")
    if expiration_time <= issued_at:
        _fail("invalid_expiration_time", "expiration time must follow issued time")

    return SiweMessage(
        domain=domain,
        address=address,
        uri=uri,
        chain_id=chain_id,
        nonce=nonce,
        issued_at=issued_at,
        expiration_time=expiration_time,
        statement=statement,
        version=version,
    )


def validate_siwe_message(
    message: str,
    *,
    expected_domain: str,
    expected_uri: str,
    expected_address: str,
    expected_nonce: str,
    allowed_chain_ids: Collection[int],
    now: datetime,
    consumed_nonce_digests: Collection[str] = (),
    max_age: timedelta = timedelta(minutes=5),
    max_lifetime: timedelta = timedelta(minutes=10),
    clock_skew: timedelta = timedelta(seconds=30),
) -> SiweMessage:
    """Validate signed-message inputs without mutating replay state.

    The caller must atomically mark ``nonce_digest(result.nonce)`` consumed when
    creating the authenticated session.  This pure pre-check cannot by itself
    prevent two concurrent database transactions from consuming one challenge.
    """

    parsed = parse_siwe_message(message)
    domain = normalize_domain(expected_domain)
    uri = normalize_uri(expected_uri)
    address = normalize_eoa_address(expected_address)
    nonce = validate_nonce(expected_nonce)
    current_time = _normalize_datetime(now, name="now")
    if max_age <= timedelta(0) or max_lifetime <= timedelta(0):
        _fail("invalid_time_policy", "time limits must be positive")
    if clock_skew < timedelta(0):
        _fail("invalid_time_policy", "clock skew cannot be negative")

    chains = {validate_chain_id(chain_id) for chain_id in allowed_chain_ids}
    if not chains:
        _fail("invalid_chain_policy", "at least one allowed chain ID is required")
    if parsed.domain != domain:
        _fail("domain_mismatch", "SIWE domain does not match this relying party")
    if parsed.uri != uri:
        _fail("uri_mismatch", "SIWE URI does not match this authentication request")
    if parsed.address != address:
        _fail("address_mismatch", "SIWE address does not match the challenged wallet")
    if parsed.chain_id not in chains:
        _fail("chain_mismatch", "SIWE chain ID is not allowed")
    if not hmac.compare_digest(parsed.nonce, nonce):
        _fail("nonce_mismatch", "SIWE nonce does not match the challenge")
    if nonce_digest(parsed.nonce) in consumed_nonce_digests:
        _fail("nonce_replayed", "SIWE nonce has already been consumed")
    if parsed.issued_at > current_time + clock_skew:
        _fail("issued_in_future", "SIWE issued time is in the future")
    if parsed.issued_at < current_time - max_age:
        _fail("issued_too_old", "SIWE message is older than the allowed age")
    if parsed.expiration_time <= current_time:
        _fail("message_expired", "SIWE message has expired")
    if parsed.expiration_time - parsed.issued_at > max_lifetime:
        _fail("lifetime_too_long", "SIWE message lifetime exceeds policy")
    return parsed


def recover_eoa_address(message: str, signature: str) -> str:
    """Recover the EOA that made an EIP-191 ``personal_sign`` signature."""

    _require_message(message)
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        _fail("invalid_signature", "signature must be a 65-byte hexadecimal value")
    try:
        recovered = Account.recover_message(
            encode_defunct(text=message), signature=signature
        )
    except Exception:
        _fail("invalid_signature", "signature recovery failed")
    return normalize_eoa_address(recovered)


def verify_siwe_eoa(
    message: str,
    signature: str,
    *,
    expected_domain: str,
    expected_uri: str,
    expected_address: str,
    expected_nonce: str,
    allowed_chain_ids: Collection[int],
    now: datetime,
    consumed_nonce_digests: Collection[str] = (),
    max_age: timedelta = timedelta(minutes=5),
    max_lifetime: timedelta = timedelta(minutes=10),
    clock_skew: timedelta = timedelta(seconds=30),
) -> SiweMessage:
    """Validate the SIWE profile and prove that its address signed the message."""

    parsed = validate_siwe_message(
        message,
        expected_domain=expected_domain,
        expected_uri=expected_uri,
        expected_address=expected_address,
        expected_nonce=expected_nonce,
        allowed_chain_ids=allowed_chain_ids,
        now=now,
        consumed_nonce_digests=consumed_nonce_digests,
        max_age=max_age,
        max_lifetime=max_lifetime,
        clock_skew=clock_skew,
    )
    recovered = recover_eoa_address(message, signature)
    if recovered != parsed.address:
        _fail("signature_mismatch", "signature was not produced by the SIWE address")
    return parsed

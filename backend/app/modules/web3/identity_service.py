"""Transactional SIWE wallet binding and login services.

Wallet signatures prove control of an address only.  Platform roles are always
derived from current server-side organization and space records.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import NoReturn
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import AuditInvariantError, canonical_json_digest_v1
from app.modules.identity.local_auth import session_digest
from app.modules.identity.models import (
    LocalDemoSession,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.spaces.models import (
    Space,
    SpaceParticipant,
    SpaceParticipantRole,
)
from app.modules.web3.models import (
    ChainEventReceipt,
    WalletAuthChallenge,
    WalletIdentityBinding,
)
from app.modules.web3.siwe import (
    SiweValidationError,
    build_siwe_message,
    generate_nonce,
    nonce_digest,
    normalize_domain,
    normalize_eoa_address,
    normalize_uri,
    parse_siwe_message,
    validate_chain_id,
    verify_siwe_eoa,
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HASH_RE = re.compile(r"0x[0-9a-f]{64}\Z")
_RFC3339_UTC_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z\Z"
)
_UINT256_MAX = 2**256 - 1
_SUPPORTED_ROLES = frozenset(
    {"data_provider", "model_provider", "data_requester", "space_operator"}
)
_MEMBER_ROLES_BY_SPACE_ROLE = {
    "data_provider": frozenset({"provider_data_admin"}),
    "model_provider": frozenset({"contract_signer"}),
    "data_requester": frozenset(
        {"consumer_researcher", "consumer_ai_developer"}
    ),
    "space_operator": frozenset({"contract_signer"}),
}
_RECEIPT_PAYLOAD_FIELDS = frozenset(
    {
        "schema_version",
        "binding_id",
        "token_id",
        "holder_address",
        "issuer_address",
        "role_code",
        "credential_scope_digest",
        "credential_expires_at",
    }
)


class WalletIdentityError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _fail(code: str, message: str, *, status_code: int = 400) -> NoReturn:
    raise WalletIdentityError(code, message, status_code=status_code)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        _fail("invalid_time", f"{field_name} must be a datetime")
    if value.tzinfo is None:
        # SQLite drops timezone metadata. Production values are stored as UTC.
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _normalized_wallet(address: str) -> tuple[str, str]:
    try:
        checksum = normalize_eoa_address(address)
    except SiweValidationError as exc:
        _fail(
            "invalid_wallet_address",
            f"wallet address is invalid: {exc.code}",
        )
    return checksum, checksum.lower()


def _validate_digest(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        _fail("invalid_receipt", f"{field_name} must be a lowercase SHA-256 digest")
    return value


def _validate_hash(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail("invalid_receipt", f"{field_name} must be a 32-byte lowercase hash")
    return value


def _validate_uuid(value: object, *, field_name: str) -> UUID:
    try:
        result = value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail("invalid_receipt", f"{field_name} must be a UUID")
    if result.int == 0:
        _fail("invalid_receipt", f"{field_name} cannot be the nil UUID")
    return result


def _validate_token_id(value: object) -> str:
    if isinstance(value, bool):
        _fail("invalid_receipt", "credential token ID must be a uint256")
    text_value = str(value)
    if not re.fullmatch(r"0|[1-9][0-9]*", text_value):
        _fail("invalid_receipt", "credential token ID must be canonical decimal")
    if int(text_value) > _UINT256_MAX:
        _fail("invalid_receipt", "credential token ID exceeds uint256")
    return text_value


@dataclass(frozen=True, slots=True)
class WalletIdentityPolicy:
    domain: str
    login_uri: str
    bind_uri: str
    allowed_chain_ids: tuple[int, ...]
    challenge_ttl: timedelta = timedelta(minutes=5)
    session_lifetime: timedelta = timedelta(hours=12)
    clock_skew: timedelta = timedelta(seconds=30)
    minimum_receipt_confirmations: int = 1

    def __post_init__(self) -> None:
        try:
            domain = normalize_domain(self.domain)
            login_uri = normalize_uri(self.login_uri)
            bind_uri = normalize_uri(self.bind_uri)
            chains = tuple(
                sorted(
                    {
                        validate_chain_id(value)
                        for value in self.allowed_chain_ids
                    }
                )
            )
        except SiweValidationError as exc:
            _fail("invalid_policy", f"wallet identity policy is invalid: {exc.code}")
        if any(
            normalize_domain(urlsplit(uri).netloc) != domain
            for uri in (login_uri, bind_uri)
        ):
            _fail(
                "invalid_policy",
                "wallet identity URI authority must match the signing domain",
            )
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "login_uri", login_uri)
        object.__setattr__(self, "bind_uri", bind_uri)
        if not chains:
            _fail("invalid_policy", "at least one chain must be allowed")
        if not timedelta(seconds=30) <= self.challenge_ttl <= timedelta(minutes=10):
            _fail("invalid_policy", "challenge TTL must be between 30 seconds and 10 minutes")
        if not timedelta(minutes=1) <= self.session_lifetime <= timedelta(days=1):
            _fail("invalid_policy", "session lifetime must be between 1 minute and 1 day")
        if self.clock_skew < timedelta(0) or self.clock_skew > timedelta(minutes=2):
            _fail("invalid_policy", "clock skew must be between 0 and 2 minutes")
        if (
            isinstance(self.minimum_receipt_confirmations, bool)
            or self.minimum_receipt_confirmations < 1
        ):
            _fail("invalid_policy", "receipt finality must require confirmations")
        object.__setattr__(self, "allowed_chain_ids", chains)

    def uri_for(self, purpose: str) -> str:
        if purpose == "login":
            return self.login_uri
        if purpose == "bind":
            return self.bind_uri
        _fail("invalid_purpose", "unsupported wallet challenge purpose")


@dataclass(frozen=True, slots=True)
class ChallengeIssue:
    challenge_id: UUID
    purpose: str
    message: str
    expires_at: datetime
    binding_id: UUID


@dataclass(frozen=True, slots=True)
class WalletSessionIssue:
    session_id: UUID
    secret: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class WalletLoginIssue:
    session: WalletSessionIssue
    user_id: UUID
    binding_id: UUID


@dataclass(frozen=True, slots=True)
class VerifiedCredentialReceipt:
    binding_id: UUID
    chain_id: int
    contract_address: str
    token_id: str
    holder_address: str
    issuer_address: str
    role_code: str
    credential_scope_digest: str
    credential_expires_at: datetime
    transaction_hash: str
    block_number: int
    block_hash: str
    log_index: int
    confirmations: int
    event_name: str = "CredentialIssued"
    status: str = "finalized"


WalletSessionFactory = Callable[
    [AsyncSession, User, WalletIdentityBinding, datetime, timedelta],
    Awaitable[WalletSessionIssue],
]


@dataclass(frozen=True, slots=True)
class _BindingScope:
    space_id: UUID
    organization_id: UUID
    role_code: str


def binding_scope_digest(binding: WalletIdentityBinding) -> str:
    return canonical_json_digest_v1(
        {
            "schema_version": "medtrust.wallet-credential-scope/v1",
            "binding_id": str(binding.id),
            "space_id": str(binding.space_id),
            "user_id": str(binding.user_id),
            "organization_id": str(binding.organization_id),
            "role_code": binding.role_code,
            "chain_id": binding.chain_id,
            "wallet_address": binding.wallet_address,
            "did_uri": binding.did_uri,
        }
    )


def _pending_binding_digest(binding: WalletIdentityBinding) -> str:
    return canonical_json_digest_v1(
        {
            "schema_version": "medtrust.wallet-binding-pending/v1",
            "credential_scope_digest": binding_scope_digest(binding),
        }
    )


def _wallet_proof_digest(
    binding: WalletIdentityBinding, challenge: WalletAuthChallenge
) -> str:
    return canonical_json_digest_v1(
        {
            "schema_version": "medtrust.wallet-binding-proof/v1",
            "credential_scope_digest": binding_scope_digest(binding),
            "challenge_id": str(challenge.id),
            "message_digest": challenge.message_digest,
            "consumed_at": _as_utc(
                challenge.consumed_at, field_name="consumed_at"
            ).isoformat(),
        }
    )


def _membership_is_current(member: OrganizationMember, now: datetime) -> bool:
    if member.status != "active":
        return False
    if member.valid_from is not None and _as_utc(
        member.valid_from, field_name="valid_from"
    ) > now:
        return False
    return member.valid_until is None or _as_utc(
        member.valid_until, field_name="valid_until"
    ) > now


async def _eligible_binding_scopes(
    session: AsyncSession,
    *,
    user: User,
    space_id: UUID,
    now: datetime,
    require_verified_organization: bool,
) -> list[_BindingScope]:
    if user.status != "active":
        return []
    rows = (
        await session.execute(
            select(
                OrganizationMember,
                Organization,
                Space,
                SpaceParticipant,
                SpaceParticipantRole.role_code,
            )
            .join(
                Organization,
                Organization.id == OrganizationMember.organization_id,
            )
            .join(
                SpaceParticipant,
                SpaceParticipant.organization_id == Organization.id,
            )
            .join(Space, Space.id == SpaceParticipant.space_id)
            .join(
                SpaceParticipantRole,
                SpaceParticipantRole.space_participant_id == SpaceParticipant.id,
            )
            .where(
                OrganizationMember.user_id == user.id,
                Space.id == space_id,
                Organization.status == "active",
                Space.status == "active",
                SpaceParticipant.admission_status == "admitted",
                SpaceParticipantRole.role_code.in_(_SUPPORTED_ROLES),
            )
        )
    ).all()
    membership_ids = {
        member.id for member, _, _, _, _ in rows if _membership_is_current(member, now)
    }
    member_roles: dict[UUID, set[str]] = {identifier: set() for identifier in membership_ids}
    if membership_ids:
        role_rows = (
            await session.execute(
                select(
                    OrganizationMemberRole.organization_member_id,
                    OrganizationMemberRole.role_code,
                ).where(
                    OrganizationMemberRole.organization_member_id.in_(membership_ids)
                )
            )
        ).all()
        for membership_id, role_code in role_rows:
            member_roles[membership_id].add(role_code)

    result: set[_BindingScope] = set()
    for member, organization, space, _, role_code in rows:
        if member.id not in membership_ids:
            continue
        if require_verified_organization and organization.verification_status != "verified":
            continue
        required_roles = _MEMBER_ROLES_BY_SPACE_ROLE[role_code]
        if not member_roles[member.id].intersection(required_roles):
            continue
        if (
            role_code == "space_operator"
            and organization.id != space.operator_organization_id
        ):
            continue
        result.add(
            _BindingScope(
                space_id=space.id,
                organization_id=organization.id,
                role_code=role_code,
            )
        )
    return sorted(
        result,
        key=lambda item: (str(item.organization_id), item.role_code),
    )


async def _assert_binding_qualification(
    session: AsyncSession,
    binding: WalletIdentityBinding,
    *,
    now: datetime,
    require_credential: bool,
    require_verified_organization: bool = True,
) -> User:
    user = await session.get(User, binding.user_id)
    if user is None:
        _fail("wallet_not_eligible", "wallet is not eligible", status_code=403)
    scopes = await _eligible_binding_scopes(
        session,
        user=user,
        space_id=binding.space_id,
        now=now,
        require_verified_organization=require_verified_organization,
    )
    expected = _BindingScope(
        space_id=binding.space_id,
        organization_id=binding.organization_id,
        role_code=binding.role_code,
    )
    if expected not in scopes:
        _fail("wallet_not_eligible", "wallet is not eligible", status_code=403)
    if require_credential:
        expires_at = binding.credential_expires_at
        if (
            binding.status != "active"
            or not binding.credential_contract_address
            or binding.credential_token_id is None
            or binding.credential_scope_digest != binding_scope_digest(binding)
            or expires_at is None
            or _as_utc(expires_at, field_name="credential_expires_at") <= now
        ):
            _fail("wallet_not_eligible", "wallet is not eligible", status_code=403)
    return user


async def _require_space_operator(
    session: AsyncSession, *, user: User, space_id: UUID, now: datetime
) -> None:
    scopes = await _eligible_binding_scopes(
        session,
        user=user,
        space_id=space_id,
        now=now,
        require_verified_organization=True,
    )
    if not any(scope.role_code == "space_operator" for scope in scopes):
        _fail("operator_required", "space operator authority is required", status_code=403)


def _require_allowed_chain(policy: WalletIdentityPolicy, chain_id: int) -> int:
    try:
        validated = validate_chain_id(chain_id)
    except SiweValidationError as exc:
        _fail("invalid_chain_id", f"wallet chain is invalid: {exc.code}")
    if validated not in policy.allowed_chain_ids:
        _fail("chain_not_allowed", "wallet chain is not allowed", status_code=400)
    return validated


async def _create_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    purpose: str,
    binding: WalletIdentityBinding,
    subject_user_id: UUID | None,
    now: datetime,
) -> ChallengeIssue:
    issued_at = now.replace(microsecond=0)
    expires_at = issued_at + policy.challenge_ttl
    nonce = generate_nonce()
    checksum_address, stored_address = _normalized_wallet(binding.wallet_address)
    if stored_address != binding.wallet_address:
        _fail("wallet_not_eligible", "wallet is not eligible", status_code=403)
    message = build_siwe_message(
        domain=policy.domain,
        address=checksum_address,
        uri=policy.uri_for(purpose),
        chain_id=binding.chain_id,
        nonce=nonce,
        issued_at=issued_at,
        expiration_time=expires_at,
        statement=(
            "Sign in to MedTrust Space."
            if purpose == "login"
            else "Bind this wallet to your existing MedTrust Space account."
        ),
    )
    challenge = WalletAuthChallenge(
        purpose=purpose,
        wallet_binding_id=binding.id,
        subject_user_id=subject_user_id,
        chain_id=binding.chain_id,
        wallet_address=binding.wallet_address,
        domain=policy.domain,
        uri=policy.uri_for(purpose),
        nonce_digest=nonce_digest(nonce),
        message_digest=_sha256_text(message),
        issued_at=issued_at,
        expires_at=expires_at,
    )
    session.add(challenge)
    await session.flush()
    return ChallengeIssue(
        challenge_id=challenge.id,
        purpose=purpose,
        message=message,
        expires_at=expires_at,
        binding_id=binding.id,
    )


async def create_login_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    chain_id: int,
    wallet_address: str,
    now: datetime | None = None,
) -> ChallengeIssue:
    current_time = _as_utc(now or utc_now(), field_name="now")
    validated_chain = _require_allowed_chain(policy, chain_id)
    _, stored_address = _normalized_wallet(wallet_address)
    binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(
            WalletIdentityBinding.chain_id == validated_chain,
            WalletIdentityBinding.wallet_address == stored_address,
            WalletIdentityBinding.status == "active",
        )
        .with_for_update()
    )
    if binding is None:
        _fail("wallet_not_eligible", "wallet is not eligible", status_code=403)
    await _assert_binding_qualification(
        session, binding, now=current_time, require_credential=True
    )
    return await _create_challenge(
        session,
        policy=policy,
        purpose="login",
        binding=binding,
        subject_user_id=None,
        now=current_time,
    )


async def create_bind_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    user: User,
    space_id: UUID,
    chain_id: int,
    wallet_address: str,
    now: datetime | None = None,
) -> ChallengeIssue:
    """Create a pending binding using only roles resolved from server state."""

    current_time = _as_utc(now or utc_now(), field_name="now")
    validated_chain = _require_allowed_chain(policy, chain_id)
    _, stored_address = _normalized_wallet(wallet_address)
    scopes = await _eligible_binding_scopes(
        session,
        user=user,
        space_id=space_id,
        now=current_time,
        require_verified_organization=False,
    )
    if not scopes:
        _fail("binding_not_allowed", "account has no bindable server role", status_code=403)
    if len(scopes) != 1:
        _fail(
            "ambiguous_server_role",
            "account has multiple server roles; an operator must resolve the scope",
            status_code=409,
        )
    scope = scopes[0]

    scope_binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(
            WalletIdentityBinding.space_id == scope.space_id,
            WalletIdentityBinding.user_id == user.id,
            WalletIdentityBinding.role_code == scope.role_code,
        )
        .with_for_update()
    )
    wallet_binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(
            WalletIdentityBinding.chain_id == validated_chain,
            WalletIdentityBinding.wallet_address == stored_address,
        )
        .with_for_update()
    )
    if scope_binding is not None:
        same_wallet = (
            scope_binding.chain_id == validated_chain
            and scope_binding.wallet_address == stored_address
        )
        if (
            scope_binding.status != "pending"
            or not same_wallet
            or scope_binding.organization_id != scope.organization_id
        ):
            _fail("binding_conflict", "server role is already bound", status_code=409)
        binding = scope_binding
    elif wallet_binding is not None:
        _fail("binding_conflict", "wallet is already bound", status_code=409)
    else:
        binding = WalletIdentityBinding(
            space_id=scope.space_id,
            user_id=user.id,
            organization_id=scope.organization_id,
            role_code=scope.role_code,
            chain_id=validated_chain,
            wallet_address=stored_address,
            did_uri=f"did:pkh:eip155:{validated_chain}:{stored_address}",
            status="pending",
            identity_evidence_digest="sha256:" + "0" * 64,
        )
        session.add(binding)
        await session.flush()
        binding.identity_evidence_digest = _pending_binding_digest(binding)
    if wallet_binding is not None and wallet_binding.id != binding.id:
        _fail("binding_conflict", "wallet is already bound", status_code=409)
    return await _create_challenge(
        session,
        policy=policy,
        purpose="bind",
        binding=binding,
        subject_user_id=user.id,
        now=current_time,
    )


async def _lock_and_verify_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    challenge_id: UUID,
    purpose: str,
    message: str,
    signature: str,
    subject_user_id: UUID | None,
    now: datetime,
) -> tuple[WalletAuthChallenge, WalletIdentityBinding]:
    challenge = await session.scalar(
        select(WalletAuthChallenge)
        .where(WalletAuthChallenge.id == challenge_id)
        .with_for_update()
    )
    if challenge is None or challenge.purpose != purpose:
        _fail("challenge_invalid", "wallet challenge is invalid", status_code=401)
    if challenge.subject_user_id != subject_user_id:
        _fail("challenge_invalid", "wallet challenge is invalid", status_code=401)
    if (
        challenge.domain != policy.domain
        or challenge.uri != policy.uri_for(purpose)
        or challenge.chain_id not in policy.allowed_chain_ids
    ):
        _fail("challenge_invalid", "wallet challenge is invalid", status_code=401)
    if challenge.consumed_at is not None:
        _fail("challenge_replayed", "wallet challenge was already consumed", status_code=409)
    if _as_utc(challenge.expires_at, field_name="expires_at") <= now:
        _fail("challenge_expired", "wallet challenge has expired", status_code=401)
    if not hmac.compare_digest(challenge.message_digest, _sha256_text(message)):
        _fail("challenge_tampered", "wallet challenge message was changed", status_code=401)

    try:
        parsed = parse_siwe_message(message)
    except SiweValidationError as exc:
        _fail("siwe_invalid", f"invalid SIWE message: {exc.code}", status_code=401)
    if not hmac.compare_digest(challenge.nonce_digest, nonce_digest(parsed.nonce)):
        _fail("challenge_tampered", "wallet challenge nonce was changed", status_code=401)
    try:
        verify_siwe_eoa(
            message,
            signature,
            expected_domain=challenge.domain,
            expected_uri=challenge.uri,
            expected_address=challenge.wallet_address,
            expected_nonce=parsed.nonce,
            allowed_chain_ids=policy.allowed_chain_ids,
            now=now,
            max_age=policy.challenge_ttl,
            max_lifetime=policy.challenge_ttl,
            clock_skew=policy.clock_skew,
        )
    except SiweValidationError as exc:
        _fail("siwe_invalid", f"invalid SIWE proof: {exc.code}", status_code=401)
    if (
        parsed.chain_id != challenge.chain_id
        or parsed.issued_at != _as_utc(challenge.issued_at, field_name="issued_at")
        or parsed.expiration_time
        != _as_utc(challenge.expires_at, field_name="expires_at")
    ):
        _fail("challenge_tampered", "wallet challenge metadata was changed", status_code=401)

    binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(WalletIdentityBinding.id == challenge.wallet_binding_id)
        .with_for_update()
    )
    if binding is None:
        _fail("challenge_invalid", "wallet challenge is invalid", status_code=401)
    if (
        binding.chain_id != challenge.chain_id
        or binding.wallet_address != challenge.wallet_address
    ):
        _fail("challenge_invalid", "wallet challenge is invalid", status_code=401)
    return challenge, binding


async def verify_bind_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    user: User,
    challenge_id: UUID,
    message: str,
    signature: str,
    now: datetime | None = None,
) -> WalletIdentityBinding:
    current_time = _as_utc(now or utc_now(), field_name="now")
    challenge, binding = await _lock_and_verify_challenge(
        session,
        policy=policy,
        challenge_id=challenge_id,
        purpose="bind",
        message=message,
        signature=signature,
        subject_user_id=user.id,
        now=current_time,
    )
    if binding.user_id != user.id or binding.status != "pending":
        _fail("binding_not_pending", "wallet binding is not pending", status_code=409)
    await _assert_binding_qualification(
        session,
        binding,
        now=current_time,
        require_credential=False,
        require_verified_organization=False,
    )
    challenge.consumed_at = current_time
    binding.identity_evidence_digest = _wallet_proof_digest(binding, challenge)
    binding.row_version += 1
    await session.flush()
    return binding


async def issue_local_siwe_session(
    session: AsyncSession,
    user: User,
    binding: WalletIdentityBinding,
    now: datetime,
    requested_lifetime: timedelta,
) -> WalletSessionIssue:
    """Default injectable session factory for the existing opaque cookie."""

    credential_expiry = _as_utc(
        binding.credential_expires_at, field_name="credential_expires_at"
    )
    expires_at = min(now + requested_lifetime, credential_expiry)
    secret = secrets.token_urlsafe(32)
    login_session = LocalDemoSession(
        user_id=user.id,
        session_digest=session_digest(secret),
        auth_method="siwe",
        wallet_binding_id=binding.id,
        expires_at=expires_at,
        last_seen_at=now,
        created_at=now,
    )
    user.last_authenticated_at = now
    session.add(login_session)
    await session.flush()
    return WalletSessionIssue(
        session_id=login_session.id,
        secret=secret,
        expires_at=expires_at,
    )


async def verify_login_challenge(
    session: AsyncSession,
    *,
    policy: WalletIdentityPolicy,
    challenge_id: UUID,
    message: str,
    signature: str,
    session_factory: WalletSessionFactory = issue_local_siwe_session,
    now: datetime | None = None,
) -> WalletLoginIssue:
    current_time = _as_utc(now or utc_now(), field_name="now")
    challenge, binding = await _lock_and_verify_challenge(
        session,
        policy=policy,
        challenge_id=challenge_id,
        purpose="login",
        message=message,
        signature=signature,
        subject_user_id=None,
        now=current_time,
    )
    user = await _assert_binding_qualification(
        session, binding, now=current_time, require_credential=True
    )
    issued = await session_factory(
        session, user, binding, current_time, policy.session_lifetime
    )
    if not isinstance(issued, WalletSessionIssue):
        _fail("invalid_session_factory", "wallet session factory returned invalid data")
    normalized_expiry = _as_utc(
        issued.expires_at,
        field_name="session_expires_at",
    )
    if (
        not isinstance(issued.session_id, UUID)
        or issued.session_id.int == 0
        or not isinstance(issued.secret, str)
        or len(issued.secret) < 32
        or not issued.secret.isascii()
        or any(ord(character) < 33 or ord(character) == 127 for character in issued.secret)
        or normalized_expiry <= current_time
        or normalized_expiry
        > _as_utc(binding.credential_expires_at, field_name="credential_expires_at")
    ):
        _fail("invalid_session_factory", "wallet session factory returned invalid data")
    challenge.consumed_at = current_time
    await session.flush()
    return WalletLoginIssue(
        session=WalletSessionIssue(
            session_id=issued.session_id,
            secret=issued.secret,
            expires_at=normalized_expiry,
        ),
        user_id=user.id,
        binding_id=binding.id,
    )


def validate_verified_credential_receipt(
    receipt: VerifiedCredentialReceipt,
    *,
    binding: WalletIdentityBinding,
    now: datetime,
    minimum_confirmations: int,
) -> VerifiedCredentialReceipt:
    if not isinstance(receipt, VerifiedCredentialReceipt):
        _fail("invalid_receipt", "credential receipt was not verified upstream")
    if receipt.event_name != "CredentialIssued" or receipt.status not in {
        "finalized",
        "applied",
    }:
        _fail("invalid_receipt", "credential issue event is not finalized")
    if (
        isinstance(minimum_confirmations, bool)
        or not isinstance(minimum_confirmations, int)
        or minimum_confirmations < 1
    ):
        _fail("invalid_receipt", "minimum confirmations must be a positive integer")
    try:
        chain_id = validate_chain_id(receipt.chain_id)
        contract_address = normalize_eoa_address(receipt.contract_address).lower()
        holder_address = normalize_eoa_address(receipt.holder_address).lower()
        issuer_address = normalize_eoa_address(receipt.issuer_address).lower()
    except SiweValidationError as exc:
        _fail("invalid_receipt", f"credential receipt is invalid: {exc.code}")
    token_id = _validate_token_id(receipt.token_id)
    scope_digest = _validate_digest(
        receipt.credential_scope_digest,
        field_name="credential_scope_digest",
    )
    expires_at = _as_utc(
        receipt.credential_expires_at,
        field_name="credential_expires_at",
    )
    transaction_hash = _validate_hash(
        receipt.transaction_hash, field_name="transaction_hash"
    )
    block_hash = _validate_hash(receipt.block_hash, field_name="block_hash")
    for value, field_name in (
        (receipt.block_number, "block_number"),
        (receipt.log_index, "log_index"),
        (receipt.confirmations, "confirmations"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail("invalid_receipt", f"{field_name} must be a nonnegative integer")
    if receipt.confirmations < minimum_confirmations:
        _fail("invalid_receipt", "credential receipt lacks finality confirmations")
    if (
        receipt.binding_id != binding.id
        or chain_id != binding.chain_id
        or holder_address != binding.wallet_address
        or receipt.role_code != binding.role_code
        or scope_digest != binding_scope_digest(binding)
        or expires_at <= now
    ):
        _fail("receipt_mismatch", "credential receipt does not match binding scope")
    return VerifiedCredentialReceipt(
        binding_id=binding.id,
        chain_id=chain_id,
        contract_address=contract_address,
        token_id=token_id,
        holder_address=holder_address,
        issuer_address=issuer_address,
        role_code=receipt.role_code,
        credential_scope_digest=scope_digest,
        credential_expires_at=expires_at,
        transaction_hash=transaction_hash,
        block_number=receipt.block_number,
        block_hash=block_hash,
        log_index=receipt.log_index,
        confirmations=receipt.confirmations,
        event_name=receipt.event_name,
        status=receipt.status,
    )


def _parse_receipt_expiry(value: object) -> datetime:
    if not isinstance(value, str) or _RFC3339_UTC_RE.fullmatch(value) is None:
        _fail("invalid_receipt", "credential expiry must be an RFC 3339 UTC value")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail("invalid_receipt", "credential expiry must be an RFC 3339 UTC value")


async def load_verified_credential_receipt(
    session: AsyncSession, receipt_id: UUID
) -> VerifiedCredentialReceipt:
    """Load a finality-checked credential event from the trusted chain mirror."""

    record = await session.scalar(
        select(ChainEventReceipt)
        .where(ChainEventReceipt.id == receipt_id)
        .with_for_update()
    )
    if (
        record is None
        or record.event_name != "CredentialIssued"
        or record.status not in {"finalized", "applied"}
        or record.subject_type != "wallet_identity_binding"
        or record.finalized_at is None
        or (record.status == "applied" and record.applied_at is None)
    ):
        _fail("receipt_not_verified", "credential receipt is not finalized", status_code=409)
    payload = record.payload_snapshot
    if not isinstance(payload, Mapping) or frozenset(payload) != _RECEIPT_PAYLOAD_FIELDS:
        _fail("invalid_receipt", "credential receipt payload has an invalid shape")
    try:
        calculated_payload_digest = canonical_json_digest_v1(dict(payload))
    except AuditInvariantError:
        _fail("invalid_receipt", "credential receipt payload is not canonical JSON")
    if not isinstance(record.payload_digest, str) or not hmac.compare_digest(
        record.payload_digest,
        calculated_payload_digest,
    ):
        _fail("invalid_receipt", "credential receipt payload digest does not match")
    if payload["schema_version"] != "medtrust.role-credential-issued/v1":
        _fail("invalid_receipt", "credential receipt schema is unsupported")
    binding_id = _validate_uuid(payload["binding_id"], field_name="binding_id")
    if record.subject_key != str(binding_id):
        _fail("invalid_receipt", "credential receipt subject does not match payload")
    issuer_address = str(payload["issuer_address"])
    if record.actor_wallet != issuer_address:
        _fail("invalid_receipt", "credential receipt issuer does not match its actor")
    return VerifiedCredentialReceipt(
        binding_id=binding_id,
        chain_id=record.chain_id,
        contract_address=record.contract_address,
        token_id=_validate_token_id(payload["token_id"]),
        holder_address=str(payload["holder_address"]),
        issuer_address=issuer_address,
        role_code=str(payload["role_code"]),
        credential_scope_digest=str(payload["credential_scope_digest"]),
        credential_expires_at=_parse_receipt_expiry(
            payload["credential_expires_at"]
        ),
        transaction_hash=record.transaction_hash,
        block_number=record.block_number,
        block_hash=record.block_hash,
        log_index=record.log_index,
        confirmations=record.confirmations,
        event_name=record.event_name,
        status=record.status,
    )


async def _require_completed_bind_proof(
    session: AsyncSession, binding: WalletIdentityBinding
) -> None:
    challenge = await session.scalar(
        select(WalletAuthChallenge)
        .where(
            WalletAuthChallenge.wallet_binding_id == binding.id,
            WalletAuthChallenge.purpose == "bind",
            WalletAuthChallenge.consumed_at.is_not(None),
        )
        .order_by(WalletAuthChallenge.consumed_at.desc())
        .limit(1)
    )
    if challenge is None or not hmac.compare_digest(
        binding.identity_evidence_digest,
        _wallet_proof_digest(binding, challenge),
    ):
        _fail("wallet_proof_missing", "wallet ownership proof is missing", status_code=409)


async def validate_credential_issuance_eligibility(
    session: AsyncSession,
    binding: WalletIdentityBinding,
    *,
    now: datetime | None = None,
) -> datetime:
    """Recheck current platform qualification and the consumed wallet proof.

    This public service boundary must run before an irreversible credential
    issuance transaction is returned or relayed.
    """

    current_time = _as_utc(now or utc_now(), field_name="now")
    await _assert_binding_qualification(
        session,
        binding,
        now=current_time,
        require_credential=False,
    )
    await _require_completed_bind_proof(session, binding)
    return current_time


async def approve_wallet_binding(
    session: AsyncSession,
    *,
    binding_id: UUID,
    operator_user: User,
    receipt: VerifiedCredentialReceipt,
    minimum_confirmations: int = 1,
    now: datetime | None = None,
) -> WalletIdentityBinding:
    current_time = _as_utc(now or utc_now(), field_name="now")
    binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(WalletIdentityBinding.id == binding_id)
        .with_for_update()
    )
    if binding is None:
        _fail("binding_not_found", "wallet binding was not found", status_code=404)
    if binding.status != "pending":
        _fail("binding_not_pending", "wallet binding is not pending", status_code=409)
    await _require_space_operator(
        session,
        user=operator_user,
        space_id=binding.space_id,
        now=current_time,
    )
    await _assert_binding_qualification(
        session,
        binding,
        now=current_time,
        require_credential=False,
    )
    await _require_completed_bind_proof(session, binding)
    normalized_receipt = validate_verified_credential_receipt(
        receipt,
        binding=binding,
        now=current_time,
        minimum_confirmations=minimum_confirmations,
    )
    binding.status = "active"
    binding.credential_contract_address = normalized_receipt.contract_address
    binding.credential_token_id = normalized_receipt.token_id
    binding.credential_scope_digest = normalized_receipt.credential_scope_digest
    binding.credential_expires_at = normalized_receipt.credential_expires_at
    binding.verified_at = current_time
    binding.verified_by = operator_user.id
    binding.row_version += 1
    await session.flush()
    return binding


async def revoke_wallet_binding(
    session: AsyncSession,
    *,
    binding_id: UUID,
    operator_user: User,
    reason: str,
    now: datetime | None = None,
) -> WalletIdentityBinding:
    current_time = _as_utc(now or utc_now(), field_name="now")
    clean_reason = reason.strip() if isinstance(reason, str) else ""
    if not 3 <= len(clean_reason) <= 500 or any(
        ord(character) < 32 for character in clean_reason
    ):
        _fail("invalid_revocation_reason", "revocation reason must be 3 to 500 characters")
    binding = await session.scalar(
        select(WalletIdentityBinding)
        .where(WalletIdentityBinding.id == binding_id)
        .with_for_update()
    )
    if binding is None:
        _fail("binding_not_found", "wallet binding was not found", status_code=404)
    if binding.status != "active":
        _fail("binding_not_active", "wallet binding is not active", status_code=409)
    await _require_space_operator(
        session,
        user=operator_user,
        space_id=binding.space_id,
        now=current_time,
    )
    binding.status = "revoked"
    binding.revoked_at = current_time
    binding.revocation_reason = clean_reason
    binding.row_version += 1
    await session.execute(
        update(LocalDemoSession)
        .where(
            LocalDemoSession.wallet_binding_id == binding.id,
            LocalDemoSession.revoked_at.is_(None),
        )
        .values(revoked_at=current_time)
    )
    await session.flush()
    return binding

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity.local_auth import (
    ROLE_BY_SUBJECT,
    USERNAME_BY_ROLE,
    _password_hash,
)
from app.modules.identity.models import (
    LocalDemoCredential,
    Organization,
    OrganizationMember,
    OrganizationMemberRole,
    User,
)
from app.modules.spaces.models import Space, SpaceParticipant, SpaceParticipantRole

PUBLIC_ALPHA_SPACE_CODE = "MEDTRUST-PUBLIC-ALPHA"

ACCOUNT_METADATA = {
    "space_operator": ("MedTrust Space Public Alpha", "operator", "Public Alpha Operator"),
    "data_provider": ("Synthetic Hospital Provider", "hospital", "Hospital Demo Administrator"),
    "model_provider": ("Public Model Provider", "ai_company", "Model Demo Administrator"),
    "data_requester": ("Synthetic Research Requester", "research_institute", "Requester Demo Administrator"),
    "catalog_curator": ("Public Catalog Curator", "service_provider", "Catalog Curator"),
}


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"medtrust:public-alpha:{label}")


@dataclass(frozen=True)
class PublicAlphaAccountsResult:
    created: bool
    operator_id: UUID
    space_id: UUID


async def public_alpha_account_status(session: AsyncSession) -> dict[str, object]:
    expected_roles = set(ACCOUNT_METADATA)
    expected_user_ids = {_id(f"user:{role}") for role in expected_roles}
    expected_organization_ids = {
        _id(f"organization:{role}") for role in expected_roles
    }
    expected_usernames = {USERNAME_BY_ROLE[role] for role in expected_roles}

    users = list(
        (
            await session.scalars(
                select(User).where(User.id.in_(expected_user_ids))
            )
        ).all()
    )
    organizations = list(
        (
            await session.scalars(
                select(Organization).where(
                    Organization.id.in_(expected_organization_ids)
                )
            )
        ).all()
    )
    credentials = list(
        (
            await session.scalars(
                select(LocalDemoCredential).where(
                    LocalDemoCredential.username.in_(expected_usernames)
                )
            )
        ).all()
    )
    memberships = list(
        (
            await session.scalars(
                select(OrganizationMember).where(
                    OrganizationMember.id.in_(
                        {_id(f"membership:{role}") for role in expected_roles}
                    )
                )
            )
        ).all()
    )
    member_roles = list(
        (
            await session.scalars(
                select(OrganizationMemberRole).where(
                    OrganizationMemberRole.organization_member_id.in_(
                        {_id(f"membership:{role}") for role in expected_roles}
                    )
                )
            )
        ).all()
    )
    participants = list(
        (
            await session.scalars(
                select(SpaceParticipant).where(
                    SpaceParticipant.id.in_(
                        {_id(f"participant:{role}") for role in expected_roles}
                    )
                )
            )
        ).all()
    )
    participant_roles = list(
        (
            await session.scalars(
                select(SpaceParticipantRole).where(
                    SpaceParticipantRole.space_participant_id.in_(
                        {_id(f"participant:{role}") for role in expected_roles}
                    )
                )
            )
        ).all()
    )
    space = await session.scalar(
        select(Space).where(
            Space.id == _id("space"),
            Space.code == PUBLIC_ALPHA_SPACE_CODE,
        )
    )
    operator = next(
        (user for user in users if user.id == _id("user:space_operator")),
        None,
    )
    counts = {
        "users": len(users),
        "organizations": len(organizations),
        "credentials": len(credentials),
        "memberships": len(memberships),
        "member_roles": len(member_roles),
        "participants": len(participants),
        "participant_roles": len(participant_roles),
        "spaces": int(space is not None),
    }
    expected_counts = {
        "users": 5,
        "organizations": 5,
        "credentials": 5,
        "memberships": 5,
        "member_roles": 5,
        "participants": 5,
        "participant_roles": 5,
        "spaces": 1,
    }
    users_by_id = {user.id: user for user in users}
    organizations_by_id = {
        organization.id: organization for organization in organizations
    }
    credentials_by_username = {
        credential.username: credential for credential in credentials
    }
    memberships_by_id = {membership.id: membership for membership in memberships}
    participants_by_id = {
        participant.id: participant for participant in participants
    }
    relationships_valid = all(
        (
            users_by_id.get(_id(f"user:{role}")) is not None
            and users_by_id[_id(f"user:{role}")].identity_issuer
            == "medtrust-public-alpha"
            and users_by_id[_id(f"user:{role}")].identity_subject
            == f"public-alpha:{role}"
            and organizations_by_id.get(_id(f"organization:{role}")) is not None
            and organizations_by_id[
                _id(f"organization:{role}")
            ].external_identity_ref
            == f"public-alpha:{role}"
            and credentials_by_username.get(USERNAME_BY_ROLE[role]) is not None
            and credentials_by_username[USERNAME_BY_ROLE[role]].user_id
            == _id(f"user:{role}")
            and memberships_by_id.get(_id(f"membership:{role}")) is not None
            and memberships_by_id[_id(f"membership:{role}")].user_id
            == _id(f"user:{role}")
            and memberships_by_id[_id(f"membership:{role}")].organization_id
            == _id(f"organization:{role}")
            and participants_by_id.get(_id(f"participant:{role}")) is not None
            and participants_by_id[_id(f"participant:{role}")].organization_id
            == _id(f"organization:{role}")
            and participants_by_id[_id(f"participant:{role}")].space_id
            == _id("space")
        )
        for role in expected_roles
    )
    member_role_pairs = {
        (row.organization_member_id, row.role_code) for row in member_roles
    }
    participant_role_pairs = {
        (row.space_participant_id, row.role_code) for row in participant_roles
    }
    roles_valid = all(
        (
            (_id(f"membership:{role}"), "auditor") in member_role_pairs
            and (_id(f"participant:{role}"), role) in participant_role_pairs
        )
        for role in expected_roles
    )
    space_valid = (
        space is not None
        and space.operator_organization_id == _id("organization:space_operator")
    )
    foundation_complete = (
        counts == expected_counts
        and relationships_valid
        and roles_valid
        and space_valid
    )
    foundation_present = any(counts.values())
    return {
        "foundation_complete": foundation_complete,
        "foundation_present": foundation_present,
        "counts": counts,
        "expected_counts": expected_counts,
        "operator_id": str(operator.id) if operator else None,
        "space_id": str(space.id) if space else None,
    }


async def ensure_public_alpha_accounts(
    session: AsyncSession,
    *,
    passwords: dict[str, str],
    min_password_length: int,
) -> PublicAlphaAccountsResult:
    status = await public_alpha_account_status(session)
    if status["foundation_present"]:
        if not status["foundation_complete"]:
            raise RuntimeError("public alpha account foundation is incomplete")
        return PublicAlphaAccountsResult(
            created=False,
            operator_id=UUID(str(status["operator_id"])),
            space_id=UUID(str(status["space_id"])),
        )

    if set(passwords) != set(USERNAME_BY_ROLE.values()) or not all(passwords.values()):
        raise ValueError("all invitation account passwords are required")

    now = datetime.now(timezone.utc)
    users: dict[str, User] = {}
    organizations: dict[str, Organization] = {}
    for subject, role in ROLE_BY_SUBJECT.items():
        legal_name, organization_type, display_name = ACCOUNT_METADATA[role]
        user = User(
            id=_id(f"user:{role}"),
            identity_issuer="medtrust-public-alpha",
            identity_subject=f"public-alpha:{role}",
            display_name=display_name,
            email=f"{role}@public-alpha.medtrust.invalid",
            status="active",
            mfa_status="disabled",
            is_demo=True,
        )
        organization = Organization(
            id=_id(f"organization:{role}"),
            legal_name=legal_name,
            display_name=legal_name,
            organization_type=organization_type,
            verification_status="verified",
            status="active",
            external_identity_ref=f"public-alpha:{role}",
            contact_metadata={
                "schema_version": "1.0",
                "demo": True,
                "synthetic_or_public": True,
                "non_clinical": True,
                "hard_isolation": False,
            },
            is_demo=True,
        )
        session.add_all([user, organization])
        users[role] = user
        organizations[role] = organization
    await session.flush()

    for role, user in users.items():
        organization = organizations[role]
        member = OrganizationMember(
            id=_id(f"membership:{role}"),
            organization_id=organization.id,
            user_id=user.id,
            status="active",
            valid_from=now,
            created_by=user.id,
        )
        session.add(member)
        await session.flush()
        session.add(
            OrganizationMemberRole(
                organization_member_id=member.id,
                role_code="auditor",
                granted_by=user.id,
            )
        )
        session.add(
            LocalDemoCredential(
                user_id=user.id,
                username=USERNAME_BY_ROLE[role],
                password_hash=_password_hash(
                    passwords[USERNAME_BY_ROLE[role]],
                    min_length=min_password_length,
                ),
                is_enabled=True,
            )
        )

    operator = users["space_operator"]
    operator_org = organizations["space_operator"]
    space = Space(
        id=_id("space"),
        code=PUBLIC_ALPHA_SPACE_CODE,
        name="MedTrust Public Alpha",
        space_type="industry",
        operator_organization_id=operator_org.id,
        status="active",
        ruleset_version="public-alpha-v1",
        classification_scheme_version="synthetic-public-non-clinical-v1",
        default_retention_policy={"schema_version": "1.0", "days": 30},
        is_demo=True,
        created_by=operator.id,
    )
    session.add(space)
    await session.flush()
    for role, organization in organizations.items():
        participant = SpaceParticipant(
            id=_id(f"participant:{role}"),
            space_id=space.id,
            organization_id=organization.id,
            admission_status="admitted",
            ruleset_accepted_version=space.ruleset_version,
            admitted_at=now,
            created_by=operator.id,
        )
        session.add(participant)
        await session.flush()
        session.add(
            SpaceParticipantRole(
                space_participant_id=participant.id,
                role_code=role,
                granted_by=operator.id,
            )
        )
    return PublicAlphaAccountsResult(created=True, operator_id=operator.id, space_id=space.id)

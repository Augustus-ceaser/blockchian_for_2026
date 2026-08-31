from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import DataProductSource, DataProductVersion, DataResource
from app.modules.connectors.models import Connector, ConnectorCapability
from app.modules.external_catalog.models import (
    DataProductExternalSourceLink,
    ExternalCatalogSource,
)


SERVICE_CAPABILITY_SCHEMA = "medtrust.data-service-capability/v1"
HEARTBEAT_MAX_AGE = timedelta(minutes=5)
REQUIRED_CONTROLLED_COMPUTE_CAPABILITIES = frozenset(
    {
        "controlled_compute_execution",
        "egress_policy_enforcement",
        "audit_evidence_emit",
    }
)

ServiceMode = Literal["metadata_only", "controlled_compute"]
Requestability = Literal["eligible", "not_eligible"]
RuntimeAvailability = Literal["ready", "degraded", "unavailable", "not_applicable"]

_SERVICE_MODE_LABELS: dict[ServiceMode, str] = {
    "metadata_only": "目录发现",
    "controlled_compute": "受控计算",
}
_REQUESTABILITY_LABELS: dict[Requestability, str] = {
    "eligible": "可申请",
    "not_eligible": "不可申请",
}
_AVAILABILITY_LABELS: dict[RuntimeAvailability, str] = {
    "ready": "当前可用",
    "degraded": "部分可用",
    "unavailable": "暂不可用",
    "not_applicable": "仅目录信息",
}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized else None


@dataclass(frozen=True)
class ConnectorServiceFacts:
    connector_key: str
    verification_status: str
    runtime_status: str
    last_heartbeat_at: datetime | None
    verified_capabilities: frozenset[str]


@dataclass(frozen=True)
class ResourceServiceFacts:
    resource_key: str
    connectors: tuple[ConnectorServiceFacts, ...]


@dataclass(frozen=True)
class DataServiceCapabilityProjection:
    service_mode: ServiceMode
    requestability: Requestability
    runtime_availability: RuntimeAvailability
    blocker_codes: tuple[str, ...]
    evidence_at: datetime | None
    evaluated_at: datetime

    @property
    def application_eligible(self) -> bool:
        return self.requestability == "eligible"

    @property
    def execution_readiness(self) -> Literal["ready", "not_ready"]:
        return "ready" if self.runtime_availability == "ready" else "not_ready"

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": SERVICE_CAPABILITY_SCHEMA,
            "service_mode": self.service_mode,
            "service_mode_label": _SERVICE_MODE_LABELS[self.service_mode],
            "requestability": self.requestability,
            "requestability_label": _REQUESTABILITY_LABELS[self.requestability],
            "runtime_availability": self.runtime_availability,
            "runtime_availability_label": _AVAILABILITY_LABELS[
                self.runtime_availability
            ],
            "blocker_codes": list(self.blocker_codes),
            "evidence_at": _iso(self.evidence_at),
            "evaluated_at": _iso(self.evaluated_at),
        }


@dataclass(frozen=True)
class _ConnectorEvaluation:
    connector_key: str
    requestable: bool
    availability: Literal["ready", "degraded", "unavailable"]
    blocker_codes: tuple[str, ...]
    evidence_at: datetime | None


def _evaluate_connector(
    connector: ConnectorServiceFacts,
    *,
    evaluated_at: datetime,
) -> _ConnectorEvaluation:
    now = _as_utc(evaluated_at)
    assert now is not None
    heartbeat = _as_utc(connector.last_heartbeat_at)
    blockers: set[str] = set()

    verified = connector.verification_status == "verified"
    if not verified:
        blockers.add("CONNECTOR_UNVERIFIED")

    if connector.runtime_status == "degraded":
        blockers.add("CONNECTOR_DEGRADED")
    elif connector.runtime_status != "online":
        blockers.add(
            "CONNECTOR_OFFLINE"
            if connector.runtime_status in {"offline", "maintenance"}
            else "CONNECTOR_UNAVAILABLE"
        )

    if heartbeat is None:
        blockers.add("HEARTBEAT_MISSING")
    elif heartbeat < now - HEARTBEAT_MAX_AGE:
        blockers.add("HEARTBEAT_STALE")

    if not connector.verified_capabilities.issuperset(
        REQUIRED_CONTROLLED_COMPUTE_CAPABILITIES
    ):
        blockers.add("CAPABILITY_INCOMPLETE")

    requestable = verified and "controlled_compute_execution" in (
        connector.verified_capabilities
    )
    if not blockers:
        availability: Literal["ready", "degraded", "unavailable"] = "ready"
    elif blockers == {"CONNECTOR_DEGRADED"}:
        availability = "degraded"
    else:
        availability = "unavailable"
    return _ConnectorEvaluation(
        connector_key=connector.connector_key,
        requestable=requestable,
        availability=availability,
        blocker_codes=tuple(sorted(blockers)),
        evidence_at=heartbeat,
    )


def _best_connector(
    connectors: tuple[ConnectorServiceFacts, ...],
    *,
    evaluated_at: datetime,
) -> _ConnectorEvaluation | None:
    if not connectors:
        return None
    evaluations = [
        _evaluate_connector(connector, evaluated_at=evaluated_at)
        for connector in connectors
    ]
    ranks = {"ready": 3, "degraded": 2, "unavailable": 1}

    def key(item: _ConnectorEvaluation) -> tuple[int, int, float, str]:
        evidence = _as_utc(item.evidence_at)
        timestamp = evidence.timestamp() if evidence else float("-inf")
        return (
            ranks[item.availability],
            1 if item.requestable else 0,
            timestamp,
            item.connector_key,
        )

    return max(evaluations, key=key)


def project_controlled_compute_service(
    *,
    resources: tuple[ResourceServiceFacts, ...],
    evaluated_at: datetime,
) -> DataServiceCapabilityProjection:
    now = _as_utc(evaluated_at)
    if now is None:
        raise ValueError("evaluated_at is required")
    if not resources:
        return DataServiceCapabilityProjection(
            service_mode="controlled_compute",
            requestability="not_eligible",
            runtime_availability="unavailable",
            blocker_codes=("RESOURCE_MISSING",),
            evidence_at=None,
            evaluated_at=now,
        )

    selected: list[_ConnectorEvaluation | None] = [
        _best_connector(resource.connectors, evaluated_at=now)
        for resource in sorted(resources, key=lambda item: item.resource_key)
    ]
    requestable = all(item is not None and item.requestable for item in selected)
    available_states = [
        "unavailable" if item is None else item.availability for item in selected
    ]
    if all(state == "ready" for state in available_states):
        runtime_availability: RuntimeAvailability = "ready"
    elif any(state in {"ready", "degraded"} for state in available_states):
        runtime_availability = "degraded"
    else:
        runtime_availability = "unavailable"

    blockers = {
        blocker
        for item in selected
        for blocker in (
            ("RESOURCE_SOURCE_MISSING",) if item is None else item.blocker_codes
        )
    }
    evidence_values = [
        _as_utc(item.evidence_at)
        for item in selected
        if item is not None and item.evidence_at is not None
    ]
    evidence_at = (
        min(value for value in evidence_values if value is not None)
        if len(evidence_values) == len(selected)
        else None
    )
    return DataServiceCapabilityProjection(
        service_mode="controlled_compute",
        requestability="eligible" if requestable else "not_eligible",
        runtime_availability=runtime_availability,
        blocker_codes=tuple(sorted(blockers)),
        evidence_at=evidence_at,
        evaluated_at=now,
    )


def project_metadata_only_service(
    *,
    evidence_at: datetime | None,
    evaluated_at: datetime,
) -> DataServiceCapabilityProjection:
    now = _as_utc(evaluated_at)
    if now is None:
        raise ValueError("evaluated_at is required")
    return DataServiceCapabilityProjection(
        service_mode="metadata_only",
        requestability="not_eligible",
        runtime_availability="not_applicable",
        blocker_codes=("EXTERNAL_METADATA_ONLY",),
        evidence_at=_as_utc(evidence_at),
        evaluated_at=now,
    )


async def resolve_data_service_capability(
    session: AsyncSession,
    *,
    version: DataProductVersion,
    external_link: DataProductExternalSourceLink | None = None,
    evaluated_at: datetime | None = None,
) -> DataServiceCapabilityProjection:
    now = _as_utc(evaluated_at or datetime.now(timezone.utc))
    assert now is not None
    if external_link is not None:
        source = await session.get(
            ExternalCatalogSource, external_link.external_catalog_source_id
        )
        evidence_at = (
            source.last_synced_at
            if source is not None and source.last_synced_at is not None
            else external_link.created_at
        )
        return project_metadata_only_service(
            evidence_at=evidence_at,
            evaluated_at=now,
        )

    rows = (
        await session.execute(
            select(DataResource, DataProductSource, Connector)
            .select_from(DataResource)
            .outerjoin(
                DataProductSource,
                DataProductSource.data_resource_id == DataResource.id,
            )
            .outerjoin(Connector, Connector.id == DataProductSource.connector_id)
            .where(DataResource.data_product_version_id == version.id)
            .order_by(
                DataResource.position_no,
                DataProductSource.source_role,
                DataProductSource.local_resource_alias,
            )
        )
    ).all()
    connector_ids = {
        connector.id for _, _, connector in rows if connector is not None
    }
    capabilities_by_connector: dict[Any, set[str]] = {
        connector_id: set() for connector_id in connector_ids
    }
    if connector_ids:
        capability_rows = (
            await session.execute(
                select(
                    ConnectorCapability.connector_id,
                    ConnectorCapability.capability_code,
                ).where(
                    ConnectorCapability.connector_id.in_(connector_ids),
                    ConnectorCapability.status == "verified",
                    ConnectorCapability.verified_at.is_not(None),
                )
            )
        ).all()
        for connector_id, capability_code in capability_rows:
            capabilities_by_connector.setdefault(connector_id, set()).add(
                capability_code
            )

    resource_connectors: dict[str, dict[str, ConnectorServiceFacts]] = {}
    for resource, _, connector in rows:
        resource_key = str(resource.id)
        connector_map = resource_connectors.setdefault(resource_key, {})
        if connector is None:
            continue
        connector_key = str(connector.id)
        connector_map[connector_key] = ConnectorServiceFacts(
            connector_key=connector_key,
            verification_status=connector.verification_status,
            runtime_status=connector.runtime_status,
            last_heartbeat_at=connector.last_heartbeat_at,
            verified_capabilities=frozenset(
                capabilities_by_connector.get(connector.id, set())
            ),
        )
    resources = tuple(
        ResourceServiceFacts(
            resource_key=resource_key,
            connectors=tuple(
                connector_map[key] for key in sorted(connector_map)
            ),
        )
        for resource_key, connector_map in sorted(resource_connectors.items())
    )
    return project_controlled_compute_service(resources=resources, evaluated_at=now)

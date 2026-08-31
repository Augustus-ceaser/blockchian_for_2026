from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.data_services.projection import (
    REQUIRED_CONTROLLED_COMPUTE_CAPABILITIES,
    ConnectorServiceFacts,
    ResourceServiceFacts,
    project_controlled_compute_service,
    project_metadata_only_service,
)


NOW = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)


def _connector(
    *,
    verification_status: str = "verified",
    runtime_status: str = "online",
    heartbeat_age: timedelta | None = timedelta(minutes=1),
    capabilities: frozenset[str] = REQUIRED_CONTROLLED_COMPUTE_CAPABILITIES,
) -> ConnectorServiceFacts:
    return ConnectorServiceFacts(
        connector_key="connector-a",
        verification_status=verification_status,
        runtime_status=runtime_status,
        last_heartbeat_at=None if heartbeat_age is None else NOW - heartbeat_age,
        verified_capabilities=capabilities,
    )


def _project(*connectors: ConnectorServiceFacts):
    return project_controlled_compute_service(
        resources=(
            ResourceServiceFacts(resource_key="resource-a", connectors=connectors),
        ),
        evaluated_at=NOW,
    )


def test_ready_projection_requires_fresh_verified_connector_and_all_capabilities() -> None:
    projection = _project(_connector())

    assert projection.service_mode == "controlled_compute"
    assert projection.requestability == "eligible"
    assert projection.runtime_availability == "ready"
    assert projection.application_eligible is True
    assert projection.execution_readiness == "ready"
    assert projection.blocker_codes == ()
    assert projection.evidence_at == NOW - timedelta(minutes=1)
    assert projection.to_payload()["schema_version"] == (
        "medtrust.data-service-capability/v1"
    )


@pytest.mark.parametrize(
    ("connector", "blocker"),
    [
        (_connector(runtime_status="offline"), "CONNECTOR_OFFLINE"),
        (_connector(heartbeat_age=timedelta(minutes=6)), "HEARTBEAT_STALE"),
        (_connector(heartbeat_age=None), "HEARTBEAT_MISSING"),
        (
            _connector(
                capabilities=frozenset(
                    {
                        "controlled_compute_execution",
                        "egress_policy_enforcement",
                    }
                )
            ),
            "CAPABILITY_INCOMPLETE",
        ),
    ],
)
def test_runtime_failures_do_not_automatically_remove_requestability(
    connector: ConnectorServiceFacts,
    blocker: str,
) -> None:
    projection = _project(connector)

    assert projection.requestability == "eligible"
    assert projection.application_eligible is True
    assert projection.runtime_availability == "unavailable"
    assert projection.execution_readiness == "not_ready"
    assert blocker in projection.blocker_codes


def test_unverified_or_unbound_service_is_not_requestable() -> None:
    unverified = _project(_connector(verification_status="pending"))
    unbound = _project()

    assert unverified.requestability == "not_eligible"
    assert unverified.runtime_availability == "unavailable"
    assert "CONNECTOR_UNVERIFIED" in unverified.blocker_codes
    assert unbound.requestability == "not_eligible"
    assert "RESOURCE_SOURCE_MISSING" in unbound.blocker_codes


def test_partial_multi_resource_availability_is_degraded_and_deterministic() -> None:
    resources = (
        ResourceServiceFacts(resource_key="resource-b", connectors=(_connector(),)),
        ResourceServiceFacts(
            resource_key="resource-a",
            connectors=(_connector(runtime_status="offline"),),
        ),
    )
    first = project_controlled_compute_service(resources=resources, evaluated_at=NOW)
    second = project_controlled_compute_service(
        resources=tuple(reversed(resources)), evaluated_at=NOW
    )

    assert first.runtime_availability == "degraded"
    assert first.execution_readiness == "not_ready"
    assert first.to_payload() == second.to_payload()


def test_metadata_only_projection_is_discoverable_but_never_requestable_or_executable() -> None:
    projection = project_metadata_only_service(
        evidence_at=NOW - timedelta(hours=2),
        evaluated_at=NOW,
    )

    assert projection.service_mode == "metadata_only"
    assert projection.requestability == "not_eligible"
    assert projection.runtime_availability == "not_applicable"
    assert projection.application_eligible is False
    assert projection.execution_readiness == "not_ready"
    assert projection.blocker_codes == ("EXTERNAL_METADATA_ONLY",)


def test_public_payload_does_not_expose_connector_or_secret_material() -> None:
    serialized = str(_project(_connector()).to_payload()).lower()

    for forbidden in (
        "connector-a",
        "endpoint",
        "certificate",
        "credential",
        "secret",
        "local_resource_alias",
    ):
        assert forbidden not in serialized

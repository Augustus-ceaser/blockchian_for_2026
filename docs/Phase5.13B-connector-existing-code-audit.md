# Phase 5.13B Existing Connector Audit

Date: 2026-07-29

## Finding

The pre-existing `Connector` and `ConnectorCapability` tables are central demo
control records. They are not an independently deployed hospital process.
Readiness and the fixed PathMNIST compute path depend on their verified
`controlled_compute_execution`, `egress_policy_enforcement`, and
`audit_evidence_emit` capabilities.

## Decision

Phase 5.13B therefore uses a parallel `HospitalConnector` control domain and an
independent `hospital-connector` service. It does not populate or reinterpret
legacy capabilities. This preserves Phase 5.12 readiness, contracts, run
counts and the PathMNIST reference chain.

The alpha accepts no local path, data asset, model weight, ExecutionOrder,
ComputeJob, Artifact or EvidenceBundle. An active HospitalConnector means only
that identity and control-plane communication passed the local-alpha checks.


# Phase 5.13B Capability Manifest

Capability manifests are append-only version records with Connector-local
sequence, schema/version metadata, OS/architecture, canonical payload digest
and current-version pointer.

Identical capability content may legitimately retain the same digest across
later sequences. Version uniqueness is `(connector_id, sequence)`, while the
digest verifies content.

The database rejects any alpha manifest that enables execution, data transfer,
model transfer, Local Asset Registry, Artifact egress or hard isolation.
Isolation maturity is limited to L0 or L1.


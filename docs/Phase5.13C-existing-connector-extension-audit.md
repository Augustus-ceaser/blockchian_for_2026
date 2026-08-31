# Phase 5.13C Existing Connector Extension Audit

## Baseline

- Git baseline: `c82b9f9f6d15b54152130ee78f1a69bb8f08f4c7`
- Central migration head: `20260729_0050`
- Phase 5.13B control implementation: `c504dc2`
- Phase 5.13B acceptance documentation: `c82b9f9`
- Release candidate tag remains at `c65d154d6200052b419366e84882e295e90243db`.

## Findings

1. The local Connector state is suitable for extension. Its SQLite database owns
   only local state and an append-only audit chain. Phase 5.13C can add
   independently migrated local registry tables without sharing central PGDATA.
2. Phase 5.13B already provides canonical JSON digests and mTLS message
   transport. Phase 5.13C reuses those primitives for a signed metadata bundle;
   it does not add file transfer.
3. The capability manifest can move
   `local_asset_registry_enabled`, `metadata_sync_enabled`, and
   `data_quality_summary_enabled` to `true`. Execution, data transfer, model
   transfer, artifact egress, and hard isolation remain `false`.
4. A paused Connector may send heartbeats only. Metadata synchronization must
   fail closed.
5. A revoked Connector and its revoked certificate must reject metadata
   synchronization.
6. Central ingress already binds an mTLS certificate fingerprint to a Connector.
   The metadata endpoint additionally checks an increasing bundle sequence,
   message digest, payload digest, timestamp, nonce, and active status.
7. Local location references stay in a dedicated SQLite table. Bundle assembly
   uses an allowlist and therefore cannot serialize a path, locator, filename,
   connection string, internal address, patient identifier, or encryption key.
8. No central endpoint can request or read an arbitrary Connector path. Phase
   5.13C does not add such an endpoint.
9. No general-user proxy exists. Central mirror reads are limited to the
   hospital participant that owns the Connector and the platform operator.
10. Local asset mirrors are separate from `DataProduct`, `ModelProduct`, and
    materialization tables. No service or foreign key converts a mirror into a
    product or makes it requestable or executable.

## Extension Decision

Proceed with separate local registry tables, immutable local versions and
quality profiles, an approved metadata-bundle outbox, and append-only central
mirror tables. Do not change the Phase 5.1-5.12 business state machines or the
Phase 5.13B Connector lifecycle.

# Phase 5.13C Metadata Bundle Protocol

The Connector sends an approved JSON bundle through the existing mTLS ingress.
Central validation requires:

- active Connector and active matching certificate;
- enabled registry, metadata-sync, and quality-summary capabilities;
- timestamp within five minutes, unique nonce, increasing sequence;
- canonical bundle, metadata, and quality digests;
- recursive prohibited-field and prohibited-value rejection.

The same bundle ID and digest are idempotent. Reusing a bundle ID with different
content is rejected. Paused and revoked Connectors fail closed.

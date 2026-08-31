# Phase 5.13D Policy Signing Boundary

The policy signer uses a dedicated Ed25519 key, separate from the Connector
RSA/mTLS identity. The private key is generated under the configured D-drive
policy signing root and is never stored in PostgreSQL or Git. PostgreSQL stores
only key ID, algorithm, public key material, fingerprint, status, and dates.

Canonical payloads use sorted compact JSON and SHA-256 digests. OpenSSL performs
signing and verification. This is a local engineering-test key, not an HSM,
production KMS, certificate authority, or clinical-security claim.

Revoked or unknown signing keys fail Connector validation. Signed policy
versions, revocations, receipts, and decisions are protected by immutable
database triggers.

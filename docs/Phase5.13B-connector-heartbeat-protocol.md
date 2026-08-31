# Phase 5.13B Heartbeat Protocol

Heartbeat ingress requires a verified nginx mTLS client certificate. Central
binds certificate subject and fingerprint to the Connector record, then checks
strictly increasing sequence, five-minute timestamp window, known manifest
digest, nonce and canonical message digest.

An active Connector receives `accepted`. A paused Connector receives
`paused_read_only`. A revoked Connector cannot submit a Manifest or Heartbeat
and receives HTTP 403. Heartbeat evidence is append-only and ordinary success
is held in the dedicated heartbeat table and connector audit stream.


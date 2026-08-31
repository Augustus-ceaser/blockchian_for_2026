# Phase 5.13C Prohibited Fields Security Report

Recursive rejection tests cover path and locator fields, Windows and POSIX
absolute paths, filenames and file lists, patient identifiers, database URLs,
credentials, private or encryption keys, internal hosts and addresses, and
nested prohibited values.

Additional fail-closed controls cover invalid certificate identity, disabled
capability, paused/revoked/non-active Connector, stale timestamp, digest
mismatch, non-increasing sequence, and bundle idempotency conflict.

Runtime payload inspection confirmed that the two accepted bundles contain no
local location reference. Original data transfer, model transfer, execution,
Artifact creation, and MinIO object changes were zero.

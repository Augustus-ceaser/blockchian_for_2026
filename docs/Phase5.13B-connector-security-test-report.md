# Phase 5.13B Connector Security Test Report

Verified negative boundaries:

- no-client-certificate ingress: HTTP 400 at nginx;
- hospital self-approval: HTTP 403;
- requester Connector read: HTTP 403;
- revoked Connector Manifest/Heartbeat: HTTP 403;
- token plaintext absent from list API;
- old certificate superseded after rotation;
- revoked certificate recorded as revoked;
- connector and main audit chains valid;
- tracked private keys: 0;
- sensitive token/session findings in diff: 0;
- public data/model files, weights and MinIO objects: delta 0.

Defects found and fixed during runtime acceptance:

1. DemoActor space ownership was read from the wrong object.
2. Container D-drive PKI mount was rejected by a host-only path guard.
3. httpx did not present the client certificate until an explicit SSLContext
   was used.
4. heartbeat audit referenced an unflushed UUID.
5. a shared immutability trigger accessed table-specific fields.
6. manifest digest was incorrectly globally unique.
7. revoked Manifest submission and local revoked-state display were tightened.


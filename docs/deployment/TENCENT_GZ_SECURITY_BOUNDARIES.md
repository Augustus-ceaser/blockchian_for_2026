# Public Alpha Security Boundaries

## Enforced

- Pre-ICP entry is loopback-only.
- Public 80/443 belongs only to Caddy.
- Database, MinIO, Backend, Gateway, Connector, Executor, and Docker API are
  not publicly published.
- No privileged, host-network, or Docker-socket service.
- Pinned base image tags; no `latest`.
- Backend runs as UID/GID 10001 with read-only root filesystem, tmpfs, dropped
  capabilities, and `no-new-privileges`.
- Secrets are root-owned files and are not stored in Compose or images.
- Trusted Host, CORS, trusted Origin/Referer checks, Secure public cookies,
  configurable sessions, strong invitation passwords, and login throttling.
- Debug and demo role switching are disabled.
- Swagger/OpenAPI UI is disabled in deployed modes.

## Not Claimed

- `hard_isolation=false`.
- No clinical validation or patient-data authorization.
- No national certification or compliance attestation.
- No arbitrary model/code execution.
- No real Hospital Connector or Executor on the Tencent central host.
- No protection against a fully compromised Docker host.
- No multi-region availability or automatic off-host backup.

Only Synthetic/Public, Non-clinical demonstration objects are allowed.

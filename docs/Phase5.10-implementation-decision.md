# Phase 5.10 Implementation Decision

Date: 2026-07-26

## 1. Scope

Phase 5.10 adds deployment and access preparation around the frozen Phase 5.1-5.9 business system. It does not change lifecycle state machines, contracts, `run_count`, controlled execution, Artifact quarantine, release-package allowlists, one-time downloads or database business state.

The four explicit modes are:

- `local`: safe default, loopback-only development and roadshow workflow.
- `lan-roadshow`: one Windows host and multiple devices on one trusted Private network.
- `remote-preview`: a protected external preview through Cloudflare Access and Tunnel.
- `production-template`: deployment configuration examples only; it is not a production deployment.

Missing configuration always resolves to `local`.

## 2. Unified Gateway

LAN and remote modes use one pinned Caddy gateway. No second proxy is introduced.

The gateway:

- serves the frontend production build;
- proxies `/api/*` to the internal FastAPI service;
- provides SPA fallback for `/roadshow`, `/join` and `/portal/*`;
- publishes only one user-facing port;
- adds conservative security headers;
- does not expose Vite, backend port 8000, PostgreSQL, MinIO or Docker.

The existing local workflow remains host Uvicorn plus Vite on loopback.

## 3. Frontend Build And Routing

Production builds default to relative `/api/v1`. The local Vite script explicitly injects the existing loopback API URL.

The new read-only join page exposes only:

- deployment mode and safe health status;
- four relative portal URLs;
- locally generated QR codes containing only those URLs;
- prototype and non-clinical boundaries.

It never includes usernames, passwords, tokens, sessions, grants, object-storage details or internal service addresses.

## 4. Backend Mode And Security

Backend settings validate the deployment mode and public origin.

- LAN HTTP cookies keep `Secure=false`.
- Remote HTTPS cookies use `Secure=true`.
- `HttpOnly`, `SameSite=Lax` and `Path=/` remain.
- Credentialed CORS never uses a wildcard.
- Authenticated browser mutations validate `Origin` or `Referer` against configured trusted same-origin values.
- Existing non-browser PowerShell and API workflows without browser origin headers remain supported.

API documentation policy:

- local: normal FastAPI docs remain available;
- lan-roadshow: docs and OpenAPI require an authenticated operator session;
- remote-preview: disabled by default;
- production-template: disabled by default.

No database migration is required. Existing session fields are sufficient for a safe operator-only device-status projection without full IP addresses, fingerprints, cookie values or token digests.

## 5. Network And Firewall

`scripts/get_roadshow_network.ps1` enumerates active IPv4 candidates and marks physical, virtual and suspected VPN interfaces. It rejects loopback and APIPA addresses and does not silently choose an ambiguous or suspicious interface.

The selected interface and IP are written only to an ignored local configuration file.

LAN startup requires a Windows `Private` network profile. The current audited WLAN profile is `Public`, so real LAN startup and firewall acceptance are currently blocked.

Firewall behavior is explicit:

- only the gateway TCP port;
- Private profile only;
- stable, idempotent rule name;
- no automatic change without user confirmation;
- no disabling of Windows Firewall;
- no changes to unrelated rules.

## 6. LAN Runtime

The LAN Compose layer uses immutable application images without source bind mounts or Uvicorn reload.

- Caddy publishes the gateway on the selected LAN IP.
- Backend is reachable only on the Docker network.
- PostgreSQL and MinIO remain internal or loopback-maintenance-only.
- Worker responsibilities preserve the existing fixed controlled-execution design.

Preparation does not reset business data unless `-Reset` is explicitly supplied.

## 7. Remote Preview

Remote preview reuses the same gateway but binds it to loopback. `cloudflared` connects outbound to that origin.

The scripts do not:

- install or authenticate cloudflared;
- create a Cloudflare account, domain, DNS route or tunnel;
- use Quick Tunnel as formal delivery;
- print or commit tunnel credentials;
- start unless Access protection is explicitly confirmed.

Actual remote acceptance remains a manual gate until an account, protected hostname, Access policy, cloudflared installation and explicit user authorization are available.

## 8. Production Template

The production example contains the gateway, static frontend build, backend, PostgreSQL, MinIO and the existing dispatcher/coordinator/executor/callback responsibilities with health checks, restart policies, log limits, persistent volumes and secret placeholders.

It contains no real secrets, public database/object-storage ports, source bind mounts or claim of production readiness.

## 9. Tests And Manual Gates

Automated acceptance covers mode validation, cookie/CORS/CSRF policy, docs policy, safe status projections, relative frontend API use, join/portal routes, QR payload safety, Compose rendering and PowerShell parser/guard behavior.

Real multi-device LAN acceptance requires at least two physical devices on a Private network and explicit firewall authorization. Actual Cloudflare acceptance requires protected external infrastructure. Missing either condition is recorded as a manual gate, not disguised as a pass or treated as a code failure.

## 10. Preserved Boundaries

- `hard_isolation=false`
- `Executor=unknown`
- engineering roadshow prototype, not clinical deployment
- no real hospital or patient data
- no arbitrary model upload or arbitrary code execution
- no production privacy-computing or certification claim
- no movement of `v0.12` or historical tags

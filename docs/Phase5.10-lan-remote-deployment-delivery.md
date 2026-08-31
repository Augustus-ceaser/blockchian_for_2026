# Phase 5.10 LAN, Remote Preview And Deployment Preparation

Date: 2026-07-26

Status: loopback release candidate ready; real multi-device LAN and remote preview remain gated.

## 2026-07-26 Loopback Candidate Update

- Real fixed PathMNIST Coordinator is running without a Fake Executor.
- Controlled execution, result review, safe package, one-time download and callback replay passed.
- Wheelhouse round trip and the negative attack matrix passed.
- Real Chrome four-context, Console, Network, refresh and responsive acceptance passed.
- Gateway production builds explicitly use `VITE_DATA_MODE=api`.
- The 390px lifecycle-table page overflow was fixed with local containment.
- Candidate state: `loopback_ready=true`, `lan_manual_gate=true`,
  `remote_manual_gate=true`, `v0.13=not_created`.

## Delivered

- Safe-default `local`, `lan-roadshow`, `remote-preview` and `production-template` modes.
- Relative `/api/v1` production frontend behavior while local Vite keeps an explicit loopback API.
- One pinned Caddy gateway design with SPA fallback and conservative security headers.
- `/join` and four role portal entry routes with URL-only locally generated QR codes.
- Mode-aware Cookie security, trusted origins, browser mutation Origin/Referer checks and API docs policy.
- Operator-only safe active-session projection without token, cookie, full IP or fingerprint fields.
- Standalone LAN, remote-preview and production-template Compose files.
- PowerShell 5.1 network discovery, Private-only firewall, LAN prepare/status/stop and remote preview gate scripts.
- LAN, firewall, four-device, Cloudflare, deployment-mode, production and troubleshooting guides.

No database migration or Phase 5.1-5.9 business-state change was introduced.

## Verification

- Python compile: passed.
- Phase 5.10 backend focused tests: 6 passed.
- Disposable PostgreSQL regression: 152 passed; 12 environment-gated skips under the single-database command.
- Frontend: 46 tests passed.
- TypeScript: passed.
- Production frontend build: passed, 3707 modules.
- Phase 5.10 deployment/security rerun after the runtime fixes: 4 passed.
- PowerShell 5.1 parser: passed.
- LAN, remote-preview and production Compose config: passed.
- LAN rendered ports: only selected host IP TCP 8080 is published.
- `git diff --check`: passed before the final documentation update.
- Exact gateway images `node:22-alpine` and `caddy:2.10.2-alpine` were pulled successfully.
- Gateway and backend images built successfully after excluding host dependencies with the root `.dockerignore`.
- Loopback services running: gateway, backend, PostgreSQL, MinIO, Outbox Dispatcher and Callback Worker.
- Host listeners: only `127.0.0.1:8080`; ports 5173, 8000, 5432, 9000 and 9001 are not published.
- `/`, `/roadshow`, `/join` and all four `/portal/*` routes returned 200, including direct SPA refreshes.
- Anonymous `/docs` and `/openapi.json` returned 401; authenticated operator access returned 200.
- Authenticated OpenAPI contained 111 paths and 114 operations.
- LAN HTTP session Cookie was `HttpOnly`, `SameSite=Lax` and intentionally not `Secure`.
- Valid same-origin CORS preflight returned 200; an untrusted Origin returned 400 without `Access-Control-Allow-Origin`.
- Invalid-Origin browser mutation was rejected with 403.

The Phase 5.9 authoritative four skips are:

1. `test_catalog_postgresql.py::test_concurrent_publication_attempts_have_one_winner` - destructive Catalog race flag not enabled.
2. `test_compute_postgresql.py::test_run_count_atomic_reservation_and_rollback` - committed Compute race flag not enabled.
3. `test_pathmnist_controlled_smoke_postgresql.py` - explicit external PathMNIST controlled-smoke environment not configured.
4. `test_phase3_demo_api_postgresql.py` - independent Phase 3 demo database not configured.

The prior Phase 5.9 sentence describing all four as PathMNIST asset gates was inaccurate.

## Remaining Runtime Gates

The initial image failure was a transient Docker Registry authentication/proxy-path failure. Docker Engine remained healthy, and both exact images were later obtained through Docker Desktop's configured internal proxy. No untrusted mirror or global proxy change was introduced.

Loopback acceptance does not complete Phase 5.10:

- the in-app browser webview did not attach during this continuation, so visual/responsive and browser-console acceptance is not claimed;
- the current backend image does not contain `torch`, `numpy` or the fixed PathMNIST assets;
- the Execution Coordinator therefore remains an explicit environment gate and was not started with a Fake Executor;
- the active WLAN profile remains `Public`;
- no firewall rule was added;
- no second physical device has accessed `/join`;
- no four-device/four-session cross-device workflow has been accepted;
- no Cloudflare Access infrastructure or `cloudflared` runtime is available;
- no Tunnel or production deployment was started.

The current branch is `phase5.10-lan-deployment-wip` with protection commit `1456a27`. Subsequent runtime fixes remain uncommitted at the time of this report. Phase 5.10 is not sealed and `v0.13-phase5.10-lan-remote-deployment` must not be created.

## Preserved Boundaries

- `hard_isolation=false`
- `Executor=unknown`
- no real hospital or patient data
- no arbitrary model upload or arbitrary code execution
- no raw Artifact release
- one-time download semantics unchanged
- no movement of `v0.12` or historical tags

# Phase 5.10 Loopback Release Candidate

Date: 2026-07-26

## Decision

- `loopback_ready=true`
- `lan_manual_gate=true`
- `remote_manual_gate=true`
- `v0.13=not_created`

The unified gateway, fixed PathMNIST Coordinator, callback/result closure,
wheelhouse portability, negative security tests and real local-browser
acceptance are complete.

This is not a claim that Phase 5.10 is fully complete. Physical second-device
LAN, Private-network firewall and protected remote preview remain manual gates.

## Technical Evidence

- Host listener: only `127.0.0.1:8080`
- Frontend: 46 passed, TypeScript passed, 3707-module build passed
- Backend default suite: passed; PostgreSQL suites were environment-gated
- OpenAPI: 111 paths, 114 operations, 0 duplicate IDs
- Alembic head/current: `20260725_0032`
- Compose: local, LAN, remote-preview and production example passed
- PowerShell 5.1 parser: 24 scripts passed
- Wheelhouse: 40 wheels, pristine digests restored
- Browser: four contexts, no unexpected Console/page/network failures
- `hard_isolation=false`

No Phase 5.1-5.9 state machine, contract policy, run quota, Artifact quarantine,
package allowlist or one-time download semantic was changed.

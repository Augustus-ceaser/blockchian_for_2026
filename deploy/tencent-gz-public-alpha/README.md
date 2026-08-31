# Tencent Guangzhou Public Alpha Deployment

This directory is a local-only deployment package for the frozen
`v0.14-hospital-controlled-execution-alpha` baseline.

## Modes

- Pre-ICP: Gateway only on `127.0.0.1:18080`; no Caddy or public 80/443.
- Public: Caddy alone publishes 80/443 after ICP, DNS, firewall, backup, and
  human go-live gates.

## Default services

`postgres`, `minio`, `backend`, `dispatcher`, and `gateway`. The React
frontend is compiled into the Gateway image; there is no separate frontend
service. Coordinator, callback worker, Hospital Connector, and Executor are
not started on this 2-core/4GB central host.

## Order

`bootstrap-server.sh` -> `init-secrets.sh` -> `deploy-pre-icp.sh` ->
`create-admin.sh` -> `security-check.sh` -> `backup.sh` ->
`restore-test.sh`.

`create-admin.sh` initializes invitation accounts and the minimal Public Alpha
workspace only. `seed-public-alpha-demo.sh` is a separate optional action for
Synthetic/Public Non-clinical demo metadata; it is never run automatically.

Public activation is a later manual operation documented in
`GO_LIVE_CHECKLIST.md`.

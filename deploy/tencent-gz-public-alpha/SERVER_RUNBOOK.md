# Server Runbook

## Pre-ICP

1. Keep Tencent firewall limited to SSH.
2. Run `bootstrap-server.sh`, review the swap warning, then run
   `init-secrets.sh`.
3. Install the verified package under `/opt/medtrust`.
4. Run `deploy-pre-icp.sh`.
5. From the operator PC, use:

   `ssh -L 18080:127.0.0.1:18080 ubuntu@<SERVER_IP>`

6. Open `http://127.0.0.1:18080`.

Do not configure public DNS, open 80/443, start public Caddy, or request a
certificate while ICP review is pending.

## Post-ICP

1. Enter the real domain and ICP number in the root-only environment file.
2. Create the DNS A record and verify resolution.
3. Manually open only 80/443 in Tencent firewall.
4. Run a backup and `restore-test.sh`.
5. Run `go-live.sh` and verify HTTPS, redirect, footer, health, and port state.
6. Increase HSTS only after stable HTTPS operation.

Hospital Connector and Executor are not services in this central deployment.

# Pre-ICP Testing

During ICP review:

- Tencent firewall keeps only SSH open.
- Public DNS is not configured.
- Caddy is not started.
- Port 80 and 443 are not published.
- Gateway binds only `127.0.0.1:18080` on the server.
- PostgreSQL, MinIO, Backend, Connector, and Executor publish no host ports.

After the operator has intentionally deployed the verified package:

```powershell
.\scripts\deployment\tencent-gz\05-open-pre-icp-tunnel.ps1
```

Open `http://127.0.0.1:18080` locally. The browser connects to the local SSH
tunnel endpoint, not directly to a public application port.

Expected checks:

```bash
sudo deploy/tencent-gz-public-alpha/status.sh
sudo deploy/tencent-gz-public-alpha/health-check.sh pre-icp
sudo deploy/tencent-gz-public-alpha/security-check.sh
```

Pre-ICP uses loopback HTTP, so the session cookie is HttpOnly and SameSite=Lax
but intentionally not Secure. Public mode switches it to Secure under HTTPS.

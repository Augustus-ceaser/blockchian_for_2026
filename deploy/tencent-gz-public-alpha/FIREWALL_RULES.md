# Tencent Firewall Rules

## During ICP review

- Allow inbound SSH TCP 22 only from the operator's current trusted IP where
  practical.
- Do not open TCP 80, TCP 443, UDP 443, 8000, 8080, 5432, 9000, or 9001.
- The server entry binds only `127.0.0.1:18080`.
- Access is through an operator-created SSH tunnel.

## After ICP approval

- Keep SSH restricted.
- Open TCP 80 and TCP/UDP 443 only after DNS and `PUBLIC_DOMAIN` are verified.
- Do not expose database, object storage, Backend, Gateway, Connector,
  Executor, or Docker API ports.

These are operator instructions. No script in this package changes Tencent
Cloud firewall rules or the host firewall.

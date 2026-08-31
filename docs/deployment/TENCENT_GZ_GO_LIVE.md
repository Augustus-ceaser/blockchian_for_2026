# Tencent Guangzhou Public Go-Live

Public go-live is a separate manual gate after ICP approval.

1. Obtain the real ICP number. Never use a placeholder.
2. Set `PUBLIC_DOMAIN`, `PUBLIC_BASE_URL`, `ICP_NUMBER`, and optional ACME
   contact in `/etc/medtrust/production.env`.
3. Create and verify the DNS A record.
4. Complete backup and independent restore testing.
5. Manually open only 80/443 in Tencent firewall.
6. Run `go-live.sh` and type its explicit confirmation.
7. Verify HTTPS, HTTP redirect, API health, login, logout, and the real ICP
   footer link.
8. Confirm all internal ports remain unreachable externally.
9. Keep HSTS at `max-age=0` until certificate renewal and HTTPS operation are
   stable, then increase it deliberately.

Caddy stores certificate material in named volumes. It uses normal certificate
verification and contains no `tls_insecure_skip_verify`.

The script does not configure DNS or Tencent firewall and cannot determine
legal ICP approval by itself.

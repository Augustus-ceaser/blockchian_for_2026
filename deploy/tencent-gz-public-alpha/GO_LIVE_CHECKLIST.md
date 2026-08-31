# Go-Live Checklist

- [ ] ICP approval is confirmed and the real ICP number is available.
- [ ] `PUBLIC_DOMAIN` is final and the DNS A record resolves to the server.
- [ ] The ICP number is set in `/etc/medtrust/production.env`.
- [ ] Backup and independent restore test both pass.
- [ ] Package, secret, port, and Compose security checks pass.
- [ ] Tencent firewall 80/443 opening is a documented manual action.
- [ ] `go-live.sh` human confirmation is completed.
- [ ] HTTPS certificate issuance succeeds without insecure TLS options.
- [ ] HTTP redirects to HTTPS.
- [ ] The real ICP number appears in the site footer.
- [ ] PostgreSQL, MinIO, Backend, Gateway, Connector, and Executor ports remain private.
- [ ] `hard_isolation=false` and Non-clinical Alpha notices remain visible.
- [ ] HSTS remains `max-age=0` until HTTPS stability is verified.

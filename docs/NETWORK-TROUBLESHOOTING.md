# Network Troubleshooting

- **Public network:** connect to or configure a trusted Private network; do not bypass the guard.
- **Multiple adapters:** select the physical Wi-Fi or Ethernet alias explicitly. Do not choose VPN, WSL, Hyper-V or APIPA addresses.
- **Gateway port occupied:** stop the owning process or choose a controlled alternate port in the ignored local environment file and firewall rule.
- **Frontend opens but API fails:** verify the gateway and backend containers; clients must not use port 8000 directly.
- **Portal refresh returns 404:** verify the Caddy SPA fallback and rebuild the gateway image.
- **Login cookie fails on LAN:** confirm the browser uses the exact HTTP gateway origin and LAN mode, not an HTTPS URL or direct API port.
- **Remote preview blocked:** verify cloudflared is installed, the named configuration exists and Access protection was confirmed.
- **Stop leaves firewall rule:** inspect and remove only the stable MedTrust rule with `configure_lan_firewall.ps1`.

Never expose database, object storage, Docker or development server ports as a workaround.

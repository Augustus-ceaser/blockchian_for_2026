# Deployment Modes

| Mode | Purpose | Public entry | API docs |
|---|---|---|---|
| `local` | Existing host Uvicorn and Vite workflow | Loopback only | Enabled |
| `lan-roadshow` | Multiple devices on one trusted Private network | One Caddy gateway port | Operator session only |
| `remote-preview` | Invited external reviewers through Access and Tunnel | Protected HTTPS hostname | Disabled |
| `production-template` | Configuration review only | Not deployed by Phase 5.10 | Disabled |

The default is always `local`. PostgreSQL, MinIO, backend port 8000, Vite and Docker are never client-facing entries.

This remains an engineering prototype with `hard_isolation=false` and `Executor=unknown`.

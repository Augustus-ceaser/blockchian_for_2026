# Roadshow Quick Start

Run from the repository root.

## Local Loopback

```powershell
.\scripts\roadshow-preflight.ps1 -SkipHttp
.\scripts\roadshow-start.ps1
.\scripts\roadshow-status.ps1
```

Open `http://127.0.0.1:5173/roadshow`.

Credentials are read from ignored local configuration. Scripts never print
passwords. Phase 5.12.7 start is read-only: it does not run migrations, reset
the database, prepare demo records, download assets, or create a ComputeJob.

## Stop

```powershell
.\scripts\roadshow-stop.ps1
```

The stop command releases application ports 3000, 4173, 5173, 8000 and 8080.
Canonical PostgreSQL and MinIO remain running on loopback.

## Fail-Closed Checks

Preflight rejects:

- wrong Alembic head or canonical counts;
- wrong PostgreSQL or MinIO volume identity;
- multiple PostgreSQL writers;
- invalid audit chain or changed audit head;
- changed manifest state;
- non-loopback canonical storage listeners;
- unavailable managed application services during full preflight;
- insufficient disk space.

LAN and remote preview remain manual gates. This phase does not change the
Windows network category, firewall, Tunnel, gateway exposure, or WLAN ports.

